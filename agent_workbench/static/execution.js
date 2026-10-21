import { api } from '/api.js';
import { e, icon, toast } from '/ui.js';
import { mountModelPicker } from '/model-picker.js';
import { cursorTrustMarkup, mountCursorTrust } from '/cursor-trust.js';
import { directoryExtras, rememberDirectory } from '/directories.js';

const labels = {
  starting: '正在启动',
  running: '运行中',
  cancelling: '正在取消',
  succeeded: '已完成',
  failed: '执行失败',
  cancelled: '已取消',
  timed_out: '已超时',
  interrupted: '已中断',
};
const active = (run) => ['starting', 'running', 'cancelling'].includes(run?.status);
const draftKey = 'awb-execution-draft';
let draft = {
  agent: 'codex',
  prompt: '',
  cwd: '',
  sandbox: 'read-only',
  model: '',
  timeout_minutes: 30,
};
let initialized = false;
let selectedId = null;
let rows = [];
let runners = [];
let timer = null;
let generation = 0;
let pendingId = null;
let starting = false;
let workspaceTrust = null;
let historyQuery = '';
let historyKind = 'all';
let historyPage = 1;
let historyMeta = { total: 0, page: 1, pages: 1, query: '', kind: 'all' };
let request = 0;
let chooseRun = () => {};

export function openExecution(id) {
  chooseRun(id);
}

export function selectExecution(id) {
  selectedId = id;
}

function loadDraft() {
  if (initialized) return;
  initialized = true;
  try {
    const saved = JSON.parse(localStorage.getItem(draftKey) || '{}');
    for (const key of Object.keys(draft)) {
      if (typeof saved[key] === typeof draft[key]) draft[key] = saved[key];
    }
  } catch {
    // An invalid browser draft does not affect execution records.
  }
}
function saveDraft() {
  try {
    localStorage.setItem(draftKey, JSON.stringify(draft));
  } catch {
    // The current in-memory draft remains usable.
  }
}

export function executionPage() {
  loadDraft();
  return `<section class="execution-page is-empty">
    <div class="page-head" data-page="execution">
      <div class="page-avatar">${icon('terminal')}</div>
      <div class="page-titles"><h1>让 Agent 开始工作</h1>
        <p class="page-handle">AGENT EXECUTION · 选择目录，写下任务，确认权限后开始。</p></div>
      <div class="page-actions"><button class="btn" data-tab="teams">${icon('network')} 多 Agent 协作</button></div>
    </div>
    <div class="execution-layout"><section class="execution-compose settings-card">
      <div class="section-title"><h2>新任务</h2><span id="runner-status" class="muted">检查调用入口…</span></div>
      <form id="execution-form">
        <label class="field">执行 Agent<select id="execution-agent" name="agent"><option value="codex">Codex CLI</option></select>
          <small id="runner-note">复用本机已有登录与配置。找到调用入口不等于账号已登录。</small></label>
        <label class="field">工作目录<input id="execution-cwd" name="cwd" required maxlength="4096"
          placeholder="选择已有项目目录，或填写完整路径" value="${e(draft.cwd)}"><small>任务会在这个目录中运行。</small></label>
        ${directoryExtras('execution-cwd')}
        <label class="field">任务<textarea id="execution-prompt" name="prompt" required maxlength="30000" rows="4"
          placeholder="描述要完成的工作，以及你希望得到的结果…">${e(draft.prompt)}</textarea></label>
        <label class="field">文件权限<select id="execution-sandbox" name="sandbox">
          <option value="read-only" ${draft.sandbox === 'read-only' ? 'selected' : ''}>只读文件</option>
          <option value="workspace-write" ${draft.sandbox === 'workspace-write' ? 'selected' : ''}>允许修改工作目录</option>
        </select><small id="execution-permission-note">权限含义随所选客户端显示。</small></label>
        <details class="execution-options"><summary>运行选项</summary>
          <label class="field">模型（可选）<select name="model" id="execution-model"><option value="">沿用客户端当前模型</option></select>
            <small id="execution-model-note">读取模型目录…</small></label>
          <button class="text-link" type="button" id="execution-model-refresh">刷新模型列表</button>
          <label class="field">最长运行时间（分钟）<input name="timeout_minutes" type="number" min="1" max="120" value="${e(draft.timeout_minutes)}"></label>
        </details>
        ${cursorTrustMarkup}
        <p id="execution-readiness" class="execution-readiness" role="status">正在检查调用入口和权限。</p>
        <div class="execution-submit"><button id="execution-start" class="btn primary" type="submit" disabled>${icon('play')} 开始执行</button>
          <p class="execution-hint" id="execution-start-hint">任务会使用所选客户端账号的额度。</p></div>
      </form>
    </section><div class="execution-monitor">
      <section class="settings-card execution-history"><div class="section-title"><h2>查找记录</h2>
        <button type="button" id="execution-refresh" class="btn small">${icon('refresh')} 刷新</button></div>
        <div class="history-tools"><label class="history-search">搜索<input id="execution-query" type="search" placeholder="任务、目录或日期，例如 2026-10-21" aria-label="搜索执行记录"></label>
          <label class="history-search">范围<select id="execution-kind" aria-label="区分单任务和协作步骤"><option value="all">全部记录</option><option value="task">仅单任务</option><option value="step">仅协作步骤</option></select></label></div>
        <p id="execution-history-note" class="muted">读取记录…</p>
        <nav class="history-pager" aria-label="执行记录分页"><button type="button" class="btn small" id="execution-prev">上一页</button><span id="execution-page-label">第 1 / 1 页</span><button type="button" class="btn small" id="execution-next">下一页</button></nav>
        <p class="muted">记录列表在对象列。窄屏先点顶部的「对象列表」。</p></section>
      <section class="settings-card execution-detail" id="execution-detail"><p class="muted">提交任务或选择一条记录。</p></section>
    </div></div></section>`;
}

