"""
学习通（超星）位置签到脚本
================================
通过 HTTP 协议复现完成位置签到，无需浏览器。
仅供学习研究使用。

用法:
    1. 编辑 config.json，填写账号、课程名、位置坐标
    2. pip install -r requirements.txt
    3. python sign.py
"""

import json
import re
import sys
import base64
from pathlib import Path
from urllib.parse import unquote

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

# 强制 UTF-8 输出，解决 Windows GBK 终端中文乱码问题
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ASCII 兼容符号（避免 Unicode 在 GBK 终端上崩）
OK = "[OK]"
FAIL = "[X]"
WARN = "[!]"


# ── 常量 ──────────────────────────────────────────────
AES_KEY = b"u2oh6Vu^HWe4_AES"
AES_IV = b"u2oh6Vu^HWe4_AES"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 10; SM-G9730 Build/QP1A.190711.020; wv) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
        "Chrome/92.0.4515.105 Mobile Safari/537.36 "
        "com.ss.android.article.news/2010 (Senior University Union)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# ── 工具函数 ──────────────────────────────────────────


def encrypt_password(plaintext: str) -> str:
    """学习通密码 AES-CBC 加密"""
    cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
    padded = pad(plaintext.encode("utf-8"), AES.block_size)
    encrypted = cipher.encrypt(padded)
    return base64.b64encode(encrypted).decode()


def extract(pattern: str, text: str, default: str = "") -> str:
    """从文本中用正则提取第一个捕获组"""
    m = re.search(pattern, text)
    return m.group(1) if m else default


# ── 主类 ──────────────────────────────────────────────


