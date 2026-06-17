---
name: exchange-mail-operation
description: >
  Exchange 邮件与日历操作助手，通过 EWS 协议连接自建 Exchange 服务器。
  支持邮件读取/搜索（按主题/正文/发件人）/发送（HTML 自动识别、二次确认）/标记已读（二次确认）/附件处理/通讯录搜索，
  日历日程创建（二次确认）/读取/搜索/更新（二次确认）/删除（二次确认）。
  提供浏览器交互式配置页面，凭证通过 OS Keyring 安全存储。
  所有写入操作需二次确认（--confirm），读取类操作前校验模型安全性（M1/M2 模型），支持 Exchange 2007~O365，需要 Python 3.7+。
---

# Exchange 邮件与日历操作助手

## 核心能力

- **邮件**：读取、搜索（按主题/正文/发件人）、发送（二次确认，自动识别 HTML）、标记已读（二次确认）、附件下载与文本提取（PDF/TXT/DOCX/XLSX）
- **通讯录**：搜索全局通讯录（GAL），按姓名/邮箱查找联系人
- **日历**：日程创建（二次确认）、列出、搜索（按主题/正文）、详情、更新（二次确认）、删除（二次确认）
- **配置**：浏览器交互式配置页面（唯一凭证入口），凭证存入 OS Keyring；支持切换账号（`--clear-before`）
- **调试**：`--verbose` / `--debug` 参数查看详细日志

## 安全机制

- **凭证唯一入口**：用户名和密码只能通过浏览器配置页面输入，禁止命令行/环境变量传入
- **安全存储**：凭证存入 OS Keyring（Windows 凭据管理器 / macOS 钥匙串 / Linux Secret Service），仅存用户名和密码；服务器地址/域名/版本等为代码常量
- **自动迁移**：检测到旧版明文 config.json 时，自动迁移到 Keyring 并删除明文密码
- **本地监听**：配置页面仅监听 127.0.0.1，不暴露到局域网
- **模型安全校验**：读取类操作前提醒用户确认当前模型是否为 M1/M2 合规模型（`deepbank/` 或 `360/360-` 开头），用户回复"确认"后继续执行（退出码 2）；写入类操作不校验
- **写入操作二次确认**：所有写入/修改/删除类操作（send、mark-read、create-event、update-event、delete-event）均需二次确认，首次调用仅预览（退出码 3），用户确认后追加 `--confirm` 才实际执行。**严禁 AI 助手自行添加 `--confirm`**

## 首次使用 - 配置流程

每次用户请求操作时，AI 助手应按以下流程处理：

1. **环境检测**：执行 `env_check.py` 获取 Python 路径（`python_path`），后续所有命令使用该路径
2. **检测依赖**：脚本自动检查 `exchangelib` 等依赖，缺失时自动安装
3. **检测配置**：脚本自动从 OS Keyring 读取凭证
4. **未配置**：引导用户通过浏览器配置页面输入凭证
5. **模型安全校验**：读取类操作前提醒用户确认当前模型是否为 M1/M2 合规模型（退出码 2）
6. **执行操作**：连接服务器并执行请求的操作

### 步骤零：Python 环境检测（必须首先执行）

```bash
python scripts/env_check.py
# 输出: {"status": "ok", "python_path": "C:/Users/.../python.exe", "version": "Python 3.13.12", "platform": "Windows", "cached": true/false, ...}
```

**AI 助手必须：**
1. 执行 `env_check.py`，从输出中获取 `python_path`
2. **后续所有命令都使用该 `python_path`**，不再使用裸 `python` 命令
3. 将 `python_path` 缓存到当前会话上下文中，同一会话内只需检测一次

**为什么必须这样做：**
- WorkBuddy 等沙箱平台：`python` 不在 PATH，裸命令返回非标准退出码（如 49）
- 本地终端：`python` 可用，但 `env_check.py` 会缓存路径（7天有效），后续毫秒级返回
- `env_check.py` 会自动排除 VM shim（截断中文参数的假 Python），确保路径可靠

**缓存机制：** 检测结果自动写入 `scripts/cache/python_path.txt`（7天有效期），后续执行直接读缓存，无需重新检测。

### 步骤一：环境准备

```bash
{python_path} scripts/setup.py
# 输出: {"success": true, "message": "环境配置完成 (Python 3.11.5)", ...}
```

### 步骤二：浏览器配置（唯一凭证入口）