function renderRows() {
  const compact =
    !historyQuery && historyKind === 'all' && !historyMeta.total && !rows.some(active);
  document.querySelector('.execution-page')?.classList.toggle('is-empty', compact);
  const note = document.getElementById('execution-history-note');
  if (note) {
    note.textContent = historyMeta.total
      ? `第 ${historyMeta.page} / ${historyMeta.pages} 页，共 ${historyMeta.total} 条。对象列显示这一页；更早的记录可搜索或翻页后打开、下载。`
      : historyQuery || historyKind !== 'all'
        ? '没有符合条件的记录。更早的记录仍在本机，可换任务、目录或日期再找。'
        : '还没有执行记录。任务完成后会出现在对象列。';
  }
  const label = document.getElementById('execution-page-label');
  if (label) label.textContent = `第 ${historyMeta.page || 1} / ${historyMeta.pages || 1} 页`;
  const prev = document.getElementById('execution-prev');
  const next = document.getElementById('execution-next');
  if (prev) prev.disabled = (historyMeta.page || 1) <= 1;
  if (next) next.disabled = (historyMeta.page || 1) >= (historyMeta.pages || 1);
  document.dispatchEvent(
    new CustomEvent('awb:execution-rows', {
      detail: {
        rows: rows.map((row) => ({
          id: row.id,
          title: row.title,
          status: labels[row.status] || row.status,
          created_at: row.created_at,
          workflow: Boolean(row.workflow_id),
          running: active(row),
        })),
        selectedId,
        page: historyMeta.page,
        pages: historyMeta.pages,
        total: historyMeta.total,
      },
    }),
  );
}
function updateSubmit() {
  const button = document.getElementById('execution-start');
  if (!button) return;
  const busy = rows.some(active);
  const model = document.getElementById('execution-model');
  button.disabled =
    starting ||
    busy ||
    !runners.some((r) => r.id === draft.agent && r.available) ||
    (!!draft.model && model?.dataset.loading === 'true');
  document.getElementById('execution-start-hint').textContent = busy
    ? '已有任务运行中。完成或取消后可提交下一个任务；切换页面不会停止任务。'
    : '任务会使用已登录账号的额度。所选目录及任务内容会交给 Agent 处理。';
}

