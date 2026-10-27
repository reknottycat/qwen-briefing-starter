# 运行维护

本文面向已经完成 [快速开始](quickstart.md) 的部署。运维时以实际服务地址、安装目录、运行用户和版本为准，凭据与私人内容只保存在本地受限目录。

## Docker 实例与私有配置

首次部署先完成 [来源和服务器确认](setup.md)。Docker 模板运行 Pi worker，具体命令、UID/GID、秘密文件和目录在 [容器部署说明](../deploy/docker/README.md) 维护；Codex 路线使用下文 Linux 服务。不要用 systemd 的 api.env、worker.env 代替 Compose 的 .env。

API 与 worker 的 BRIEFING_SOURCE_POLICY_PATH 必须指向同一份已确认策略。BRIEFING_INITIAL_PREFERENCES_PATH 仅用于新状态，运行偏好以管理 API 回读为准。新的初始化文件不是已有状态的迁移工具。

Docker 日常检查使用 docker compose ps，以及 docker compose logs api worker。备份时先停 worker、再停 API，复制宿主的 runtime/docker/data/ 和 runtime/docker/pi-settings/ 到受限备份目录；恢复时保留对应策略版本。后一个目录包含模型密钥，不能作为日志附件分享。默认端口 8787 只绑定回环，公网访问经过 HTTPS 代理。若使用现有端口为 8786 的 Nginx 模板，必须同步代理端口，并按容器说明核对精确可信代理 IP。

## 生成器配置

BRIEFING_GENERATOR 只支持 codex 或 pi。原有命令默认 codex，Docker 模板明确选择 pi；两者使用相同来源、段落、偏好和发布校验。配置助手可由 Codex/OpenCode 调用，这不改变服务器的生成方式。

Codex 使用 CODEX_BIN（默认 codex）以及运行用户已配置的认证和中转地址。先在同一用户、同一服务环境确认现有 CLI 可用；不要为改用 Docker 擅自迁移或覆盖其个人配置。本文下方的用户服务可直接承载该路径。

Pi 使用固定版本 @earendil-works/pi-coding-agent@1.4.0；独立 Linux 安装需 Node.js 22.19+，Docker 镜像已包含相应 CLI。

### Docker：在设置页配置 Pi

Docker 启动只需来源文件、四角色 token 和容器运行参数，不需预填模型提供方或 API key。用本实例 manage.token 登录 HTTPS 设置页，在“整理早报，用哪个模型”中选择“内置服务”，按目录选择提供方与模型；或选择“第三方兼容接口”，填写服务标识、HTTPS 基础地址、模型 ID 和支持的协议。当前自定义协议为 OpenAI Chat Completions。密钥由用户直接填入密码输入框，不经聊天或命令参数传递。

内置目录来自固定 Pi 版本的 [模型目录快照](../briefing/pi_model_catalog.json)。它包含来源与版本元数据，不实时查询账号开通情况、价格或可用性。表单只承载单个服务凭据；OAuth 或还需区域、部署、账号等额外配置的内置路径不在此通用流程内。

保存后只回读非敏感配置与“已配置”状态，不返回原密钥。目标不变时，密钥留空可沿用已存值；只改模型也可沿用。切换内置/自定义模式、提供方、基础地址或协议时必须重新输入密钥。页面不会将密钥写入浏览器持久存储或日志。并发保存返回 409 时，回读最新版本、核对差异后再保存；被清空的密钥需重新输入，不能强制覆盖。

保存供 worker **下一次生成**使用，不需要重启服务；保存本身不排队、不调用模型，也不代表完成连接测试、已有缓存重建或内容质量验收。正在运行的任务保留开始时取得的配置。用下一次授权生成核对账号、模型与实际输出；模型配置与朗读偏好分别保存，修改模型不等于修改来源、分类或详略。

Compose 为 API 与 worker 设置同一个 BRIEFING_PI_SETTINGS_DIR：容器路径为 /var/lib/briefing-pi-settings，宿主目录为 runtime/docker/pi-settings/。API 可写、worker 只读，两者使用相同 UID。宿主目录为 0700，pi-model-settings.json 由 API 以 0600 原子保存。worker 每轮先读取完整设置，再领取任务；文件尚不存在时返回 model_not_configured 并等待，不领取或消耗任务尝试。即使所有来源都选原文，首次仍须完成模型配置；配置完成后，原文正文依然不经过模型。已设置此目录时，不回落到 PI_PROVIDER、PI_MODEL 或旧 key 文件。不要在线手改设置文件。

