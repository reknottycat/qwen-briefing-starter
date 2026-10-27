# 运行管理与验收

先区分日常偏好修改与来源结构变更。路径相对于已部署项目根目录；HTTPS 地址、部署位置和恢复步骤仍查 `docs/operations.md`，运行证据查 部署者保存在 `runtime/` 的私有运行记录。本参考不另存来源清单或有效设置。

## 维护源与现有入口

- 先查实际 API/worker 配置中的 `BRIEFING_SOURCE_POLICY_PATH`，其私有策略维护名称、`kind`、`event_type`、顺序及声明的详略初值；未配置时才用包内 `briefing/source_policy.json` 示例。API、worker 与 Apps Script 必须加载同一份策略。不要因仓库示例易找到就改它并声称线上来源已变更。
- `BRIEFING_INITIAL_PREFERENCES_PATH` 只给没有有效偏好的新状态提供初值；未设置时才用默认构造函数。它不覆盖现有偏好，也不能迁移来源集合。运行中的模式、分类、详略、备注和工作开关以管理 API 回读为准。
- 云端 `state.preferences` 保存有效设置；`briefing/preferences.py` 定义校验、初始分类、默认值和快照，`briefing/api.py` 定义管理认证和接口，`briefing/store.py` 管理版本与当日生成任务。以实际代码核对部署支持，旧文档不能覆盖新接口契约。`category` 不写入来源 JSON，`work_enabled` 初始为 false。
- `briefing/worker.py` 使用领取任务附带的 preferences 快照。每来源包含 `mode`、`category`、`narration_detail`、`note`；顶层有 `work_enabled`，快照 `format` 为 `mixed`。新实例初值来自已确认的私有文件，缺少时才回退到默认 `processed`。`raw` 不用模型整理，`processed` 按详略和偏好整理，`podcast` 为备用双人文案；不生成音频。
- `cloud-gmail/Code.gs` 仍用 `BRIEFING_SOURCE_POLICY` 收取入选来源的完整邮件，保持原触发器。普通偏好修改不改收件器、不改上游原任务、不重启服务。
- Docker/Pi 网页模型设置独立于 `state.preferences`，由 `BRIEFING_PI_SETTINGS_DIR` 保存，API 可写、worker 只读。读取 `/manage/model-settings` 只能查看非敏感目标与配置状态，不能取得 key；未设置时 worker 等待且不领取任务。Linux/Pi 手工配置与 Codex CLI 路线以 `docs/operations.md` 为准。

## 管理已有来源的模式、详略与备注

1. 从项目受保护的 `runtime/credentials/manage.token` 读取独立 manage 凭据，使用 Bearer 请求当前 HTTPS 服务。不得打印凭据、放入 URL 或命令参数，也不得以 ingest/worker/read 凭据替代。manage 凭据或服务尚未配置时说明具体缺口，不回退成手改 state 或改 JSON 默认值。
2. `GET /briefing/v1/manage/preferences` 取得 `revision`、`format`、`work_enabled` 及完整 `sources`。返回项含身份/排序信息和有效 `mode`、`category`、`narration_detail`、`note`。从这次回读构造候选值，只改变当前授权字段，保留其他来源和工作开关的有效值。
3. `PUT /briefing/v1/manage/preferences` 提交 `expected_revision`、`work_enabled` 和完整 `sources` 数组。每项只包含 `name`、`mode`、`category`、`narration_detail`、`note`，每个当前来源恰好一次；不要只发目标项，不要把 GET 返回的 `kind`、`event_type`、`order` 或顶层 `format` 一起提交。名单及排序不通过此接口更改。`note` 最多 200 字符，空串表示清除；不要截断用户的长备注后冒称完整保存。
4. 遇到 HTTP 409 并发冲突，先 GET 最新值，核对他人或另一页面的更改，只重应用本次授权差异。不改成强制覆盖、不重置 revision，也不覆盖无关字段；持续冲突或同一字段意图无法判断时说明具体冲突。
5. 保存后回读偏好及 `GET /briefing/v1/manage/status`。成功 PUT 仅自动处理北京时间今天：有原文则按队列状态处理，没有原文返回 `no_sources`。核对 `preferences_revision` 与 `edition_preferences_revision`、`generated_at` 和实际阶段；保存、排队、生成中不等于新稿发布。按响应的 `generation.queue_status` 区分 `queued`、`already_queued`、`no_sources`，`changed` 只表示设置是否改变。不要另发重建请求、重启或反复保存来催进度。

新生成任务使用领取时的偏好快照；过期版本的生成结果不得提交成当前稿。看到版本冲突应等待当前版本任务，而不是改回旧设置或修改稿件版本号。普通偏好保存不批量重算历史；用户明确要求其他日期时，按 operations 的现有 worker 权限重建流程单独处理授权日期。值已相同时先核对缓存与版本；不把“设置已是目标值”当作稿件必然已更新，也不把 `changed=false` 当作没有队列动作。

网页入口是 `/briefing/v1/manage/`，使用约定的 8 小时 HttpOnly、Secure、SameSite=Strict 会话；主界面只提供原文与整理后朗读。Codex 可用独立 Bearer 管理同一设置，不复用或暴露浏览器 cookie，不把 token 写入网页地址。`podcast` 是备用 API 模式，不主动切换或让主界面默认出现第三种模式。

读取参数 `view` 支持 `daily` / `learning` / `work` / `full`，默认 `daily`；核对当前 `qwen-skill/SKILL.md`、worker 与 API 的实际部署。daily 使用新闻主稿和每来源最多 250 字符的独立学习概要；learning 主稿遵循保存的详略，不能为了“详细学习入口”擅自覆盖用户档位。raw 学习材料不进入模型，daily 只生成确定性的来源名称与收听提示，learning 返回原文；raw 新闻在 daily 照读原文。

