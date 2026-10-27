# 在小服务器运行 Docker

此部署把 API 与后台整理程序放在两个容器中，使用同一镜像。后台复用固定版本的官方 Pi CLI（`@earendil-works/pi-coding-agent@1.4.0`）调用用户配置的云端模型；服务器不需要 DGX 或 GPU。Codex、OpenCode 是配置 Skill 的调用入口，不必安装到这台 Docker 主机。

先完成仓库的[配置流程](../../docs/setup.md)：确认每日非监控任务及分类，确认部署服务器，再导出配置。模型提供方、模型和 API key 在启动后的设置网页中填写。不要用示例来源替换用户已经确认的来源。

## 准备文件

在部署仓库中放好以下文件。`runtime/` 和 `.env` 均不进入 Git：

| 文件 | 来源与用途 |
| --- | --- |
| `runtime/config/source-policy.json` | 配置流程导出的确认来源，API 与 worker 使用同一份 |
| `runtime/config/initial-preferences.json` | 导出的新闻、学习、工作分类及朗读设置；只初始化新状态 |
| `runtime/credentials/{ingest,worker,read,manage}.token` | 配置生成器创建的四个不同权限的口令 |
| `.env` | Linux UID/GID、监听端口与精确可信代理 IP；不放模型密钥 |
| `runtime/docker/data/` | API 持久数据，容器重建后保留 |
| `runtime/docker/worker/` | Pi 每次整理的临时输入与运行目录 |
| `runtime/docker/pi-settings/` | 网页保存的私有模型设置与密钥；首次启动时为空，API 写入、worker 只读 |

首次使用可复制 `deploy/docker/env.example` 为 `.env`。Docker 启动不要求 `PI_PROVIDER`、`PI_MODEL`、`PI_BASE_URL` 或模型 key 文件；未配置模型时 API 和设置网页仍正常可用，worker 等待配置，不领取待处理任务。

在 Linux 上，`.env` 的 `BRIEFING_UID` 和 `BRIEFING_GID` 应等于持有 `runtime/` 文件的非 root 用户的 `id -u`、`id -g`。先由该用户创建目录，并把凭据文件限制为仅本人可读写；不需要把 token 改成所有用户可读。已有文件保留原值，不要重复生成或覆盖。

```bash
mkdir -p runtime/docker/data runtime/docker/worker runtime/docker/pi-settings
chmod 700 runtime/credentials runtime/config runtime/docker/data runtime/docker/worker runtime/docker/pi-settings
chmod 600 runtime/credentials/*.token
```

以上目录须在首次启动前由该非 root 用户创建。如果此前由 root 创建过空目录，先由管理员将这些明确目录的所有权交给配置的 UID/GID，不要以开放所有用户写入的权限代替。API 和 worker 必须使用相同 UID；设置目录在 API 中可写，在 worker 中只读。程序将 `pi-model-settings.json` 以仅所有者可读写的 `0600` 权限原子保存。

Compose 把角色口令作为逐服务授权的只读 secret 文件挂载：API 获得四个角色口令，worker 仅获得 worker 口令。启动入口只把需要的角色口令读入本进程环境，不打印口令。模型 key 由用户通过自己的 HTTPS 设置网页提交，不放进聊天、Compose 或 `.env`。不要把系统服务用的 `api.env`、`worker.env` 当作 Compose `.env` 使用。