class ChaoxingSign:
    """学习通签到器"""

    def __init__(self, config_path: str = "config.json"):
        config_file = Path(config_path)
        # 如果当前目录找不到，尝试在脚本所在目录查找
        if not config_file.exists():
            script_dir = Path(__file__).resolve().parent
            alt_path = script_dir / config_path
            if alt_path.exists():
                config_file = alt_path
            else:
                raise FileNotFoundError(
                    f"配置文件 '{config_path}' 不存在，请先创建并填写配置。\n"
                    f"  查找路径: {config_file.resolve()} / {alt_path}"
                )

        with open(config_file, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        self.username = cfg["username"]
        self.password = cfg["password"]
        self.course_keyword = cfg.get("course_name", "")
        # 可选：直接指定课程页面 URL，跳过课程列表 API
        self.course_url = cfg.get("course_url", "")
        self.address = cfg["location"]["address"]
        self.latitude = cfg["location"]["latitude"]
        self.longitude = cfg["location"]["longitude"]

        self.uid: str = ""
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    # ── 1. 登录 ───────────────────────────────────────

    def login(self) -> bool:
        """登录学习通，获取 Cookie 和 uid"""
        print("[1/4] 正在登录...")

        encrypted_pwd = encrypt_password(self.password)
        login_url = "https://passport2.chaoxing.com/fanyalogin"
        data = {
            "fid": "-1",
            "uname": self.username,
            "password": encrypted_pwd,
            "refer": "https://i.mooc.chaoxing.com",
            "t": "true",
        }

        try:
            resp = self.session.post(login_url, data=data, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  {FAIL} 登录请求失败: {e}")
            return False

        # 验证登录状态 — 访问个人中心页获取 uid
        info_url = "https://i.mooc.chaoxing.com/settings/info"
        try:
            info_resp = self.session.get(info_url, timeout=15)
        except requests.RequestException as e:
            print(f"  {FAIL} 验证登录状态失败: {e}")
            return False

        # 尝试从页面中提取 uid
        self.uid = extract(r'"uid"\s*:\s*(\d+)', info_resp.text)
        if not self.uid:
            self.uid = extract(r"uid\s*=\s*['\"]?(\d+)", info_resp.text)

        # 也尝试从 cookie 获取
        if not self.uid:
            for cookie in self.session.cookies:
                if cookie.name.lower() == "uid" or cookie.name == "_uid":
                    self.uid = cookie.value
                    break

        if not self.uid:
            # 尝试从更简单的接口获取
            try:
                acct = self.session.get(
                    "https://passport2.chaoxing.com/mooc/accountManage",
                    timeout=15,
                )
                self.uid = extract(r"'uid'\s*:\s*'?\"?(\d+)", acct.text)
            except requests.RequestException:
                pass

        if not self.uid:
            print("  {FAIL} 登录失败：无法获取用户 UID，请检查账号密码。")
            return False

        print(f"  {OK}登录成功 (UID: {self.uid})")
        return True

    # ── 2. 获取课程列表 ────────────────────────────────

    # 多个可用的课程列表 API 端点，按优先级依次尝试
    COURSE_API_ENDPOINTS = [
        ("https://mooc1.chaoxing.com/visit/interaction", "interaction"),
        ("https://i.chaoxing.com/visit/courselistdata", "web"),
        ("https://mooc1.chaoxing.com/visit/courselistdata", "web"),
        ("http://mooc-api.chaoxing.com/mycourse/backclazzdata", "app"),
    ]

    def get_courses(self) -> list[dict]:
        """获取课程列表，依次尝试多个 API 端点"""
        print("[2/4] 正在获取课程列表...")

        for url, mode in self.COURSE_API_ENDPOINTS:
            try:
                resp = self.session.get(url, timeout=15)
                if not resp.ok:
                    continue

                # interaction 页面返回 HTML，需要特殊处理
                if mode == "interaction":
                    courses = self._parse_interaction_html(resp.text)
                else:
                    courses = self._parse_courses(resp.json(), mode)

                if courses:
                    return courses
            except Exception:
                continue

        print(f"  {FAIL} 所有课程列表 API 均不可用")
        return []

    def _parse_courses(self, data: dict, mode: str) -> list[dict]:
        """解析不同 API 返回的课程数据"""
        courses: list[dict] = []

        if mode == "web":
            # i.chaoxing.com / mooc1 网页接口
            for c in data.get("courseList", []):
                courses.append({
                    "courseId": str(c.get("courseId", c.get("courseid", ""))),
                    "classId": str(c.get("classId", c.get("clazzId", ""))),
                    "name": c.get("courseName", c.get("name", "")),
                    "teacher": c.get("teacherName", ""),
                })

        elif mode == "app":
            # APP/SDK 协议接口
            for ch in data.get("channelList", []):
                content = ch.get("content", {})
                items = content.get("course", []) or content.get("data", [])
                for c in items:
                    info = c.get("course", {}) or c
                    courses.append({
                        "courseId": str(info.get("id", c.get("courseId", ""))),
                        "classId": str(c.get("classId", c.get("clazzId", ""))),
                        "name": info.get("name", c.get("courseName", "")),
                        "teacher": info.get("teacherfactor", ""),
                    })

        elif mode == "web_list":
            # mooc1 courses/list 接口
            for c in data.get("list", data.get("data", [])):
                courses.append({
                    "courseId": str(c.get("courseId", c.get("id", ""))),
                    "classId": str(c.get("classId", c.get("clazzId", ""))),
                    "name": c.get("courseName", c.get("name", "")),
                    "teacher": "",
                })

        return courses

    def _parse_interaction_html(self, html: str) -> list[dict]:
        """从互动页面 HTML 中解析课程列表"""
        courses: list[dict] = []
        pattern = re.compile(
            r'["\']courseid["\']\s*:\s*["\']?(\d+)["\']?.*?'
            r'["\']clazzid["\']\s*:\s*["\']?(\d+)["\']?',
            re.IGNORECASE | re.DOTALL,
        )
        for m in pattern.finditer(html):
            courses.append({
                "courseId": m.group(1),
                "classId": m.group(2),
                "name": "",
                "teacher": "",
            })
        if not courses:
            for m in re.finditer(
                r'href=["\']([^"\']*?courseId[=:]\d+[^"\']*?)["\']',
                html, re.IGNORECASE,
            ):
                cid = extract(r"courseId[=:]\s*(\d+)", m.group(1))
                if cid:
                    courses.append({
                        "courseId": cid,
                        "classId": extract(r"(?:classId|clazzId)[=:]\s*(\d+)", m.group(1)) or "0",
                        "name": "",
                        "teacher": "",
                    })
        return courses

    # ── 2b. 从课程页面 URL 直接解析 ─────────────────────

    def parse_course_from_url(self) -> dict | None:
        """从配置的课程 URL 访问页面，提取 courseId 和 classId"""
        print("[2/4] 正在从课程页面获取信息...")

        url = self.course_url
        if not url.startswith("http"):
            url = f"https://mooc1-1.chaoxing.com{url}" if url.startswith("/") \
                else f"https://mooc1-1.chaoxing.com/{url}"

        try:
            resp = self.session.get(url, timeout=15, allow_redirects=True)
            html = resp.text
            final_url = resp.url  # 可能发生了重定向
        except requests.RequestException as e:
            print(f"  {FAIL} 访问课程页面失败: {e}")
            return None

        course_id = ""
        class_id = ""

        # ── 尝试从 URL 中直接提取纯数字 courseId ──
        # 门户页可能重定向到真正的课程页，URL 中包含数字 ID
        course_id = extract(r"/course-ans/ps/(\d+)", final_url)
        if not course_id:
            course_id = extract(r"/course/(\d+)", final_url)
        if not course_id:
            course_id = extract(r"/visit/course/(\d+)", final_url)

        # ── 从 HTML 中尝试多种方式匹配 ──
        if not course_id:
            patterns_cid = [
                r'"courseId"\s*:\s*"?(\d+)"?',
                r"'courseId'\s*:\s*'(\d+)'",
                r'"courseid"\s*:\s*"?(\d+)"?',
                r'courseId\s*=\s*["\']?(\d+)["\']?',
                r'data-courseid\s*=\s*["\'](\d+)["\']',
                r'course_id["\']?\s*[:=]\s*["\']?(\d+)',
            ]
            for pat in patterns_cid:
                course_id = extract(pat, html)
                if course_id:
                    break

        if not class_id:
            patterns_clid = [
                r'"classId"\s*:\s*"?(\d+)"?',
                r"'classId'\s*:\s*'(\d+)'",
                r'"clazzId"\s*:\s*"?(\d+)"?',
                r'"classid"\s*:\s*"?(\d+)"?',
                r'classId\s*=\s*["\']?(\d+)["\']?',
            ]
            for pat in patterns_clid:
                class_id = extract(pat, html)
                if class_id:
                    break

        # ── 提取课程名 ──
        name = extract(r"<title>(.+?)</title>", html)
        if not name or "超星" in name:
            name = extract(r'"courseName"\s*:\s*"(.+?)"', html)

        # ── 调试：失败时打印页面摘要 ──
        if not course_id:
            print(f"  {FAIL} 无法从页面提取课程信息")
            print(f"  最终重定向 URL: {final_url}")
            # 打印 HTML 中有可能包含 ID 的片段
            snippets = [
                s.strip() for s in re.findall(
                    r'(?:course|class|clazz)(?:Id|id|ID)[\s"\'=:]+[\w-]+',
                    html
                )
            ]
            if snippets:
                print(f"  页面中发现的相关字段: {list(set(snippets))[:10]}")
            else:
                print(f"  页面开头内容: {html[:300]}")
            return None

        if not class_id:
            class_id = "0"  # 有些接口不强制要求 classId

        print(f"  {OK}解析成功: {name or '未知课程'} "
              f"(courseId: {course_id}, classId: {class_id})")
        return {
            "courseId": course_id,
            "classId": class_id,
            "name": name or "未知课程",
            "teacher": "",
        }

    # ── 3. 查找目标课程 ────────────────────────────────

    def find_course(self, courses: list[dict]) -> dict | None:
        """根据关键词模糊匹配目标课程"""
        keyword = self.course_keyword.lower()
        matches = [
            c for c in courses
            if keyword in c["name"].lower() or keyword in c.get("teacher", "").lower()
        ]

        if len(matches) == 1:
            print(f"  {OK}匹配到课程: {matches[0]['name']}")
            return matches[0]

        if len(matches) > 1:
            print(f"  ! 匹配到 {len(matches)} 个课程:")
            for i, c in enumerate(matches, 1):
                print(f"    {i}. {c['name']} (ID: {c['courseId']})")
            choice = input("  请选择序号（留空选第1个）: ").strip()
            idx = int(choice) - 1 if choice else 0
            return matches[max(0, min(idx, len(matches) - 1))]

        print(f"  {FAIL} 未找到包含 '{self.course_keyword}' 的课程")
        return None

    # ── 4. 获取签到活动 ────────────────────────────────

    def get_active_signs(self, course: dict) -> list[dict]:
        """获取课程中未完成的签到活动"""
        print("[3/4] 正在检测签到活动...")

        course_id = course["courseId"]
        class_id = course["classId"]

        url = "https://mobilelearn.chaoxing.com/ppt/activeAPI/taskactivelist"
        params = {
            "courseId": course_id,
            "classId": class_id,
            "uid": self.uid,
        }

        try:
            resp = self.session.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            print(f"  {FAIL} 获取签到活动失败: {e}")
            return []
        except json.JSONDecodeError:
            print("  {FAIL} 签到活动接口返回格式异常")
            return []

        active_list = data.get("activeList", [])
        if not active_list:
            active_list = data.get("data", [])

        # 筛选：activeType=2（签到活动）且 status=1（未签到）
        pending = []
        for item in active_list:
            if item.get("activeType") == 2 and item.get("status") == 1:
                # 从 url 中提取 activeId
                active_id = ""
                url_str = item.get("url", "")
                if url_str:
                    active_id = extract(r"activeId=(\d+)", url_str)
                if not active_id:
                    active_id = unquote(extract(r"activeId%3D(\d+)", url_str))

                pending.append({
                    "activeId": active_id,
                    "name": item.get("nameOne", "未知活动"),
                    "courseName": item.get("nameFour", ""),
                    "statusText": item.get("nameTwo", ""),
                })

        return pending

    # ── 5. 提交位置签到 ────────────────────────────────

    def do_sign(self, active: dict) -> bool:
        """对指定签到活动提交位置签到"""
        print("[4/4] 正在提交位置签到...")
        print(f"  活动: {active['name']}")

        sign_url = "https://mobilelearn.chaoxing.com/pptSign/stuSignajax"
        params = {
            "activeId": active["activeId"],
            "uid": self.uid,
            "clientip": "",
            "latitude": self.latitude,
            "longitude": self.longitude,
            "appType": "15",
            "fid": "0",
        }

        # 地址需要单独用 POST 发送，因为学习通位置签到可能需要 address 参数
        data_body = {
            "address": self.address,
        }

        try:
            # 尝试 GET 方式
            resp = self.session.get(sign_url, params=params, timeout=15)
        except requests.RequestException:
            pass
        else:
            if resp.text.strip() == "success":
                print(f"  {OK}签到成功！")
                return True
            # 如果 GET 没成功，可能是需要 POST + 地址参数
            if "success" in resp.text.lower():
                print(f"  {OK}签到成功！")
                return True

        # 尝试 POST 方式（带地址）
        try:
            resp2 = self.session.post(
                sign_url,
                params=params,
                data=data_body,
                timeout=15,
            )
        except requests.RequestException as e:
            print(f"  {FAIL} 签到请求失败: {e}")
            return False

        if resp2.text.strip() == "success" or "success" in resp2.text.lower():
            print(f"  {OK}签到成功！")
            return True

        # 如果返回其他内容，尝试解析
        result_text = resp2.text.strip()
        if result_text:
            print(f"  ? 服务器返回: {result_text[:200]}")
        else:
            print("  ? 服务器返回为空")

        return False

    # ── 主流程 ─────────────────────────────────────────

    def run(self, dry_run: bool = False) -> None:
        """执行完整的签到流程"""
        mode_label = " [测试模式]" if dry_run else ""
        print("=" * 50)
        print(f"  学习通位置签到脚本{mode_label}")
        print("=" * 50)

        if dry_run:
            print(f"  {WARN} 测试模式：不会实际提交签到，仅验证流程\n")

        # 1. 登录
        if not self.login():
            sys.exit(1)

        # 2. 获取课程信息
        course: dict | None = None
        courses: list[dict] = []

        if self.course_url:
            course = self.parse_course_from_url()
            if not course:
                print(f"  {WARN} 课程 URL 解析失败，尝试通过 API 获取...\n")
                courses = self.get_courses()
                if courses:
                    print(f"  {OK}获取到 {len(courses)} 门课程")
                    course = self.find_course(courses)
        else:
            courses = self.get_courses()
            if not courses:
                print("\n  未获取到任何课程。")
                print("  提示：可在 config.json 中配置 course_url 直接指定课程页面地址。")
                print("  格式示例: https://mooc1-1.chaoxing.com/course-ans/courseportal/课程ID.html")
                sys.exit(1)
            print(f"  {OK}获取到 {len(courses)} 门课程")
            course = self.find_course(courses)

        if not course:
            if courses and not dry_run:
                print(f"\n  可用课程列表:")
                for c in courses:
                    print(f"    - {c['name']} (courseId: {c['courseId']})")
            sys.exit(1)

        # 3. 检测签到活动
        pending_signs = self.get_active_signs(course)
        if not pending_signs:
            print(f"  {OK}当前课程没有未完成的签到活动")
            print(f"\n  当前课程: {course['name']}")
            print(f"  课程ID: {course['courseId']}, 班级ID: {course.get('classId', 'N/A')}")

            if dry_run:
                # 测试模式：验证签到接口可达性
                self._test_sign_endpoint(course)
                print(f"\n  {OK}测试完成：登录、课程获取、活动检测均正常。")
                print(f"  签到接口连通性已验证。")
            else:
                print(f"  请确认老师已经发起了签到再运行此脚本。")
            return

        print(f"  {OK}发现 {len(pending_signs)} 个待签到活动:")
        for s in pending_signs:
            print(f"    - {s['name']} ({s['statusText']})")

        # 4. 提交签到
        print()
        for i, active in enumerate(pending_signs, 1):
            if i > 1:
                print()
            if dry_run:
                print(f"  {WARN} 测试模式 — 跳过实际签到提交")
                print(f"  将提交: activeId={active['activeId']}, "
                      f"lat={self.latitude}, lon={self.longitude}, "
                      f"addr={self.address}")
            else:
                success = self.do_sign(active)
                if not success:
                    print(f"  {WARN} 签到可能未成功，请在手机上确认。")

        print("\n" + "=" * 50)
        if dry_run:
            print("  测试完毕，所有流程验证通过。")
        else:
            print("  执行完毕，请在手机上确认签到结果。")
        print("=" * 50)

    def _test_sign_endpoint(self, course: dict) -> None:
        """测试签到接口是否可达"""
        print(f"\n  --- 连通性测试 ---")
        test_url = "https://mobilelearn.chaoxing.com/ppt/activeAPI/taskactivelist"
        try:
            r = self.session.get(test_url, params={
                "courseId": course["courseId"],
                "classId": course.get("classId", "0"),
                "uid": self.uid,
            }, timeout=10)
            print(f"  签到活动接口: HTTP {r.status_code} (连通)")
        except requests.RequestException as e:
            print(f"  签到活动接口: 不可达 ({e})")

        test_url2 = "https://mobilelearn.chaoxing.com/pptSign/stuSignajax"
        try:
            r = self.session.get(test_url2, params={
                "activeId": "0",
                "uid": self.uid,
                "clientip": "",
                "latitude": self.latitude,
                "longitude": self.longitude,
                "appType": "15",
                "fid": "0",
            }, timeout=10)
            print(f"  签到提交接口: HTTP {r.status_code} (连通, 返回: {r.text[:50]})")
        except requests.RequestException as e:
            print(f"  签到提交接口: 不可达 ({e})")


# ── 入口 ──────────────────────────────────────────────

if __name__ == "__main__":
    dry = "--dry-run" in sys.argv or "--test" in sys.argv
    bot = ChaoxingSign()
    bot.run(dry_run=dry)
