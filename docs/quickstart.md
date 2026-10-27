# 手动运行与本地开发

首次安装请先按 [安装流程](setup.md) 发现并确认已有任务，再选择服务器和生成方式。本文是本地开发及已有 Codex/Linux 环境的手动运行参考；新建 Docker 实例使用 [容器说明](../deploy/docker/README.md)。以下命令以 Linux Bash 为例，Python API 与测试不依赖 Windows。

## 1. 准备环境并运行测试

需要 Python 3.11+、Git；运行 Gmail 采集器测试另需 Node.js 22，使用下文的 Skill 安装命令需 Node.js 22.20+。完整链路还需要：

- 自己的 Gmail 与可运行 Apps Script 的账号。
- 可被 Gmail 和千问访问、证书有效的 HTTPS 域名。
- 一台能调用已认证 Codex CLI 的常开主机。
- 可配置 HTTP 工具和 Speak 的千问开发者环境，以及要验证的设备。

~~~bash
git clone https://github.com/reknottycat/qwen-briefing-starter.git
cd qwen-briefing-starter
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest
node --test tests/test_collector.cjs
~~~

测试使用合成数据，不需要读取自己的邮箱或调用模型。仓库来源只用于示例。真实部署使用首次安装流程导出的私有策略与初始偏好，不必编辑源码里的示例名称；接口约定见 [邮件格式与来源策略](mail-format.md)。

## 2. 生成本地凭据

下面的域名是占位符，替换为自己的 HTTPS 地址。API 固定前缀是 /briefing/v1。

~~~bash
python deploy/create-runtime-config.py --base-url https://briefing.example.com/briefing/v1 --generator codex --codex-bin codex --source-policy runtime/config/source-policy.json --initial-preferences runtime/config/initial-preferences.json
~~~

生成器默认把文件放到仓库的 runtime/credentials/：

这条命令使用首次配置导出的两份文件，并把对应绝对路径写进 API/worker 的环境配置。此处路径适合当前用户的本地试运行；安装 systemd 时必须按 [运行维护](operations.md) 把 API 配置复制到 /etc 下，调整 API/worker 各自可读取的绝对路径。ProtectHome=true 的 API 不能读取普通用户主目录中的导出。不能漏掉这两个参数后将示例默认来源当作已确认来源。

| 文件 | 用途 |
| --- | --- |
| ingest.token | 仅交给 Gmail 采集器或手工导入工具 |
| worker.token | 仅交给生成主机 |
| read.token | 仅交给千问读取工具 |
| manage.token | 设置页管理口令 |
| api.env | API 的 systemd EnvironmentFile |
| worker.env | worker 的 systemd EnvironmentFile |

已有 token 会保留，重复运行不是轮换。生成器不打印秘密。api.env、worker.env 是 systemd 环境文件，**不要用 source 执行**。

可选 --output-dir 指定凭据输出目录；--codex-bin 是**生成主机**上的可执行文件路径或 PATH 名称；--data-dir 默认 /var/lib/qwen-daily-briefing。自定义数据目录时，必须创建并授权给 API 用户，同时把 [API 服务模板](../deploy/briefing-api.service) 的 ReadWritePaths 改成同一路径；仅改 api.env 会被 ProtectSystem=strict 阻止写入。

## 3. 本地启动 API

在已激活虚拟环境的终端中，只从 token 文件读取密钥。此步骤把数据放在本地 runtime/cloud，不使用生成器默认的系统目录。

~~~bash
export BRIEFING_DATA_DIR="$PWD/runtime/cloud"
export BRIEFING_SOURCE_POLICY_PATH="$PWD/runtime/config/source-policy.json"
export BRIEFING_INITIAL_PREFERENCES_PATH="$PWD/runtime/config/initial-preferences.json"
export BRIEFING_INGEST_TOKEN="$(cat runtime/credentials/ingest.token)"
export BRIEFING_WORKER_TOKEN="$(cat runtime/credentials/worker.token)"
export BRIEFING_READ_TOKEN="$(cat runtime/credentials/read.token)"
export BRIEFING_MANAGE_TOKEN="$(cat runtime/credentials/manage.token)"
python -m uvicorn briefing.api:create_app --factory --host 127.0.0.1 --port 8786 --workers 1 --no-access-log
~~~

另一个终端检查：

~~~bash
curl --fail http://127.0.0.1:8786/briefing/v1/health
~~~

上面的两个配置路径来自首次安装导出；它们需要先存在。纯开发环境若明确使用仓库示例，可不设置这两个变量。健康请求应得到 status 为 ok。用浏览器打开 http://127.0.0.1:8786/briefing/v1/manage/，输入 manage.token 的内容，即可查看实际加载的来源。初始没有邮件时显示“暂无来源”属于正常空状态，不表示已经生成简报。

API 必须保持一个进程、一个 worker。本地 HTTP 只适合回环调试。Gmail 采集器和生成 worker 要求 HTTPS；下一步需配置自己的 TLS 入口。

## 4. 提供 HTTPS 入口

[nginx-briefing.conf](../deploy/nginx-briefing.conf) 将 HTTPS 请求代理至本机 127.0.0.1:8786。替换 server_name、证书和私钥路径，确保证书覆盖实际域名。已有 Nginx 的服务器可合并其中 server 配置；不要同时让两个服务占用同一个 443 端口。

