# 执行任务与自动调度

Agent 工坊 1.2.0 支持真实执行、自动调度、多 Agent 会话与 Windows 托盘，本机数据默认保存在 `%LOCALAPPDATA%\AgentWorkbench`，可在设置中校验迁移到空目录，资源发现、装配、备份和协作方案统一管理。

## 单个任务

打开 **执行任务**，选择客户端，填写已有工作目录及任务，选择权限并开始。可查看实时日志、结果、取消任务或下载回答。

已刷新的输出及时进入解析，无需等待缓冲区填满或进程退出。按完整行解析事件，并处理 EOF 前无换行的最后一条记录；单条记录超过 8 MB 会明确停止解析。这不改变客户端自身的输出刷新频率。

模型下拉列表随 Agent 联动；默认沿用客户端当前设置，刷新按钮重新读取本机目录。切换 Agent 会重置模型，失效的历史模型会保留显示并要求重新选择。“多 Agent 协作”按钮进入现有协作画布。

| 客户端 | 调用入口 | 权限与模型 |
| --- | --- | --- |
| Codex | 本机 `codex exec --json` | 默认只读，可允许工作目录写入；模型来自当前配置与本地模型目录 |
| DeepSeek Harness | 本机 `dsh --profile headless --json` | 复用 headless profile；按模型选项同时传递 provider 与 model，本次 overlay 不修改 profile |
| Zcode | 安装包中的 Node CLI，`--output-format stream-json` | 跟随桌面端 `dataBaseDir`；可沿用模型或选择已配置模型。指定模型通过运行目录中的临时 provider 配置传入，结束时移除；原始配置不改写。权限为 plan 或 edit |
| Cursor | 官方 Cursor Agent CLI，`--print --output-format stream-json` | 从 CLI 当前账号读取模型列表；需单独完成 `cursor-agent login`。默认 `--mode plan`，编辑时使用 Agent 模式；仍遵守客户端批准规则及工作区信任设置 |

Cursor 首次访问工作目录可能返回 `Workspace Trust Required`。执行页、协作页和会话页提供“信任此工作目录（Cursor 将记住）”，核对显示的路径后勾选，再启动。Cursor 会保存该目录的信任记录，后续运行可不勾选；工坊只向本次选中的 Cursor 调用传递确认。更换目录、客户端或权限、成功提交后会取消勾选。服务端拒绝目录不匹配的确认，不使用 `--force`、`--yolo` 或 `--approve-mcps`。

Zcode 的 plan / edit 是客户端权限控制，不是工作目录级操作系统沙箱。Codex / Harness 的文件沙箱也不等于 MCP 工具全部只读：MCP 行为仍取决于对应客户端和服务的权限。

CLI 复用本机已有账号。工坊不要求新增 API Key，也不改写模型凭据。任务文本、所需上下文及工具输出可能交给模型服务，执行会使用账号额度。CLI 继承已有环境代理；缺少显式环境代理时读取系统代理，工坊不修改系统设置。

