---
name: im-teams-auth
description: 获取并缓存 360Teams 公网 IM token。通过 Teams 客户端完成认证，兑换 token 写入 OS Keyring（macOS/Windows），支持检查、重新认证与清除。业务 skill 需要 360Teams IM token、收到认证失效（退出码 4）、或用户要求重新认证/退出登录时使用。
---

# IM Teams Auth

用于从 Teams 客户端内的项目认证页获取短期认证凭证，由本地脚本兑换公网 IM token 并存入 OS Keyring。

这是一个通用认证 skill，负责统一处理鉴权检查、认证拉起、浏览器兜底、token 缓存与认证结果返回。其他业务 skill 只负责在需要鉴权时调用它，不在各自 skill 内重复定义认证规则。

运行环境统一由 `scripts/config.py` 顶部的 `ACTIVE_ENVIRONMENT` 控制；命令行 `--env` 仅用于临时覆盖本次执行，不修改配置。

强约束：

- 运行环境属于内部实现细节，禁止在面向最终用户的回复中主动提及、解释或展示当前环境、默认环境、`ACTIVE_ENVIRONMENT`、`--env`、`test`、`production` 等字样。
- 除非开发者明确要求排查配置或查看脚本行为，否则代理只按配置执行，不向用户解释环境选择。
- 作为被调用方，本 skill 独占认证策略定义。调用它的业务 skill 只能消费其结果，不能在各自文档里复制、改写或扩展认证规则。

脚本职责：

- `scripts/config.py`：只维护环境、URL、端口、超时、Keyring 名称等静态配置。
- `scripts/credential_store.py`：负责环境规范化和 token 的 Keyring 读写、清理。
- `scripts/session_store.py`：负责待完成认证会话的文件锁、读写、复用判定与时效计算。
- `scripts/runtime.py`：负责日志初始化和 keyring 依赖检测。
- `scripts/auth.py`：负责认证流程编排和本地 receiver。

兼容性：

- Python `>=3.9`
- macOS Keychain
- Windows Credential Manager
- 自动安装 `keyring`（若缺失），其余仅依赖 Python 标准库

运行前置：脚本需要 Python 3.9+。**被直接调用时，每次任务开始探测一次可用解释器并复用**（若由 `t5t-skill` 等业务 skill 调起，业务 skill 已在任务开始探测、且脚本经 `sys.executable` 继承同一解释器，这里无需重复探测），依次尝试：

```bash
python3 --version || python --version || py -3 --version
```

用第一个成功且版本 ≥ 3.9 的命令（`python3` / `python` / `py -3`）作为本次任务所有脚本的解释器（文档示例统一写 `python3`，按探测结果替换即可）。三者都失败、或报 `command not found`/`不是内部或外部命令`、或 macOS 弹「安装命令行开发者工具」、Windows 跳应用商店时，再提示用户：「需要安装 Python 3.9+（https://www.python.org/downloads/），或确认 Python 已加入 PATH，然后重试」，不要直接断言没装，也不要反复重试同一命令。

## 原理

浏览器页面不能直接写系统钥匙串。本技能的脚本会先启动一个只监听 `127.0.0.1` 的一次性 HTTP receiver，再通过 Teams scheme 以 `navigation_to=win` 打开 `max-oplatform` 认证页。脚本会为当前环境维护一个待完成认证会话；同一会话未超时前，后续重复认证请求必须复用同一个链接和 receiver，不得生成新的互斥链接。认证页从 Teams 已登录态生成短期认证凭证，用户确认授权后通过 POST 回传给本地 receiver；脚本校验 `state`、`Origin` 和认证会话时效，通过 HTTPS 将短期凭证兑换为 token，写入 Keyring 并立即退出。长期 token 不经过页面到本地 receiver 的传输。

## Commands

环境检测：

```bash
python3 scripts/env_check.py
```

登录认证：

```bash
python3 scripts/auth.py
```

未显式传入 `--env` 时，脚本使用 `scripts/config.py` 中的配置。下面带 `--env` 的命令仅用于开发调试示例，不是面向最终用户的话术。

测试环境：

```bash
python3 scripts/auth.py --env test
```

检查认证状态：

```bash
python3 scripts/auth.py --check --env production
```

强制重新认证：

```bash
python3 scripts/auth.py --no-cache
```

清除当前环境 token：

```bash
python3 scripts/auth.py --clear --env production
```

清除所有环境 token：

```bash
python3 scripts/auth.py --clear-all
```

macOS/Linux 也可以用包装脚本：

```bash
./scripts/run.sh auth
```

Windows 可以用：

```bat
scripts\run.bat auth
```

脚本默认不会直接打开落地页，而是打开 Teams scheme：

```text
teamssit://applink/link?url=<encoded landing url>
sk360teams://applink/link?url=<encoded landing url>
```

## Storage

凭证只存 OS Keyring，不写明文文件。Token 默认过期时间为 3 天。

认证进行中会在 `cache/pending_session_<env>.json` 记录待完成会话（`state`、`receiver`、`landingUrl`、超时等元数据，**不含 token**），供并发触发时复用同一窗口；认证成功后删除，残留也会在下次运行按状态/时效清理。配套 `cache/pending_session_<env>.lock` 为短时互斥锁，进程异常退出残留时超过 30 秒会被自动抢占。

Keyring 位置：

- service: `im-teams-auth:production` 或 `im-teams-auth:test`
- token key: `gateway_token`
- expiry key: `gateway_token_expiry`

其他技能读取方式：

```python
import keyring

token = keyring.get_password("im-teams-auth:production", "gateway_token")
```

