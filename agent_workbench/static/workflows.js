import { api } from '/api.js';
import { e, icon, toast, syncMarkup } from '/ui.js';
import { editor, exportTeam, previewSteps, storeTeamDraft } from '/team-model.js';
import { selectExecution } from '/execution.js';
import { state, agentName } from '/state.js';
import { executionBindings } from '/runner-status.js';
import { cursorTrustMarkup, mountCursorTrust } from '/cursor-trust.js';
import { directoryExtras, rememberDirectory } from '/directories.js';

const statuses = {
  waiting: '等待执行',
  starting: '正在启动',
  running: '运行中',
  cancelling: '正在取消',
  succeeded: '已完成',
  failed: '失败',
  cancelled: '已取消',
  timed_out: '已超时',
  interrupted: '已中断',
  blocked: '未执行',
};
const phases = { assign: '分派', deliver: '执行', review: '审核汇总' };
const active = (row) => ['starting', 'running', 'cancelling'].includes(row?.status);
let selected = null;
let epoch = 0;
let timer = null;
let pending = null;
let starting = false;
let rows = [];
let snapshot;
let checkingRunners = true;
let runnerError = '';
let historyQuery = '';
let historyPage = 1;
let historyMeta = { total: 0, page: 1, pages: 1 };
let request = 0;
let chooseWorkflow = () => {};

export function openWorkflow(id) {
  chooseWorkflow(id);
}

function draft() {
  try {
    return JSON.parse(localStorage.getItem('awb-workflow-settings') || '{}');
  } catch {
    return {};
  }
}

export function workflowsPage() {
  snapshot = exportTeam(editor.team);
  const settings = draft();
  const steps = previewSteps(snapshot);
  return `<section class="execution-page">
    <div class="page-head" data-page="workflows">
      <div class="page-avatar">${icon('play')}</div>
      <div class="page-titles"><h1>让协作方案开始执行</h1>
        <p class="page-handle">AUTOMATIC COLLABORATION · 分派任务 → 逐步执行 → 审核汇总，失败即停止。</p></div>
    </div>
    <div class="execution-layout"><section class="execution-compose settings-card">
      <div class="section-title"><h2>${e(snapshot.name)}</h2><button class="btn small" data-tab="teams">返回画布</button></div>
      <p class="muted">使用当前画布的独立快照，后续编辑不会改变已启动的任务。</p>
      <p>${e(snapshot.goal || '请返回画布填写总目标、各节点职责，并绑定执行客户端。')}</p>
      <details class="execution-options"><summary>${snapshot.nodes.length} 个节点 · ${steps.length} 次调用</summary>
        <ol>${steps.map((s) => `<li>${e(s.name)} · ${e(phases[s.kind])}</li>`).join('')}</ol></details>
      <section class="workflow-readiness" aria-label="节点执行检查">
        <div class="section-title"><h3>节点执行检查</h3><button class="btn small" id="workflow-check-runners" type="button">重新检查</button></div>
        <p id="workflow-binding-summary" role="status" class="muted">正在检查本机执行入口…</p>
        <div id="workflow-bindings"></div>
        <p class="muted">每个节点都参与调用，包括负责人和审核节点。点击节点可修改绑定；登录和额度由客户端在运行时检查。</p>
      </section>
      <form id="workflow-form">
        <label class="field">共同工作目录<input id="workflow-cwd" name="cwd" required maxlength="4096" value="${e(settings.cwd || '')}" placeholder="选择已有项目目录，或填写完整路径"></label>
        ${directoryExtras('workflow-cwd')}
        <label class="field">执行权限<select name="permission">
          <option value="read-only" ${settings.permission !== 'edit' ? 'selected' : ''}>只读 / 规划</option>
          <option value="edit" ${settings.permission === 'edit' ? 'selected' : ''}>允许编辑</option>
        </select><small>Codex / DeepSeek 使用文件沙箱；Cursor / Zcode 使用客户端规划或编辑模式。工具操作仍遵守各客户端权限。</small></label>
        <details class="execution-options"><summary>运行选项</summary>
          <label class="field">每一步最长运行时间（分钟）<input name="timeout_minutes" type="number" min="1" max="120" value="${e(settings.timeout_minutes || 30)}"></label>
        </details>
        ${cursorTrustMarkup}
        <div class="execution-submit"><button class="btn primary" id="workflow-start" type="submit" disabled>${icon('play')} 开始自动调度</button>
          <p class="execution-hint" id="workflow-hint">按画布层级串行执行，共用工作目录。审核节点会列出需修改项；当前不会自动打回或重试。需要修改时，请调整任务后再次手动执行。所有步骤正常返回只表示流程跑完，不代表业务验收通过。</p></div>
        <p class="notice error" id="workflow-start-error" hidden></p>
      </form>
      <p class="muted">画布上的 Skill / MCP 是引用，开始调度不会自动装配。多个节点绑定同一客户端时共享该客户端的全局配置。账号和额度由客户端在运行时检查。</p>
    </section><div class="execution-monitor">
      <section class="settings-card"><div class="section-title"><h2>查找协作记录</h2><button id="workflow-refresh" class="btn small" type="button">刷新</button></div>
        <label class="history-search">搜索<input id="workflow-query" type="search" placeholder="方案、目录或日期" aria-label="搜索协作执行记录"></label>
        <p id="workflow-history-note" class="muted">读取记录…</p>
        <nav class="history-pager" aria-label="协作记录分页"><button type="button" class="btn small" id="workflow-prev">上一页</button><span id="workflow-page-label">第 1 / 1 页</span><button type="button" class="btn small" id="workflow-next">下一页</button></nav>
        <p class="muted">记录列表在对象列。窄屏先点顶部的「对象列表」。</p></section>
      <section class="settings-card" id="workflow-detail"><p class="muted">开始调度或选择历史记录。</p></section>
    </div></div></section>`;
}

