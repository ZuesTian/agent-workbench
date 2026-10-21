# Agent 工坊 · UI 审阅记录

## 0.4.10 真实环境故障与宽屏修复

0.4.9 的真实验收发现 AppData 合并视图触发路径误判，异常清理未释放会话，取消后无法续发；2292px 下主聊天区被挤到第二行。0.4.10 实测记录目录的读写物理位置并保留路径防护，补齐异常退出、取消信号和占用释放。宽屏三列 / 装配台四列布局覆盖 1749、1750、1920、2292、2560px；同时保留 1440、1024、390px 回归。真实客户端调用与隔离协议测试分开记录于 `releases/0.4.10/verification/README.md`。

## 0.4.9 会话与发布修复（2026-10-21）

补齐会话成员权限编辑与 Cursor 目录信任，修复中文输入法误发、切页草稿丢失及窄屏按钮文字换行。端到端用例检查两轮上下文交接、忙碌拒绝后重试、取消后继续、切换会话和刷新后的独立草稿；浏览器宽度覆盖 1440 / 1024 / 390。桌面窗口识别兼容页面切换后的真实标题。最新发布包、测试结果和桌面入口验证见 `releases/0.4.9/verification/README.md`。

以下为历史审阅记录，测试计数、运行地址和旧发布边界均对应当时版本。

## 多 Agent 会话（2026-10-21，本轮）

新增「会话」页：多个本机 Agent 在同一段对话里依次回答，复用执行记录的真实调用链路。

- **页头**：会话名 + 工作区路径 + 成员头像组 + 本轮进度（正在回复 / 已完成）+ 任务列表入口。
- **消息流**：用户消息右对齐深色气泡；成员回复左对齐，带头像、名称、状态、耗时、时间与「查看执行日志」。
- **输入条**：底部悬浮卡片，Enter 发送、Shift+Enter 换行；运行中显示「取消本轮」。
- **本轮成员**：输入条上方成员胶囊默认全选；点掉某个成员后这一轮只让其余成员回答（至少保留一个）。
- **对象列**：会话列表（新建 + 历史），显示预览与消息数；运行中的会话带状态点。
- **工作区**：创建/编辑会话时选择目录；所有成员在同一目录执行，`runs/` 里保留每次调用日志。
- **任务列表**：会话页右上角弹窗，按顺序列出每次回复对应的客户端调用、状态、耗时与日志入口。
- **后端**：`conversations.py` 串行执行成员回复（复用 `ExecutionManager` 的 reserve/start/get），消息与成员持久化在 `conversations/`；中断恢复把未完成回复标为「已中断」。

实测：同一会话中 Codex CLI（65.0s）与 DeepSeek Harness（13.0s）依次返回并落库；`pytest` 189 项（含 11 项会话测试）与 `node --test` 29 项通过；浏览器 0 console 错误。截图：`.test-artifacts/ui-review/chat/`。

打磨：资源卡片图标按 Skill / MCP / 角色上色；装配台打开时（≤1500px）收起对象列给主区让位；会话消息改用短时间与任务列表弹窗；成员选择框加高可滚动。

## Marvis 界面重排（2026-10-21，本轮）

按腾讯 Marvis 9 月新版界面（团队管家 / 电脑管家 / 文件管家）重排为三栏布局，参考截图保存在 `.test-artifacts/marvis-ref/`。

- **壳层**：`216px 侧栏 / 244px 对象列 / 主区` 三栏（装配台打开时加 336px 第四栏）；≤1350 收窄，≤1180 收起对象列，≤820 只留图标导航，≤580 图标栏 56px。
- **对象列**：按页面提供 Agent 列表、资源来源、角色来源、协作方案、执行记录、协作执行记录、装配方案、备份记录、设置分区；点击即切换主区内容，不再全部堆在主区。
- **主区**：统一 Marvis 式页头（彩色头像 + 标题 + @handle + 操作）；总览改为统计 + 绿色渐变 Hero + 快捷入口能力卡；选中 Agent 时显示该客户端详情页（统计行、简介、Skill/MCP/配置/执行/协作/装配能力卡）。
- **色板**：暖白 `#f4f1ec` 画布、`#fbf9f5` 侧栏、大圆角 20–26px、胶囊按钮、绿色 `#2fb765` 点缀与渐变 Hero；深色主题改为暖中性（`#171613` / `#221f1b`）。
- **图标**：导航与对象列改为彩色底小图标（每页一个色调），Agent 头像用品牌色浅底 + 首字母。
- **交互**：执行 / 协作执行记录通过 `awb:execution-rows`、`awb:workflow-rows` 事件同步到对象列；点击对象列记录直接在主区打开；设置分区点击滚动定位；导航回总览会清除选中的 Agent；装配台以圆角卡片呈现。

