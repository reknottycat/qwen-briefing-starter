# 首次安装：从已有任务到千问眼镜

这套流程先确定你要听什么，再配置服务，最后连接眼镜。Codex 或 OpenCode 可以作为配置助手；服务器用 **Codex 或 Pi** 调云端模型。已有 Codex 账号与中转配置可以继续使用；Pi 提供一条通用、轻量的生成路径。无需 DGX 或 GPU。

## 四个模块与准备条件

| 模块 | 负责什么 | 你需要已有或准备什么 |
| --- | --- | --- |
| 原任务宿主 | 按原计划生成每日完整报告 | 已存在、启用、每日、非监控的任务；实际可用的邮件交付能力 |
| Gmail 云采集 | 读取符合格式的邮件并提交 API | 自己的 Gmail/Apps Script 账号 |
| Linux 服务器 | API 保存状态，worker 提前生成并发布缓存 | SSH 别名、部署目录、HTTPS 域名；Docker Compose 或现有 Linux 环境 |
| 千问平台与设备 | HTTP 工具读取缓存，原生 Speak 朗读 | 自己的开发者登录、可绑定的设备和实际插件标识 |

服务器 worker 发起模型请求，整理模式下会发送报告正文和朗读偏好；云模型并不运行在眼镜上。眼镜的 HTTP 请求只读取已生成缓存。模型 API key、API 角色 token 和千问平台登录是三组不同凭据。

## 让 Codex 或 OpenCode 帮你配置

安装首次配置 Skill：

~~~bash
npx skills add reknottycat/qwen-briefing-starter --skill setup-qwen-briefing -a codex
~~~

