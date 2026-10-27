# 验收

验收应记录实际代码版本、环境、输入范围、结果与未验证部分。以下各层不能相互替代：源码测试通过、部署后的 HTTP 通过、千问工具调用成功、真机完整播放。

## 1. 自动化检查

在仓库虚拟环境中运行：

~~~bash
python -m pytest
node --test tests/test_collector.cjs
python scripts/verify_live.py --help
python -m briefing.onboarding --help
~~~

[CI 工作流](../.github/workflows/ci.yml) 使用 Python 3.11 和 Node.js 22 执行同类检查。发布时检查对应提交的真实 CI 结果，不将“工作流文件存在”记作通过。

测试覆盖 API 权限、输入校验、去重、任务租约、偏好并发、视图隔离、原文保留、模型输出校验、邮件 MIME 解析及验收脚本本身。合成测试不读取真实 Gmail，不验证部署域名、真实 Codex 服务、千问平台或眼镜声音。

首次配置还应使用合成清单核对：监控/非每日/暂停任务被排除；未知目的不能直接选入；未确认任务不能确认服务器；两份确认缺一不能导出；修改方案会使对应 digest 失效；导出保留原任务且不包含秘密。命令记录确认并不证明人类已经回复，Agent 行为需按真实对话另核对。

Docker 与 Pi 的独立合成验收见 [容器说明](../deploy/docker/README.md)。执行 scripts/verify_docker.py 需要真实 Docker 环境和所列依赖；其模型替身不使用真实付费供应商。报告实际结果与覆盖环境，不把脚本存在、镜像可构建或替身响应成功写成云模型/设备验收。

## 2. 部署后的 HTTP 验收

前提：对应视图已有可读取的稿件，运行机器能够访问目标服务。read.token 只包含读取 token。报告放在忽略的 runtime/ 目录。

~~~bash
python scripts/verify_live.py --base-url https://briefing.example.com/briefing/v1 --read-token-file runtime/credentials/read.token --view daily --output runtime/acceptance/daily.json
python scripts/verify_live.py --base-url https://briefing.example.com/briefing/v1 --read-token-file runtime/credentials/read.token --view learning --output runtime/acceptance/learning.json
python scripts/verify_live.py --base-url https://briefing.example.com/briefing/v1 --read-token-file runtime/credentials/read.token --view full --output runtime/acceptance/full.json
python scripts/verify_live.py --base-url https://briefing.example.com/briefing/v1 --read-token-file runtime/credentials/read.token --view work --expect-status disabled --output runtime/acceptance/work-disabled.json
~~~

若已开启工作并准备了工作来源，改用 --view work 的默认 ready 验收。显式 --expect-status view_missing 只核对空视图契约，结果始终为 limited，不能当作内容准备成功。

脚本通过真实 HTTP 请求检查：未授权请求被拒、只读 token 无写入权限、参数校验、视图回显、日期和来源、完整分页、逐页正文与全文的一致性，以及每个请求的 3000 毫秒门槛。关闭的工作视图还检查不泄露正文、来源、标题、最近日期和生成信息。

| 结果 | 退出码 | 含义 |
| --- | --- | --- |
| passed | 0 | 此次选定契约在该请求路径通过 |
| failed | 1 | 存在错误或缺少必需证据 |
| limited | 2 | 有受限条件；不得记作完整通过 |

报告 scope 为 http_only，不包含 token、正文、标题、原始邮件或来源 ID。它仍应保存在本地。超过三秒、网络失败或缺稿时保留失败结果并排查；不要放宽门槛或只挑最快一次作为验收证据。

## 3. 设置页与内容核对

使用隔离的合成数据至少核对这些真实行为：

- 正确口令登录，错误口令被拒；退出或会话失效后页面不显示私人来源。
- 保存后刷新，分类、模式、详略、短偏好与工作开关一致。
- 两个页面并发保存，旧 revision 收到 409，并保留用户尚未保存的修改供合并。
- 原文模式禁用不适用的整理输入，切回整理后保留先前值。
- 设置变更为今天排队，worker 完成后能读取对应新稿。
- 键盘可操作主要控件，窄屏不横向溢出，控制台没有影响功能的错误。

原文模式将去除约定传输头后的正文与所有该来源段落按顺序拼接，逐字比较；不要先 trim 或换行归一化。整理与播客模式人工核对事实、条件、教学例子、数字和来源引用，日常学习概要需与完整学习正文一致。结构通过不能代替语义忠实度。

## 4. 千问工具和真机

使用合成内容记录日期、设备型号、固件、平台环境、代码版本和稿件长度。分别保存工具真实入参、HTTP 返回状态、Speak 输入长度及设备观察结果；私人文本与账号信息不上传仓库。

| 场景 | 通过条件 |
| --- | --- |
| 今日简报 | 实际调用 daily，日期正确，新闻与学习概要完整，工作开关生效 |
| 学习入口 | 实际调用 learning，保留完整学习正文，不回退为概要 |
| 工作关闭 | 返回 disabled，仅朗读关闭提示，不泄露工作内容 |
| 默认完整收听 | Speak 使用 full_spoken_text，设备持续播到服务返回的结束语 |
| 明确段号 | Speak 使用对应 spoken_text，没有播放整期 |
| 缺稿与网络失败 | 使用规定提示，不编造内容，不把旧稿改称今天 |
| 更新后重取 | 在新会话中观察到实际工具调用及更新的日期或内容 |
| 长稿 | 分别验证短稿、多个段落及计划使用的最大实际长度，没有截断或重排 |

网页 TTS、模拟器、原生 Speak 与眼镜设备分别记录。不要用网页 500 字符输入框推导原生上限，也不要把开始发声写成整期完整播放。容量与连接方面的当前边界见 [已知限制](known-limitations.md)。