代理需保留 Host 并传递 X-Forwarded-Proto: https；应用只应信任自己的代理。示例 API 监听回环地址。外网健康检查成功后，再打开 HTTPS 管理页面确认登录与保存可用。长期运行、安装服务和续证见 [运行维护](operations.md)。

## 5. 接入 Gmail 云端采集

在自己的 Apps Script 项目中加入 [Code.gs](../cloud-gmail/Code.gs)，采用 [appsscript.json](../cloud-gmail/appsscript.json) 的配置并启用其中声明的 Gmail 高级服务。将以下值放入脚本属性，不写进源码：

| 属性 | 值 |
| --- | --- |
| EXPECTED_ACCOUNT | 实际 Gmail 账号，例如 owner@example.com |
| BRIEFING_BASE_URL | https://briefing.example.com/briefing/v1 |
| BRIEFING_INGEST_TOKEN | ingest.token 的完整内容 |
| BRIEFING_SOURCE_POLICY | 与 API、worker 相同的私有 source-policy.json 完整 JSON |

采集器先核对 Gmail profile 与 EXPECTED_ACCOUNT，再读取匹配邮件。先放入一封 [符合格式的合成邮件](mail-format.md)，手动执行 collectBriefings 并查看 collectorStatus。ingested 增加、失败数为零后，执行 installCollector 安装十五分钟触发器；这个函数也会立即执行一次采集。

它只读邮箱并向 API 提交，不修改邮件、不发送邮件。重复采集同一内容应去重。原始报告由你现有的任务或信息来源产生，本仓库不创建上游订阅。

## 6. 启动生成 worker

在生成主机上获取同一版本代码并安装 requirements.txt 中的依赖。先确认以将运行 worker 的同一用户执行 codex 能使用自己的账号。没有兼容 CLI 或账号尚未认证时，先完成该环境配置。

在新终端激活虚拟环境，设置以下值。worker 只需要自己的 token；若不与 API 同机，用安全方式复制 worker.token，不复制其他角色凭据。

~~~bash
export BRIEFING_BASE_URL="https://briefing.example.com/briefing/v1"
export BRIEFING_GENERATOR=codex
export BRIEFING_SOURCE_POLICY_PATH="$PWD/runtime/config/source-policy.json"
export BRIEFING_WORKER_TOKEN="$(cat runtime/credentials/worker.token)"
export CODEX_BIN=codex
export BRIEFING_WORKER_RUNTIME="$PWD/runtime/worker"
python -m briefing.worker --once
~~~

日志 outcome=published 表示本次稿件已发布；idle 表示当前没有待处理任务。其他结果需按 [运行维护](operations.md) 排查。生成会使用该 Codex 账号；项目不需要 GPU。

一次运行成功后，用以下命令持续检查，或安装 systemd 用户服务：

~~~bash
python -m briefing.worker
~~~

默认间隔为 120 秒，可通过 --interval 调整；--timeout 控制生成时限。程序允许范围以 --help 为准。没有收到来源邮件时，启动 worker 不会凭空生成简报。

## 7. 配置千问工具与朗读 Skill

本节的字段供手工配置参考。账号登录、真实插件标识、设备绑定、开发者模式和私有发布的完整步骤见 [首次安装的千问接入部分](setup.md)。本项目采用 HTTP 云云对接，无需安装手机 APP SDK。

在自己的千问开发者项目中创建 HTTP 读取工具：

| 项目 | 配置 |
| --- | --- |
| 方法与地址 | POST https://briefing.example.com/briefing/v1/read |
| 请求头 | Content-Type: application/json；Authorization: Bearer 加 read.token |
| 输入参数 | date 为字符串、section 为整数、topic 为字符串 |
| 请求体 | 将三个输入字段映射为同名 JSON 字段 |
| 参考定义 | [tool_schema.json](../qwen-skill/tool_schema.json) |
| Skill 指令 | [qwen-skill/SKILL.md](../qwen-skill/SKILL.md) |

替换两份模板中的 YOUR_PLUGIN_ID 为自己工具实际分配的插件 ID。不要把管理或生成 token 放到千问工具中，也不要把 read.token 写进公开 Skill 文本。

默认完整朗读用服务返回的 full_spoken_text；明确指定段号才用 spoken_text。只有三参数的工具使用保留 topic 别名选择视图，具体规则已写在 Skill 中。

分别测试“今日简报”“今天应该学什么”“今天工作有什么”和一个明确日期。工具调用、返回文字、Speak 输入和设备实际播放按 [验收文档](acceptance.md) 分别核对。

## 可选：安装 Codex 管理 Skill

已有后端后，可以安装本仓库的 add-to-briefing Skill：

~~~bash
npx skills add reknottycat/qwen-briefing-starter --skill add-to-briefing -a codex
~~~

它帮助 Codex 管理已存在的来源与设置，**安装 Skill 不会部署 API、worker、Gmail 采集器或千问插件**。使用前仍需按 [Skill 说明](../skills/add-to-briefing/SKILL.md) 配置目标服务与凭据。
