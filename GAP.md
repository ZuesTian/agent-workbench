# Agent 工坊 0.4.6 · 功能缺失审计报告

审计日期：2026-10-21（Asia/Shanghai）
审计对象：`D:\OneDrive\文档\Local\agent-workbench`，源码版本 `__version__ = "0.4.6"`
审计方式：**全程只读**。代码逐行阅读 + 关键字穷举搜索（每条"确认没有"都附搜索依据）+ 实跑测试 + 实机环境核对 + 交付物哈希核对。未修改任何源码。
证据标注：`文件:行号` 为实读位置；标 **[已复核]** 的条目由主审计人二次亲自确认，其余由分区审计代理给出并保留行号可查。

## 0.4.8 可靠性复核与补齐

本节覆盖下文已经过时的 0.4.7 修复判断，历史审计原文保留。修改前源码及哈希清单保存在 `audits/2026-10-21/pre-0.4.8/`；0.4.7 版本压缩包与验证记录保留。

| 复核发现 | 0.4.8 实现与验证 |
| --- | --- |
| 恢复在检查路径前移动暂存原件，可能先写入重定向位置再报错 | 恢复前对整笔事务只读检查；目标身份、暂存路径归属及其内容、备份全部通过后才开始修改。Windows 目录联接重定向及非法暂存路径测试验证拒绝时没有文件变化。 |
| 恢复自身的重命名阶段未记录可恢复状态 | 应用与恢复分别使用已记录的确定暂存路径；恢复移动目标前写入 `restore_path` 与 `restore_phase`。文件、覆盖 Skill、新装 Skill 的重命名中断可由新 Engine 实例从磁盘重试；后来用户编辑仍阻止恢复。 |
| 流式 `read(65536)` 阻塞小事件直到 EOF | 使用缓冲流的 `read1` 获取已到达输出，保留分段、末尾无换行及 8 MB 上限。真实子进程握手测试要求收到首条事件后才允许子进程继续退出。 |
| 多配置只报错，没有选择入口 | 预览返回已知可写候选，界面选择后必须重新预览；仅修改选定配置，其他配置字节不变。本机草稿和方案保留选择，导出不带目标路径选择；任意路径或失效选择被拒绝。 |

源码验证：Python `178 passed`（56.77 秒）；JavaScript `29 passed`。独立用户目录的源码启动、托盘可用、浏览器选择配置/应用/恢复/草稿重载和 1440/390 像素窗口检查通过。缩窄窗口时抽屉覆盖预览的问题已修复，并用按钮实际可点击检查与截图验证。

发布产物与源码哈希、打包后实际启动和界面冒烟结果见 `releases/0.4.8/verification/release.json` 及同目录验证记录。真实客户端账号调用本轮未执行。执行项目 diff / 回滚仍属于后续批次，不能用装配配置的恢复能力代替。

## 0.4.7 基线纠正（同日下午，可靠性修复前）

下面这些判断覆盖本文件后文里已经过时或修法错误的句子。后文保留作审计记录，不再当作 0.4.7 的实施说明。

| 事项 | 纠正 |
| --- | --- |
| 源码基线 | `audits/2026-10-21/baseline-pre-0.4.7.json` 记录可靠性修复前的 72 个源码与测试文件 SHA-256。其中已包含审核之后的易用性修改，**不在**已发布的 0.4.6 安装包里。 |
| 测试数字 | 前文 `138 passed` 与另一次 `143` 都不能当作当前基线。可靠性修改后必须重跑。 |
| 父级 Git | 工坊目录没有自己的 `.git`。父目录 `D:\OneDrive\文档\Local` 是一个 **尚无提交** 的 Git 仓库，工坊在其中只是未跟踪目录。不要在工坊内再 `git init`，也不要用父仓库的 `git add` 代替版本发布。 |
| 旧发布包 | `releases/0.3.1` 至 `releases/0.4.6` 及其 `verification/` 保留，作为回退入口。本轮不删除、不覆盖那些验证记录。 |
| P1-3 修法 | **不要**改成只写 `defaultModelSelection` 差量。临时文件必须保留完整配置语义，并限制访问权限；成功、失败、取消和启动清理都要删掉已停用的副本。日志和分享包不能带上凭据。 |
| P1-4 修法 | **不要**把所有 `item.error` 判成失败。非致命的条目错误之后，若回合正常完成，仍应判成功。`turn.failed` 这类终结错误才判失败。 |
| Claude 配置 | `~/.claude/settings.json` 不是 MCP 用户配置。Claude Code 继续写 `~/.claude.json`。Cline 的独立安装与 VS Code 安装、OpenCode 的 JSON 与 JSONC 要分开判断；多份并存时停止并要求选择。 |
| P1-6 | 执行 diff 和回滚不在 0.4.7。只保存哈希或 `git status` 不能承诺回滚。 |
| 界面旧结论 | 执行历史固定 100 条、资源选择只显示 60 项、选择按钮没有稳定标识，这三条对应当前工作区已经不成立。行号也因后续补丁前移，不能再按本文件旧行号下补丁。 |

已用回归测试复现并在 0.4.7 修复的失败场景：Zcode 远程 MCP 在生成连接字段前写 `out`；流式输出被 1 MB 行限制或缺失换行丢掉；非致命 `item.error` 与 `turn.failed` 被混为一谈；Zcode 模型临时文件残留且权限过宽；装配把目标改名后进程中断，恢复时找不到原件；Cline / OpenCode 多配置或 Claude `settings.json` 被当成唯一下笔位置。

修复后的源码回归：Python `155 passed`，JavaScript `27 passed`。这只说明协议和隔离夹具通过。

本轮仍未验证、不能写成通过：真实 Codex / Cursor / Zcode / Harness 账号调用，以及打包后的桌面冒烟。入口可定位、协议测试、真实调用必须分开记录。

---

---

## 0. 总体判断

核心链路是**完整且可用**的：资源发现 → 装配 → 预览 → 备份 → 应用 → 恢复，协作画布（拓扑/预演/动态），四客户端真实执行与自动调度。安全设计（凭据不外泄、Host/Origin/会话校验、CSP、事务+哈希乐观并发、Job Object 进程树）做得扎实。

缺失分三类，性质完全不同：

| 类别 | 性质 | 数量 | 处理建议 |
| --- | --- | --- | --- |
| **A. 实现缺口** | 现在就会崩 / 丢数据 / 泄凭据 / 静默无效 | 8 条 P1 | 立即修，每条都在几十行内 |
| **B. 能力天花板** | 功能没做，产品能力受限 | 40+ 条 P2 | 按价值排序补齐 |
| **C. 工程基建** | 无 git / LICENSE / CI / 覆盖率 / 清理策略 | 15+ 条 P2-P3 | 一次性补齐，成本极低 |

**不属于缺失**的部分（文档已明确声明为设计边界，行为与文案一致）单列在第 6 节，避免把"有意不做"误判为"漏做"。

---

## 1. 当前真实状态（实测，非文档转述）