function updateStartButton() {
  document.getElementById('workflow-start').disabled =
    starting ||
    rows.some(active) ||
    checkingRunners ||
    !!runnerError ||
    !snapshot.goal.trim() ||
    executionBindings(snapshot, state.runners).some(
      ({ node, kind }) => kind !== 'ready' || !node.prompt.trim(),
    );
}

function renderReadiness() {
  const bindings = executionBindings(snapshot, state.runners);
  const missing = bindings.filter(({ node, kind }) => kind !== 'ready' || !node.prompt.trim());
  document.getElementById('workflow-binding-summary').textContent = checkingRunners
    ? '正在检查本机执行入口…'
    : runnerError ||
      (!snapshot.goal.trim()
        ? '请返回画布填写总目标。'
        : missing.length
          ? `${missing.length} 个节点的调用入口或职责未完成。`
          : `${bindings.length} 个节点已找到调用程序。这不表示账号已登录，也不表示 Skill / MCP 已写入客户端。`);
  document.getElementById('workflow-bindings').innerHTML = bindings
    .map(({ node, kind, label, detail }) => {
      const needsPrompt = !node.prompt.trim();
      const resources = `${node.skills.length} 个 Skill 引用 · ${node.mcps.length} 个 MCP 引用`;
      return `<button type="button" class="execution-run workflow-binding ${kind !== 'ready' || needsPrompt ? 'needs-config' : ''}" data-workflow-config-node="${e(node.id)}">
      <span><strong>${e(node.name)}</strong><small>${e(node.agentId ? agentName(node.agentId) : '未选择客户端')} · ${e(needsPrompt ? '请填写职责与交付要求。' : detail)} · ${e(resources)}</small></span>
      <span class="tag">${e(needsPrompt ? '缺少职责' : label)}</span></button>`;
    })
    .join('');
  document.getElementById('workflow-check-runners').disabled = checkingRunners;
  updateStartButton();
}

async function checkRunners(current) {
  checkingRunners = true;
  runnerError = '';
  renderReadiness();
  try {
    const runners = await api('runners');
    if (current === epoch) state.runners = runners;
  } catch (error) {
    if (current === epoch) {
      state.runners = null;
      runnerError = `无法检查执行入口：${error.message}`;
    }
  } finally {
    if (current === epoch) {
      checkingRunners = false;
      renderReadiness();
    }
  }
}

