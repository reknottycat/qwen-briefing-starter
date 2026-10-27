# Qwen Briefing Starter

把邮件中的资讯、学习材料和工作更新，变成千问可以直接朗读的个人简报。

A self-hosted starter for cached briefings: Gmail → FastAPI → Codex worker → Qwen HTTP tool and Speak.

这个项目先收集邮件、提前准备稿件，再让千问读取缓存。语音请求不临时访问 Gmail，也不等待模型生成。生成端可以运行在一台常开的 Linux 主机上，**不需要 DGX、GPU 或常开 Windows 电脑**。

- 设置页可为每个来源选择原文或整理稿，并设置详略和短偏好；API 另保留备用播客对话模式。
- “今日简报”包含新闻与学习概要；“今天应该学什么”读取完整学习内容。
- 工作内容默认关闭，开启后才能进入相应视图。
- 自带浏览器设置页、Gmail 云端采集器、角色隔离的 API、部署模板和验收脚本。

## 开始使用

先安装 Python 3.11+ 和 Node.js 22.20+，然后在仓库目录运行：

~~~bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest
node --test tests/test_collector.cjs
~~~

这些检查使用合成数据。接入自己的邮箱、HTTPS 服务与千问工具，请按 [快速开始](docs/quickstart.md) 操作。仓库附带的十个来源是示例，不会创建邮件订阅或自动生产这些内容。

已有后端时，可安装 Codex 管理 Skill：

~~~bash
npx skills add reknottycat/qwen-briefing-starter --skill add-to-briefing -a codex
~~~

这个 Skill 只管理已部署的后端；安装它不会部署 API、worker、Gmail 采集器或千问插件。详见 [Skill 说明](skills/add-to-briefing/SKILL.md)。

本项目是面向开发者的部署起点。千问开发者能力、账号权限、网络连接和设备播放需要在自己的环境完成验证；代码测试或 HTTP 通过不代表眼镜已经完整播完。详见 [已知限制](docs/known-limitations.md)。

## 文档

| 文档 | 内容 |
| --- | --- |
| [快速开始](docs/quickstart.md) | 本地检查、运行 API、接入 Gmail、worker 和千问 |
| [邮件格式与来源策略](docs/mail-format.md) | 标题、事件类型、正文，以及如何定制来源 |
| [架构与接口](docs/architecture.md) | 数据流、权限、朗读模式和四个读取视图 |
| [运行维护](docs/operations.md) | 设置、重建、状态、备份与凭据轮换 |
| [验收](docs/acceptance.md) | 自动化检查、HTTP 验收、平台与设备验收 |
| [已知限制](docs/known-limitations.md) | 容量、会话、超时、单进程与平台边界 |
| [贡献指南](CONTRIBUTING.md) · [安全报告](SECURITY.md) · [变更记录](CHANGELOG.md) | 协作与维护 |

代码使用 [MIT 许可证](LICENSE)。Qwen、Gmail 和 Codex 为各自提供方的产品；本仓库是独立集成项目。