也支持环境变量：

```bash
IM_TEAMS_GATEWAY_TOKEN_PRODUCTION
IM_TEAMS_GATEWAY_TOKEN_TEST
```

环境变量按环境区分，且优先于对应环境的 Keyring。环境变量 token 没有本地过期时间。

## Workflow

标准流程：

1. 执行 `python3 scripts/env_check.py` 检查 Python 与 keyring。
2. 按当前环境执行 `python3 scripts/auth.py --check`；如需临时覆盖再显式传 `--env`。
3. 已认证则直接复用当前环境对应的 Keyring token。
4. 未认证则执行 `python3 scripts/auth.py`，如需临时覆盖再显式传 `--env`。
5. 脚本先启动本地 receiver：`http://127.0.0.1:35101-35110/token`。
6. 脚本拼出认证页 URL，并带上 `navigation_to=win`、`win_config`、`state`、`receiver`、`request_expires_at`、`session_id`、`win_id`。
7. 脚本打开 Teams scheme：`<scheme>applink/link?url=<encoded landing url>`。
   - 只有需要浏览器兜底的那类场景，脚本才会额外输出用户可点击的浏览器认证链接。
   - 该链接必须是认证落地页的 `https/http` 页面链接，不带 `teamssit://`、`sk360teams://` 等 scheme。
   - 代理拿到该链接后，必须明确提示用户“请点击下面链接在浏览器完成认证”，不能只保留在内部日志或工具输出里。
   - 该提示默认是认证过程中的并行兜底提示；如果当前业务命令还在继续执行，代理不得把这句提示表述成“流程已暂停”。
   - 如果当前环境已经存在未超时的待完成认证会话，脚本必须复用已有链接，只提示用户继续使用同一个窗口或链接，不得再生成新的认证链接。
8. Teams 客户端在窗口中打开认证页，页面从 Teams 已登录态生成短期认证凭证。
9. 用户确认授权后，认证页 POST 短期认证凭证到 receiver。
10. receiver 校验 `state`、`Origin` 和 `request_expires_at` 对应的会话时效，通过 HTTPS 兑换 token，写入 Keyring 和过期时间。
11. 认证成功后重试原操作。

## 用户提示约束

- 认证成功时，只需告诉用户认证已完成或继续当前流程，不要附带环境名、token 来源、Keyring 位置等内部细节。
- `--check` 或正式认证返回退出码 `4` 时，只有当脚本已经结束并明确输出了面向用户的浏览器认证链接或对应提示文案，代理才需要把该链接整理成可点击链接发给用户，并暂停后续操作等待用户完成认证。
- 如果脚本仍在执行中，只是提前输出了浏览器认证链接或提示文案，代理应将其视为并行兜底提示，同时继续等待当前命令的业务结果，不要自行宣告流程中断。
- 当脚本已经给出浏览器认证链接时，禁止只回复“认证失败”“认证过期”或“请重新登录”这类无操作指引的话。
- 只有在开发者明确要求时，才可以讨论 `--env`、scheme 差异、落地页域名或配置字段。

## 与业务 Skill 的边界

- `im-teams-auth` 负责：是否需要认证、如何认证、是否展示浏览器兜底链接、认证完成后的结果格式。
- 业务 skill 负责：在自己的业务流程里调用 `im-teams-auth`，并在收到认证结果后决定是继续业务流程还是中断等待用户。
- 业务 skill 不负责：解释认证策略、补充浏览器认证条件、改写认证文案、推断是否应该给链接。

## Landing Page Contract

落地页路径：

```text
/discover/imTeamsAuth
```

认证页接收 query：

- `navigation_to`: 固定为 `win`，让客户端在 Teams 窗口中打开认证页。
- `win_config`: Teams 窗口配置。
- `state`: 本地脚本生成的一次性随机值，用于防止其他页面伪造短期认证凭证回传。
- `receiver`: 本地 receiver 地址，必须是 `http://127.0.0.1:35101-35110/token`，用于让认证页把短期认证凭证交回脚本。
- `request_expires_at`: 本次认证会话的截止时间（ISO 时间），应与脚本等待超时保持一致；页面可以用它提示用户链接何时失效。
- `session_id`: 本次认证会话 ID，由脚本生成；同一待完成认证会话内保持不变。
- `win_id`: 客户端窗口 ID，固定复用 `session_id`，用于让 Teams 客户端区分并复用认证窗口。

认证页必须从框架已登录态中生成短期认证凭证，不读取、展示或打印长期 token。

POST 到 receiver 的 JSON（只回传 `state` 和 `encrypt`；token 过期时间由脚本侧默认 3 天，页面不回传 `expiresAt`，脚本仍兼容传入但页面不再发送）：

```json
{
  "state": "一次性 state",
  "encrypt": "框架生成的短期认证凭证"
}
```

## Security Rules

- receiver 只能监听 `127.0.0.1`，禁止监听 `0.0.0.0`。
- receiver 端口只能使用 `35101-35110`，路径固定为 `/token`。
- 页面只能向 receiver 回传短期认证凭证，禁止回传长期 token。
- receiver 必须校验请求 `Origin` 与落地页 Origin 一致。
- `state` 必须校验，且本次认证只接受一次成功 POST。
- receiver 必须拒绝超过 `request_expires_at` 的回传。
- token 不得写入日志或明文文件。
- receiver 超时后必须退出。
- 认证过期或未认证统一返回退出码 `4`。

## Exit Codes

| Exit Code | Meaning |
| --- | --- |
| 0 | 成功 |
| 1 | 失败 |
| 4 | 认证过期或未认证 |
