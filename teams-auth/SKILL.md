---
name: teams-auth
description: >
  360Teams 统一认证技能。
  触发词：
  "Teams认证"、"Teams登录"、"360Teams登录"、
  "Teams认证过期"、"Teams凭证失效"、
  以及任何需要登录/重新认证 360Teams 平台的场景。

  当其他 Teams 技能返回退出码 4（认证过期）时，
  必须调用本技能重新认证。

  排除：
  飞书、钉钉、微信等非 360Teams 平台认证场景。

compatibility:
  python: ">=3.9"
  platforms: [ claude-code, trae, codex, claude-ai ]
  dependencies: >
    自动安装 keyring（若缺失）。
    其余仅依赖 Python 标准库。
---

# 360Teams Auth

360Teams 平台统一认证技能。

负责：

- 浏览器 SSO 登录
- 网关 token 获取与缓存
- 认证状态恢复
- 为其他 Teams 技能提供统一凭证

# Commands

## 环境检测

```bash
python scripts/env_check.py
```

## 登录认证

```bash
python scripts/auth.py
```

## 检查认证状态

仅检查当前是否已认证，不触发浏览器登录。

```bash
python scripts/auth.py --check
```

- 已认证 → 退出码 0，输出 `{"status": "ok", "authenticated": true, "expires_at": "..."}`
- 未认证/过期 → 退出码 4，输出 `{"status": "expired", "authenticated": false, "message": "..."}`

## 强制重新认证

用于：

* 凭证过期
* 账号切换
* token 失效

```bash
python scripts/auth.py --no-cache
```

# Authentication Storage

凭证仅存储于 OS Keyring。

不写入本地明文文件。

支持：

* Windows Credential Manager
* macOS Keychain
* Linux Secret Service / keyring backend

其他技能读取方式：

```python
import keyring

token = keyring.get_password(
    "360teams",
    "gateway_token"
)
```

也支持环境变量：

```bash
TEAMS_GATEWAY_TOKEN
```

环境变量优先级高于 Keyring。

# Workflow

## 标准流程

1. 执行 `python scripts/env_check.py`
2. 检查 Python 与 keyring 是否可用
3. 执行 `python scripts/auth.py --check` 检查认证状态
4. 如已认证 → 直接使用
5. 如未认证 → 执行 `python scripts/auth.py`
6. 浏览器完成 SSO 登录
7. token 写入 OS Keyring
8. 其他 Teams 技能读取 token

## 认证过期流程

当：

* 用户明确说“认证过期”
* 用户明确说“凭证失效”
* 其他技能返回退出码 4

必须执行：

```bash
python scripts/auth.py --no-cache
```

重新认证成功后：

* 必须重新执行原操作

# Agent Integration Rules

## 必须遵守

* 直接用 `python` / `python3` 调用 `.py` 脚本（`run.bat` / `run.sh` 作为可选辅助，非必须）
* 认证过期时必须使用 `--no-cache`
* 重新认证成功后必须重试原操作
* 不要要求用户在聊天中输入密码
* 凭证只能存储于 OS Keyring
* 同一时间仅允许一个 auth 实例运行

## 浏览器行为

认证时会自动打开默认浏览器。

如果浏览器无法自动打开：

* 脚本会输出登录链接
* 用户手动访问即可

本地 SSO 回调端口：

```text
35001-35010
```

# Exit Codes

| Exit Code | Meaning              |
|-----------|----------------------|
| 0         | 成功                   |
| 1         | 失败                   |
| 4         | 认证过期（供其他 Teams 技能使用） |


# Output Examples

## 缓存命中

```json
{
  "status": "ok",
  "message": "Already authenticated (using cached token)."
}
```

## 新登录成功

```json
{
  "status": "ok",
  "message": "Login successful, gateway token cached."
}
```

## 检查认证状态（已认证）

```json
{
  "status": "ok",
  "authenticated": true,
  "expires_at": "2026-06-08T14:30:00"
}
```

## 检查认证状态（已过期）

```json
{
  "status": "expired",
  "authenticated": false,
  "message": "认证已过期或未认证，需重新登录"
}
```

## Python 不可用

```json
{
  "status": "error",
  "message": "未找到可用的 Python 3.9+ 环境",
  "platform": "Windows"
}
```

# Error Handling

| Scenario    | Handling       |
|-------------|----------------|
| Python 未安装  | 输出安装指引并停止      |
| keyring 不可用 | 自动尝试安装 keyring |
| 浏览器无法打开     | 输出登录链接         |
| SSO 回调超时    | 提示用户重试         |
| 用户取消登录      | 不视为失败          |


# Retry Rules

* 认证失败最多重试 3 次
* 用户主动取消登录不计入失败
* 认证恢复后必须重新执行原操作

# Examples

## 首次登录

用户：

```
帮我登录 360Teams
```

执行：

```
python scripts/env_check.py -> python scripts/auth.py -> 浏览器登录 -> 成功
```

## Teams 认证过期

用户：

```
Teams 登录过期了
```

执行：

```
python scripts/auth.py --no-cache
```

## 其他技能认证失效

其他技能返回：

```
exit code 4
```

执行：

```
python scripts/auth.py --no-cache
```

成功后：

```
重新执行原操作
```