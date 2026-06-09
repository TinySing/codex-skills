# UI Skill 固定输出模板

按需使用，不要机械复制。若用户未指定设计风格，先执行风格选择提问，再生成文件。

## 0. 风格选择提问

```text
请选择 UI 风格方向，只需要回复一个选项：
1. 自由发挥：我按产品类型和用户场景直接收敛风格，并在文档中记录为“风格决策依据”。
2. 从内置内容风格中选择：可选 `apple-productivity`、`google-adaptive`、`fluent-enterprise`、`editorial-minimal`、`social-warm`、`premium-quiet`、`playful-modern`、`future-light`、`messaging-focus`、`design-forward`。如果你需要，我可以先推荐 1 到 2 个更适合当前产品的方向。
3. 从 getdesign.md 选择设计系统：请先浏览 https://getdesign.md ，如果你看中了某个设计系统，只需要告诉我设计系统名称，例如 `Linear`、`Raycast`、`Claude`、`Stripe`。我会优先去 getdesign.md，必要时再去 https://github.com/VoltAgent/awesome-design-md 找对应的 `DESIGN.md`，把它作为风格提示词来源。
```

## 1. `ui-blueprint.md`

```markdown
# UI Blueprint

## 1. 输入摘要

- 原始输入：
- 产品类型：
- 目标用户：
- 核心任务：
- 端形态：
- 已选设计风格：

## 2. 规划前提与边界

- 本轮覆盖范围：
- 明确不做：
- 已采纳决策：
- 本稿采用前提：

## 3. Screen Map

| 页面 / 视图 | 页面目标 | 进入方式 | 主要动作 | 关键状态 |
|---|---|---|---|---|

## 4. User Flows

### 4.1 主任务流程

1. 入口
2. 用户动作
3. 系统反馈
4. 成功结果
5. 失败恢复

### 4.2 次级流程

### 4.3 异常流程

## 5. UI Inventory

### 5.1 页面骨架级

### 5.2 交互模式级

### 5.3 基础信息单元级

### 5.4 状态反馈级

## 6. Page-by-Page Wireframe Notes

### 6.x 页面名

- 顶部区：
- 主内容区：
- 操作区：
- 空态：
- 错误态：
- 禁用 / 权限态：

## 7. 状态覆盖

- Loading：
- Empty：
- Error：
- Success：
- Disabled：
- Permission denied：

## 8. 视觉与落地约束

- 风格方向：
- 信息密度：
- 布局节奏：
- 状态表达：
- 可访问性底线：
- 样式 token 落地建议：

## 9. 交付固定约束
```

## 2. `ui-design-spec.md`

```markdown
# UI Design Spec

## 1. 风格基线

- Style key：
- 风格关键词：
- 风格来源：用户指定 / 用户要求推荐后选择 / 用户授权自由发挥 / getdesign.md / awesome-design-md
- 适用原因：
- 不适用部分：
- 折中策略：

## 2. 产品类型设计推理

- 产品类型判断：
- 推荐布局模式：
- 信息密度判断：
- 信任感 / 效率感 / 情绪感权重：
- 色彩情绪判断：
- 字体气质判断：
- 反馈强弱判断：
- 需要避免的视觉误导：

## 3. Design Principles

## 4. Design Tokens

### 4.1 Semantic Color Tokens

| Token | 用途 | Light | Dark 预留 |
|---|---|---|---|
| @color-primary | 主按钮、当前导航、链接 |  |  |
| @color-text-primary | 主文本 |  |  |
| @color-text-secondary | 次级文本 |  |  |
| @color-bg-page | 页面背景 |  |  |
| @color-bg-surface | 面板 / 卡片背景 |  |  |
| @color-border | 分割线 / 边框 |  |  |
| @color-success | 成功状态 |  |  |
| @color-warning | 警告状态 |  |  |
| @color-danger | 错误 / 危险状态 |  |  |

### 4.2 Typography Tokens

### 4.3 Spacing / Radius / Border / Shadow Tokens

### 4.4 Z-index / Motion Tokens（如需要）

## 5. 主题扩展规则

- 建议文件：`src/styles/variables.less`
- 全局入口：`src/styles/index.less` 或项目现有等价入口
- 主题映射：`src/styles/theme.less`
- 禁止业务组件硬编码颜色、字号、阴影和状态色
- 后续主题切换只改 token，不改业务组件结构

## 6. 信息密度规则

## 7. 核心组件视觉规则

## 8. 状态视觉规则

## 9. 可访问性底线

## 10. 反模式清单

## 11. 可选实现映射建议
```

## 3. `low-fi-wireframes.html`

要求：

- 只做低保真结构预览
- 每页有清晰标题、区域、状态说明
- 覆盖主路径和关键异常态
- 不做高保真视觉定稿
- 使用稳定尺寸和响应式约束，避免文字溢出

## 4. `ui-frontend-handoff.md`

```markdown
# UI Frontend Handoff

## 1. 交付目标

## 2. 页面实现顺序

## 3. 页面与 UI 单元映射

| 页面 | 复用 UI 单元 | 特有 UI 单元 | 状态要求 |
|---|---|---|---|

## 4. 页面跳转与关闭返回规则

## 5. 关键交互规则

## 6. 视觉不变量

## 7. 状态覆盖要求

## 8. 样式 token 与主题落地

## 9. 实现弹性空间
```
