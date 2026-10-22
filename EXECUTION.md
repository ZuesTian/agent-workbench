# 执行任务与自动调度

Agent 工坊 0.4.11 支持真实执行、自动调度、多 Agent 会话与 Windows 托盘，数据仍保存在 `%LOCALAPPDATA%\AgentWorkbench`。原来的资源发现、装配、备份和协作方案继续使用。

## 单个任务

打开 **执行任务**，选择客户端，填写已有工作目录及任务，选择权限并开始。可查看实时日志、结果、取消任务或下载回答。

0.4.8 使用一次底层管道读取处理已刷新的输出，小事件不再等待缓冲区填满或进程退出。仍按完整行解析事件，并处理 EOF 前无换行的最后一条记录；单条记录超过 8 MB 会明确停止解析。这不改变客户端自身的输出刷新频率。

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

## 自动调度

1. 在 **协作编排** 填写总目标及各节点职责，将每个节点绑定到上述可执行客户端之一。每个节点可沿用客户端模型，或从联动的下拉列表选择模型。
2. 点击 **执行协作**，在“节点执行检查”确认全部节点的执行入口可用。出现“仅装配”“未绑定”或“入口不可用”时，点击对应节点返回配置。客户端选择器按执行能力分组；发现桌面程序不代表已接入自动执行，工坊不会自动替换已有绑定。然后选择共同工作目录、权限、每步最长时间。
3. 点击 **开始自动调度**。全部节点先进行输入与调用入口检查；无法调用的节点会阻止整组启动。
4. 调度按画布的层级及节点顺序串行执行：有下级的节点先分派，下级逐一执行，上级最后审核汇总。一个有两个叶子下级的负责人对应四次调用。
5. 前序结果自动传入后续步骤。为控制上下文大小，文本交接限制长度，截断处会明确标记，并提供完整结果文件位置。节点职责、总目标、审核策略及资源引用随任务发送。
6. 在执行记录中点击任一步骤查看日志；完成后下载 Markdown 汇总。页面重载或切换不停止任务。

开始时保存独立快照；之后修改画布不会改变已启动任务。Skills / MCP 引用不会自动装配、安装或授权，需先在装配台应用，运行时仍采用客户端实际配置。

当前调度为串行执行，共享工作目录；不自动创建 Git 分支或合并用户项目代码。任何步骤失败、超时或取消后停止后续步骤，记录已完成与未执行部分。审核节点输出质量结论与未完成项，当前不自动打回重试；所有步骤正常退出表示流程完成，不代表产物已通过业务验收。

## 多 Agent 会话

会话按成员顺序串行回答，每位成员都能获得此前已完成的回复。当前用户消息按编号单独加入一次；尚未完成、失败或取消的回复不作为成功结果传递。历史上下文最多保留 12000 个字符，实际长度还受单次 30000 字符提示词上限约束；截取处有标记，完整消息保留在本机会话记录中。

开始前预占统一执行入口并检查所有选中成员，入口忙碌或参数无效时不写入新消息。取消会终止当前调用并停止本轮剩余成员，随后可以发送新一轮。成员权限采用对应客户端的原生模式；旧会话若保存了非法模式，重新打开“成员与工作区”选择合法权限并保存后即可继续。

## 生命周期与记录

- 一次允许一个独立任务、一组协作或一轮会话运行。调度与会话在步骤间持有执行入口，避免其他请求穿插。单任务重复提交同一请求编号不会重复启动。
- 最小化或关闭界面后，任务继续运行，右下角托盘可恢复窗口。界面与托盘的“完全退出”会关闭后台服务、取消当前任务、移除托盘并释放运行锁。
- 每个调用默认最多 30 分钟，可设为 1–120 分钟。取消或超时终止本次进程树；退出管理服务也会取消。Windows 使用独立 Job Object，先建立归属再放行进程，不按程序名称批量结束其他客户端。
- 成功需要客户端完成事件与零退出码同时成立，并且没有失败事件。服务重启将未完成任务标为中断，不自动重跑，也不根据旧 PID 结束程序。
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

Windows CI 显式运行 Python、JavaScript、固定版本 Ruff、格式检查，以及执行页、协作、会话和生命周期四类隔离浏览器 smoke；构建后再运行打包会话与生命周期 smoke。浏览器脚本不由 pytest 自动收集。检查结果与截图作为 CI 附件保存。仓库配置了远程并实际触发后，才能据此报告远程 CI 通过；本地发布仍以各次保存的执行记录为准。

执行适配依据本机 CLI 帮助、已安装客户端的协议实现、Harness headless 文档及 [OpenAI 非交互模式文档](https://learn.chatgpt.com/docs/non-interactive-mode)。客户端升级后若协议变化，应重新运行集成验证。既有 MCP 的全部连通性不在本次验证范围内。