### Linux：手工配置 Pi

不启用 BRIEFING_PI_SETTINGS_DIR 的独立 Linux worker 继续使用下表。先安装当前版本依赖，再设置：

| 变量 | 含义 |
| --- | --- |
| BRIEFING_GENERATOR=pi | 选择 Pi 适配器 |
| PI_PROVIDER | Pi 提供方标识，必须与模型配置一致 |
| PI_MODEL | 提供方实际支持的精确模型 ID |
| PI_API_KEY_FILE | 用户在生成主机上保存的单行 API key 文件 |
| PI_BASE_URL | 可选 HTTPS OpenAI Chat Completions 兼容基础地址；不含凭据、查询或片段 |
| PI_CA_FILE | 可选自有 CA 文件，用于验证私有证书；不关闭 TLS 验证 |
| PI_BIN | 可选可执行文件名或路径，默认 pi |

Pi 不使用个人 OAuth 登录文件。适配器为每次运行建立独立 HOME/XDG/agent 目录，关闭工具、扩展、MCP、skills 和会话保存，结束后清理临时目录。它通过云端提供方生成文字；结构通过仍需核对内容忠实度。--offline 等 CLI 约束不表示“模型在本机运行”，模型网络请求仍会发往已配置服务。

Linux 手动安装 Pi 的命令：

~~~bash
npm install --global @earendil-works/pi-coding-agent@1.4.0
~~~

此手工模式的模型 key 文件在用户机器上编辑，勿放进 shell 参数或聊天。Pi 的 provider/model 和基础地址可写入 worker 服务环境，key 只以文件路径传递。重启 worker 后核对实际 BRIEFING_GENERATOR 与结果状态；不要以配置已保存替代生成成功。Docker 的网页设置流程不使用这份 worker 环境文件。

Linux/Pi 的环境文件可由配置生成器生成。在实际部署主机上，先安全保存单行 API key 文件；将下面非秘密的 provider/model/域名替换为确认值：

~~~bash
python deploy/create-runtime-config.py --base-url https://briefing.example.com/briefing/v1 --generator pi --source-policy runtime/config/source-policy.json --initial-preferences runtime/config/initial-preferences.json --pi-provider openai --pi-model replace-with-your-approved-model --pi-api-key-file runtime/credentials/llm-api-key
~~~

--source-policy 与 --initial-preferences 指向首次流程导出的文件；后者必须与前者一起提供。自定义中转可加 --pi-base-url，私有 CA 可加 --pi-ca-file，均不包含秘密值。生成器校验后保存绝对路径，API/worker 用户必须读得到相应文件。Docker 仍使用其独立 Compose 配置，不用 source 执行这里的环境文件。

## systemd 与 HTTPS 模板

| 模板 | 需要对齐的配置 |
| --- | --- |
| [briefing-api.service](../deploy/briefing-api.service) | User/Group、WorkingDirectory、虚拟环境路径、EnvironmentFile、ReadWritePaths |
| [dgx-worker.service](../deploy/dgx-worker.service) | 用户主目录下的 daily-briefing、虚拟环境、worker.env 和 Codex PATH |
| [nginx-briefing.conf](../deploy/nginx-briefing.conf) | 域名、证书、私钥、监听端口和 API 端口 |
| [nginx-briefing.service](../deploy/nginx-briefing.service) | Nginx 可执行文件和配置路径 |
| [reload-certificate.sh](../deploy/reload-certificate.sh) | 实际证书域名和需要 reload 的服务 |

worker 模板保留历史文件名 dgx-worker.service，但不依赖 DGX。它作为用户服务运行；模板的 %h 由 systemd 展开为运行用户的主目录。

API 模板默认源码位于 /opt/qwen-daily-briefing，虚拟环境位于该目录的 .venv，运行用户为 briefing。安装前创建该用户，让它能读取代码，创建 /var/lib/qwen-daily-briefing 并将该数据目录所有权交给它。不要把虚拟环境直接从另一台主机复制过来，应在目标主机安装依赖。

准备完用户、源码、虚拟环境和数据目录后，先把确认过的策略与初始化偏好安装到 API 用户可读的位置。API 模板启用 ProtectHome=true，不能直接引用普通用户 HOME 下的 0600 导出文件：