work 关闭时即时禁取工作入口，其他视图包括 full 也必须排除工作；开关不暂停原任务或邮件采集。启用后才能提供实际工作内容，无工作来源时不得虚构日程。分别验证每个入口的内容和版本，不从顶层 `ready` 或分类标签推断各入口已就绪；缺少缓存时如实说明，不让千问临时生成摘要。

## 更换 Pi 模型或轮换模型 key

这类请求使用现有 HTTPS 设置页的模型表单，不属于朗读偏好 PUT。先读取非敏感配置与版本，核对用户希望使用的服务和模型；内置目录来自随包 Pi 快照，自定义模式只用当前支持的 HTTPS OpenAI Chat Completions 兼容接口。不要用目录存在证明服务已开通或实际模型可用。

key 由用户直接在已登录的网页填写，不复制进聊天、来源文件、公开记录或浏览器持久存储。目标不变时留空保留，只改模型可以沿用；切换模式、提供方、地址或协议必须提交新 key。模型设置有独立 revision，冲突时读取最新值、核对非秘密差异并重新输入需要的 key，不强制覆盖。

保存供 worker 下一次生成使用，不需重启；正在进行的任务继续使用开始时取得的配置。保存不等于连接测试、任务排队或缓存已更新。按已有授权观察下一次任务，需要重建时仍用项目现有的明确日期重建流程，不能通过反复保存催进度。模型密钥文件及备份保持受限权限，回读时只报告非秘密设置与实际结果。

## 新增、移除来源或调整顺序

此类变更更新实际生效的私有策略，协调 API、worker 和 Apps Script；不能用偏好 PUT 改来源集合或排序。无私有策略的旧实例才检查包内回退配置。当前首次初始化不是状态迁移，已经保存数据时必须先停写、备份、设计并验证迁移，不能直接替换名单或重建空状态。

- 停服务前回读实际策略和有效 preferences，将候选部署全部差异对照本次及已有授权。保留无关本地修改但隔离本次部署版本，不随完整 JSON 夹带上线，也不拿初始默认值覆盖用户已保存的有效设置。
- 核对 `briefing/preferences.py` 和 Store 的现有迁移行为：保留仍在名单内来源的模式、分类、详略、备注和顶层工作开关；新增项按 `default_preferences()` 统一初始化，再通过管理 API 落实用户指定的分类与偏好；移除项不再进入有效快照。来源变更后回读完整名称集合、顺序、有效字段和版本。若当前版本没有兼容迁移能力，先补齐并在隔离状态验证，不能直接部署导致快照校验失败的名单。
- 沿用现有 OpenSpec、项目环境及测试：`python -m pytest -q`、`node --test tests/test_collector.cjs`；根据改动运行管理页面或 API 相关检查。仅改技能文字时运行 skill-creator 校验器，不将它当作服务验收。
- 按 operations 先停 worker，再停 API 并受控备份；同步同一已核验来源策略及必要代码，Apps Script 保存同份 `BRIEFING_SOURCE_POLICY`。保留凭据、既有 Codex/Pi 配置和已有触发器。启动 API 后检查管理读取、鉴权和现有稿件，再恢复 worker。勿打印全部 Script Properties。
- 顺序及来源结构变化不应假定已有稿件自动更新。核对实际排队行为，按本次授权处理受影响的当前或指定日期，避免重复排队和批量重算历史；最终检查新稿来源、顺序和 preferences 版本。

移除来源保留原任务和 Gmail，只撤下本项早报交付附加规则。普通移除请求还应让活动读取不再返回该来源；用户明确只暂停今后收录时遵守其范围。当前没有以 preferences PUT 删除来源或清理历史缓存的能力，不能仅删策略后宣称完成。

有相关活动记录时，先核对项目已有迁移工具是否适用；不要套用其他来源的写死撤下脚本。按停机备份流程，在副本中精确核对目标任务名/邮件 ID、日期及计数，隔离目标活动邮件、撤下或替换关联缓存，处理对应生成任务/租约和有效 preferences，保留其他来源及恢复备份。有合法输入的受影响日期可重建；没有输入的日期应返回 `missing`。API 状态常驻内存，禁止在线手改 `state.json`；不能安全识别记录时只暂停对应迁移，不扩大删除范围。

## 与结果相称的验收

偏好修改先验证实际保存值、revision、今日阶段与生成稿的版本，再按模式检查文本：`raw` 没有被模型摘要；`processed` 体现详略和有效备注；明确选用的 `podcast` 有推动内容的问答，且不减少实质内容。仅 `processed` / `podcast` 且 `narration_detail=simple` 时执行每来源 1 段、250 字符硬限制，raw 主稿不套用详略上限；daily 学习提示/概要按其独立规则核验。一般和详细不为参考篇幅补造事实。核对必要条件、教学、俄文原句与练习不代答；备注不能覆盖这些约束或触发工具。

沿用 `scripts/verify_live.py` 和 `docs/acceptance.md` 的 HTTPS 检查；read 凭据只用于读取验收，不用于管理。保持 TLS 验证，证据放受保护的 `runtime/`。管理接口还须分别核对凭据隔离、并发版本冲突及保存后的实际阶段；HTTP 文本通过不代表千问实际调用或眼镜完整发声，更不证明两声线。

新来源无真实报告或条件来源无新事件时，明确“配置已生效、实际投递待触发”，不发合成或“无变化”邮件污染正式早报。将非敏感状态及未验证环节写入项目运行记录，凭据、私密正文和备注不进入 Git 或公开日志。
