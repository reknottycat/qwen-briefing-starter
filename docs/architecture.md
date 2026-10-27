# 架构与接口

这个服务把采集、生成和朗读分开。眼镜读取的是已经发布的文字缓存。

~~~mermaid
flowchart LR
  M[符合格式的 Gmail 邮件] --> C[Apps Script 采集器]
  C -->|ingest token| A[FastAPI 与 JSON 状态]
  A -->|领取任务| W[常开主机上的 Codex worker]
  W -->|发布已校验稿件| A
  U[浏览器设置页] -->|管理会话| A
  Q[千问 HTTP 工具] -->|read token| A
  A -->|缓存文字| Q
  Q --> S[平台原生 Speak]
~~~

## 各组件负责什么

| 组件 | 输入与输出 | 实现 |
| --- | --- | --- |
| 邮件采集 | 读取 Gmail 中符合标题和事件标记的正文，提交摄入接口 | [Code.gs](../cloud-gmail/Code.gs) |
| API | 校验来源、保存原文和偏好、租出生成任务、原子发布缓存 | [api.py](../briefing/api.py)、[store.py](../briefing/store.py) |
| worker | 取得任务和偏好快照，保留原文或调用 Codex 整理，校验后发布 | [worker.py](../briefing/worker.py) |
| 设置页 | 修改分类、朗读模式、详略、短偏好和工作开关 | [management](../briefing/management/) |
| 读取视图 | 按当前分类和工作开关选择缓存内容，不调用模型或网络 | [views.py](../briefing/views.py) |
| 千问 Skill | 把自然语言映射到读取参数，将服务返回文字交给 Speak | [SKILL.md](../qwen-skill/SKILL.md) |

API、worker 可在不同主机上运行，也可共享一台 Linux 主机。worker 调用已安装、已认证的 Codex CLI；项目本身不加载本地大模型，不依赖 GPU。系统没有自建音频播放器或音频文件生成链路。

## 数据与生成

来源名称、事件类型、顺序和初始详略以 [source_policy.json](../briefing/source_policy.json) 为准。第一次启动时生成默认偏好；之后运行中的偏好保存在服务状态里，修改源文件不会覆盖已经保存的偏好。

邮件按标题日期归入一期，日期判断使用 UTC+8。相同消息 ID 与完全相同内容再次摄入会去重；相同 ID 携带不同内容返回冲突。系统保留消息原文，生成结果通过引用 ID 指回真实来源。

worker 默认每 120 秒检查一次任务，单次生成默认允许 600 秒。任务有 900 秒租约，同一来源与偏好版本最多尝试三次，并记录退避时间。新邮件或偏好改变后，过时的生成结果不能覆盖当前任务。成功发布使用原子文件替换；生成失败时保留已有稿件，设置页显示当前生成状态。

保存设置会为**今天**已有邮件排队重建；历史日期如需按新朗读设置生成，需明确重建该日。读取时采用当前分类与工作开关，因此分类和关闭工作内容可以立即改变可见内容；正文的重新整理仍须等待 worker 发布。

## 朗读模式与视图

| 模式 | 行为 |
| --- | --- |
| 原文 raw | 去掉约定的开头传输头，保留报告正文字符并分段；不让模型改写正文 |
| 整理 processed | 按详略和短偏好生成讲述稿，并保留来源引用 |
| 播客 podcast | 生成“主持人：”“讲解人：”交替的对话文字；语音是否呈现不同声音由平台决定 |

网页主界面只显示原文与整理两种模式；播客是需要明确选择的备用 API 模式。

简单档每个来源最多一段、250 字符；一般与详细档的字数是生成提示中的参考范围，仍受整期容量约束。原文模式中的详略和短偏好不用于改写，设置值会保留，以便切回整理模式。

| API view | 千问三参数工具的 topic 别名 | 返回内容 |
| --- | --- | --- |
| daily | __view_daily | 新闻正文、每个学习来源一条缓存概要，以及已开启的工作正文 |
| learning | __view_learning | 学习分类的完整正文 |
| work | __view_work | 已开启的工作正文；关闭时返回 disabled |
| full | __view_full | 所有可见正文；仍受工作开关限制 |

工作开关默认关闭。示例默认分类见 [preferences.py](../briefing/preferences.py)；启用工作开关本身不会把某来源改为工作分类。来源顺序在策略文件中确定，设置页不提供增删来源或重排。

日常视图里的学习概要最多 250 字符。老稿或原文学习稿没有概要时，服务提供“可请求完整学习内容”的固定提示，不把截断的课文称为摘要。

## 权限与 API

所有路径均位于 /briefing/v1。受保护接口使用 Authorization: Bearer 加对应角色 token。

| 角色 | 可访问接口 | 凭据使用位置 |
| --- | --- | --- |
| ingest | POST /ingest | Gmail 采集器 |
| worker | GET /jobs/next、POST /jobs/failure、POST /jobs/rebuild、POST /editions | 生成主机 |
| read | GET 或 POST /read | 千问 HTTP 工具 |
| manage | 管理偏好、状态和会话 | 设置页或受信任的管理客户端 |

四个 token 应相互独立。API 至少需要 ingest、worker、read 三个不同且不少于 24 字符的值；缺少 manage 时管理功能关闭。配置生成器会生成四个角色的 token。

GET /health 不需要凭据，只报告服务可响应。它不证明有邮件、生成成功或千问可用。

管理页面位于 /briefing/v1/manage/。登录用管理 token 换取最长八小时的 HttpOnly、SameSite=Strict 会话 cookie；通过 HTTPS 设置 Secure。写请求校验 Origin，偏好提交携带 expected_revision，并发修改返回 409。会话保存在单进程内存，API 重启后需重新登录。管理接口只接受 HTTPS，或本机回环地址上的 HTTP。

读取参数：

~~~json
{"date": "today", "section": 1, "topic": "all", "view": "daily"}
~~~

date 支持 today、latest 或 YYYY-MM-DD；section 从 1 开始；普通 topic 按缓存中的主题名称精确匹配。旧的千问工具只声明 date、section、topic，使用上表保留别名，不额外发送 view。

ready 响应的 full_spoken_text 是所选视图的整期朗读文字，包含真实日期、段号和结束语；spoken_text 是单段。默认完整收听使用前者，用户明确指定段号才用后者。来源 URL、warnings 和生成时间不应当被当作朗读正文。

missing、view_missing、topic_missing、disabled、unavailable、finished 都有对应提示。缺今天的稿件不能把旧稿当作今天；latest 也必须保留实际日期。详见 [千问 Skill](../qwen-skill/SKILL.md)。