验证：23 张截图覆盖 10 页 × 1600/1280/1024/820/390 × 浅色/深色 + 装配台/Agent 详情/深色详情（`.test-artifacts/ui-review/marvis/`）；浏览器断言 6/6 通过、0 console 错误、四档宽度无横向溢出；`pytest` 178 项、`node --test` 29 项通过；`prettier --check` 通过。

边界：`dist/` 与根目录 ZIP 仍是旧静态文件，桌面快捷方式指向 `releases/0.4.6/`；从源码运行 `python launcher.py` 或重新打包后才能看到新界面。

日期：2026-10-21
审阅对象：本机运行的 0.4.8 实例（`127.0.0.1:47831`，源码 `agent_workbench/static/`），真实数据：9 个 Agent、411 个 Skill 名称、60 个 MCP、20 个角色、8 条执行记录、1 条协作执行记录。
方式：只读审阅。Playwright + Edge 无头，10 个页面 × 7 档宽度（1600 / 1440 / 1280 / 1100 / 1024 / 768 / 390），浅色 / 深色，交互态（装配台、资源详情、变更预览、空搜索、列表视图、移动端抽屉），计算样式实测栅格、溢出、字号、对比度、点击目标与焦点。未触发任何写入、备份恢复、任务执行或重新扫描。
证据：`.test-artifacts/ui-review/`（`run1/`、`run2/` 截图；`measure.json` 断点度量；`run2/audit.json` 对比度与目标尺寸；`capture*.py` / `measure.py` / `fix_preview.py` 可复跑）。并行审阅明细见 `findings-a11y.md`、`findings-flow.md` 及 `a11y/`、`flow/`。

## 总体判断

视觉语言统一：浅灰画布 + 白色圆角卡片、克制的黑灰文字、充足留白，深浅两套主题都成立；状态与边界文案诚实（“不启动 MCP”“启用状态未核验”“已完成不代表业务验收通过”）；无脚本异常、无横向溢出、无外部 CDN 依赖，窄屏收成图标栏。

问题不在审美，而在四个层面：**一个 CSS 语法错误让全站密度退化到接近移动端布局**；**焦点管理缺失**；**小字对比度与深色禁用态**；以及**同名不同源、同词不同义的状态语义**。

## 修复记录（2026-10-21，本轮已处理）

| # | 修复 | 文件 | 验证 |
| --- | --- | --- | --- |
| 1 | 删除泄漏的 `}`，2024–2175 行重新回到 `@media (max-width: 580px)` 内 | `style.css:2023` | 括号平衡归零；1440 下总览 3 列、资源 3 列，1024 下 2 列，390 下 1 列；页高 2810→1417（总览）、5120→2071（Skill 库）；`Ctrl K` 提示与装配台按钮尺寸恢复 |
| 2 | 切页后把焦点移入新视图，并同步页面标题 | `app.js` `switchTab()`、`views.js` `renderChrome()` | 键盘切页后 `activeElement=#main-content`，`document.title` 变为「执行任务 · Agent 工坊」，下一次 Tab 直接进入页面内容而不是导航 |
| 3 | 对话框关闭后焦点回到触发元素；触发元素已重渲染时回退到主内容区 | `ui.js` `showModal()` / `run()` | Esc 与“×”关闭后焦点均回到原“详情”按钮（`data-detail`）；`run()` 在禁用控件前记录 opener，避免禁用导致 `activeElement` 变成 body |
| 4 | 画布焦点环改内阴影，不再被 `overflow:hidden` 裁掉 | `collaboration.css` `.team-canvas:focus-visible` | 键盘 Tab 到画布时 `box-shadow: inset 0 0 0 3px` 可见（截图 `verify/05-teams-canvas-focus.png.png`） |