| 项目 | 实测结果 |
| --- | --- |
| Python 测试 | `138 passed in 37.70s`（`--basetemp=.test-artifacts/pytest-gap-audit`） |
| JS 测试 | `node --test tests/*.test.js` → 27 pass / 0 fail |
| 桌面快捷方式 | `D:\OneDrive\Desktop\Agent 工坊.lnk` → `releases\0.4.6\dist\AgentWorkbench\AgentWorkbench.exe`，目标存在 ✓ |
| 交付物哈希 | 根 `dist` exe SHA-256 `38EB33BB…156462` = `releases/0.4.6/verification/release.json` 的 `exe_sha256`；根 `AgentWorkbench-Windows.zip` SHA-256 `71C3EA30…5985d` = `zip_sha256` ✓ |
| 分享包时效 | zip 内 README 首行 `# Agent 工坊 0.4.6`，exe 构建 23:15:40 **晚于**源码最新 mtime 23:12:56 → **根目录分享包就是当前源码，不存在"分享包比源码旧"** ✓ |
| 真实数据目录 | `%LOCALAPPDATA%\AgentWorkbench` 现在**只有一个 0 字节 `server.log`**；`runs/ workflows/ profiles/ teams/ backups/ library/ cache/` 全部不存在 |
| 历史可复核性 | 0.4.6 验证记录里的那条 3 步协作（`release.json` → `live_workflow.id = 1018882f…`，目录 `…\workflows\1018882f…`）**已不在盘上** → 历史页与备份页当前为空，无法复核昨天的执行证据 |
| 执行入口可用性 | 本机 PATH 与 npm 全局 bin 中**均无 `codex`、`cursor-agent`、`claude`（PATH）、`gemini`、`opencode`**；仅 `dsh`（`%LOCALAPPDATA%\dsh\bin\dsh.cmd`）与 `node` 可直取。`~\.codex\.sandbox-bin\` 下只有 `codex-command-runner-*.exe`（沙箱辅助件，非 CLI 主体） |
| 0.4.6 实测覆盖 | `release.json` 记录的真实推理仅覆盖 **Cursor + Harness**（`cursor_authenticated: true`，3 次真实调用）；**Codex 与 Zcode 的真实执行本轮未验证** |
| 未被利用的能力 | `C:\Users\AMD\.local\bin\claude.exe`（约 220 MB，Claude Code 原生 CLI）**实际存在**，但执行注册表未包含它 |
| 磁盘占用 | `releases/` 633.6 MB（2832 文件，8 个版本全量快照）、`.test-artifacts/` 102.1 MB、`dist/` 21.6 MB、`build/` 7.8 MB、`node_modules/` 9.5 MB |
| OneDrive 脱水风险 | `releases\0.4.6\dist` 98 个文件 `offline=0`（均为本地实体）→ 当前无按需同步导致启动失败的风险；但整个项目位于 `D:\OneDrive\` 命名目录下，若启用按需同步则桌面快捷方式指向的 exe 可能脱水 |

---

## 2. A 类：P1 实现缺口（现在就会出问题）

### P1-1 Zcode 远程 MCP 装配必崩（UnboundLocalError） **[已复核]**

- 证据：`agent_workbench/adapters.py:370-380`。`out` 只在 `if n["transport"] == "stdio":`（371 行）与 `else:`（382 行）两个分支被创建；夹在中间的 `elif target.kind == "zcode":`（376 行）直接执行 `out["type"] = n["transport"]`（377 行）→ `out` 未定义。
- 触发条件：把任意 **非 stdio**（streamable-http / sse）的 MCP 装配到 Zcode。因为大多数来源客户端 kind 是 `json`，`adapters.py:331` 的同 kind 直通分支（`source_kind not in ("json",)`）不会命中，所以 `json → zcode` 这条最常见路径**必然进入崩溃分支**。
- 后果放大：异常被 `engine.py:248-251` 的宽 `try` 压成"请检查路径或文件权限"，用户拿到完全错误的诊断方向；`server.py:63-65` 的 `log_message` 被 pass、全包无 `logging`，现场无任何日志可查。
- 测试盲区：`tests/test_discovery.py:117` 只覆盖 stdio，http/sse → zcode 分支零覆盖。
- 最小补法：进入分支前先 `out = {"url": n["url"]}`（并按需带 `headers`），再补 2 条单测（http→zcode、sse→zcode）。

### P1-2 执行流式解析丢事件 → 成功任务被判失败 **[已复核]**

- 证据：`agent_workbench/execution.py:278-296`。
  - `while raw := stream.readline(LINE_LIMIT)`（281 行）在**行长超过 `LINE_LIMIT = 1_000_000`**（26 行）时会返回不完整片段；
  - 判定 `if source == "stderr" or partial or not raw.endswith(b"\n")`（285 行）→ 不完整片段只写日志、不解析；
  - 更关键：`partial` 置位后（287 行），**补齐剩余内容并以 `\n` 结尾的那一块也会因 `partial` 为真而被当日志丢弃**，直到下一块才恢复解析 → 一个被切分的 JSON 事件整体丢失。
  - EOF 处若最后一行不带换行，同样走 285 行分支被丢弃。
- 后果：`result` 事件丢失 → `result.md` 不写；`completed` 事件丢失 → `execution.py:353` 判 `failed`；自动调度里该步骤失败即 `break`（`scheduling.py:268-271`），后续全部标 `blocked`。长回答（>1 MB 单事件）与"末尾无换行"的 CLI 输出都会命中。
- 最小补法：改为累积 buffer，只在换行处 `json.loads`；EOF 时对残块再试一次解析；超长行给独立的"事件过大已截断"状态而不是静默丢弃。

### P1-3 Zcode 指定模型时明文凭据落盘，且无清理出口 **[已复核]**

- 证据：`agent_workbench/model_catalog.py:180-191`。为了传模型选择，代码 `document = read_json(source)` 读入**整份**个人 provider 配置（含密钥），只改 `document["config"]["defaultModelSelection"]`（188 行），然后把**整份文档**写到 `runs/<id>/zcode-model.json`（189-190 行）。
- 删除路径只有一处：`execution.py:372-376` 的 `finally` 中 `(directory / "zcode-model.json").unlink(missing_ok=True)`。启动时的历史清理（`execution.py:89-99` 区间）只把 ACTIVE 状态改 `interrupted`，**不扫描残留密钥文件**。
- 触发条件：强杀进程、断电、退出超时（见 P2-服务-4）→ 密钥明文长期留在 `%LOCALAPPDATA%\AgentWorkbench\runs\<id>\`，且该目录没有清理接口（见 P2-资源-3），用户无从发现。
- 与项目自身承诺冲突：README:102「资源盘点不上传这些文件…导出方案不嵌入配置正文」、`catalog.py:46-61` / `scan_cache.py:44` 的脱敏设计，都被这条旁路绕过。
- 最小补法：只写差量（`{"config": {"defaultModelSelection": {...}}}`）而非整份配置；并在 `ExecutionManager.__init__` 里扫描 `runs/*/zcode-model.json` 一并清除。

### P1-4 Codex 错误事件不置失败

- 证据：`agent_workbench/runners.py:285-292`。Codex 的 `item.completed` 中 `type == "error"` 只被转成文本（经 `execution.py:60-61`），**不设置 `failed`**；若 CLI 随后仍发 `turn.completed`，`decode_event` 置 `completed` → 任务判"成功"。
- 后果：错误被当成正常结论交给下游节点（`scheduling.py:272-274` 把 result 原样传递），审核节点也可能"通过"。
- 最小补法：codex 分支遇到 `item.completed` 且 `type=="error"` 时同时置 `failed`。

### P1-5 装配只写 `agent.config`，忽略 `extra_configs` → 静默无效 **[已复核]**

- 证据：`agent_workbench/engine.py:102` `doc, before_hash = read_document(agent.config, agent.kind)` —— 只处理单一主配置路径；而**盘点侧**遍历了主配置 + `extra_configs`（`catalog.py:346`）。
- 受影响的适配器：
  - Cline：真实配置常在 VS Code `globalStorage/saouden.rizwan.claude-dev/settings/cline_mcp_settings.json`（`adapters.py:136-139`，属 extra）；
  - OpenCode：可能是 `opencode.jsonc`（`adapters.py:126`，属 extra）；
  - Claude Code：`~/.claude/settings.json`（`adapters.py:74`，属 extra）。
- 后果：目标主文件不存在时 `read_document` 返回 `{}` 并**静默新建**（`adapters.py:242-243`）→ 界面显示装配成功、备份记录齐全，但客户端根本不读那个文件，**用户以为装上了，实际零效果，且无任何提示**。
- 最小补法：`engine` 在写前解析"该客户端实际生效的配置文件"（主 + extra 中存在者优先），或至少在新建文件时给出显式 warning「已新建 X，请确认客户端读取该路径」。

### P1-6 执行结果无 diff、无回滚

- 证据：`EXECUTION.md:43`「已经发生的文件修改不会自动回滚」；`execution.py:377` 同义注释；`diff` 关键字在 `agent_workbench/*.py` **零命中**；`engine.py` 的 `apply/restore` 只覆盖**配置文件装配**，不覆盖任务执行对用户项目的改动（`service.py:36-38`）。
- 后果：任务失败/取消/审核不通过后，用户项目已被改动，既不能审阅改了哪些文件，也不能一键回退。这是"多 Agent 自动改代码"场景里最硬的缺口。
- 最小补法：执行前对 cwd 记录基线（`git stash create` / `git status --porcelain` 快照，或文件哈希清单），执行后生成 diff 报告；提供"回滚到基线"入口。

### P1-7 回滚残留文件不可寻

- 证据：`agent_workbench/resources.py:115-121` 两段 `os.replace`；失败时目标目录只剩隐藏文件 `.<name>.awb-old-<uuid>`，而 `engine.py:400-403` 写入 `recovery_errors` 的**只有文件名、没有完整路径**。
- 后果：README:106 承诺「备份清单显示中断状态时，可在备份页继续恢复」，但用户拿不到路径就无法自助处理隐藏残留文件；README:104「不静默丢弃文件」在这条路径上不成立。
- 最小补法：`recovery_errors` 记录绝对路径 + 在备份记录页显示可复制的残留清单。

### P1-8 无版本控制、无 LICENSE、无 CI

- 证据：`Test-Path .git` = **False**、`.github` = **False**；全盘 grep 无 `LICENSE`；`.gitignore` 存在（9 条规则）却没有仓库；无 `pyproject.toml`、无 `ruff.toml`（尽管有 `.ruff_cache`，说明跑过 ruff 但配置未落盘）、无 `.editorconfig`、无 `Makefile`。
- 现状代偿：`releases/0.3.1 … 0.4.6` 共 8 份**全量目录快照**（633.6 MB / 2832 文件），每份含 source + build + dist + zip + verification。
- 后果：无提交历史、无 diff、无细粒度回滚、无并行开发、无法合法分发（缺 LICENSE）；每次发布靠手工复制目录，磁盘单向增长。
- 最小补法：`git init && git add -A && git commit`（`.gitignore` 已就绪）+ 选定 LICENSE + 用 tag 替代 `releases/` 全量快照。

---

## 3. B 类：能力天花板（P2，按域分组）

### 3.1 执行与调度

| 缺口 | 证据 | 影响 | 最小补法 |
| --- | --- | --- | --- |
| **只有 4/16 客户端可执行**（codex / deepseek-harness / zcode / cursor） | `runners.py:139-146` 注册表；其余客户端 `runners.py:151` 直接抛"仅支持装配" | 协作团队无法混编 Claude Code、Gemini CLI、OpenCode 等；**本机 `~\.local\bin\claude.exe` 实际存在却未接入** | 按同一 `prepare_command/decode_event` 契约加 claude（`-p --output-format stream-json`）作为第 5 个 runner |
| **纯串行 + 单执行槽** | `execution.py:108-109, 208-211`；`scheduling.py:242-274`；`queue\|ThreadPool\|asyncio\|parallel\|并行` 在包内仅命中 `discovery.py:304-306` 的 BFS deque | 同层叶子本可并行；一个卡死的 CLI 独占全局唯一槽位 | 引入 N 槽执行池，调度按拓扑层并行同层节点 |
| **审核结论不进控制流：无打回、无重试、无单步重跑** | `scheduling.py:265-271` 只看 run 状态即 `break`；`service.py:87-103` 路由只有 `workflows/start\|cancel`、`runs/start\|cancel`；`retry\|重试` 命中全为文案；`static/execution.js` 无重试按钮 | 「审核不通过」只是一段文本，流程照样算完成；失败后只能整组重跑 | review 步骤要求结构化 verdict（pass/fail + 未完成项），fail 时按 `max_retries` 重跑该节点；提供"从此步重跑" |
| **无断点续跑** | 重启把 ACTIVE 标 `interrupted`（`scheduling.py:107-118`）；`previous` 只在内存（`scheduling.py:235, 272-274`）；同 `request_id` 重提只返回旧记录（`scheduling.py:188-192`） | 服务重启/崩溃后整组作废，已完成步骤的结果无法续接 | `previous` 落盘到 `workflow.json`，提供"从第 N 步续跑" |
| **无节点级超时、无 workflow 总时长上限** | `scheduling.py:174, 252`；每步上限 1–120 分钟（`execution.py:187-188`） | 100 节点 × 120 分钟理论无界；无整体预算概念 | 增加 workflow 级 deadline 与节点级 timeout 字段 |
| **无"无输出/空闲"超时** | `execution.py:335-344` 只有总时长判据 | 卡死但不退出的 CLI 会占满整个时限 | 记录最近输出时间，空闲超阈值即终止 |
| **交接截断按字符不按 token，且首条超长即整体丢弃** | `scheduling.py:77, 81-91`（base 17 000 / 交接 11 000 / 单条 5 000），`scheduling.py:88-89` 的 `break` | 深层节点会**丢掉全部早期交接**；只传文本，不传产物清单/文件路径 | 按优先级保留（父分派 > 最近兄弟 > 早期），改 `continue` 而非 `break`；附产物清单 |
| **assign 不修改子任务** | `scheduling.py:61, 250-254`：子节点仍用静态 `node["prompt"]`，负责人分派结果只作附加文本 | 「上级先分派」实际不改变下级做什么，与 README:53 的语义有落差 | 允许 assign 输出结构化的子任务改写，或明确文档化 |
| **无并行/队列/定时触发** | `cron\|schedule_at\|trigger\|定时` 唯一命中 `scheduling.py:64`（内容是禁止 agent 自建定时任务） | 无法做"每天跑一次巡检"这类编排 | 可延后 |
| **无用量/成本聚合** | `usage` 只写单条 `run.json`（`execution.py:302-304`）；`service.py:49-85` 无聚合路由；Cursor 的 `total_cost_usd` 被丢弃（`runners.py:319`） | 跑完一组协作不知道花了多少额度 | 汇总到 `workflows/<id>/summary.md` + 前端成本视图 |
| **无 MCP 运行时连通性验证** | `adapters.validate_mcp` 只做结构校验（`service.py:192`）；`static/dialogs.js:24` 明写"尚未检查连通性"；`health\|connectivity\|ping\|tools/list\|initialize` 在包内只命中 `server.py:114`（自身存活） | 装配完不知道 MCP 能不能连上，要到客户端里才发现 | 提供"测试连接"（stdio 起进程握手 / http 发 initialize） |
| **无多工作目录** | 单一 cwd（`execution.py:173-178`、`scheduling.py:251`）；team 快照无 per-node cwd 字段（`teams.py:76-95`） | 前端节点 + 后端节点无法各自在自己的仓库里跑 | 节点级 cwd（默认继承 workflow cwd） |
| **无 Git 分支隔离** | `git\|branch` 在 `agent_workbench/*.py` 零命中；`EXECUTION.md:35` 声明不建分支 | 多 Agent 直接改用户工作区，配合 P1-6（无回滚）风险叠加 | 可选"在临时分支/worktree 中执行" |

各客户端适配的细节缺口：

- **Codex**：事件覆盖窄，只认 `thread.started / turn.completed / turn.failed`（`runners.py:228-236`），其余靠 `execution.py:45-74` 兜底成"事件 · 类型"；result 完全依赖 `--output-last-message`（`runners.py:232`），CLI 不写文件则 **succeeded 但结果为空，调度交接空串且无告警**（`scheduling.py:272-274`）；模型不校验存在性（`runners.py:173-178` 只校验 zcode/cursor），错模型名要到运行时才失败；无 profile/CODEX_HOME 隔离（`runners.py:24-25`）。
- **DeepSeek Harness**：无 `final` 时 result 为空，但 `turn_end.reason.kind == "completed"` 仍算成功（`runners.py:296-302`）；`approval: never`（`runners.py:241, 244`）造成的"需批准被自动拒绝"没有专用提示，混入通用失败文案（`execution.py:358`）；未处理 assistant / tool_error / permission_denied；`headless.patch.json`（`runners.py:252-253`）**从不清理**（对比 Zcode 有 finally 删除）。
- **Zcode**：事件解析最弱，走通用 `else` 分支，不解析 assistant content blocks，thinking 静默（`runners.py:344`），failed 仅靠 `projection.status`（`runners.py:337`）；每次启动强制 refresh 模型列表（`runners.py:179-181`）拖慢启动；prompt 经 Node bootstrap 注入（`runners.py:193-198`），无法传附件。
- **Cursor**：命令行里**没有任何 prompt 参数**，纯靠 stdin（其余三家有显式约定：`runners.py:236 / 258 / 196`）→ CLI 行为一变即静默失败；工作区信任检测靠 stderr 英文字面量 `"Workspace Trust Required"`（`execution.py:283`），本地化或改字即失效；模型校验用 5 分钟缓存（`model_catalog.py:202`），可能误拒新账号刚开的模型。
- **公共**：`wait(timeout=10)` 抛出的 `TimeoutExpired` 被通用 `except` 覆盖（`execution.py:345, 359-361`），`exit_code=None`、真实原因丢失；`pywin32`/`psutil` 缺失即完全无法执行，**无降级 Popen 分支**（`execution_process.py:18-22`）；每个 usage/thread_id 事件都整文件重写 `run.json`（`execution.py:302-304` → `storage.py:23-38`）；凭据缺失只有 Cursor 有专用文案（`model_catalog.py:82-83`），其余仅通用退出码提示；`model_catalog.py:74-76` 丢弃 CLI stdout/stderr，模型读取失败不可定位。

### 3.2 装配与资源

| 缺口 | 证据（含"确认没有"的搜索依据） | 最小补法 |
| --- | --- | --- |
| **无 Skill 卸载 / 移除** | `uninstall\|remove_skill\|delete_skill` 只命中 `discovery.py:41,47,62,224`（注册表字段名）；`resources.remove_owned` 只被事务回滚与临时文件调用（`engine.py:512`、`resources.py:125`）；README:81 已声明"取消勾选不卸载" | 提供"从目标卸载此 Skill"（走同一备份/恢复事务） |
| **导入的自定义 MCP / Skill 无删除入口** | `service.py:45-46` 只有 `/api/library/mcp`（add）与 `/api/library/skill`（import），无对应 remove；前端亦无按钮 | 加 `library/mcp/delete`、`library/skill/delete` + 界面入口 |
| **无版本比较与升级提示** | `version\|semver\|upgrade` 只命中 `engine.py:336,462,470`、`teams.py:43,53`、`server.py:115`、`__init__.py:1`；front-matter 整体解析（`catalog.py:28-38`）但只取 name/description（`catalog.py:440-442`），**version 字段被丢弃** | 保留 version，装配时提示"目标已装 v1.2，来源 v1.5" |
| **无依赖解析**（Skill → MCP / 二进制 / 其他 Skill） | `dependenc` 只命中 `resources.py:29`、`scheduling.py:1`、`storage.py:1` 的注释/docstring | 解析 front-matter 的依赖声明并在预览中提示缺失 |
| **Profile 不可移植** | `profiles.py:55` 存 catalog uid，而 uid 由**绝对路径**哈希生成（`catalog.py:426, 615`）→ 换机/改目录全失效；`profiles.list()` 不校验引用（`profiles.py:16-42`），要到预览才报"资源已不存在"（`engine.py:60-63`）；`export\|import_\|bundle\|zip\|archive\|share` 在包内只命中 `service.py:46,211` 与注释 → **Profile 无导入/导出**（只有 teams 有可移植 bundle，`teams.py:39-53`） | 仿 `teams.py:39-53` 给 profile 做 bundle（按 name+source 标签而非路径哈希） |
| **Profile 不保存 overwrite 授权** | `validation.py:39-45` 解析了该字段，`profiles.py:55` 不存 | 明确设计：要么存（带当次确认语义），要么在 UI 说明"每次需重新勾选" |
| **runs / workflows / backups 无清理与保留策略** | `rmtree\|delete\|cleanup\|purge\|prune` 在 `execution.py`/`scheduling.py` **零命中**；列表硬截断最近 100 条（`execution.py:130-142`、`scheduling.py:146`）而前端不提示（`execution.js:110`、`workflows.js:144`）；备份只列不删（`engine.py:428-463`）且每次存 before + after 双份（`engine.py:346-357`） | 加"删除记录/清理旧备份"+ 保留策略（按天数或条数）+ 界面提示"仅显示最近 100 条" |
| **无跨进程文件锁** | `msvcrt\|fcntl\|lockf\|portalocker\|FileLock` 零命中，只有 `threading.RLock`（`catalog.py:84` 等）；并发保护靠内容哈希乐观检查（`engine.py:323,377`、`resources.py:113`） | 对目标配置文件加 `msvcrt.locking`  advisory lock（能排队而非只能中止） |
| **失败现场零日志** | `server.py:63-65` `log_message` 直接 pass；`import logging\|traceback\|logger` 只命中 `desktop.py:96,125,197`；`engine.py:248-251` + `server.py:130-133, 207-211` 把异常细节全丢 | 引入 logging（脱敏后写 `server.log`），错误响应附错误编号可回查 |
| **项目级 / 工作区级只盘点不写回** | `catalog.py:374-389`（385 行 `writable=False`）、`engine.py:296` 固定 warning | 可选支持项目级写入（带明确授权） |
| **跨客户端只转连接字段** | `adapters.py:336-345, 407-423`；同 kind 不同 agent（Cursor→Cline）也不保留专有字段，因 `adapters.py:331` 用 `source_kind not in ("json",)` 排除了所有 json 客户端 | 为 json 家族建立字段映射表，无法映射时列明丢弃了哪些字段 |
| **JSON 客户端写回必丢注释** | `engine.py:188-192`、`adapters.py:272`（只 TOML 经 tomlkit 保真） | 可接受，但预览应显式提示"注释将丢失" |
| **`engine.py:89-251` 的 try 覆盖整个 agent 分支** | 任一 Skill 触发上限（`resources.py:42` 万条目 / `:53` 100MB）或异常 → **该 agent 剩余全部资源静默不处理**，只留一句含糊错误 | 收窄 try 到单个资源项，逐项记录成败 |
| **library 级 source_hash 用整个 `mcps.json` 哈希** | `catalog.py:474` → 改任意一项，其余全部在预览误报"来源配置已变化"（`engine.py:80-82`） | 改为按单条记录哈希 |
| **`_definition_hash` 只覆盖 SKILL.md，而 source_hash 覆盖整目录** | `catalog.py:435-448` vs `engine.py:215` → 扫描到预览之间改 `scripts/` 不被检出，却按新内容装配 | 统一口径 |
| **pending 预览超 30 个直接全清** | `engine.py:43-46` → 多窗口并行时别人的预览被静默作废（报"预览已过期"，`engine.py:313-314`） | 按会话隔离 pending |
| **Skill 导入同名只能拒绝** | `service.py:220-222` 无覆盖/改名选项 | 提供"覆盖（走备份）"或"改名导入" |
| **自定义 agent 要求 config 父目录已存在** | `service.py:155` → 无法为尚未创建配置目录的客户端预置配置 | 允许创建父目录（带提示） |

静默吞掉、用户看不到的路径（会导致"数量对不上但无解释"）：

- `catalog.py:357-358, 377-378`：MCP 条目非 dict → `continue`，不记 issue；
- `dsh.py:52-55`：transport 不是 stdio/streamable-http 的 Harness MCP **整项静默丢弃**（无 issue，且 issue 文案不含 serverName）→ 在工坊里完全隐形；
- `catalog.py:293`：未 detected 的客户端完全不出现在列表 → 无法区分"没装"与"没扫到"；
- `catalog.py:36-37`：SKILL.md front-matter 解析失败 `pass` → 名称退化为目录名、描述变"暂无说明"，零提示；
- `catalog.py:222-224`：后台扫描 `except Exception: pass`；
- `catalog.py:413-414`：子 Agent 读取失败只报文件名，丢异常类型；
- `discovery.py:294-295`：Cline/Roo 扩展 manifest 解析失败 `continue` → 客户端被误判"仅发现配置"，进而触发 `engine.py:96-100` 的误导警告；
- `catalog.py:373` 累加的 `mcp_count` 被 `catalog.py:485-487` 重算覆盖（死代码）；
- `catalog.py:333` skill 根目录未去重（Codex 的 `.agents/skills` 在 `adapters.py:60-62` 出现两次）→ 同目录遍历两遍。

### 3.3 界面

| 缺口 | 证据 | 最小补法 |
| --- | --- | --- |
| **全轮询无推送** | scan 500ms（`app.js:76`）、runs 1s/4s（`execution.js:241`）、workflows 1s/4s（`workflows.js:212`）；每次全量返回 160 KB 日志 + 1 MB result（`execution.py:158-159`），无 offset/ETag | 日志接口支持 `?offset=`；或上 SSE |
| **`api.js` 无超时无 AbortController** | `static/api.js:10` | 加 timeout + AbortController，超时给可重试提示 |
| **401 无专门处理 / boot 失败无重试** | `api.js:19`；`app.js:473-476`；`/api/health` 免认证却从不被前端请求（`server.py:114`） | 401 → 提示"从桌面入口重新打开"；boot 失败先探测 health 区分"服务未起/会话失效"并给重试按钮 |
| **全局 busy 锁把"取消"也禁用** | `ui.js:94-101` | busy 期间放行取消/关闭类按钮 |
| **多窗口无协调** | 4 个 localStorage key（`state.js:180,189`；`team-model.js:355`；`execution.js:54`；`workflows.js:245`）无 `storage` 监听、无 BroadcastChannel（grep 无匹配）；catalog/teams/profiles/history 从不轮询（`app.js:79-91`） | 加 `storage` 事件监听；teams 冲突（后端已实现，`teams.py:149-150,160-161`）改为弹"重新载入 / 另存为新方案"而不是只 toast（`team-controller.js:120-129`） |
| **刷新后状态丢失** | 只保留 view/sort（`state.js:189, 207-209`），tab/query/source/transport/page/onlySelected 全丢（`state.js:22-30`） | 一并写入 localStorage 或 URL hash |
| **画布：无撤销/重做** | `team-model.js` 无 undo 栈；`team-controller.js:533-543` 仅 Ctrl+S | 加轻量命令栈（节点增删/移动/改上级） |
| **画布：自动排版破坏手动布局** | `autoLayout` 是 leaf 计数布局，在增删节点/改上级时**无条件全量重排 + fitGraph**（`team-model.js:132-148`；`team-controller.js:261, 303, 485-489`）→ 手动坐标全丢、视口跳变 | 只对新增节点做局部布局，或重排前确认 |
| **画布：无复制/粘贴/克隆、无框选多选** | grep 无 clipboard；`editor.selected` 是单值（`team-model.js:333`），空白拖拽=平移（`team-canvas.js:121-131, 156-160`） | 加克隆子树与多选 |
| **画布：触屏不可用** | `touch-action:none`（`collaboration.css:410`）+ 只处理 wheel（`team-canvas.js:191-204`）→ 不能缩放；reparent 仅 Shift+拖（`team-canvas.js:154,172`）→ 触屏无法改上级 | 加 pinch 缩放与触屏"设为上级"菜单 |
| **画布性能** | 拖动时每帧全量重建 `graphEdges` innerHTML + 逐节点 querySelector（`team-canvas.js:144,150`），节点上限 100（`teams.py:46`）→ 近百节点掉帧 | 边改为增量更新或用 canvas/SVG 单次重绘 |
| **焦点管理只按 id 恢复** | `ui.js:116-134`，而卡片按钮无 id（`views.js:297-306`）→ 重绘即失焦 | 用稳定的 data-key 恢复焦点 |
| **列表无分页/无删除** | 备份记录无上限（`engine.py:428-431`）且前端无分页（`views.js:440-448`）；子 Agent 列表无分页（`views.js:373-390`）；资源 picker 硬截断 60 项无"加载更多"（`team-controller.js:184-186`） | 加分页与"加载更多" |
| **清空组合无确认无撤销** | `app.js:152-156`、`views.js:335` | 加确认 + 撤销 toast |
| 无 i18n（仅中文字面量） | `state.js:2-13`、`index.html:2`（`lang="zh-CN"`） | 若需对外分享再考虑 |
| 主题不跟随系统 | `theme.js:2-4`、`index.html:2` 硬编码 light、`style.css` 无 `prefers-color-scheme` | 加 auto 档 |
| 无骨架屏 / 搜索无高亮 / toast 无手动关闭 / 对比度与最小字号 | `index.html:18-23`、`views.js:92`、`workflows.js:56`；`state.js:224-240`（grep 无 mark）；`ui.js:70-82`；`--muted #737378` on `--canvas #f6f6f7` ≈ 4.4:1（`style.css:4,10`）；最小字号 9px + opacity .8（`style.css:222-223`） | 打磨项 |

**XSS 专项结论：未发现可利用的注入。** 41 处 `innerHTML` 赋值点逐个核对（`ui.js:62,91`；`views.js:28,50,112,130,246,251`；`app.js:276,474`；`dialogs.js:15,23,24,28,35,42,49,56,63,70,78`；`team-view.js:12,70,92,120,134,150,187,228`；`team-controller.js:52-56,138,181,183,226,296`；`execution.js:100,131,139,168`；`workflows.js:45,102,135,152,168`）：所有用户可控文本（catalog 字段、agent.name、node.name/prompt/policy、run.title/prompt/log/result/error、issues、plan.errors/warnings、导入的 profile/team JSON、localStorage 草稿）均经 `e()`（`ui.js:48-53`）或 `textContent` 写入；CSP `script-src 'self'` 无 `unsafe-inline`（`server.py:86-89`）另阻断内联事件与 `javascript:` URL。
遗留 P3（当前不可利用，属编码不一致）：`views.js:299,300,306,385,386` 与 `dialogs.js:28,29` 的 `data-id="${x.id}"` 未包 `e()`（id 为 sha256 前 24 位十六进制，`catalog.py:24-25,404,426,615`）；`ui.js:65-66` `showModal` 的 body/footer 按契约不转义（唯一注入面，调用点已全部核对）；`views.js:155,185-186,488` 的 heading/stat/empty 参数未转义但调用点全为字面量；`app.js:315` `state[d.remove].delete(d.id)` 用 dataset 直接索引 state 无白名单 → 建议前置 `['skills','mcps','agents'].includes(d.remove)`。

**监听器泄漏：未发现。** document/window 级监听器均只注册一次（`app.js:299,389,397,440,453,459,478`；`team-controller.js:388,459,479,533,544`）；ResizeObserver 已 disconnect（`team-canvas.js:77-81`）；model-picker 用 WeakMap binding + isConnected 防串扰（`model-picker.js:41-42,68,72,75`）；轮询用 epoch/generation 防竞态（`execution.js:31,225,247`；`workflows.js:24,199,218`；`app.js:46,51`）。唯一残留：`team-canvas.js:109-212` 的 6 个监听器从不显式 `removeEventListener`，靠元素丢弃回收（P3）。

### 3.4 服务与桌面

| 缺口 | 证据 | 最小补法 |
| --- | --- | --- |
| **写锁粒度粗** | `service.py:102-103` 所有资源写操作串行在 `catalog.lock`，且 `update_settings` 在锁内跑整轮 scan（`service.py:105-111`，扫描目录上限 30000，`catalog.py:547`）；执行/调度反而完全绕开该锁（`service.py:91-98`） | 拆分扫描锁与写锁 |
| **关闭可能挂死** | `daemon_threads=False`（`server.py:20`）+ worker `daemon=False`（`execution.py:225`、`scheduling.py:214`），close 只 join 20/25 s（`execution.py:392-393`、`scheduling.py:321-322`）→ 超时后进程不退、端口占用、`runtime.json` 不删（`launcher.py:113-122` 在 server_close 之后） | join 超时后强制终止并清理 runtime.json |
| **token 暴露面** | `runtime.json` 明文（`launcher.py:92,107`）+ token 以 URL fragment 出现在 Edge 命令行（`launcher.py:126,133`）→ 同用户任意进程可直接调 `/api/runs/start`，以 workspace-write 在任意目录跑 CLI | 改用 DPAPI 保护 token，或加"本次会话仅允许已确认的目录白名单" |
| **托盘只认 Edge `--app` 窗口** | 硬编码标题 + `Chrome_WidgetWin_1` + `msedge.exe`（`desktop.py:16, 53-59`）；Edge 不在 `PROGRAMFILES(X86)` 默认路径时回退默认浏览器（`launcher.py:127-137`）→ 回退后"打开工坊"每次新开标签、"最小化窗口"失效（`desktop.py:205-220, 236-240`）；标题与 `static/index.html:7` 强耦合，改标题即静默失效 | 记录自己 Popen 的 pid，或注入唯一标题后缀 |
| **退出取消不可靠** | EXIT → `request_shutdown`（`desktop.py:166-172` → `launcher.py:99`）；执行线程卡在 `process.wait`（`execution.py:345`）或 close join 超时即挂住；`stop(close_windows=True)` 只 PostMessage 不等待（`desktop.py:247-254`、`launcher.py:111-112`） | 退出流程加超时兜底 + 强制终止进程树 |
| **无崩溃恢复 / 看门狗** | 崩溃后 `runtime.json` 残留（仅正常 finally 删除，`launcher.py:113-122`）；`find_running` 只做 health 探测（`launcher.py:59-80`）；版本不一致直接抛错要用户手工退出（`launcher.py:171-175`），**未使用 runtime.json 里的 pid**；无 atexit/signal 处理 | 用 pid + 启动时间戳识别陈旧 runtime.json 并自动接管 |
| **无开机自启** | `winreg` 仅出现在 `discovery.py:44-65`（读卸载信息），`launcher.py` 无 Run 键/Startup/计划任务写入 | 可选设置项 |
| **静态资源 `no-store` 且无 ETag/gzip** | `server.py:83, 134-147` | 加 ETag/Last-Modified |
| **访问日志完全禁用** | `server.py:63-65` → 无审计线索 | 脱敏后记录方法+路径+状态 |
| **鉴权不对称 / 细节** | GET 只需 cookie（`server.py:110-118`），POST 额外要 `X-Workbench`（`server.py:150`）；`/api/health` 无认证回版本（`server.py:114-115`）；`/api/auth` 不检查 closing（`server.py:162-172` 早于 175）。Host/Origin 校验完整（`server.py:91-97`）、cookie SameSite=Strict（`server.py:171`）→ **CSRF 面基本可控** | 统一校验顺序 |
| **日志轮转不彻底** | `execution.log` 16 MB 上限确已实现（`execution.py:20, 265-272`），但只按**本次进程内存计数** `bytes_written`（`execution.py:255`），不读已有文件大小；`server.log` 以 `"ab"` 追加无上限（`launcher.py:190`） | 启动时读现有文件大小；server.log 加上限 |
| **URL 未解码** | 全包 `unquote` **零命中**，而路由用 `path.rsplit("/", 1)[-1]` 直接当 id（`service.py:66,72,77,82`）；前端 `model-picker.js:28` 用了 `encodeURIComponent` | 当前 id 均为 hex 故不触发；若将来 id 含非 ASCII 会 404 → 统一 `unquote` |
| 无速率限制/并发上限/HEAD/OPTIONS；认证后 GET 回绝对目录（`execution.py:164`、`scheduling.py:153`） | `server.py` | P3 |
| 托盘菜单无"取消当前任务"（`desktop.py:137-147`）；图标无 NIF_GUID（`desktop.py:86-96`）；MessageBox 在托盘线程内模态阻塞消息泵（`desktop.py:167-171`）；端口冲突静默换随机端口且不告知（`launcher.py:88-91`） | — | P3 |

### 3.5 健壮性 / 跨环境

- `engine.py:208` 硬编码 `"SKILL.md"`，扫描侧却按平台取 `"skill.md"/"SKILL.md"`（`catalog.py:588-590`）→ 大小写敏感文件系统上恒报"定义已变化"。
- `catalog.py:597-598` 排除目录名未 casefold（`excluded` 是小写集合，`catalog.py:528-538`）→ `Node_Modules`/`LOGS` 不被排除。
- **超长路径 >260 无处理**：`\\?\`、`MAX_PATH`、`LONG_PATH` 全包零命中，仅 `discovery.py:122` 有 `WCHAR*260` 进程名缓冲（长路径进程名截断致 `_path_agent` 漏判）；`tree_files`/`copytree` 抛的 OSError 被 `engine.py:248` 压成含糊文案。
- **云占位文件无检测**：`discovery.py:91-102` 只枚举 `GetDriveTypeW==3`（固定盘）→ 网络盘/移动盘上的客户端不发现；`catalog.py:526-601` 与 `resources.py:57-86` 对未水合的 OneDrive 占位文件会触发按需下载（长阻塞）或 OSError，无 reparse / `FILE_ATTRIBUTE_RECALL_*` 检测。项目本身就位于 `D:\OneDrive\` 命名目录下。
- **只读文件/权限拒绝无前置检查**：`resources.py:23-25` `assert_target` 只查链接与路径身份 → 预览通过、apply 才失败回滚（`engine.py:391-419`）。
- `storage.py:17-20` `read_json` 不捕获异常（损坏 JSON、目录路径直接抛；`engine.py:469` restore 未包裹）→ 500 而非可读提示。
- `storage.py:23-38` **无 flush/fsync**（只对 PermissionError 重试 5 次/0.4s），而 `engine.py:379-381` 注释声称 "Journal before mutation" → 断电时"硬中断可恢复"的承诺不成立。
- `discovery.py:253-260` `shutil.which` 在内层重复调用；`bases`（所有固定盘 × 6 目录，238-251）× PROGRAMS × names × folders 三重 stat 循环 → 机械盘/云盘上扫描显著变慢。
- `discovery.py:15-37` 的 PROGRAMS 不含 gemini/cline/roo → 独立安装的 Gemini CLI 恒显"仅发现配置"（只有 npm 入口 265-276 或扩展文件 278-295 能证明安装）。
- `catalog.py:191-197` `scan_status` 读 `self.snapshot` 不在保护锁内 → cached/ready 瞬时不一致。
- `adapters.py:337-345` 的变量/远程 env 检查只在 `translate` 内，同 agent 同 kind 的直通路径（`engine.py:123-133`）**不检查 `${VAR}`**。
- 跨平台：实质仅 Windows。`discovery.py:42,92,152` 三处 `os.name != "nt"` 直接返回空；`catalog.py:553,589` 按 nt 分支；数据目录基于 APPDATA/LOCALAPPDATA（`catalog.py:77-81`）；`desktop.py` 强依赖 win32gui/pywintypes。**文档未声明非 Windows 的支持状态**。

---

## 4. C 类：工程与交付基建

| 缺口 | 证据 | 级别 | 最小补法 |
| --- | --- | --- | --- |
| 无 git 仓库 / 无 CI | `.git`、`.github` 均不存在 | **P1** | 见 P1-8 |
| 无 LICENSE | 全盘 grep 零命中 | **P1** | 选定协议并落盘（分发前提） |
| 版本号三处手工同步、无校验 | `agent_workbench/__init__.py:1`、`README.md:1`、`static/views.js:36`（硬编码 "Agent 工坊 · 0.4.6"）；`server.py:115` health 回 `__version__` | P2 | `build_windows.py` 加三处一致性断言 |
| 分享包无版本名 + 根目录残留旧 zip | 根目录并存 `AgentWorkbench-0.3.1-Windows.zip`（zip 内 README 首行 0.3.1，exe 17:03，83 项）、`AgentWorkbench-0.4.2-Windows.zip`（0.4.2，exe 19:51，95 项）与无版本名的当前包（0.4.6，98 项） | P2 | 输出带版本名，构建后清理旧包 |
| exe 无 VERSIONINFO | 实测 FileVersion/ProductVersion 为空；`AgentWorkbench.spec` 无 `version=`，`build_windows.py` 无 `--version-file` | P2 | 生成 `version.txt` 并传入 |
| 无自动 SHA-256 清单 | 校验和只手写在 `releases/0.4.6/verification/release.json` | P2 | 构建末尾自动生成 `SHA256SUMS.txt` |
| 无代码签名 | `AgentWorkbench.spec:33` `codesign_identity=None`，无 signtool 调用 | P2 | 有证书后接入；无证书则文档说明 SmartScreen 提示 |
| 构建/测试依赖未声明 | `PyInstaller`、`Pillow`（build_windows.py）、`httpx`（test_desktop/execution/teams/workbench）、`playwright`（6 个脚本）均不在 `requirements.txt`；无 `requirements-dev.txt`、无 `conftest.py` | P2 | 补 `requirements-dev.txt` |
| 依赖未钉版本 | `requirements.txt` 全为范围约束（`>=,<`），无 lock/constraints | P2 | 生成 `constraints.txt` 供构建使用 |
| 无覆盖率度量 | `pytest.ini` 仅 `testpaths` + `--tb=short`，无 `--cov`、无 `.coveragerc` | P2 | 加 pytest-cov 与阈值 |
| **`execution_process.py` 零测试** | `tests/*.py` 中 `ProcessTree\|execution_process` 零命中 → Job Object / 进程树终止无直接断言 | P2 | 用可控子进程树做终止断言 |
| **`workflows.js` 零测试** | `tests/*.js` 中 `workflows` 零命中；`npm test` 只跑 5 个文件（cursor-trust / model-picker / runner-status / state / team） | P2 | 抽出纯逻辑做单测 |
| **README:128 声称的浏览器测试入口跑不起来** | `tests/browser_review.js`、`tests/browser_teams.js` 首行即 `async (page) => {`（函数体），注释要求 "Playwright connector"，但仓库内无任何 connector/runner 脚本 | P2 | 补 runner 脚本，或修正 README |
| REVIEW.md 实为 changelog 却断层在 0.4.2 | `^## 0\.4\.[3-6]` 零命中；0.4.3–0.4.6 变更只在 README 顶部段落 | P2 | 拆出 `CHANGELOG.md` 并补齐 0.4.3–0.4.6 |
| 无 CONTRIBUTING / SECURITY / issue 模板 / 反馈渠道 | 全盘 grep 零命中；应用内无反馈入口 | P2 | 视是否开源决定 |
| 无更新检查 | 全仓无任何外网 http(s) 请求（仅 127.0.0.1）→ 用户只能手工解压新版 | P3 | 可选：手动"检查更新"按钮 |
| spec 与 build 脚本两套并存 | `AgentWorkbench.spec` 硬编码绝对路径 `D:\OneDrive\…`、被 `.gitignore`（`*.spec`）忽略，且 `build_windows.py` 根本不用它 | P3 | 二选一 |
| 过期残留 | `build/AgentWorkbench.exe`（16:31，0.3.x，VersionInfo 全空）；`releases/0.4.6/verification/Agent 工坊 0.4.5.lnk` 未改名 | P3 | 清理 |
| 文档表述偏差 | README:43「导入文件上限 2 MB」实为 `server.py:157` 对**所有 POST** 的 `length > 2_000_000`（≈1.91 MiB），非仅导入（`catalog.py:432` 另有单 Skill 文件 2 000 000 上限）；`EXECUTION.md:72` 验证示例仍引用 `releases/0.4.1`（现 0.4.6）；README:112「复用本机已安装的 tomlkit、PyYAML 和 json5」未提 `psutil`、`pywin32` | P3 | 修正表述 |

**文档与实现一致的声明（抽查 7 项全部对得上）**：端口 47831（`launcher.py:89` ↔ README:108）；日志 16 MB（`execution.py:20` ↔ `EXECUTION.md:49`）；节点上限 100（`teams.py:46` ↔ README:43）；Skill 100 MB / 10000 条目（`resources.py:11-12,42,53` ↔ README:104）；每步默认 30 分钟、可调 1–120（`execution.py:187-188`、`scheduling.py:174` ↔ `EXECUTION.md:41`）；缓存路径 `cache/catalog.json`（`catalog.py:130` ↔ README:19,97）；Python 3.11+（`model_catalog.py:12` `import tomllib` 恰为 3.11 门槛，无 3.12+ 专属语法）。

---

## 5. 与用户提供的 NexusMind 素材（`E:\download\Edge\agent.html`，103 729 字节，仍存在）的功能对照

素材标题为「NexusMind · 多Agent协同编排工作台」。逐项核对工坊实现：

| 素材功能 | 工坊现状 | 缺口级别 |
| --- | --- | --- |
| 拖拽重叠**双模式**：【⇄ 交换位置与角色】/【↳ 挂载为TA的下级】 | 只有 Shift+拖的"挂载为下级"一种（`team-canvas.js:154, 172`）；**无"交换位置与角色"** | P2 |
| 「拖拽右侧锚点可重连父子线」 | 无锚点拖线（`anchor\|锚点\|relink\|重连` 在 static 零命中），只能在右侧下拉改汇报对象 | P3 |
| 「联动子树: 开启」开关 | 无开关，拖动永远带动子树（`subtree\|联动` 零命中） | P3 |
| 「Agent 层级组件库 · 点击或拖拽至画布」（Tutor/Employee/Sub-Agent 三类组件） | 只有"添加节点"按钮（`team-view.js:38`），角色按父节点自动推断（`team-controller.js:256`：父为 tutor → employee，否则 sub），**不能拖到画布任意位置、不能直接指定角色** | P3 |
| 「节点图标标识」字段 | 图标由角色固定（`team-view.js:82` `icon(role.icon)`），**不可自定义** | P3 |
| 「角色层级（点击实时转换身份）」 | 已实现（`team-view.js:154` role-switch） | ✓ |
| 水平导图排版 / 垂直架构排版 | 已实现（`team-view.js:40` direction） | ✓ |
| 全部展开 / 居中视图 | 已实现（`team-view.js:41, 49`） | ✓ |
| 思维导图层级大纲 | 已实现（`team-view.js:46`），但**大纲不反映折叠状态**（`team-view.js:120-130`） | P3 |
| 启动协同推演 / 协同通信日志 | 已实现（流程预演 + 流程动态，`team-view.js:33, 17-18`） | ✓ |
| 导出拓扑 JSON / 复制 JSON 配置 | 已实现（`team-controller.js:235-242`），另有 Markdown 任务说明 | ✓ |
| 驱动基座模型（LLM Backbone） | 已实现且更强（随客户端联动的模型下拉 + 刷新，`model-picker.js`） | ✓ |
| 核心职责与 System Prompt | 已实现（`teams.py:89` prompt 上限 10000 字） | ✓ |
| 上级汇报对象（含"顶层独立根节点"） | 已实现 | ✓ |
| 预设多 Agent 协同模板 ×3 | 已实现（`examples/` 三份，9/6/6 节点） | ✓ |
| **素材没有、工坊额外做到的** | 真实执行与自动调度、装配/备份/恢复、资源盘点、模型目录、托盘 — 素材只是静态编排原型 | — |

结论：**编排语义已完整对齐并超出素材；差的是画布直接操纵手感**（交换模式、锚点重连、子树联动开关、组件库拖入、自定义图标）。

---

## 6. 有意不做的设计边界（行为与文档一致，不计入缺失）

以下均在 README / EXECUTION / REVIEW 中明确声明，且代码行为与文案一致，列出以免与真实缺口混淆：

- DeepSeek Harness 的 MCP 全程只读（`adapters.py:193-194, 267-268`、`engine.py:90-95`，`mcp_writable=False`）；Cordis 动态 YAML 不求值、不导入（`dsh.py:14, 46-49`）。
- 项目级 / 工作区级配置只盘点不写回（`catalog.py:374-389`、`engine.py:296`）。
- 插件变量不迁移、插件启用状态未核验（`catalog.py:336, 583`、`engine.py:110-122, 243-247`）。
- Claude Desktop 只收 stdio（`adapters.py:172, 329-330`）；Claude Desktop / VS Code 无 Skill 目录，装 Skill 直接报错（`adapters.py:166-173, 209-217`、`engine.py:195-196`）。
- 不启动 MCP、不验证连通性（`engine.py:298`、`EXECUTION.md:77`）；取消勾选不卸载 Skill（`engine.py:297`、README:81）。
- 旧版本备份拒绝跨版本恢复（`engine.py:462, 470-473`、README:73）。
- 装配作用于客户端全局配置，不创建独立运行环境；多节点绑定同一客户端时共享配置（README:39）。
- 自动调度不自动创建 Git 分支、不合并用户项目代码（`EXECUTION.md:35`）。
- 「已完成」只表示所有步骤正常返回，不代表产物通过业务验收（README:53、`EXECUTION.md:35`）。
- 跨文件修改不提供操作系统级原子性（README:106）。
- 子 Agent 角色页只读（README:82）。

---

## 7. 建议修复路线

**第一批（正确性与安全，预计改动量小、风险低）**
1. `adapters.py:376` UnboundLocalError（P1-1）+ http/sse→zcode 单测
2. `execution.py:278-296` 行缓冲与超长行（P1-2）
3. `model_catalog.py:185-191` 改差量写 + 启动扫描清理残留密钥（P1-3）
4. `runners.py:285-292` Codex item error 置 failed（P1-4）
5. `engine.py:102` 目标解析纳入 `extra_configs`（P1-5）
6. `resources.py:115-121` + `engine.py:400-403` 残留路径可寻（P1-7）
7. `storage.py:23-38` 加 flush/fsync；`engine.py:89-251` 的 try 收窄到单资源项

**第二批（可诊断性与数据出口）**
8. 引入 `logging`（脱敏写 `server.log`）+ 错误编号，恢复最小访问日志（P2）
9. runs / workflows / backups 的删除与保留策略接口 + 前端"仅最近 100 条"提示
10. `library/mcp|skill` 删除接口；Profile bundle 导入导出
11. `api.js` 超时 + 401 处理；busy 锁放行取消；boot 失败重试
12. `execution_process.py` 与 `workflows.js` 补测试；修 `browser_*.js` 运行入口或修正 README:128

**第三批（能力扩展，按价值排序）**
13. 接入本机已存在的 `claude.exe` 作为第 5 个执行客户端
14. 审核 verdict 结构化 + 单节点重试 / 从此步重跑 / 断点续跑
15. 执行前基线快照 + diff 报告 +（可选）Git worktree 隔离（解 P1-6）
16. 同层并行执行（多槽执行池）
17. MCP 连通性测试、用量/成本聚合、节点级 cwd
18. 画布：撤销/重做、autoLayout 不破坏手动坐标、交换位置与角色、触屏支持

**第四批（工程基建，一次性）**
19. `git init` + LICENSE + `CHANGELOG.md`（补 0.4.3–0.4.6）+ 版本一致性断言
20. 构建带版本名 + 自动 SHA256SUMS + VERSIONINFO；`requirements-dev.txt` + 钉版本；覆盖率配置
21. 清理根目录旧 zip、`build/` 过期残留；评估用 git tag 替代 `releases/` 全量快照（可回收约 633 MB）

---

## 附录：本次审计使用的只读验证命令

```powershell
# 测试实跑（写入隔离目录）
python -m pytest -q --tb=line --basetemp=.test-artifacts/pytest-gap-audit   # → 138 passed
npm test                                                                     # → 27 pass

# 版本与交付物核对
Get-Content agent_workbench\__init__.py                                      # __version__ = "0.4.6"
Get-FileHash dist\AgentWorkbench\AgentWorkbench.exe -Algorithm SHA256
Get-FileHash AgentWorkbench-Windows.zip -Algorithm SHA256
# zip 只读打开取内部 README 首行（System.IO.Compression.ZipFile::OpenRead）

# 环境核对
Test-Path .git ; Test-Path .github ; Test-Path LICENSE
Get-Command claude,gemini,codex,cursor-agent,dsh,node -ErrorAction SilentlyContinue
Get-ChildItem $env:LOCALAPPDATA\AgentWorkbench                               # 仅 0 字节 server.log
WScript.Shell.CreateShortcut('D:\OneDrive\Desktop\Agent 工坊.lnk').TargetPath
```

穷举搜索关键字（用于"确认没有"的判断，均在 `agent_workbench/**` 内执行）：
`TODO|FIXME|NotImplementedError` · `unquote` · `rmtree|purge|prune|cleanup` · `diff` · `git|branch` · `queue|ThreadPool|asyncio|parallel|并行` · `retry|重试` · `cron|schedule_at|trigger|定时` · `health|connectivity|ping|tools/list|initialize` · `uninstall|remove_skill|delete_skill` · `version|semver|upgrade` · `dependenc` · `export|import_|bundle|zip|archive|share` · `msvcrt|fcntl|lockf|portalocker|FileLock` · `logging|traceback|logger` · `EventSource|WebSocket|AbortController` · `clipboard|undo|anchor|锚点|swap|交换|subtree|联动` · `\\?\|MAX_PATH|LONG_PATH`
