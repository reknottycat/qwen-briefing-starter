# 邮件格式与来源策略

采集器只处理明确登记的来源。标题、事件标记和正文都满足契约，邮件才会进入简报。

## 最小邮件

标题必须完整匹配以下格式，不能带 Re:、Fwd: 或额外空格：

~~~text
[每日早报][每日资讯][2026-01-01]
~~~

正文使用 UTF-8 纯文本最容易核对：

~~~text
早报事件类型：定时结果
今天的社区图书馆增加了晚间开放时段。试行安排从下周一开始，具体时间以馆方通知为准。
~~~

示例日期仅用于展示，实际发送时应使用报告对应日期。未来日期会被 API 拒绝。正文第一行必须与来源策略中的 event_type 完全一致，之后必须有非空正文。

第二行可选以下一种传输标记：

~~~text
简报分类：新闻
简报分类：学习
简报分类：工作
~~~

worker 会去掉开头的事件行、紧随其后的合法分类行及其后一个空行。**实际分类以设置页保存的偏好为准**，邮件中的分类文字不能绕过工作开关。原文模式保留去除这些约定传输头后的字符；服务为朗读额外添加的日期、段号、结束语不属于原邮件正文。

## 三类来源

| kind | event_type | 发送时机 |
| --- | --- | --- |
| daily | 定时结果 | 当天有可用报告时 |
| weekly | 定时结果 | 本周报告实际产出时 |
| conditional | 新回复或新信息 | 发生相应事件时 |

kind 不会自动创建计划任务，也不会保证每天或每周有邮件。条件来源没有新事件时无需发送“暂无变化”邮件；本项目不替你监控上游网站、生成原始报告或发送邮件。

[示例策略](../briefing/source_policy.json) 包含十个中性来源。采集器搜索最近七天、标题含“[每日早报]”的 Gmail 邮件，逐封再校验来源白名单。每次最多成功提交 40 封，分页扫描最多十页。收件时间用于记录，分期日期由标题确定。

采集器优先取 text/plain，只有没有纯文本正文时才转换 text/html；命名附件不属于输入契约。MIME 正文以附件形式存储时会读取正文附件，不能以摘要或邮件片段替代完整正文。采集器和手工导入按 UTF-16 code units 将单封正文限制在 80,000；API 按 Unicode 字符校验同一数值上限。含非 BMP 字符的邮件经过采集器或导入脚本时，采用前者更严格的计数边界。

## 定制来源

新安装尚无数据时：

1. 编辑 [source_policy.json](../briefing/source_policy.json)。每条保留 name、kind、event_type、narration_detail 四个字段；version 保持 1。数组顺序就是朗读来源顺序。
2. name 必须唯一，长度 1–80，首尾无空白，不能含方括号或换行。详略只支持 simple、standard、detailed。
3. API 与 worker 使用同一份策略文件；将相同 JSON 复制到 Apps Script 的 BRIEFING_SOURCE_POLICY 属性。
4. 如果新增来源需要默认归为学习类别，同步调整 [preferences.py](../briefing/preferences.py) 的初始学习来源集合，或首次启动后在设置页明确分类。
5. 用合成邮件验证允许与拒绝两类路径，再接入自己的报告来源。

已有部署的来源名称同时关联历史邮件、偏好、缓存和任务。**当前仓库没有自动来源迁移工具**。不要在运行中手改 state.json，也不要只改采集器或只替换一端策略。需要先停服务并备份，在副本中完成有映射规则的迁移与验证，再让 API、worker、采集器使用同一版本。若只是想改变详略、分类或短偏好，直接使用设置页，不需要修改策略文件。操作步骤见 [运行维护](operations.md)。

## 手工导入已有邮件

[scripts/ingest_saved_email.py](../scripts/ingest_saved_email.py) 接受 Gmail 连接器导出的完整消息 JSON，不接受任意 .eml 或仅有正文的 JSON。导出必须含 id、internal_date、payload 及已解码的 MIME 正文；仅有 attachment_id 的正文需先完整取回。

~~~bash
python scripts/ingest_saved_email.py --input runtime/mail/full-message.json --account owner@example.com --base-url https://briefing.example.com/briefing/v1 --token-file runtime/credentials/ingest.token
~~~

--account 填写这封导出邮件所属的邮箱，供来源链接定位；它不构成导出账号身份的独立证明。输入、导出、回执均放在忽略的 runtime/ 目录，不提交到 Git。脚本只打印消息 ID 和摄入结果，不打印正文。