```bash
{python_path} scripts/config_web.py
# 输出: {"success": true, "message": "配置服务器已启动", "url": "http://127.0.0.1:35xxx", "port": 35xxx}
```

AI 助手应：启动服务器（后台运行）→ 脚本自动打开浏览器（多策略回退，兼容 VM/IDE 沙箱环境；如失败则输出 URL 供手动访问）→ 等待用户输入凭证 → 配置成功后服务器自动关闭 → 继续后续操作

**端口选择**：默认从 35000~40000 随机选择可用端口，防止端口冲突；可通过 `--port` 手动指定

**关闭方式**：
- 配置成功后自动关闭（始终生效，防止服务常驻导致内存泄漏）
- `--timeout 300`：超时自动关闭

### 清除凭证

```bash
{python_path} scripts/setup.py --clear
```

### 切换账号 / 重新登录

用户主动要求切换账号或重新登录时：

```bash
{python_path} scripts/config_web.py --clear-before
```

**AI 助手识别触发词：** "切换账号"、"重新登录"、"换账号"、"登出"、"退出登录"、"用另一个账号"

**流程说明：**
1. `--clear-before` 先清除 OS Keyring 中的旧凭证
2. 打开浏览器配置页面（页面不会显示旧用户名）
3. 用户输入新账号密码
4. 配置成功后自动关闭（进程退出），新账号立即生效

**⚠️ 注意：**
- 如果用户中途关闭浏览器未完成配置，凭证已被清除，后续操作会提示需要重新配置
- 进程退出是配置成功的正常行为，**严禁 AI 助手重复启动配置服务器**

## 脚本路径

| 文件 | 用途 |
|------|------|
| `scripts/common.py` | 共享工具模块（依赖检测、模型安全校验、配置加载、连接管理、认证处理、日志配置） |
| `scripts/exchange_mail.py` | 邮件主脚本 |
| `scripts/ews_calendar.py` | 日历主脚本 |
| `scripts/setup.py` | 环境配置脚本 |
| `scripts/config_web.py` | 配置服务器（浏览器交互式模式） |
| `scripts/keyring_store.py` | Keyring 凭证存储模块（仅存用户名/密码，含固定常量 SERVER/DOMAIN/EMAIL_SUFFIX/VERSION） |
| `scripts/env_check.py` | Python 环境预检测模块 |
| `scripts/requirements.txt` | Python 依赖声明 |
| `scripts/templates/config.html` | 配置页面 HTML 模板 |

## 调用方式

**`{python_path}`** = 步骤零中 `env_check.py` 输出的 `python_path`，**严禁使用裸 `python` 命令**。

```bash
# 邮件操作
{python_path} scripts/exchange_mail.py --action <action> [options] [--verbose] [--debug] [--confirmed-risk]

# 日历操作
{python_path} scripts/ews_calendar.py <action> [options] [--verbose] [--debug] [--confirmed-risk]
```

**凭证从 OS Keyring 自动加载（仅用户名和密码），不接受 --config/--server/--email/--password 等参数。**

## 退出码

| 码 | 含义 | AI 助手处理 |
|----|------|------------|
| 0 | 成功 | 正常展示结果 |
| 1 | 操作失败 | 展示错误信息 |
| 2 | 模型安全校验待确认 | 提醒用户确认当前模型是否为 M1/M2，用户确认后追加 `--confirmed-risk` 重新执行 |
| 3 | 写入操作待确认 | 展示预览内容，用户确认后追加 `--confirm` 重新执行（适用于 send、mark-read、create-event、update-event、delete-event） |
| 4 | 凭证失效 | **脚本已自动清除 Keyring 缓存** → 重新打开浏览器配置页面 |

## 统一输出格式

所有脚本输出：成功 `{"success": true, "message": "...", ...}` / 失败 `{"success": false, "message": "错误描述", ...}`

AI 助手只需检查 `success` 字段判断成败，`message` 包含可读描述。

## 邮件操作

### 1. recent — 获取最近邮件

```bash
{python_path} scripts/exchange_mail.py --action recent [--limit 20]
```

### 2. unread — 获取未读邮件

```bash
{python_path} scripts/exchange_mail.py --action unread [--limit 50]
```

### 3. detail — 邮件详情

```bash
{python_path} scripts/exchange_mail.py --action detail --email-id <ID>
```

输出：完整正文（body）、收件人（to）、抄送（cc）、密送（bcc）、重要性（importance）等。

