# 贡献指南

欢迎修复可复现的问题、补充明确的兼容性验证或改善文档。先阅读 [架构](docs/architecture.md)、[已知限制](docs/known-limitations.md) 和 [AGENTS.md](AGENTS.md)，保持采集、提前备稿、缓存读取三个阶段独立。

## 提交问题

普通 Issue 请提供版本或提交号、操作系统、Python/Node/Codex 版本、最小复现步骤、预期与实际行为。使用合成邮件重现；不要上传自己的邮件、生成稿、凭据、私人部署地址或完整运行状态。

安全问题按 [SECURITY.md](SECURITY.md) 私密报告，不在普通 Issue 公开复现凭据或可直接利用的细节。

## 开发与验证

~~~bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest
node --test tests/test_collector.cjs
~~~

行为变更沿用仓库 OpenSpec：先明确需求和验收场景，实施后同步规格并归档完成的变更。纯文案修正不必新建规格。改动应尽量小，复用现有组件，并为真实边界行为补充测试，不增加只检查文件存在的占位测试。

涉及来源策略时，同步 API、worker 与 Apps Script；涉及已保存状态时，说明迁移方式。不要通过关闭检查、丢弃原文或放宽时限来取得通过。发布前运行完整 Python 与采集器测试，其他验证按 [验收文档](docs/acceptance.md) 选择适用范围。

修改打包内容时，构建 wheel 并在仓库外的空目录安装、运行验证。只从仓库根目录 import 成功可能掩盖遗漏的 package data；尤其要检查 source_policy.json 和 management 静态资源。

## Pull Request

说明具体问题、修改后的行为和真实验证结果。区分代码实现、功能验证和真实部署；没有执行的 CI、平台调用或设备播放明确写为未验证。只附脱敏结果，不附 runtime/。

版本以 [pyproject.toml](pyproject.toml) 为唯一来源；发布变更写入 [CHANGELOG.md](CHANGELOG.md)，不要新增第二份 VERSION 文件或为无关修正变更版本。代码采用 [MIT](LICENSE)，贡献中仅包含你有权提供的内容。