~~~bash
sudo install -d -o root -g briefing -m 0750 /etc/qwen-daily-briefing
sudo install -d -o briefing -g briefing -m 0750 /etc/qwen-daily-briefing/config
sudo install -o briefing -g briefing -m 0600 runtime/config/source-policy.json /etc/qwen-daily-briefing/config/source-policy.json
sudo install -o briefing -g briefing -m 0600 runtime/config/initial-preferences.json /etc/qwen-daily-briefing/config/initial-preferences.json
sudo install -m 0600 runtime/credentials/api.env /etc/qwen-daily-briefing/api.env
sudo install -m 0644 deploy/briefing-api.service /etc/systemd/system/briefing-api.service
sudoedit /etc/qwen-daily-briefing/api.env
~~~

在受限编辑器中保留角色 token 等其他字段，仅把这两个路径改为安装后的绝对位置：

~~~text
BRIEFING_SOURCE_POLICY_PATH="/etc/qwen-daily-briefing/config/source-policy.json"
BRIEFING_INITIAL_PREFERENCES_PATH="/etc/qwen-daily-briefing/config/initial-preferences.json"
~~~

核对 briefing 用户可读取两份文件后，再启动服务：

~~~bash
sudo -u briefing test -r /etc/qwen-daily-briefing/config/source-policy.json
sudo -u briefing test -r /etc/qwen-daily-briefing/config/initial-preferences.json
sudo systemctl daemon-reload
sudo systemctl enable --now briefing-api.service
sudo systemctl status briefing-api.service
~~~

若自定义 --data-dir，必须同时更改 API 模板 ReadWritePaths，并给 briefing 用户目标目录权限。ProtectHome=true 时，API 源码与数据不能简单放到普通用户主目录而不调整服务约束。

worker 使用已配置 Codex 或 Pi 的普通用户。让仓库与虚拟环境位于模板期望的 ~/daily-briefing，或先修改模板。同机不同用户或分机时，把同一份已确认策略和 worker.env 安全交给该 worker 用户；不复制其他三个角色 token。worker 不需要读取 API 专用的初始偏好文件。以该用户执行：

~~~bash
install -d -m 0700 "$HOME/.config/daily-briefing"
install -d -m 0700 "$HOME/.config/daily-briefing/config"
install -d -m 0755 "$HOME/.config/systemd/user"
install -m 0600 runtime/config/source-policy.json "$HOME/.config/daily-briefing/config/source-policy.json"
install -m 0600 runtime/credentials/worker.env "$HOME/.config/daily-briefing/worker.env"
install -m 0644 deploy/dgx-worker.service "$HOME/.config/systemd/user/dgx-worker.service"
~~~

用本地编辑器打开该用户的 worker.env，将 BRIEFING_SOURCE_POLICY_PATH 改为刚复制的文件的**实际绝对路径**，并核对 CODEX_BIN 或 PI_BIN、PI_API_KEY_FILE 在这台主机可用。EnvironmentFile 不展开 ~ 或 $HOME，不能把它们当作变量写入文件。API 与 worker 的文件路径可以不同，策略内容必须相同；两边用 sha256sum 核对策略一致，不输出凭据。随后启动：

~~~bash
systemctl --user daemon-reload
systemctl --user enable --now dgx-worker.service
systemctl --user status dgx-worker.service
~~~

需要退出登录后继续运行用户服务时，由主机管理员为该用户配置 linger。先验证运行用户实际能找到所选 CLI；交互终端的 PATH 不一定与 systemd 相同。api.env 与 worker.env 是 EnvironmentFile，不是 shell 脚本。

HTTPS 模板可用于独立 Nginx 实例，也可将 server 部分接入现有配置。先做配置检查，确保 443 端口、证书路径和代理目标正确，再 reload。续证 hook 只负责 reload，不负责申请证书；修改域名后同步更新 hook 中的匹配值。模板不提供域名注册或证书自动开户功能。

## 设置与任务状态

设置页为 /briefing/v1/manage/。朗读偏好与 Pi 模型设置分别保存、分别校验 revision；409 表示对应设置已有另一份修改，先回读最新值并确认合并，再保存。401 表示会话失效，重新解锁。管理页面不能新增、删除或重排来源。Codex 继续使用原有 CLI 配置，页面不管理 Codex 认证。

| 状态 | 含义与处理 |
| --- | --- |
| no_sources | 今天没有可用来源；检查标题日期、采集范围和摄入结果 |
| queued | 已等待 worker；核对 worker 服务是否运行 |
| generating | 有活动租约；等待本次生成，勿重复启动多份人工任务 |
| retry_wait | 本次失败后退避；检查 error，再观察下一次尝试 |
| blocked | 相同版本已尝试三次；修复原因后明确重建 |
| ready | 当前缓存就绪；再做读取与内容核对 |