function renderRows() {
  const note = document.getElementById('workflow-history-note');
  if (note) {
    note.textContent = historyMeta.total
      ? `第 ${historyMeta.page} / ${historyMeta.pages} 页，共 ${historyMeta.total} 条协作记录。更早的记录仍在本机，可搜索或翻页后打开、下载。`
      : historyQuery
        ? '没有符合条件的协作记录。'
        : '尚无协作执行记录。';
  }
  const label = document.getElementById('workflow-page-label');
  if (label) label.textContent = `第 ${historyMeta.page || 1} / ${historyMeta.pages || 1} 页`;
  const prev = document.getElementById('workflow-prev');
  const next = document.getElementById('workflow-next');
  if (prev) prev.disabled = (historyMeta.page || 1) <= 1;
  if (next) next.disabled = (historyMeta.page || 1) >= (historyMeta.pages || 1);
  document.dispatchEvent(
    new CustomEvent('awb:workflow-rows', {
      detail: {
        rows: rows.map((row) => ({
          id: row.id,
          title: row.title,
          status: statuses[row.status] || row.status,
          created_at: row.created_at,
          steps: `${row.steps.filter((step) => step.status === 'succeeded').length}/${row.steps.length}`,
          running: active(row),
        })),
        selectedId: selected,
        page: historyMeta.page,
        pages: historyMeta.pages,
        total: historyMeta.total,
      },
    }),
  );
  updateStartButton();
}