验证：浏览器断言 14/14 通过（`verify/verify.json`，1440/1024/768/390 无横向溢出、0 console 错误）；`pytest` 178 项通过；`node --test` 29 项通过；`prettier --check` 通过。修改前备份在 `.test-artifacts/ui-review/backup-v048/`。

注意：修复只落在**源码** `agent_workbench/static/`。`dist/` 与根目录 ZIP 仍是 13:31 打包的旧静态文件，桌面快捷方式当前指向 `releases/0.4.6/` 的旧程序；要看到新界面需从源码运行 `python launcher.py`，或重新执行 `build_windows.py` 并更新发布物。

## P1

### 1. CSS 花括号泄漏，全站栅格退化（已修复）

`agent_workbench/static/style.css:2023` 有一个多余的 `}`，提前闭合了 `@media (max-width: 580px)`；第 2024–2175 行（本应只在小屏生效）泄漏为全局规则，文件末尾因此多一个 `}`（全文件括号净差 −1，`ui-review/brace_check.py` 实测）。

浏览器 CSSOM 实测（1600px 宽、总览页）：`.agent-grid` 的 `grid-template-columns` 计算值为 `1296px`（1 列），而样式表默认声明是 `repeat(3, minmax(0, 1fr))`。同一条泄漏规则把 `.resource-grid` 压成单列，让 `.search kbd` 在任何宽度都不显示、`#composer-trigger` 长期使用小屏尺寸（`min-height: 35px; padding: 6px 9px; font-size: 12px`），`.stat` / `.page-heading h1` / `.eyebrow` 等也用了小屏字号。

| 位置（1440 宽） | 现状 | 删除该 `}` 后（注入覆盖实测） |
| --- | --- | --- |
| 总览“我的 Agents” | 9 张卡片单列，页高 2810px | 3 列，页高 1417px（−50%） |
| Skill 库（24 项/页） | 单列，页高 5120px，一屏约 5 项 | 3 列，页高 2071px，一屏约 15 项 |
| 资源卡片宽度 | 1136px | 368px |

**修复**：删除 `style.css:2023` 的 `}`（或把 2024–2175 行重新包回媒体查询），然后逐档回归：1600/1440 → 3 列；≤1350 → 2 列；≤1100 → 抽屉装配台；≤580 → 1 列。修复预览：`run2/fix-preview-skills-fixed.png`、`run2/fix-preview-overview-fixed.png`。

这是本次投入产出比最高的修复：一行代码，换回全站信息密度。

### 2. 切页、关弹窗后焦点丢回 `body`，页面变化无播报（已修复）

键盘在导航按钮按 Enter 切页后 `activeElement === body`，再 Tab **17 次**才进入 `#main-content`；详情弹窗 Esc 关闭后焦点同样回 body，而触发按钮仍在 DOM 中。根因：`app.js:95-102 switchTab()` → `views.js:54-63 renderChrome()` 用 `innerHTML` 重建导航；`ui.js:60-68 showModal()` 不记录/恢复焦点。另：`document.title` 在 4 个视图恒为「Agent 工坊 · 资源与协作」，切页对读屏零播报。

**修复**：`switchTab()` 末尾 `byId('main-content').focus({preventScroll:true})`（已有 `tabindex="-1"`）并同步 `document.title = names[tab] + ' · Agent 工坊'`；`showModal()` 保存 `document.activeElement`，在 `modal` 的 `close` 事件里恢复。

## P2

