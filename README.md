# 学习通位置签到脚本

通过 HTTP 协议复现完成超星学习通（Chaoxing）**位置签到**，无需浏览器，命令行一键运行。

> ⚠️ 仅供学习研究使用，请勿滥用。

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 创建配置文件
copy config.example.json config.json   # Windows
# cp config.example.json config.json   # Linux/Mac

# 3. 编辑 config.json，填入你的信息（详见下方配置说明）

# 4. 运行
python sign.py

# 测试模式（不会实际签到，验证流程是否畅通）
python sign.py --dry-run
```

## 配置说明

编辑 `config.json`：

```json
{
    "username": "138xxxx1234",
    "password": "你的密码",
    "course_name": "高等数学",
    "course_url": "",
    "location": {
        "address": "XX大学 教三楼201",
        "latitude": "30.123456",
        "longitude": "120.123456"
    }
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `username` | ✅ | 学习通登录手机号 |
| `password` | ✅ | 学习通密码 |
| `course_name` | 二选一 | 课程名称关键词，支持模糊匹配 |
| `course_url` | 二选一 | 课程页面完整 URL，精确指定（如 API 获取不到课程列表时使用） |
| `location.address` | ✅ | 签到地址名称（显示给老师看的） |
| `location.latitude` | ✅ | 纬度（可用[百度坐标拾取](https://api.map.baidu.com/lbsapi/getpoint/index.html)获取） |
| `location.longitude` | ✅ | 经度 |

> **`course_name` vs `course_url`**：优先填 `course_url`（直接从课程页面解析 ID）。如果留空，脚本会通过 API 获取课程列表后用 `course_name` 模糊匹配。

## 执行流程

```
登录 → 获取课程 → 匹配目标课程 → 检测签到活动 → 提交位置签到
```

每一步都有清晰的控制台输出，成功/失败一目了然。

## 依赖

- Python 3.10+
- requests
- pycryptodome

## 项目结构

```
chaoxing-checkin/
├── sign.py              # 主脚本
├── config.json          # 本地配置（已 gitignore）
├── config.example.json  # 配置模板
├── requirements.txt     # 依赖
├── .gitignore
└── README.md
```

## 常见问题

**Q: 提示"所有课程列表 API 均不可用"？**
在 `config.json` 中填写 `course_url` 字段，从浏览器地址栏复制你的课程页面完整 URL。

**Q: 位置签到需要真实的 GPS 吗？**
不需要，脚本直接向 API 提交经纬度参数，可以填写任何坐标。

**Q: 会被检测到吗？**
脚本使用手机端 User-Agent，请求频率低（手动运行），风险较小。但任何自动化工具都存在账号风控的可能。

## License

MIT