### 4. search — 搜索邮件

```bash
{python_path} scripts/exchange_mail.py --action search \
  [--keyword <关键词>] [--sender <发件人>] [--subject <主题>] \
  [--unread-only] [--folder inbox] [--limit 50]
```

搜索参数说明：
- **`--keyword`**：关键词同时匹配主题（subject）和正文（body），支持"找下包含灾备演练的邮件"这类需求
- **`--sender`**：按发件人筛选，支持"找下张三发给我的邮件"这类需求。**智能解析**：传入中文名时自动通过通讯录（GAL）解析为邮箱地址，同时匹配原名和邮箱（解决 Exchange sender 字段存邮箱地址而非中文名的问题）；传入邮箱地址时直接匹配
- **`--subject`**：按主题精确筛选
- 以上参数可组合使用（AND 逻辑）

### 5. folder — 获取文件夹邮件

```bash
{python_path} scripts/exchange_mail.py --action folder --folder-name <文件夹名> [--limit 50] [--unread-only]
```

可用文件夹名：inbox、sent、drafts、trash、junk

### 6. list-folders — 列出文件夹

```bash
{python_path} scripts/exchange_mail.py --action list-folders
```

### 7. search-gal — 搜索全局通讯录

```bash
{python_path} scripts/exchange_mail.py --action search-gal --keyword <姓名或邮箱>
```

通过 EWS ResolveNames 操作搜索 Exchange 全局通讯录（GAL），返回匹配联系人的姓名、邮箱和类型。此操作为读取类操作，受模型安全校验约束。

- **找到 1 位**：直接返回联系人信息，可用于后续 send 操作
- **找到多位**：返回所有匹配结果，**AI 助手必须让用户选择具体人员，严禁自行选择**
- **未找到**：返回空列表

**⚠️ AI 助手在发送邮件时，如果用户只提供了姓名而非完整邮箱地址，必须先通过 search-gal 查询通讯录获取邮箱，不能自行推断。**

### 8. send — 发送邮件（需二次确认）

```bash
# 第一步：预览邮件（不实际发送）
{python_path} scripts/exchange_mail.py --action send \
  --to "recipient@qifu.com" --subject "主题" --body "正文" \
  [--cc "cc@qifu.com"] [--bcc "bcc@qifu.com"]

# 第二步：用户确认后，追加 --confirm 实际发送
{python_path} scripts/exchange_mail.py --action send \
  --to "recipient@qifu.com" --subject "主题" --body "正文" \
  [--cc "cc@qifu.com"] [--bcc "bcc@qifu.com"] --confirm
```

`--to`/`--cc`/`--bcc` 支持逗号分隔多个地址；`--body` 支持 HTML（自动检测：含 HTML 标签时自动设置 body_type=HTML，收件人将看到渲染后的富文本；否则为纯文本）。

**⚠️ 发送邮件是严肃操作，必须经过二次确认：**
1. 首次调用不带 `--confirm`，脚本仅输出邮件预览（收件人、主题、正文摘要），退出码 3
2. AI 助手展示预览内容，请用户确认
3. 用户确认后，追加 `--confirm` 重新执行，邮件才会实际发送
4. **严禁 AI 助手自行添加 `--confirm` 参数**，必须由用户明确确认

**⚠️ `--to` 必须是完整的邮箱地址（如 `zhangsan-jk@qifu.com`），不能仅提供姓名自行推断。公司内可能存在同名人员，自行推断会导致邮件发错人。如果用户只提供了姓名，必须先通过 search-gal 查询通讯录获取完整邮箱地址。**

### 9. mark-read — 标记已读（需二次确认）

```bash
# 第一步：预览邮件信息（不实际标记）
{python_path} scripts/exchange_mail.py --action mark-read --email-id <ID>

# 第二步：用户确认后，追加 --confirm 实际标记
{python_path} scripts/exchange_mail.py --action mark-read --email-id <ID> --confirm
```

**⚠️ 标记已读是不可逆操作，必须经过二次确认：**
1. 首次调用不带 `--confirm`，脚本仅输出邮件预览（主题、发件人、时间），退出码 3
2. AI 助手展示预览内容，请用户确认
3. 用户确认后，追加 `--confirm` 重新执行，邮件才会实际标记为已读
4. **严禁 AI 助手自行添加 `--confirm` 参数**，必须由用户明确确认

### 10. attachments — 获取/下载附件