| # | 问题 | 证据 | 影响 / 建议 |
| --- | --- | --- | --- |
| 1 | **浅色主题小字对比度不达标**：`--muted #737378` 在 `--canvas/#f6f6f7`、`--selected` 上实测 4.07–4.37:1；每页 20–62 处 <12px 文本低于 4.5 | `run2/audit.json`；`style.css:4,10` | 状态标签、路径、说明字偏浅。把 `--muted` 压到 `#5f5f66` 一带，或仅对正文态加深 |
| 2 | **顶栏「运行中 N」重复计数且同词异义**：一次协作显示“运行中 2”（`service.py:134-140` 把 runs + workflows 相加，`scheduling.py:299` 每步又建一条 run）；无任务时总览 5 张卡片仍标“运行中”（`state.js:55` 指进程） | `run1/04-workflows.png`、`run1/03-execution.png`、`flow/topbar.json` | 任务数不可信。改“后台任务 N”、工作流只计 1；卡片改“进程运行中” |
| 3 | **自动调度左右同名不同源**：左侧「产品研发组」是未保存画布快照（全“未绑定/请返回画布填写总目标”），右侧同名历史记录却“运行中/已完成”且已绑定客户端 | `workflows.js:47-56,170-173`；`run1/04-workflows.png` | 误以为记录对应当前画布。历史行标“历史快照”，左侧标“当前画布” |
| 4 | **执行记录筛选后详情不跟随**：搜索无匹配时列表说“没有符合条件的记录”，右侧仍显示被筛掉的完整记录 | `execution.js:286-292`；`flow/15-execution-filter-mismatch.png` | 可能下载错结果。选中项不在结果集时清空详情或标注 |
| 5 | **顶栏会话菜单跨页不关闭并遮挡**：打开后切页、点空白、Esc 都保持展开，遮住搜索框与团队页「我的方案/保存/新建」，“完全退出”在遮挡区内 | `flow/probe2.json`、`flow/21-session-menu-after-nav.png` | 每页多一次点击、易误触。`switchTab`/外部点击/Esc 关闭 |
| 6 | **「返回画布」回错视图、「流程预演」无可见反馈**：先切“流程动态”再返回仍停在流程动态；收起“节点设置”后点“流程预演”无变化；390 宽点击后面板在视口外不滚动 | `workflows.js:54`、`team-model.js:341`、`collaboration.css:1429-1431`；`flow/31-33` | 按钮像失效。返回强制 canvas；预览前展开面板；窄屏滚动到位 |
| 7 | **画布键盘焦点环被裁剪**：Tab 到 `#team-canvas` 时 outline 为 3px `--focus`，但被 `.graph-pane{overflow:hidden}` 完全裁掉，截图无任何焦点环 | `a11y/probe3.json`、`a11y/46-canvas-focus-keyboard.png` | 键盘用户不敢进入唯一支持 `+/-/0` 的自定义控件。改 `box-shadow: inset 0 0 0 3px var(--focus)` |
| 8 | **深色禁用主按钮对比度 3.18:1**：`#workflow-start` disabled + `opacity:.52`；分页禁用态 2.73:1；禁用原因只在旁侧 `role=status`，按钮无 `aria-describedby` | `a11y/34-dark-disabled-primary.png`、`style.css:72-75` | 低视力读不出按钮文字。opacity 提到 .7，或 `aria-disabled` + 关联原因 |
| 9 | **协作编排页没有 h1**：标题序列只有 H3「Skills 0」「MCP 0」，主标题「产品研发组」是 `team-view.js:13` 的 div 内输入框 | `a11y/probe5.json` | 读屏按标题导航找不到页面主题。方案名改 h1 |
| 10 | **1024 宽协作页画布与设置面板贴边、设置内容被状态栏盖住**：pane（518px）与 inspector（280px）无间距，画布文字贴边裁切；`.node-editor` 高 1149px 溢出 inspector 的 642px，底部状态栏压住“职责与交付要求”输入框 | `run2/43-1024-teams.png`、`run2/teams-1024-crop.png`、`ui-review/probe_teams.py` | 中等窗口下设置面板末尾字段不可用。inspector 内部滚动或给 pane/inspector 之间加分隔 |

## P3

