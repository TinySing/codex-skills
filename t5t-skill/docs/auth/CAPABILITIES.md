# IM Teams Auth 能力清单

本文档用于快速判断 `im-teams-auth` 能做什么、如何被其他 skill 复用，以及有哪些明确限制。完整实现流程和安全设计见 [ARCHITECTURE.md](./ARCHITECTURE.md)。

## 核心能力

| 能力 | 支持内容 | 关键限制 |
|------|----------|----------|
| 环境检测 | 检查 Python 版本、运行平台和 `keyring` 可用性 | Python 需要 `>=3.9` |
| 认证状态检查 | 检查环境变量或 OS Keyring 中是否存在有效 token | 不向调用方暴露完整 token |
| 拉起 Teams 认证 | 通过 Teams scheme 自动打开项目域名认证页（仅一次） | 认证页必须从 Teams 已登录态生成短期凭证 |
| 输出认证链接 | 输出可点的 Teams 认证链接 `appLinkUrl`（https，任何客户端可点，所有环境必有）供用户手动打开；测试环境额外给浏览器落地页 `landingUrl` | `schemeUrl`（协议链接）仅脚本内部自动拉起，多数客户端点不开，不发用户；链接是并行兜底，不代表流程必然暂停 |
| 本地凭证接收 | 启动一次性回环 HTTP receiver 接收短期认证凭证 | 只监听 `127.0.0.1`，固定端口范围和路径 |
| 会话复用 | 同一待完成认证期间复用同一个 landing URL 和 receiver | 仅当旧会话过期或 receiver 失活时才新建会话 |
| Token 兑换 | 通过 HTTPS 将短期认证凭证兑换为公网 IM token | 长期 token 不经过认证页回传给 receiver |
| 安全存储 | 首选 OS Keyring；钥匙串不可用时回退本地文件兜底（不记本地过期时间） | 文件兜底为 `chmod 600` 仅属主可读的受限文件；token 不写日志 |
| 缓存复用 | Keyring/文件里有 token 即直接复用，token 失效以服务端为准（返认证码即重认证） | 环境变量优先于 Keyring，Keyring 优先于文件 |
| 强制重新认证 | 清理当前缓存并重新执行认证 | 清理 Keyring + 本地文件兜底，不会移除环境变量 |
| 凭证清理 | 清除当前或全部已配置环境的 token（Keyring + 本地文件兜底） | 环境变量需要调用方自行移除 |
| 业务 skill 集成 | 以退出码和 JSON 状态向调用方返回结果 | 认证策略只由本 skill 定义 |

## 支持的平台与依赖

- Python `>=3.9`。
- macOS Keychain。
- Windows Credential Manager。
- 自动检测并按需安装 `keyring`。
- 除 `keyring` 外只依赖 Python 标准库。

## 标准认证流程

1. 检查已有认证状态。
2. 未认证时启动一次性本地 receiver。
3. 生成带 `state`、`receiver`、`request_expires_at`、`session_id` 和 `win_id` 的项目认证页 URL。
4. 通过 Teams scheme 自动打开认证页（仅一次），并输出可点的 `appLinkUrl` 供用户手动打开；测试环境额外附浏览器落地页 `landingUrl`。
5. 认证页回传短期认证凭证。
6. receiver 校验请求并通过 HTTPS 兑换 token。
7. token 写入 OS Keyring（钥匙串不可用则回退本地文件兜底），receiver 立即退出。
8. 调用方根据成功、失败或未认证状态继续处理。

## 安全能力

- receiver 仅监听本机回环地址。
- receiver 仅接受固定路径和受限端口范围。
- 校验认证请求的 `Origin`。
- 校验一次性随机 `state`。
- 校验与等待超时一致的认证会话时效。
- 限制请求体大小和 `Content-Type`。
- 只接收短期认证凭证，不接收长期 token。
- token 不写入日志；钥匙串不可用时仅落 `chmod 600` 仅属主可读的本地文件兜底，不写无保护明文文件。
- receiver 超时后自动退出。

## 明确不支持

- 在页面中读取、展示或回传长期 token。
- 将 token 写入无保护的明文文件（文件兜底是 `chmod 600` 仅属主可读的受限缓存）。
- 监听公网地址或任意本地端口。
- 由业务 skill 复制、修改或扩展认证策略。
- 自动移除调用进程外部设置的环境变量 token。
- 将认证兜底链接一律解释为认证流程已经暂停。

## 常见使用方式

- 检查当前是否已认证。
- 首次获取认证并缓存凭证。
- 强制重新认证。
- 清除当前认证缓存。
- 清除全部认证缓存。
- 作为 T5T 等业务 skill 的统一认证依赖。

## 相关文档

- [ARCHITECTURE.md](./ARCHITECTURE.md)：完整认证流程、组件职责、调用契约和安全约束。
- `../../SKILL.md`：代理执行规则和用户提示要求。