Windows 上的 Codex 调用为本次进程设置 `features.prefer_mxc=true`，优先使用设备和策略支持的 MXC 原生沙箱，避免首次工具执行进入旧式账户与目录权限初始化。仍按用户选择传递 `read-only` 或 `workspace-write`，保持 `approval=never`，不改写全局 Codex 配置；MXC 不可用时由 Codex 沿用已配置的旧式沙箱。模式及兼容条件见 [Windows 沙箱官方说明](https://learn.chatgpt.com/docs/windows/windows-sandbox)。

## 自动调度

1. 在 **协作编排** 填写总目标及各节点职责，将每个节点绑定到上述可执行客户端之一。每个节点可沿用客户端模型，或从联动的下拉列表选择模型。
2. 点击 **执行协作**，在“节点执行检查”确认全部节点的执行入口可用。出现“仅装配”“未绑定”或“入口不可用”时，点击对应节点返回配置。客户端选择器按执行能力分组；发现桌面程序不代表已接入自动执行，工坊不会自动替换已有绑定。然后选择共同工作目录、权限、每步最长时间。
3. 点击 **开始自动调度**。全部节点先进行输入与调用入口检查；无法调用的节点会阻止整组启动。
4. 调度按画布的层级及节点顺序串行执行：有下级的节点先分派，下级逐一执行，上级最后审核汇总。一个有两个叶子下级的负责人对应四次调用。
5. 前序结果自动传入后续步骤。为控制上下文大小，文本交接限制长度，截断处会明确标记，并提供完整结果文件位置。节点职责、总目标、审核策略及资源引用随任务发送。
6. 在执行记录中点击任一步骤查看日志；完成后下载 Markdown 汇总。页面重载或切换不停止任务。

开始时保存独立快照；之后修改画布不会改变已启动任务。Skills / MCP 引用不会自动装配、安装或授权，需先在装配台应用，运行时仍采用客户端实际配置。

当前调度为串行执行，共享工作目录；不自动创建 Git 分支或合并用户项目代码。任何步骤失败、超时或取消后停止后续步骤，记录已完成与未执行部分。审核节点输出质量结论与未完成项；自动返工只由配置的文件规则失败触发，并受额外调用次数限制；所有步骤正常退出表示流程完成，不代表产物已通过业务验收。

## 文件验收与证据

协作方案的 `acceptance` 数组保存最终交付文件规则，每项包含相对路径 `path`、类型 `kind`（`file` / `json` / `contains`）、指定文本 `expected` 和是否要求本次变化 `changed`（默认 true）。最多 20 项，路径与指定文本合计不超过 4000 个字符。启动前全量验证字段、路径和文件基线；失败时释放执行入口，不启动任何节点。已有的 Cursor 信任补充流程保留首次基线。

所有步骤成功返回后才执行只读文件检查。非空文件、有效 JSON、包含文本是三种明确的机械检查，JSON 与文本要求 UTF-8。文件最多 16 MiB，路径必须位于工作目录内，不允许 `..`、绝对路径、特殊设备名、符号链接或目录联接。默认以内容 SHA256 判断是否新增或变化；重新保存相同内容不能冒充本次新交付。取消变化要求可检查已有文件。

`workflow.json` 同时保存独立的执行 `status` 和 `acceptance.status`，后者包括 `not_configured` / `pending` / `not_run` / `passed` / `failed`。验收失败不会改写已经成功的 CLI 执行状态。执行失败、取消、重启中断时不判定文件通过。每项证据保存启动前和验收时的 SHA256、字节数、修改时间及失败原因；纳秒时间戳按字符串存储，避免浏览器数值舍入。

`run.json` 的 `runtime` 记录实际启动入口路径、文件大小和修改时间、客户端、请求模型设置、工作目录与权限。默认模型记为 `client_default`，不推断实际服务端模型。方案资源保留为引用，不宣称冻结或验证了实际 Skill/MCP 版本。界面支持下载验收证据 JSON，汇总 Markdown 也列出文件检查结果。文件规则通过不等于业务或科学结论正确。

## 多 Agent 会话

会话按成员顺序串行回答，每位成员都能获得此前已完成的回复。当前用户消息按编号单独加入一次；尚未完成、失败或取消的回复不作为成功结果传递。历史上下文最多保留 12000 个字符，实际长度还受单次 30000 字符提示词上限约束；截取处有标记，完整消息保留在本机会话记录中。

开始前预占统一执行入口并检查所有选中成员，入口忙碌或参数无效时不写入新消息。取消会终止当前调用并停止本轮剩余成员，随后可以发送新一轮；任一成员失败、超时或取消后，本轮剩余成员同样不再执行。

最近一轮未完成的回复可以重试：`POST /api/conversations/retry` 接受 `id` 与可选的 `message_ids`（省略时重试最近一轮全部未完成回复）。只允许重试最近一条用户消息之后、状态为失败/出错/取消/中断的回复；重试沿用正常轮次的预检与执行入口互斥，先校验全部成员再落盘，追加带 `retry_of`、`round_id` 的新回复并保留原记录，可照常取消。会话记录中回复正文最多保存 20 000 字符（超出附截取标记，完整结果仍在执行记录的 `result.md`），错误文本最多 2 000 字符；任务列表最多返回最近 30 条回复。

成员权限采用对应客户端的原生模式；旧会话若保存了非法模式，重新打开“成员与工作区”选择合法权限并保存后即可继续。

## 生命周期与记录

- 一次允许一个独立任务、一组协作或一轮会话运行。调度与会话在步骤间持有执行入口，避免其他请求穿插。单任务重复提交同一请求编号不会重复启动。
- 关闭界面后，任务继续运行；右下角托盘会提醒工坊仍在后台，可从托盘恢复窗口。托盘的“完全退出”会关闭后台服务、取消当前任务、移除托盘并释放运行锁。
- 每个调用默认最多 30 分钟，可设为 1–120 分钟。取消或超时终止本次进程树；退出管理服务也会取消。Windows 使用独立 Job Object，先建立归属再放行进程，不按程序名称批量结束其他客户端。
- 成功需要客户端完成事件与零退出码同时成立，并且没有失败事件。服务重启将未完成任务标为中断，不在启动时自动重跑，也不根据旧 PID 结束程序；协作记录可手动从未完成步骤续跑。
- 已经发生的文件修改不会自动回滚。

| 位置 | 内容 |
| --- | --- |
| `runs/<id>/run.json` | 任务、客户端、目录、权限、状态、时间、所属调度 |
| `runs/<id>/prompt.txt` | 实际提交给客户端的任务与交接上下文 |
| `runs/<id>/execution.log` | 执行日志，最多约 16 MB，页面显示最近部分 |
| `runs/<id>/result.md` | 客户端最终回答 |
| `workflows/<id>/workflow.json` | 协作快照、逐步状态、对应执行编号 |
| `workflows/<id>/summary.md` | 运行汇总、根节点审核结果、完整结果位置 |

记录可能包含任务内容和工具输出。HTTP 接口复用本机服务的会话、Host、Origin 与写入校验。执行使用独立锁，不阻塞资源扫描。

## 验证

自动测试使用隔离目录和可控的真实子进程：

```powershell
python -m pytest -q --tb=short --basetemp=.test-artifacts/pytest-release
npm test
python tests/execution_browser_controls.py
python tests/scheduling_browser_smoke.py
python tests/conversations_browser_smoke.py
python tests/reliability_smoke.py
python tests/conversations_browser_smoke.py --exe dist/AgentWorkbench/AgentWorkbench.exe
python tests/reliability_smoke.py --exe dist/AgentWorkbench/AgentWorkbench.exe
```

真实客户端验证会使用已有账号额度，需显式运行：

```powershell
python tests/execution_live_smoke.py
python tests/scheduling_browser_smoke.py --live
python tests/execution_packaged_smoke.py --exe dist/AgentWorkbench/AgentWorkbench.exe
```

最后一项要求先启动指定 EXE，运行实例须与当前源码版本一致；脚本会运行三个客户端的交接测试并保留服务运行，证据写入当前版本的 `releases/<版本>/verification/packaged-live/`。它使用真实账号，不加入自动 CI。

Codex 的 Windows 沙箱初始化可单独验证：先启动当前打包程序，再运行 `python tests/codex_sandbox_live_smoke.py`。脚本通过工坊串行执行中文工作目录和用户主目录的两次受限文件写入与读回，核对实际子进程参数、完成事件、退出码和文件内容，并保存到 `releases/<版本>/verification/codex-sandbox-live/`。测试使用已有账号额度，记录保留在工坊执行历史中。

Windows CI 显式运行 Python、JavaScript、固定版本 Ruff、格式检查，以及执行页、协作、会话和生命周期四类隔离浏览器 smoke；构建后再运行打包会话与生命周期 smoke。浏览器脚本不由 pytest 自动收集。检查结果与截图作为 CI 附件保存。仓库配置了远程并实际触发后，才能据此报告远程 CI 通过；本地发布仍以各次保存的执行记录为准。

执行适配依据本机 CLI 帮助、已安装客户端的协议实现、Harness headless 文档及 [OpenAI 非交互模式文档](https://learn.chatgpt.com/docs/non-interactive-mode)。客户端升级后若协议变化，应重新运行集成验证。既有 MCP 的全部连通性不在本次验证范围内。


## 1.2.0 恢复与管理接口

- `POST /api/workflows/start` 接受 `max_reworks`（整数 0–3，默认 0）及 `rework_node_id`；启用时要求 `permission=edit` 和至少一项文件规则。返工节点属于启动快照，参数不能在续跑时扩额。
- `POST /api/workflows/resume` 使用原 `id` 和新的 `request_id`。同一续跑请求重复提交返回原结果；复用已成功执行及交接，未完成步骤保留 `attempts` 并换新 `run_id`。预算按返工调用累计，崩溃前预占但未能确认启动的一次也计入上限。`resumptions`、`acceptance_history` 和 `rework_calls` 随记录落盘。
- 文件检查失败才触发自动返工，Agent 的自然语言审核意见不会被当成程序控制指令。返工仍使用原权限、工作目录、时限与调用入口；每次后检查全部规则。已成功步骤不会重新调用，但现有文件仍可能被返工修改。
- `GET /api/tasks` 支持 `scope=active|archived|trash|all`、`group`、搜索与分页；`POST /api/tasks/update` 支持 `archive/unarchive/group/delete/restore`，类型为 `run/workflow/conversation`。`POST /api/task-groups/save` 创建、重命名或删除分组。元数据独立存入 `task-library.json`，不改写执行证据；不允许单独整理协作子调用。
- `POST /api/data/migrate` 接收绝对 `path` 与 `backup_only`（默认 false）；只接受空目标，并拒绝源目标包含关系、链接和目录联接。复制前后验证 SHA256 清单，迁移时仅重绑定已保存 profiles / teams 的本地资源库引用并读回校验（团队版本号更新）；历史 workflow 快照保持原样。运行任务或扫描时拒绝迁移；复制期间串行阻止其他写入。成功切换后直到重启只读，原目录保留，默认目录的运行实例发现位置不变。
- 迁移不是项目环境搬运：外部工作目录、客户端配置及登录、浏览器草稿不在范围内。历史记录中的调用命令及产物路径作为原始证据保留。