- **9–11px 小字过多**：深色每页 <12px 文本 teams 49 处、overview 24、mcps 23；最小 9px（`kbd`、节点元信息）。最小字号建议 11px，`kbd` 去掉透明度。
- **toast 的 live region 嵌套**：`#toasts` 为 `aria-live="polite"`，每条 toast 又带 `role="alert"/"status"`；4.5s/15s 自动消失、不可手动关闭。二选一，并给错误条关闭按钮。
- **主题按钮无状态**：`aria-label` 恒为“切换明暗主题”、无 `aria-pressed`、图标恒为 sun（`views.js:45`）。
- **快捷键无提示**：Ctrl+S 直接保存但无 `<kbd>`，画布 `+/-/0` 与 `Ctrl+K` 未进 `aria-keyshortcuts`。
- **触屏落在画布上不能滚动页面**：`.team-canvas{touch-action:none}` 铺满画布（`GAP.md` 已记录）。
- **子 Agent 页 20 条同名重复**：Codex 与 Claude Code 各 10 条 `nature-*`，列表无分组/去重，只能靠来源下拉区分。
- **执行日志可读性**：12px 等宽浅色，stderr 原样混入（如 `codex_models_manager::manager: failed to refresh available models: request timed out`），无级别区分，容易被误读为任务失败。
- **装配台在窄屏不自动打开**：≤1100 点 `+` 只 toast「可打开装配台继续」，已选清单需再点顶栏（`run2/42-1024-skills-drawer.png`）。
- **危险操作与普通主操作同色**：「应用并备份」与“预览变更”同为黑色主按钮；关闭窗口/完全退出的确认框语气也偏轻。
- **深色卡片层级偏弱**：`--line #373737` on `--surface #242424` 边框几乎不可见，装配台与主内容靠留白区分。
- **装配方案空态与入口矛盾**：空态提示“在装配台点击保存方案”，但页面右上“保存当前组合”可直接保存 0 项（`views.js:422/437`、`dialogs.js:36`）。
- **术语与内部值外露**：装配台/装配工作台/装配方案、绑定客户端/执行 Agent、文件权限/执行权限；详情里出现 `codex`、`workspace-write`、`gpt-6-astra`、`succeeded`、32 位 run_id、标题带 `#`。
- **扫描禁用不一致**：`views.js:103` 只禁用第一个扫描按钮，设置页「重新扫描」扫描中仍可点且静默返回。

## 数据附录

- 断点：1600/1440 → 侧栏 232px；1280/1100/1024 → 188px；768 → 68px 图标栏；390 → 56px。全档**无横向溢出**。
- 对比度（浅色）：4.07（12px nav-count）、4.29（11px 状态/标签/说明）、4.37（12px 面包屑、10px eyebrow、13px subheading）；深色正文 5.5–12.7，焦点环 5.27–7.54，失败项仅 kbd 4.52 与禁用态 2.73–4.49。
- 点击目标：原生 checkbox 16×16（有整行 label）、`summary` 高 22px，低于 24px 触控建议值。
- 运行时：全程 0 个 console 错误、0 个未捕获异常。

## 建议修复顺序

1. 删除 `style.css:2023` 的 `}`，逐档回归栅格（1 行，影响最大）。
2. 焦点：切页后聚焦 `main`、同步 `document.title`；`showModal` 记录并恢复焦点。
3. 顶栏计数改“后台任务 N / 工作流只计 1”，Agent 卡片改“进程运行中”。
4. 会话菜单的关闭逻辑（切页、外部点击、Esc）。
5. 浅色 `--muted` 压深；深色禁用态 opacity 提到 .7 并关联原因。
6. 画布焦点环改内阴影；协作页补 h1。
7. 再处理 P3（术语表、toast、快捷键提示、日志级别）。

## 做得好的地方

- 十页、两套主题共用一套卡片/状态/留白语言；图标与字体全部本地化，无外部依赖。
- 诚实的状态文案：入口≠登录≠额度、插件缓存“启用状态未核验”、自动调度“已完成不代表验收通过”、日志“切换页面不会停止任务”。
- 空态完整：备份记录、装配方案、无搜索结果、配置缺失都有图标 + 说明 + 下一步入口。
- 键盘基础扎实：跳过链接真实可用；原生 `<dialog>` 背景 inert、打开聚焦、Esc 可关；表单控件可访问名称 0 遗漏；`aria-pressed/expanded/current` 实时维护；`prefers-reduced-motion` 全覆盖。
- 无脚本异常、无横向溢出，窄屏可折叠为图标栏，200% 缩放与 320px reflow 不横向滚动。

## 边界

- 只审阅界面与只读交互；未验证“应用并备份 / 恢复 / 执行任务 / 自动调度”的真实写入与结果，这些流程的界面问题需单独走查。
- 数据规模为本机真实数据（411 个 Skill 名称）；更大或更小的资源库需另测。
- 对比度按 WCAG 2.1 AA（正文 4.5:1、大字 3:1）从严评估；产品若接受更低标准可下调优先级。