## 启动和检查

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
curl --fail http://127.0.0.1:8787/briefing/v1/health
```

默认只监听服务器本机 `127.0.0.1:8787`。在现有 HTTPS 反向代理上把 `/briefing/v1/` 转发到这个地址，使用真实域名及可信证书；不要把 8787 端口直接开放到公网。仓库的 Nginx 模板原有上游端口是 8786，采用 Docker 时须改为此处的 8787，或通过 `BRIEFING_PORT` 统一两者。

**启用公网设置页之前，必须配置 HTTPS 代理的精确来源 IP。** 宿主机上的 Nginx 通过发布端口进入容器时，API 通常看到 Docker bridge 网关地址；默认只信任 `127.0.0.1`，会忽略该网关发送的 `X-Forwarded-Proto: https`，设置页登录因此返回 `https_required`。保持默认值直到核实代理来源，不能用 `*` 或整个网段代替。

在已启动的本项目中查看 API 网络的实际网关：

```bash
docker inspect --format '{{range .NetworkSettings.Networks}}{{println .Gateway}}{{end}}' "$(docker compose ps -q api)"
```

当前 Compose 只有一个网络。对于宿主机 Nginx，将上面核实的**单个网关 IP**填入 `.env` 的 `BRIEFING_TRUSTED_PROXY_IPS`，然后重新创建 API：

```bash
docker compose up -d --no-deps --force-recreate api
```

该设置只接受最多八个以逗号分隔的精确 IPv4／IPv6 地址，不接受域名、网段、通配符或未指定地址。若代理运行在其他容器或远端，填写实际代理连接到 API 时的来源 IP，而不是照抄网关；不要让不可信容器共享代理的来源身份。网络被删除重建后，重新检查实际网关并更新配置，不能假定旧 IP 不变。反向代理必须覆盖 `Host` 和 `X-Forwarded-Proto` 为当前真实请求值，不能透传外部用户伪造的协议头。

最后通过实际 HTTPS 域名登录设置页并读取偏好，确认不再返回 `https_required`。本机 `/health` 成功不能证明这条代理路径已经配置正确。

## 在网页配置模型

1. 打开 `https://你的域名/briefing/v1/manage/`，使用 `runtime/credentials/manage.token` 中的管理口令登录。
2. 在 Pi 模型设置中选择提供方与准确模型 ID，填写该提供方的 API key。网页提供官方 Pi 目录；自定义 OpenAI Chat Completions 兼容服务可另外填写 HTTPS 接口基础地址。
3. 保存后重新读取设置。网页仅显示“已配置”及非敏感配置，不返回保存的密钥。保存表示配置已存储；第一次实际生成才能验证提供方账号、额度及模型是否可用。
4. 准备一份已确认来源的测试报告。worker 下一轮会读取新设置并处理已有任务，无须重建镜像或重新启动服务。每次新任务使用当轮读取的完整配置，正在进行的任务保留其开始时的配置。

模型设置保存在私有持久目录，API／worker 重启后仍保留。Docker 模式以该网页设置为准：尚未保存设置时不会回落到旧的模型环境变量。普通 Linux 部署仍可选择[手工 Pi 环境配置](../../docs/quickstart.md)，不与这个 Docker 网页流程混用。

API 始终只有一个 Uvicorn worker。后台通过 Docker 内部的 `http://api:8787/briefing/v1` 取任务，显式启用仅限该内部主机的 HTTP；眼镜和 Gmail 仍使用公网 HTTPS 地址。正常整理轮询间隔为 120 秒，眼镜读取缓存时不会触发云模型调用。

健康检查通过只说明 API 可用。继续按照[验收契约](../../docs/acceptance.md)发送一份用户同意的测试报告，核对生成结果、HTTPS 读取和千问设备播放。

## 验证镜像与处理链路

开发和 CI 可运行下面的独立检查；需要 Docker Compose、Python 及 OpenSSL：

```bash
python scripts/verify_docker.py --output runtime/docker-smoke/report.json
```

脚本构建真实镜像，在唯一 Compose 项目中启动 API、Pi worker 及本地 HTTPS 模型替身。它先确认空模型设置可以启动、未配置的 worker 不领取已有任务，再通过认证管理 API 保存合成模型设置，检查密钥不回显，随后使用自定义新闻和学习来源实际经过 Pi CLI、发布、缓存读取。API 与 worker 重启后再次生成，用来验证模型设置和已有内容均被保留。它还通过宿主机到容器的真实连接模拟 HTTPS 代理头：未信任网关时拒绝登录，只添加该网关后才能登录并读取设置，错误 Origin 仍被拒绝。

模型替身使用合成内容和测试 CA，不调用付费云模型。结果文件明确标记 `docker_synthetic_model`，不能代替真实模型质量、实际域名 TLS 或眼镜验收。结束时只移除这次创建的容器、网络和镜像标签，保留本次本地报告；不会停止用户其他服务。CI 仅收集报告与日志，不上传模型设置、密钥或完整运行目录。

更新前在受保护的位置备份 `runtime/docker/data/` 和 `runtime/docker/pi-settings/`。`docker compose down` 后再启动不会删除这些目录；不要用新的来源策略直接覆盖已有状态，先按配置流程处理迁移。
