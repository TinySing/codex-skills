# UI Skill 调用示例

```text
使用 $ui-skill 读取 /path/to/prd.md 并输出结果。
```

```text
使用 $ui-skill 读取 /path/to/prd.md，并先按三选一方式询问我风格方向：自由发挥、内置风格、或 getdesign.md 设计系统。
```

```text
使用 $ui-skill 读取 /path/to/prd.md 并输出结果，风格采用 apple-productivity。
```

```text
使用 $ui-skill 读取 /path/to/prd.md，先列出全部内置风格，并推荐 1 到 2 个适合 B2B 工作台的方向给我选。
```

```text
使用 $ui-skill 读取 /path/to/prd.md，风格选择第 3 种，我想用 Linear，只需要你自己去 getdesign.md 或 awesome-design-md 找对应 DESIGN.md。
```

```text
使用 $ui-skill，读取 doc/prd.md，输出 ui-blueprint.md、ui-design-spec.md、low-fi-wireframes.html 和 ui-frontend-handoff.md，但不要直接写前端代码。
```

```text
使用 $ui-skill，基于这句需求“做一个给销售团队用的客户跟进后台”，先收敛最小 P0 范围，再输出 4 份标准文档。
```

```text
使用 $ui-skill，先只做结构拆解，不做高保真视觉。基于 PRD 输出 screen map、关键 user flows、UI 单元清单和逐页 low-fi 页面线框。
```

```text
使用 $ui-skill，基于这个 PRD 输出给前端角色用的页面实现顺序、状态检查点和交互约束。
```

```text
使用 $ui-skill，先去项目目录里找现有的 UI 蓝图、handoff 和页面说明，判断哪些还能复用，哪些需要重做。
```

```text
使用 $ui-skill，帮我优化这份现有 UI handoff，补齐空态、错误态和权限态，并收敛重复组件：/absolute/path/ui-frontend-handoff.md
```

```text
使用 $ui-skill，基于现有 PRD 自由规划，优先规避页面结构上的不确定性，再输出可交付的 UI 方案。
```