function showRecord(record) {
  const target = document.getElementById('workflow-detail');
  if (target.dataset.id !== record.id) {
    target.dataset.id = record.id;
    target.innerHTML = `<div class="section-title"><h2 id="workflow-title"></h2><span class="tag" role="status" id="workflow-status"></span></div>
      <p id="workflow-meta" class="muted execution-location"></p><p class="notice error" id="workflow-error" hidden></p>
      <div class="execution-controls"><button class="btn" id="workflow-cancel">取消整个调度</button><button class="btn" id="workflow-download">下载汇总</button></div>
      <div id="workflow-steps"></div><h3>汇总结果</h3><pre class="execution-result" id="workflow-result"></pre>
      <p class="muted execution-location" id="workflow-location"></p>`;
  }
  document.getElementById('workflow-title').textContent = record.title;
  document.getElementById('workflow-status').textContent = statuses[record.status] || record.status;
  document.getElementById('workflow-meta').textContent =
    `${record.cwd} · ${record.permission === 'edit' ? '允许编辑' : '只读 / 规划'}`;
  const error = document.getElementById('workflow-error');
  error.hidden = !record.error;
  error.textContent = record.error;
  const cancel = document.getElementById('workflow-cancel');
  cancel.hidden = !active(record);
  cancel.disabled = record.status === 'cancelling';
  syncMarkup(
    document.getElementById('workflow-steps'),
    JSON.stringify(record.steps.map((step) => [step.run_id, step.status, step.launched])),
    record.steps
      .map(
        (step, index) =>
          `<button class="execution-run" type="button" data-focus-key="${e(step.run_id || index)}" data-workflow-run="${e(step.run_id)}" ${step.launched ? '' : 'disabled'}>
      <span><strong>${index + 1}. ${e(step.name)} · ${e(phases[step.phase])}</strong><small>${e(step.agent)} · 点击查看执行日志</small></span>
      <span class="tag">${e(statuses[step.status] || step.status)}</span></button>`,
      )
      .join(''),
  );
  document.getElementById('workflow-result').textContent =
    record.result ||
    (active(record) ? '调度完成后生成汇总，可点击已启动步骤查看实时日志。' : '没有汇总结果。');
  document.getElementById('workflow-location').textContent =
    `记录目录：${record.directory}。停止不会回滚已有文件修改。`;
  const download = document.getElementById('workflow-download');
  download.disabled = !record.result;
  download.textContent = record.result_truncated ? '下载显示部分' : '下载汇总';
  download.onclick = () => {
    const url = URL.createObjectURL(
      new Blob([record.result], { type: 'text/markdown;charset=utf-8' }),
    );
    const link = document.createElement('a');
    link.href = url;
    link.download = `collaboration-${record.id.slice(0, 8)}.md`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
}

async function poll(current) {
  const ticket = ++request;
  clearTimeout(timer);
  try {
    const params = new URLSearchParams({
      q: historyQuery,
      page: String(historyPage),
      page_size: '20',
    });
    const next = await api(`workflows?${params}`);
    if (epoch !== current || ticket !== request) return;
    rows = next.items || [];
    historyMeta = next;
    selected ||= rows[0]?.id;
    renderRows();
    if (selected) {
      const record = await api(`workflows/${selected}`);
      if (epoch === current && ticket === request) showRecord(record);
    }
  } catch (error) {
    if (epoch === current && ticket === request)
      document.getElementById('workflow-hint').textContent = error.message;
  } finally {
    if (epoch === current && ticket === request) {
      timer = setTimeout(() => poll(current), rows.some(active) ? 1000 : 4000);
    }
  }
}

export function unmountWorkflowsUI() {
  epoch++;
  chooseWorkflow = () => {};
  clearTimeout(timer);
}

export function mountWorkflowsUI() {
  unmountWorkflowsUI();
  const current = epoch;
  chooseWorkflow = (id) => {
    if (current !== epoch || !id) return;
    selected = id;
    poll(current);
  };
  checkingRunners = true;
  runnerError = '';
  document.getElementById('workflow-check-runners').onclick = () => checkRunners(current);
  document.getElementById('workflow-bindings').onclick = (event) => {
    const button = event.target.closest('[data-workflow-config-node]');
    if (!button || !editor.team.nodes.some((node) => node.id === button.dataset.workflowConfigNode))
      return;
    editor.selected = button.dataset.workflowConfigNode;
    editor.mode = 'canvas';
    editor.inspectorTab = 'config';
    storeTeamDraft();
    document.querySelector('[data-tab="teams"]').click();
  };
  const form = document.getElementById('workflow-form');
  const workspaceTrust = mountCursorTrust(form, () =>
    snapshot.nodes.some((node) => node.agentId === 'cursor'),
  );
  form.oninput = () => {
    pending = null;
    try {
      localStorage.setItem(
        'awb-workflow-settings',
        JSON.stringify({
          cwd: form.elements.cwd.value,
          permission: form.elements.permission.value,
          timeout_minutes: Number(form.elements.timeout_minutes.value),
        }),
      );
    } catch {
      /* The server keeps accepted snapshots. */
    }
  };
  form.onsubmit = async (event) => {
    event.preventDefault();
    if (document.getElementById('workflow-start').disabled) return;
    starting = true;
    renderRows();
    pending ||= crypto.randomUUID().replaceAll('-', '');
    try {
      const record = await api('workflows/start', {
        request_id: pending,
        team: snapshot,
        cwd: form.elements.cwd.value,
        permission: form.elements.permission.value,
        timeout_minutes: Number(form.elements.timeout_minutes.value),
        cursor_trusted_cwd: workspaceTrust.value(),
      });
      selected = record.id;
      pending = null;
      workspaceTrust.reset();
      rememberDirectory(form.elements.cwd.value);
      if (current === epoch) {
        document.getElementById('workflow-start-error').hidden = true;
        await poll(current);
      }
    } catch (error) {
      if (current === epoch) {
        const target = document.getElementById('workflow-start-error');
        target.hidden = false;
        target.textContent = error.message;
      }
    } finally {
      starting = false;
      if (current === epoch) renderRows();
    }
  };
  document.getElementById('workflow-refresh').onclick = () => poll(current);
  document.getElementById('workflow-query').oninput = (event) => {
    historyQuery = event.target.value;
    historyPage = 1;
    poll(current);
  };
  document.getElementById('workflow-prev').onclick = () => {
    historyPage = Math.max(1, historyPage - 1);
    poll(current);
  };
  document.getElementById('workflow-next').onclick = () => {
    historyPage += 1;
    poll(current);
  };
  document.getElementById('workflow-detail').onclick = async (event) => {
    const step = event.target.closest('[data-workflow-run]');
    if (step && !step.disabled) {
      selectExecution(step.dataset.workflowRun);
      document.querySelector('[data-tab="execution"]').click();
    }
    if (!event.target.closest('#workflow-cancel')) return;
    try {
      await api('workflows/cancel', { id: selected });
      if (current === epoch) await poll(current);
    } catch (error) {
      toast(error.message, true);
    }
  };
  checkRunners(current);
  poll(current);
}