常见 worker 结果包括 published、idle、source_changed、timeout、codex_failed、invalid_output、input_too_large、publish_failed、worker_interrupted、service_unavailable。Docker/Pi 的 model_not_configured 是模型配置等待状态，与上表的生成队列阶段不同：队列可继续显示 queued，worker 尚未领取任务。先登录网页完成模型设置，再观察下一轮生成。source_changed 表示生成期间任务已被新来源或设置替代，旧结果未覆盖新版本。容量错误应缩减所选来源或调整朗读方式后重建，不能静默截断原文。

可查看受限本机日志：

~~~bash
journalctl -u briefing-api.service --since today
journalctl --user -u dgx-worker.service --since today
~~~

Gmail 端执行 collectorStatus 查看最后运行时间、成功提交数和失败数。失败回执可能包含消息 ID，分享前需删去私人标识。健康端点正常不表示采集、生成或语音链路正常。

## 明确重建某一天

保存朗读偏好只自动为今天排队；保存模型配置供下一次生成使用。需要重新生成历史日期，或修复 blocked 后重试，调用 worker 角色的 POST /jobs/rebuild。下面使用当前目录的 worker.token，日期参数替换为目标日期；不会修改原始邮件。

~~~bash
export BRIEFING_BASE_URL="https://briefing.example.com/briefing/v1"
python - 2026-01-01 <<'PY'
import json
import os
import sys
from pathlib import Path
import httpx

token = Path("runtime/credentials/worker.token").read_text().strip()
with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
    response = client.post(
        os.environ["BRIEFING_BASE_URL"].rstrip("/") + "/jobs/rebuild",
        headers={"Authorization": "Bearer " + token},
        json={"date": sys.argv[1]},
    )
    response.raise_for_status()
    print(json.dumps(response.json(), ensure_ascii=False))
PY
~~~

queued 或 already_queued 只证明任务已排队，不证明新稿已发布。等待 worker，核对设置页状态与新缓存的 generated_at、preferences_revision，再按 [验收](acceptance.md) 读取。

## 备份、恢复与升级

状态文件位于 BRIEFING_DATA_DIR/state.json，包含邮件原文、稿件、任务和偏好，是私人数据。worker 临时目录也可能包含模型输入与输出，不能作为公开调试附件。

1. 记下当前代码版本、策略文件版本和实际数据目录；停止 worker，再停止 API，避免备份与迁移过程中发生写入。
2. 将整个数据目录复制到权限受限的备份目录，连同四角色凭据分别保管。启用网页 Pi 配置时，同时备份 BRIEFING_PI_SETTINGS_DIR 并保留目录与文件权限；该目录含模型密钥。备份包含原始邮件与秘密，应采用与原数据相同的访问限制。
3. 在隔离目录用目标代码与策略验证备份副本，核对消息、偏好、缓存读取和工作开关；不要把它连接到原 Gmail 触发器。
4. 完成后再切换正式目录并启动 API、worker。恢复时同样先停止写入进程，恢复匹配的状态与策略；不要直接在运行中的 state.json 上编辑。
5. 升级后重跑自动化检查与适用的 HTTP 验收。API 重启会清空浏览器会话，需重新登录。

现阶段没有自动迁移工具。更改来源身份或删除来源需要设计迁移映射，验证历史引用与偏好集合；仅更改策略 JSON 可能导致现有状态无法启动。详见 [来源定制](mail-format.md)。

## 凭据轮换

配置生成器保留已有 token，不承担轮换。需要轮换时，使用安全随机值生成新的独立角色 token，至少 24 字符，更新 API 环境文件及该角色消费者，重启 API，再验证旧 token 被拒绝、新 token 可用。当前接口没有双 token 平滑过渡，轮换期间需安排短暂中断。

ingest 更新 Apps Script 属性；worker 更新生成主机；read 更新千问工具；manage 更新受信任管理员。不要将任一角色值提交到 Git、写入公开 Skill、放入 URL 参数或发布在 Issue。安全问题通过 [SECURITY.md](../SECURITY.md) 中的私密渠道报告。

Pi 模型 key 与四角色 token 分开轮换。Docker 用户在已登录的 HTTPS 模型设置表单输入新 key 并保存，下一次生成验证新凭据；Linux 手工模式在受限 key 文件中更换。旧服务上的 key 撤销由该服务账号管理，不由本项目代办。