function showRunner() {
  const form = document.getElementById('execution-form');
  if (!form) return;
  const runner = runners.find((r) => r.id === draft.agent);
  form.elements.agent.innerHTML = runners
    .map(
      (r) =>
        `<option value="${e(r.id)}" ${r.id === draft.agent ? 'selected' : ''}>${e(r.name)}${r.available ? '' : '（未找到）'}</option>`,
    )
    .join('');
  const modes = runner?.modes || [];
  if (!modes.some((m) => m.id === draft.sandbox)) draft.sandbox = modes[0]?.id || '';
  form.elements.sandbox.innerHTML = modes
    .map(
      (m) =>
        `<option value="${e(m.id)}" ${m.id === draft.sandbox ? 'selected' : ''}>${e(m.name)}</option>`,
    )
    .join('');
  mountModelPicker(
    form.elements.model,
    document.getElementById('execution-model-note'),
    document.getElementById('execution-model-refresh'),
    draft.agent,
    draft.model,
    updateSubmit,
  );
  document.getElementById('runner-status').textContent = runner?.available
    ? '调用入口已找到'
    : '未找到调用入口';
  document.getElementById('runner-note').textContent =
    runner?.path || '请检查客户端安装，再刷新入口。';
  document.getElementById('execution-permission-note').textContent = runner?.note || '';
  const readiness = document.getElementById('execution-readiness');
  const permission =
    document.getElementById('execution-sandbox')?.selectedOptions?.[0]?.textContent || '';
  if (readiness) {
    readiness.textContent = runner?.available
      ? `调用入口已找到（${runner.name}）。文件权限：${permission}。这不表示账号已登录或额度充足。本页不会安装 Skill 或 MCP，任务使用该客户端当前已经应用的全局配置。`
      : '未找到调用入口，还不能开始。本页不会安装 Skill 或 MCP。';
  }
  workspaceTrust?.sync();
  saveDraft();
  updateSubmit();
}
function showRun(run) {
  const target = document.getElementById('execution-detail');
  if (!target || run.id !== selectedId) return;
  if (target.dataset.runId !== run.id) {
    target.dataset.runId = run.id;
    target.innerHTML = `<div class="section-title"><h2 id="run-title"></h2><span id="run-status" class="tag"></span></div>
      <p class="muted" id="run-meta"></p><p class="notice error" id="run-error" hidden></p>
      <div class="execution-controls"><button type="button" class="btn" id="execution-cancel">取消任务</button>
        <button type="button" class="btn" id="execution-download">${icon('save')} 下载结果</button></div>
      <details class="execution-request"><summary>查看任务内容</summary><pre id="run-prompt"></pre></details>
      <h3>执行日志</h3><p class="muted" id="run-log-note"></p><pre id="run-log" class="execution-log" aria-label="执行日志"></pre>
      <h3>结果</h3><pre id="run-result" class="execution-result"></pre>
      <p class="muted execution-location" id="run-location"></p>
      <p class="muted">取消或失败不会回滚已经发生的修改。记录和结果保存在本机。</p>`;
  }
  document.getElementById('run-title').textContent = run.title;
  const status = document.getElementById('run-status');
  status.textContent = labels[run.status] || run.status;
  status.setAttribute('role', 'status');
  document.getElementById('run-meta').textContent =
    `${run.agent} · ${run.cwd} · ${run.sandbox} · ${run.model || '客户端默认模型'}${run.workflow_id ? ' · 协作调度步骤' : ''}`;
  const error = document.getElementById('run-error');
  error.hidden = !run.error;
  error.textContent = run.error || '';
  const cancel = document.getElementById('execution-cancel');
  cancel.hidden = !active(run);
  cancel.disabled = run.status === 'cancelling';
  cancel.textContent = run.status === 'cancelling' ? '正在取消…' : '取消任务';
  const download = document.getElementById('execution-download');
  download.disabled = !run.result;
  download.textContent = run.result_truncated ? '下载显示部分' : '下载结果';
  download.onclick = () => {
    const url = URL.createObjectURL(
      new Blob([run.result], { type: 'text/markdown;charset=utf-8' }),
    );
    const link = document.createElement('a');
    link.href = url;
    link.download = `agent-result-${run.id.slice(0, 8)}.md`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  document.getElementById('run-prompt').textContent = run.prompt;
  const log = document.getElementById('run-log');
  const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  if (log.textContent !== run.log) {
    log.textContent = run.log || '等待 Agent 输出…';
    if (atBottom) log.scrollTop = log.scrollHeight;
  }
  document.getElementById('run-log-note').textContent = run.log_truncated
    ? '显示最近日志；完整保留部分见本机 execution.log（上限 16 MB）。'
    : active(run)
      ? '每秒刷新；关闭此页面后任务仍会继续。'
      : '执行已结束。';
  document.getElementById('run-result').textContent =
    run.result || (active(run) ? '等待最终结果…' : '本次没有生成文本结果，请查看日志。');
  document.getElementById('run-location').textContent =
    `记录目录：${run.directory}${run.result_truncated ? '；页面仅显示结果末尾，完整内容见 result.md。' : ''}`;
}

async function poll(epoch) {
  const ticket = ++request;
  clearTimeout(timer);
  try {
    const params = new URLSearchParams({
      q: historyQuery,
      page: String(historyPage),
      page_size: '20',
      kind: historyKind,
    });
    const next = await api(`runs?${params}`);
    if (epoch !== generation || ticket !== request) return;
    rows = next.items || [];
    historyMeta = next;
    if (historyPage > (next.pages || 1)) historyPage = next.pages || 1;
    if (!selectedId) selectedId = rows[0]?.id || null;
    renderRows();
    updateSubmit();
    if (selectedId) {
      const run = await api(`runs/${selectedId}`);
      if (epoch !== generation || ticket !== request) return;
      showRun(run);
    }
  } catch (error) {
    if (epoch !== generation || ticket !== request) return;
    document.getElementById('execution-start-hint').textContent = error.message;
  } finally {
    if (epoch === generation && ticket === request) {
      timer = setTimeout(() => poll(epoch), rows.some(active) ? 1000 : 4000);
    }
  }
}

export function unmountExecutionUI() {
  generation += 1;
  chooseRun = () => {};
  clearTimeout(timer);
  timer = null;
  workspaceTrust = null;
}

export function mountExecutionUI() {
  unmountExecutionUI();
  const epoch = generation;
  chooseRun = (id) => {
    if (epoch !== generation || !id) return;
    selectedId = id;
    poll(epoch);
  };
  const form = document.getElementById('execution-form');
  if (!form) return;
  const trust = mountCursorTrust(form, () => form.elements.agent.value === 'cursor');
  workspaceTrust = trust;
  form.addEventListener('input', () => {
    const previousAgent = draft.agent;
    for (const key of Object.keys(draft)) {
      draft[key] =
        key === 'timeout_minutes' ? Number(form.elements[key].value) : form.elements[key].value;
    }
    pendingId = null;
    if (draft.agent !== previousAgent) {
      draft.model = '';
      showRunner();
    }
    updateSubmit();
    saveDraft();
  });
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (starting || rows.some(active) || !form.reportValidity()) return;
    starting = true;
    pendingId ||= crypto.randomUUID().replaceAll('-', '');
    updateSubmit();
    try {
      const run = await api('runs/start', {
        ...draft,
        request_id: pendingId,
        cursor_trusted_cwd: trust.value(),
      });
      rememberDirectory(draft.cwd);
      selectedId = run.id;
      pendingId = null;
      trust.reset();
      if (epoch === generation) await poll(epoch);
    } catch (error) {
      toast(`${error.message} 可先刷新执行记录确认是否已启动。`, true);
    } finally {
      starting = false;
      if (epoch === generation) updateSubmit();
    }
  });
  document.getElementById('execution-refresh').onclick = () => poll(epoch);
  document.getElementById('execution-query').oninput = (event) => {
    historyQuery = event.target.value;
    historyPage = 1;
    poll(epoch);
  };
  document.getElementById('execution-kind').onchange = (event) => {
    historyKind = event.target.value;
    historyPage = 1;
    poll(epoch);
  };
  document.getElementById('execution-prev').onclick = () => {
    historyPage = Math.max(1, historyPage - 1);
    poll(epoch);
  };
  document.getElementById('execution-next').onclick = () => {
    historyPage += 1;
    poll(epoch);
  };
  document.getElementById('execution-detail').onclick = async (event) => {
    if (!event.target.closest('#execution-cancel')) return;
    const id = selectedId;
    try {
      const run = await api('runs/cancel', { id });
      if (epoch === generation) {
        showRun(run);
        await poll(epoch);
      }
    } catch (error) {
      toast(error.message, true);
    }
  };
  api('runners')
    .then((value) => {
      if (epoch !== generation) return;
      runners = value;
      showRunner();
    })
    .catch((error) => {
      if (epoch === generation)
        document.getElementById('runner-status').textContent = error.message;
    });
  poll(epoch);
}