也可从仓库把完整的 skills/setup-qwen-briefing/ 文件夹复制到项目的 .agents/skills/setup-qwen-briefing/。同一份 SKILL.md 可供 Codex 与 OpenCode 使用；OpenCode 官方列出了项目 .agents/skills 和用户 ~/.agents/skills 发现位置。不要在多个位置安装同名但不同版本的副本。[OpenCode Skills 文档](https://opencode.ai/docs/skills/)

向配置助手提出：“用 setup-qwen-briefing 检查我已有的每日非监控任务，先让我确认来源与朗读方案，再配置服务器和眼镜。”如果使用 Codex，也可以直接点名 $setup-qwen-briefing。

安装 Skill 只提供操作指引，不会自动创建任务、部署后端、取得账号权限或登录平台。OpenCode 能加载 Skill 不代表它内置定时任务；需要读取你实际使用的调度系统，或由你提供该系统的任务导出。

以下是相同流程的手动操作说明。命令在仓库目录、Python 3.11+ 环境运行；安装依赖参见 [手动运行与开发](quickstart.md)。JSON、确认记录和导出都保存在已忽略的 runtime/。不要把真实任务目录、提示词或凭据提交到仓库。

## 第一步：发现任务并确认朗读方案

### 读取真实任务

Codex 本地自动化可只读发现：

~~~bash
python -m briefing.onboarding discover --codex-dir "$HOME/.codex/automations" --output runtime/inventory.json
~~~

若设置了不同的 CODEX_HOME，用其实际 automations 路径替换。这个命令不修改任务，不把提示词或秘密复制到清单，也不推断“每天执行”就一定不是监控。发现结果的 monitoring 一律为 null，仍需核对原任务目的和完整提示词。

其他任务宿主先通过其已有接口、浏览器或导出功能读取真实任务，再整理为下面的 JSON，保存在 runtime/tasks.json。示例仅展示字段，不能直接拿示例名称替代你的任务：

~~~json
{
  "version": 1,
  "tasks": [
    {
      "id": "existing-daily-report-id",
      "name": "我的每日报告",
      "cadence": "daily",
      "enabled": true,
      "monitoring": false,
      "evidence": "已核对原任务：每天生成完整报告，不依赖变化或告警。"
    }
  ]
}
~~~

cadence 支持 daily、weekly、other、unknown；monitoring 为 true、false 或 null。只有已核实的 active daily non-monitoring 项才可选。证据应描述实际检查，不填写虚假的“已核对”。实际时间、时区和原日程仍在原任务宿主管理，导出不会重建这些任务。

~~~bash
python -m briefing.onboarding discover --tasks runtime/tasks.json --output runtime/inventory.json
~~~

两种 discover 方式选其一。文件已经存在时，命令拒绝覆盖；复核新状态请换新的 runtime 文件名。仓库的 [合成任务示例](../examples/tasks.example.json) 仅供演示和测试。

### 提出选择与偏好

配置助手应先展示原任务 ID、名称、实际日程、选择理由与建议分类/模式/详略。非每日、暂停、监控和用途未核实的任务排除，不复制成新任务。

把选择保存为 runtime/selection.json：

~~~json
{
  "version": 1,
  "work_enabled": false,
  "sources": [
    {
      "task_id": "existing-daily-report-id",
      "category": "news",
      "mode": "processed",
      "narration_detail": "standard",
      "monitoring_review": {
        "monitoring": false,
        "evidence": "已阅读原任务，确认按日交付完整报告，不是有变化才通知。"
      }
    }
  ]
}
~~~

category 为 news、learning、work；mode 为 raw、processed、podcast；narration_detail 为 simple、standard、detailed。原文模式不按详略缩写。podcast 是备用双人文字格式，默认方案使用原文或整理。短偏好在服务启动后通过设置页修改，初始化导出的 note 为空。

monitoring_review 用于补充原值为 null 的人工核对；它不能把已知监控任务改成非监控。工作开关默认 false，只有明确同意启用工作朗读时才填 true。来源顺序按 sources 数组保存，每个 task_id 只能出现一次。

~~~bash
python -m briefing.onboarding plan --inventory runtime/inventory.json --selection runtime/selection.json --output runtime/plan.json
python -m briefing.onboarding status --plan runtime/plan.json
~~~

核对 status 展示的 tasks、excluded、work_enabled 和 task_digest。**先让用户确认这份具体方案，再执行下一条确认命令。** --confirm 只是记录先前的决定，不是人类确认的自动证明。

确认后，下面命令从同一份已核对文件读取 task_digest 并记录确认：

~~~bash
task_digest="$(python -m briefing.onboarding status --plan runtime/plan.json | python -c 'import json,sys; print(json.load(sys.stdin)["task_digest"])')"
python -m briefing.onboarding confirm-tasks --plan runtime/plan.json --digest "$task_digest" --confirm --output runtime/tasks-confirmed.json
~~~

选择、分类或详略发生变化时，从 plan 生成新方案并重新确认，不能沿用旧 digest。

## 第二步：再确认服务器与生成方式

来源方案确认后，才询问服务器配置。需要的是 SSH 别名、操作系统/Docker 情况、目录、HTTPS 域名和生成方式；不要把 SSH 密钥、服务器密码、登录 Cookie 或 API key 发到聊天里。

| 生成方式 | 适合情况 | 模型配置的位置 |
| --- | --- | --- |
| Codex | 已有可工作的 Codex 登录或中转服务配置 | 生成主机上同一运行用户的 Codex 配置 |
| Pi / Docker | 希望先启动，再通过网页选择模型服务 | 登录 HTTPS 设置页后选择内置目录或自定义兼容接口，并填写 API key |
| Pi / Linux | 在已有 Linux 服务中手动配置 Pi | 生成主机的 provider/model、可选基础地址与私有 key 文件 |

两种方式都由 worker 提前生成。OpenCode 可以帮助配置这两种方式，但本仓库不另设 OpenCode 服务器后端。

把已讨论的方案写入 runtime/server.json。Codex 示例：

~~~json
{
  "deployment": "linux",
  "base_url": "https://briefing.example.com/briefing/v1",
  "ssh_alias": "briefing-host",
  "generator": "codex"
}
~~~

Pi/Docker 示例，可先只确认服务器，模型稍后在网页中设置：

~~~json
{
  "deployment": "docker",
  "base_url": "https://briefing.example.com/briefing/v1",
  "ssh_alias": "briefing-host",
  "generator": "pi"
}
~~~

将域名、SSH 别名替换为确认值。Pi/Docker 可以省略全部三项 provider、model、llm_base_url，表示先确认服务器、后在网页配置模型。若预先指定模型，provider 与 model 必须成对提供，llm_base_url 仅在自定义接口时填写；不能只给服务、模型或基础地址中的单项。Pi/Linux 仍要求 provider 与 model，密钥另存本地文件。基础地址须为不含凭据、查询参数或片段的 HTTPS URL。SSH 别名应已在用户本机配置，JSON 不接受 SSH 选项、密码或私钥。deployment 只接受 docker 或 linux；当前 Docker 模板只支持 Pi，Codex 使用 Linux 路线。

当前 server.json 只保存上述字段。部署目录、凭据文件位置和额外的邮件交付约定写入另一份受保护的 runtime/deployment-notes.md；服务器确认时一并展示，恢复时一并核对。server_digest 不覆盖这些额外说明，不应声称它验证了全部部署细节。

~~~bash
python -m briefing.onboarding status --plan runtime/tasks-confirmed.json --server runtime/server.json
~~~

**展示目标主机、目录、域名、生成方式和拟执行动作，等用户确认后再记录服务器确认。** 不在两份方案确认前连接千问控制台。确认后运行：

~~~bash
server_digest="$(python -m briefing.onboarding status --plan runtime/tasks-confirmed.json --server runtime/server.json | python -c 'import json,sys; print(json.load(sys.stdin)["server_digest"])')"
python -m briefing.onboarding confirm-server --plan runtime/tasks-confirmed.json --server runtime/server.json --digest "$server_digest" --confirm --output runtime/server-confirmed.json
python -m briefing.onboarding export --plan runtime/server-confirmed.json --output-dir runtime/config
~~~

导出目录必须尚不存在，防止覆盖已有安装。生成四个文件：

| 文件 | 用途 |
| --- | --- |
| source-policy.json | 所选任务名称、顺序和邮件事件契约 |
| initial-preferences.json | 确认过的分类、模式、详略与工作开关 |
| setup-manifest.json | 方案摘要与后续配置要求 |
| mail-delivery.md | 原任务需追加的邮件交付规则，尚未自动应用 |

export 不连接 SSH，不修改原任务，不部署服务器，也不验证设备。manifest 中 server_deployed、device_verified 仍为 false。改服务器时从 tasks-confirmed.json 生成新的服务器确认；改任务选择时重做两份确认，旧文件保留供核对。

## 第三步：部署服务、接入邮件并验证缓存

Docker 模板内置 Pi，按 [容器部署说明](../deploy/docker/README.md) 执行，使用上一步 runtime/config 下的文件；具体变量、秘密文件、容器用户和挂载路径以该文档为准。选择 Codex 时采用 [手动运行](quickstart.md) 与 [systemd 运维](operations.md)，复用生成主机现有认证和中转配置。当前 Docker 镜像不内置 Codex，不能仅把 JSON 的 generator 改为 codex 就在该镜像中运行。

Docker 无需先准备模型 API key 就能启动。先按容器说明启动 API/worker 并配置 HTTPS，打开 /briefing/v1/manage/，用本实例 manage.token 解锁，再配置 Pi 模型。没有保存模型配置时，worker 等待，不领取生成任务；这不影响管理页面和缓存服务启动。

网页提供两条路径：选择随 Pi 版本提供的内置服务与模型目录；或者选择自定义 OpenAI Chat Completions 兼容接口，填写服务标识、HTTPS 基础地址、模型 ID 与 API key。目录是版本快照，不是对当前账号可用模型、额度或价格的查询，仍需使用自己实际开通的服务。网页只支持单个 key/token 的认证信息；OAuth 或需要额外凭据的服务不作通用兼容承诺。

密钥由用户在已登录的 HTTPS 页面输入，不发到聊天。读取设置不会返回密钥，网页不把它写入浏览器持久存储或日志。保持相同服务和接口时，密钥留空可沿用已保存值；更换服务或接口必须输入新的密钥，避免把旧凭据送往新地址。

保存后，下一次生成使用新配置，无需重启；它不会自动证明服务连接成功、模型质量合格，也不代表已有缓存已经重建。先核对保存反馈，再用授权的实际生成任务验证。Codex 继续使用原有工具配置，网页不会更改 Codex 认证或中转设置。

Pi/Linux 手动模式仍使用 PI_PROVIDER、PI_MODEL、PI_API_KEY_FILE，可选 PI_BASE_URL；密钥是本机单行文件。Pi 适配器使用独立临时运行目录，不复制个人 OAuth 或聊天会话。两种配置方式的边界见 [运行维护](operations.md)。

API 和 worker 都要加载同一份 BRIEFING_SOURCE_POLICY_PATH。BRIEFING_INITIAL_PREFERENCES_PATH 只用于首次创建状态；已经运行的实例不会被初始化文件改写。已有数据需要显式迁移和备份，不允许用新安装输出覆盖。

新部署的四角色凭据可由现有工具生成：

~~~bash
python deploy/create-runtime-config.py --base-url https://briefing.example.com/briefing/v1 --generator codex --source-policy runtime/config/source-policy.json --initial-preferences runtime/config/initial-preferences.json
~~~

上面命令创建/保留四角色 token，并生成 Linux/Codex 的 systemd 环境文件，同时绑定本次私有策略。Docker/Pi 只使用其 token 文件和导出 JSON，模型在网页登录后配置，不需要把模型 key 放进 Compose .env，也不能直接使用这份 worker.env。若部署 Linux/Pi，在同一命令中将 --generator 改为 pi，并传 --pi-provider、--pi-model、--pi-api-key-file；可选 --pi-base-url、--pi-ca-file。确切示例见 [运行维护](operations.md)。

模型 key 与四角色 token 相互独立。Linux/Pi 手动模式先在服务器保存 key 文件，生成器只验证并记录路径；Docker/Pi 由已认证的设置接口保存到私有持久目录。命令应在实际部署主机和最终目录运行，因为 Linux 配置文件记录绝对路径；异机部署要分别核对权限。不要把秘密内容粘贴到聊天、源码或普通日志。

Gmail 采集器使用 [手动运行文档](quickstart.md) 中的 EXPECTED_ACCOUNT、BRIEFING_BASE_URL、BRIEFING_INGEST_TOKEN，并将 BRIEFING_SOURCE_POLICY 设置为本次导出的私有策略 JSON。配置前核对实际 Gmail 账号。

对选中的原任务，只追加 mail-delivery.md 中的收件与邮件格式要求，保留原目的、原日程、启用状态与完整报告正文。原任务宿主没有邮件能力时，先解决真实交付入口；创建本地配置不能冒充已发信或已收件。本项目不把 OpenCode 的 Skill 支持当成内置调度器或邮件工具。

用隔离合成数据验证服务、权限与生成，再接入真实报告。查看来源已摄入、worker 已发布缓存，以及 [HTTP 验收](acceptance.md) 的实际结果。队列已创建不等于生成完成，健康端点成功也不代表存在可读内容。

## 第四步：连接自己的千问平台与眼镜

本项目采用千问官方的云云对接路线：注册 HTTP/OpenAPI 工具，由 Skill 调用并交给原生 Speak。**无需安装手机 APP SDK，也无需另建 MCP Server**；主动向设备推送属于另一种可选路线，不是这里的点播流程。[平台介绍](https://device.qianwen.com/docs/qwen/6679322)

1. 在前两项确认和后端检查完成后，用户登录 [千问 AI 硬件开放平台](https://device.qianwen.com/)。配置助手可复用已授权的浏览器会话，先确认账号与目标设备。不要把密码、Cookie 或所谓统一“Gateway Key”交给助手。
2. 创建自己的 OpenAPI 插件和读取工具。基础域名指向已验证的 HTTPS 服务，工具 code 使用 get_daily_briefing，最终请求为 POST /briefing/v1/read。Bearer Token 来自本实例 read.token，只填在私有鉴权配置中。[工具接入](https://device.qianwen.com/docs/qwen/6679330)
3. 输入参数按 [tool_schema.json](../qwen-skill/tool_schema.json) 配置：date、topic 为字符串；section 在平台使用 number，取正整数；参数位于 body，来源为 model。它是字段参考，不是通用一键导入包。用平台实际分配的 pluginCode 替换 YOUR_PLUGIN_ID，形成 pluginCode.get_daily_briefing；不要复用作者 ID。
4. 创建眼镜侧 Skill，采用 [qwen-skill/SKILL.md](../qwen-skill/SKILL.md)。先调试真实工具参数、HTTP 返回及 Speak 内容。默认整期用 full_spoken_text，明确段号才用 spoken_text。[调试说明](https://device.qianwen.com/docs/qwen/6679387)
5. 在设备管理选择自己的设备，或按平台页面用千问 AI 穿戴 App 扫码绑定；开启设备开发者模式后进行编辑态真机测试。开发者模式与正式版本是不同链路，测试安装不等于发布完成。[真机与设备管理](https://device.qianwen.com/docs/qwen/6681505)
6. 完成实际播放核对后，按平台流程先发布插件，再提交个人私有 Skill 到自己的已绑定设备。提交、审核通过与设备可用分别记录；包含私人鉴权的集成不默认公开共享。[发布流程](https://device.qianwen.com/docs/qwen/6679382)、[使用限制](https://device.qianwen.com/docs/qwen/6683675)

工具需要在 3000 毫秒内返回，因此不能在读取请求中临时生成稿件。项目曾观察到约一秒的连接失败、同会话沿用旧结果等情况；新会话真实调用、原生 Speak 和眼镜整期播完仍须分别验证。双人播客文字不证明双声线音频，也不证明平台拒绝了相关权限。参见 [已知限制](known-limitations.md)。

## 日常使用与恢复

配置成功后，改分类、原文/整理、详略、短偏好和工作开关，使用设置页或 [add-to-briefing](../skills/add-to-briefing/SKILL.md)，无需重走首次安装。需要增加、移除来源时以运行中的私有策略与实际状态为准，先规划迁移。

中断后执行 onboarding status 读取已有阶段。确认绑定方案内容；文件已写入只代表该阶段已记录。保留有效授权与配置，不反复创建新任务、重发邮件或重新生成 token 来催进度。
