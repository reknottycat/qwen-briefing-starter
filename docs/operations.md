# 运行维护

本文面向已经完成 [快速开始](quickstart.md) 的部署。运维时以实际服务地址、安装目录、运行用户和版本为准，凭据与私人内容只保存在本地受限目录。

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

准备完用户、源码、虚拟环境和数据目录后，可在仓库根目录安装已核对的模板：

~~~bash
sudo install -d -m 0750 /etc/qwen-daily-briefing
sudo install -m 0600 runtime/credentials/api.env /etc/qwen-daily-briefing/api.env
sudo install -m 0644 deploy/briefing-api.service /etc/systemd/system/briefing-api.service
sudo systemctl daemon-reload
sudo systemctl enable --now briefing-api.service
sudo systemctl status briefing-api.service
~~~

若自定义 --data-dir，必须同时更改 API 模板 ReadWritePaths，并给 briefing 用户目标目录权限。ProtectHome=true 时，API 源码与数据不能简单放到普通用户主目录而不调整服务约束。

worker 推荐使用单独、已完成 Codex 登录的普通用户。让仓库与虚拟环境位于模板期望的 ~/daily-briefing，或先修改模板。以该用户执行：

~~~bash
install -d -m 0700 "$HOME/.config/daily-briefing"
install -d -m 0755 "$HOME/.config/systemd/user"
install -m 0600 runtime/credentials/worker.env "$HOME/.config/daily-briefing/worker.env"
install -m 0644 deploy/dgx-worker.service "$HOME/.config/systemd/user/dgx-worker.service"
systemctl --user daemon-reload
systemctl --user enable --now dgx-worker.service
systemctl --user status dgx-worker.service
~~~

需要退出登录后继续运行用户服务时，由主机管理员为该用户配置 linger。先验证运行用户实际能找到 Codex；交互终端的 PATH 不一定与 systemd 相同。api.env 与 worker.env 是 EnvironmentFile，不是 shell 脚本。

HTTPS 模板可用于独立 Nginx 实例，也可将 server 部分接入现有配置。先做配置检查，确保 443 端口、证书路径和代理目标正确，再 reload。续证 hook 只负责 reload，不负责申请证书；修改域名后同步更新 hook 中的匹配值。模板不提供域名注册或证书自动开户功能。

## 设置与任务状态

设置页为 /briefing/v1/manage/。保存时使用云端 revision 校验；409 表示有另一份修改，先回读最新值并确认合并，再保存。401 表示会话失效，重新解锁。管理页面不能新增、删除或重排来源。

| 状态 | 含义与处理 |
| --- | --- |
| no_sources | 今天没有可用来源；检查标题日期、采集范围和摄入结果 |
| queued | 已等待 worker；核对 worker 服务是否运行 |
| generating | 有活动租约；等待本次生成，勿重复启动多份人工任务 |
| retry_wait | 本次失败后退避；检查 error，再观察下一次尝试 |
| blocked | 相同版本已尝试三次；修复原因后明确重建 |
| ready | 当前缓存就绪；再做读取与内容核对 |

常见 worker 结果包括 published、idle、source_changed、timeout、codex_failed、invalid_output、input_too_large、publish_failed、worker_interrupted、service_unavailable。source_changed 表示生成期间任务已被新来源或设置替代，旧结果未覆盖新版本。容量错误应缩减所选来源或调整朗读方式后重建，不能静默截断原文。

可查看受限本机日志：

~~~bash
journalctl -u briefing-api.service --since today
journalctl --user -u dgx-worker.service --since today
~~~

Gmail 端执行 collectorStatus 查看最后运行时间、成功提交数和失败数。失败回执可能包含消息 ID，分享前需删去私人标识。健康端点正常不表示采集、生成或语音链路正常。

## 明确重建某一天

保存设置只自动为今天排队。需要重新生成历史日期，或修复 blocked 后重试，调用 worker 角色的 POST /jobs/rebuild。下面使用当前目录的 worker.token，日期参数替换为目标日期；不会修改原始邮件。

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
2. 将整个数据目录复制到权限受限的备份目录，连同四角色凭据分别保管。备份包含原始邮件，应采用与原数据相同的访问限制。
3. 在隔离目录用目标代码与策略验证备份副本，核对消息、偏好、缓存读取和工作开关；不要把它连接到原 Gmail 触发器。
4. 完成后再切换正式目录并启动 API、worker。恢复时同样先停止写入进程，恢复匹配的状态与策略；不要直接在运行中的 state.json 上编辑。
5. 升级后重跑自动化检查与适用的 HTTP 验收。API 重启会清空浏览器会话，需重新登录。

现阶段没有自动迁移工具。更改来源身份或删除来源需要设计迁移映射，验证历史引用与偏好集合；仅更改策略 JSON 可能导致现有状态无法启动。详见 [来源定制](mail-format.md)。

## 凭据轮换

配置生成器保留已有 token，不承担轮换。需要轮换时，使用安全随机值生成新的独立角色 token，至少 24 字符，更新 API 环境文件及该角色消费者，重启 API，再验证旧 token 被拒绝、新 token 可用。当前接口没有双 token 平滑过渡，轮换期间需安排短暂中断。

ingest 更新 Apps Script 属性；worker 更新生成主机；read 更新千问工具；manage 更新受信任管理员。不要将任一角色值提交到 Git、写入公开 Skill、放入 URL 参数或发布在 Issue。安全问题通过 [SECURITY.md](../SECURITY.md) 中的私密渠道报告。