```bash
{python_path} scripts/exchange_mail.py --action attachments --email-id <ID> [--download-path <目录>]
```

不提供 `--download-path` 仅返回附件信息（名称、大小、类型）。

### 11. extract — 提取附件文本

```bash
{python_path} scripts/exchange_mail.py --action extract --email-id <ID> [--attachment-name <名称>]
```

支持格式：TXT、CSV、PDF、DOCX、XLSX（pypdf/lxml 为可选依赖，首次使用自动安装）

## 日历操作

### 12. create-event — 创建日程（需二次确认）

```bash
# 第一步：预览日程（不实际创建）
{python_path} scripts/ews_calendar.py create-event \
  --subject "会议主题" --start "2026-05-22 12:00" --end "2026-05-22 13:00" \
  [--body "描述"] [--location "地点"] \
  [--attendees "a@co.com,b@co.com"] [--optional-attendees "c@co.com"] \
  [--timezone "Asia/Shanghai"]

# 第二步：用户确认后，追加 --confirm 实际创建
{python_path} scripts/ews_calendar.py create-event \
  --subject "会议主题" --start "2026-05-22 12:00" --end "2026-05-22 13:00" \
  [--body "描述"] [--location "地点"] \
  [--attendees "a@co.com,b@co.com"] [--optional-attendees "c@co.com"] \
  [--timezone "Asia/Shanghai"] --confirm
```

`--subject`/`--start`/`--end` 必填；时间格式 YYYY-MM-DD HH:MM；时区默认 Asia/Shanghai。

**⚠️ 创建日程是严肃操作，必须经过二次确认：**
1. 首次调用不带 `--confirm`，脚本仅输出日程预览（主题、时间、地点、参会者），退出码 3
2. AI 助手展示预览内容，请用户确认
3. 用户确认后，追加 `--confirm` 重新执行，日程才会实际创建
4. **严禁 AI 助手自行添加 `--confirm` 参数**，必须由用户明确确认

### 13. list-events — 列出日程

```bash
{python_path} scripts/ews_calendar.py list-events \
  [--start "2026-05-22 00:00"] [--end "2026-05-29 00:00"] \
  [--days 7] [--limit 50] [--timezone "Asia/Shanghai"]
```

`--days` 默认 7 天（仅未指定 `--end` 时生效）。

### 14. search-events — 搜索日程

```bash
{python_path} scripts/ews_calendar.py search-events --keyword "项目" \
  [--start "2026-05-01 00:00"] [--end "2026-06-30 00:00"] \
  [--limit 50] [--timezone "Asia/Shanghai"]
```

`--keyword` 同时匹配主题（subject）和正文（body）。默认搜索范围：30 天前 ~ 90 天后。

### 15. get-event — 日程详情

```bash
{python_path} scripts/ews_calendar.py get-event --event-id <ID>
```

### 16. update-event — 更新日程（需二次确认）

```bash
# 第一步：预览变更（不实际更新）
{python_path} scripts/ews_calendar.py update-event --event-id <ID> \
  [--subject "新主题"] [--start "2026-05-22 14:00"] [--end "2026-05-22 15:00"] \
  [--body "新描述"] [--location "新地点"] [--timezone "Asia/Shanghai"]

# 第二步：用户确认后，追加 --confirm 实际更新
{python_path} scripts/ews_calendar.py update-event --event-id <ID> \
  [--subject "新主题"] [--start "2026-05-22 14:00"] [--end "2026-05-22 15:00"] \
  [--body "新描述"] [--location "新地点"] [--timezone "Asia/Shanghai"] --confirm
```

未指定任何更新字段时返回错误。传空字符串可清空 body/location。

**⚠️ 更新日程是严肃操作，必须经过二次确认：**
1. 首次调用不带 `--confirm`，脚本输出当前日程信息和变更对比（原值→新值），退出码 3
2. AI 助手展示变更对比，请用户确认
3. 用户确认后，追加 `--confirm` 重新执行，日程才会实际更新
4. **严禁 AI 助手自行添加 `--confirm` 参数**，必须由用户明确确认

### 17. delete-event — 删除日程（需二次确认）

```bash
# 第一步：预览日程（不实际删除）
{python_path} scripts/ews_calendar.py delete-event --event-id <ID>

# 第二步：用户确认后，追加 --confirm 实际删除
{python_path} scripts/ews_calendar.py delete-event --event-id <ID> --confirm
```

