# Qwen Briefing Starter

把邮件中的资讯、学习材料和工作更新，变成千问可以直接朗读的个人简报。

A self-hosted starter for cached briefings: existing daily tasks → Gmail → Codex or Pi → Qwen HTTP tool and Speak.

这个项目复用你已有的每日报告任务，先收集邮件、提前准备稿件，再让千问读取缓存。服务器通过 Codex 或轻量的 Pi 调用云端模型，**不需要 DGX、GPU 或常开 Windows 电脑**。眼镜发起请求时只读缓存，不等待模型生成。

- 设置页可为每个来源选择原文或整理稿，并设置详略和短偏好；API 另保留备用播客对话模式。
- “今日简报”包含新闻与学习概要；“今天应该学什么”读取完整学习内容。
- 工作内容默认关闭，开启后才能进入相应视图。
- 自带浏览器设置页、Gmail 云端采集器、角色隔离的 API、部署模板和验收脚本。

## 开始使用

让 Codex 或 OpenCode 使用首次配置 Skill：

~~~bash
npx skills add reknottycat/qwen-briefing-starter --skill setup-qwen-briefing -a codex
~~~

OpenCode 与 Codex 共用同一份标准 SKILL.md；[安装说明](docs/setup.md) 提供共同的 .agents/skills 放置方式。OpenCode 作为配置助手使用，不是服务器生成后端。

配置助手会按顺序完成：

1. 找到你已有、启用的每日非监控任务，提出新闻/学习/工作分类和朗读方案，等你确认。
2. 再确认服务器、Docker 或 Linux 部署，以及 Codex 或 Pi 生成方式；密钥由你在本地配置。
3. 导出私有来源配置，部署并核对 Gmail、生成任务和缓存接口。
4. 最后在你登录的千问开发者平台配置 HTTP 工具，绑定自己的眼镜并验证完整朗读。

完整自助步骤见 [首次安装](docs/setup.md)。Skill 不会把不存在的任务补建出来；仓库十个来源仅作示例。安装 Skill 本身也不会创建服务器、完成登录或通过设备验收。已有部署继续用下面的管理 Skill，不必重走安装。

已有后端时，可安装 Codex 管理 Skill：

~~~bash
npx skills add reknottycat/qwen-briefing-starter --skill add-to-briefing -a codex
~~~

这个 Skill 只管理已部署的后端；安装它不会部署 API、worker、Gmail 采集器或千问插件。详见 [Skill 说明](skills/add-to-briefing/SKILL.md)。

本项目是面向开发者的部署起点。千问开发者能力、账号权限、网络连接和设备播放需要在自己的环境完成验证；代码测试或 HTTP 通过不代表眼镜已经完整播完。详见 [已知限制](docs/known-limitations.md)。

## 文档

| 文档 | 内容 |
| --- | --- |
| [首次安装](docs/setup.md) | 任务发现、两步确认、私有配置、Docker 与千问接入 |
| [手动运行与开发](docs/quickstart.md) | 本地检查及已有 Codex/Linux 环境的手动运行 |
| [邮件格式与来源策略](docs/mail-format.md) | 标题、事件类型、正文，以及如何定制来源 |
| [架构与接口](docs/architecture.md) | 数据流、权限、朗读模式和四个读取视图 |
| [运行维护](docs/operations.md) | 设置、重建、状态、备份与凭据轮换 |
| [验收](docs/acceptance.md) | 自动化检查、HTTP 验收、平台与设备验收 |
| [已知限制](docs/known-limitations.md) | 容量、会话、超时、单进程与平台边界 |
| [贡献指南](CONTRIBUTING.md) · [安全报告](SECURITY.md) · [变更记录](CHANGELOG.md) | 协作与维护 |

代码使用 [MIT 许可证](LICENSE)。Qwen、Gmail 和 Codex 为各自提供方的产品；本仓库是独立集成项目。