**⚠️ 删除日程是严肃操作，必须经过二次确认：**
1. 首次调用不带 `--confirm`，脚本仅输出日程预览（主题、时间、地点），退出码 3
2. AI 助手展示预览内容，请用户确认
3. 用户确认后，追加 `--confirm` 重新执行，日程才会实际删除
4. **严禁 AI 助手自行添加 `--confirm` 参数**，必须由用户明确确认

## 交互式配置

### 18. config-web — 启动配置页面

```bash
{python_path} scripts/config_web.py [--port 0] [--timeout 300]
```

AI 助手：后台启动 → 脚本自动打开浏览器（失败时输出 URL 供手动访问）→ 等待用户输入 → 配置成功后自动关闭 → 继续操作

**⚠️ 关键行为说明：**
- `config_web.py` 配置成功后**会自动关闭并退出进程**，这是正常行为，不是失败
- **严禁 AI 助手在进程退出后重复启动配置服务器**——进程退出意味着配置已完成
- 进程退出后，AI 助手应直接继续原操作（如重新执行邮件/日历操作）
- 只有在用户明确表示"配置失败"或"没有完成配置"时，才重新启动配置服务器
- **判断配置是否成功的唯一标准**：脚本最后一行输出 `"config_completed": true` 且退出码 0 = 成功；`"config_completed": false` = 超时未完成；沙箱路径拦截警告不影响判断

### 19. setup — 环境准备

```bash
{python_path} scripts/setup.py
```

### 20. clear — 清除凭证

```bash
{python_path} scripts/setup.py --clear
```

## 结果处理规范

- **邮件列表**（recent/unread/search/folder）：精简摘要格式，显示主题、发件人、时间、未读状态、收件人（最多 3 个+人数）、正文预览（80 字符）。如需完整收件人列表或正文，请用 detail 操作
- **邮件详情**（detail）：展示完整信息，包括完整 to/cc/bcc、正文、重要性等；正文过长时截断（前 2000 字符）并提示
- **日程列表**：整理成易读列表，显示主题、时间、地点、组织者
- **操作结果**：检查 `success` 字段，明确告知成功或失败
- **模型安全提醒**：退出码 2 时提醒用户确认当前模型是否为 M1/M2，用户确认后追加 `--confirmed-risk` 重新执行
- **写入操作确认**：退出码 3 时展示预览内容（邮件/日程信息、变更对比），用户确认后追加 `--confirm` 重新执行
- **通讯录搜索**：search-gal 找到多人时，必须让用户选择具体人员，严禁自行选择
- **凭证失效**：退出码 4 时自动重新打开浏览器配置页面
- **切换账号**：用户主动要求"切换账号"/"重新登录"时，执行 `config_web.py --clear-before` → 清除旧凭证 → 打开浏览器 → 输入新凭证 → 继续原操作

## 错误处理

| 场景 | 处理 |
|------|------|
| Python 不满足 | 脚本输出安装指引，AI 助手引导安装 Python 3.7+ |
| 依赖缺失 | 脚本自动安装；失败时提示手动 pip install |
| 可选依赖缺失 | extract 操作时自动安装；失败时提示不支持的格式 |
| Keyring 无配置 | 启动浏览器配置页面 |
| 认证失败（退出码 4） | 脚本已自动清除 Keyring；AI 助手重新打开配置页面 |
| 连接超时 | 检查网络；使用 `--debug` 查看详细日志 |
| 邮件/日程未找到 | 告知 ID 可能已失效 |

## 依赖管理

### 核心依赖（自动安装）

| 包 | 用途 |
|----|------|
| exchangelib | EWS 协议通信 |
| keyring | OS Keyring 凭证存储 |
| backports.zoneinfo | Python < 3.9 时区支持 |

### 可选依赖（extract 时自动安装）

| 包 | 用途 |
|----|------|
| pypdf | PDF 文本提取 |
| lxml | DOCX/XLSX 文本提取 |

一键安装：`pip install -r scripts/requirements.txt`

## 连接优化

配置了服务器地址时自动关闭 Autodiscover（直连 2-5 秒），避免超时（30+ 秒）；无服务器地址时启用 Autodiscover（O365 场景）。

## 示例交互

**首次使用**：用户"查看邮件" → `env_check.py`（获取 python_path）→ 检测无配置 → `setup.py` → `config_web.py` → 浏览器输入凭证 → `config_completed: true` → 自动关闭 → 执行操作

**已配置查看邮件**：用户"最近邮件" → `env_check.py`（获取 python_path，缓存命中时毫秒级）→ `exchange_mail.py --action recent` → 整理展示

**模型安全校验**：退出码 2 → 提醒确认当前模型是否为 M1/M2 → 用户确认后追加 `--confirmed-risk` 重新执行

**凭证失效**：退出码 4 → 脚本自动清除 Keyring → 重新打开配置页面 → 用户输入新凭证 → 重新执行

**搜索邮件**：用户"搜索项目计划邮件" → `env_check.py` → `exchange_mail.py --action search --keyword "项目计划"`

**搜索通讯录**：用户"给王云飞发邮件" → `env_check.py` → `exchange_mail.py --action search-gal --keyword "王云飞"` → 找到邮箱 → 发送邮件

**发送邮件**：用户"给 xxx@qifu.com 发测试邮件" → `env_check.py` → `exchange_mail.py --action send --to "xxx@qifu.com" --subject "测试" --body "内容"` → 退出码 3（预览）→ 用户确认 → 追加 `--confirm` 实际发送

**创建日程**：用户"明天下午2点会议" → `env_check.py` → 计算时间 → `ews_calendar.py create-event --subject "会议" --start "2026-05-22 14:00" --end "2026-05-22 15:00"` → 退出码 3（预览）→ 用户确认 → 追加 `--confirm` 实际创建

**更新日程**：用户"会议改到3点" → `env_check.py` → 找到 ID → `ews_calendar.py update-event --event-id <ID> --start "2026-05-22 15:00" --end "2026-05-22 16:00"` → 退出码 3（变更对比）→ 用户确认 → 追加 `--confirm` 实际更新

**删除日程**：用户"取消会议" → `env_check.py` → 找到 ID → `ews_calendar.py delete-event --event-id <ID>` → 退出码 3（预览）→ 用户确认 → 追加 `--confirm` 实际删除

**调试**：用户"连接失败" → `env_check.py` → `exchange_mail.py --action recent --debug` → 查看 DEBUG 日志

**切换账号**：用户"我要切换账号" → `env_check.py` → `config_web.py --clear-before` → 清除旧凭证 → 打开浏览器 → 输入新凭证 → 自动关闭 → 继续操作

## 注意事项

1. 不直接暴露原始 JSON，整理成易读格式后展示
2. 邮件/日程 ID 较长，展示时可截断
3. 邮件列表为精简摘要（to 最多 3 人、body_preview 80 字符），如需完整信息请用 detail 操作；详情正文过长时截断前 2000 字符并提示
4. 大附件下载可能耗时，提前告知
5. 首次使用必须先检测配置，未配置时引导浏览器配置
6. 发送邮件时 `--to` 必须是完整邮箱地址，不能仅凭姓名推断（可能重名）。用户只提供姓名时，须先通过 search-gal 查询通讯录获取邮箱地址；search-gal 找到多人时，必须让用户选择具体人员
7. 所有写入操作必须经过二次确认：首次不带 `--confirm` 仅预览（退出码 3），用户确认后追加 `--confirm` 实际执行。适用于 send、mark-read、create-event、update-event、delete-event。**严禁 AI 助手自行添加 `--confirm`**
8. 支持 Exchange2007SP1~O365，默认 Exchange2010SP2
9. Python 3.7+，3.9 以下自动安装 backports.zoneinfo。**所有脚本命令必须使用步骤零获取的 `{python_path}`，严禁使用裸 `python`**
10. 统一输出格式：`{"success": true/false, "message": "..."}`
11. 可选依赖 pypdf/lxml 安装失败时提示手动安装
12. 配置了服务器地址时自动关闭 Autodiscover 加速连接
13. 配置服务器默认随机端口（35000~40000），配置成功后自动关闭（进程退出）；脚本自动打开浏览器（多策略回退），失败时输出 URL 供手动访问；**进程退出是配置成功的正常行为，严禁 AI 助手重复启动配置服务器**
14. 脚本自动确保 stdout/stderr UTF-8 编码，防止 Windows/VM 环境下中文输出乱码
15. 模型安全校验：读取类操作前提醒用户确认当前模型是否为 M1/M2 合规模型（`deepbank/` 或 `'360/360-'` 开头）；用户确认后 AI 助手追加 `--confirmed-risk` 参数重新执行
