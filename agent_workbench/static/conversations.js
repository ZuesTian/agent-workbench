import { api } from '/api.js';
import { state } from '/state.js';
import { byId, e, icon, modal, preserveFocus, showModal, timeLabel, toast } from '/ui.js';
import { directoryExtras } from '/directories.js';
import { cursorTrustMarkup, mountCursorTrust } from '/cursor-trust.js';

const STATUS = {
  queued: '排队中',
  running: '正在回复',
  succeeded: '已完成',
  failed: '失败',
  error: '出错',
  cancelled: '已取消',
  interrupted: '已中断',
};
const RUNNING = new Set(['queued', 'running']);
let pollTimer = null;
// null means every member answers this turn; a Set limits the replies.
let turnTargets = null;
let workspaceTrust = null;
let selectionRequest = 0;
const sending = new Set();
const drafts = new Map();
let draftStorageWarning = false;

function draftFor(id) {
  if (!drafts.has(id)) {
    let value = '';
    try {
      value = localStorage.getItem(`awb-chat-draft:${id}`) || '';
    } catch {
      // The in-memory draft still survives view changes.
    }
    drafts.set(id, value.slice(0, 20000));
  }
  return drafts.get(id);
}

function saveDraft(id, value) {
  drafts.set(id, value);
  try {
    if (value) localStorage.setItem(`awb-chat-draft:${id}`, value);
    else localStorage.removeItem(`awb-chat-draft:${id}`);
  } catch {
    if (!draftStorageWarning) {
      draftStorageWarning = true;
      toast('无法保存浏览器草稿；当前窗口内仍会保留，关闭前请复制未发送内容。', true);
    }
  }
}

function selectedMembers(conv) {
  return (conv.members || []).filter((member) => !turnTargets || turnTargets.has(member.id));
}

function permissionScope(conv) {
  return JSON.stringify(selectedMembers(conv).map((member) => [member.id, member.sandbox]));
}

function active() {
  return state.activeConversation;
}

function memberTone(member) {
  const tones = ['#2f9e5a', '#3b74d1', '#d17424', '#7d52c9', '#2a8f9e', '#c04f7d'];
  const index = (state.activeConversation?.members || []).findIndex((m) => m.id === member.id);
  return tones[(index < 0 ? 0 : index) % tones.length];
}

function memberInitial(member) {
  return (member.name || '?').trim().slice(0, 1).toUpperCase();
}

function runningCount(conv) {
  return (conv?.messages || []).filter((m) => m.role === 'assistant' && RUNNING.has(m.status))
    .length;
}

function doneCount(conv) {
  return (conv?.messages || []).filter((m) => m.role === 'assistant' && m.status === 'succeeded')
    .length;
}

function shortTime(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const now = new Date();
  const sameDay = date.toDateString() === now.toDateString();
  return sameDay
    ? date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })
    : date.toLocaleString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      });
}

function memberBubbleMeta(message, conv) {
  const member = (conv.members || []).find((m) => m.id === message.member) || {
    id: message.member,
    name: message.name || message.member,
  };
  const status = STATUS[message.status] || message.status;
  const busy = RUNNING.has(message.status);
  const seconds = message.duration_ms ? `${(message.duration_ms / 1000).toFixed(1)}s` : '';
  return { member, status, busy, seconds };
}

function messageBubble(message, conv) {
  if (message.role === 'user') {
    return `<article class="chat-message user"><div class="chat-bubble">${e(message.text)}</div>
      <small class="chat-time">${e(shortTime(message.created_at))}</small></article>`;
  }
  const { member, status, busy, seconds } = memberBubbleMeta(message, conv);
  const tone = memberTone(member);
  const open = message.run_id
    ? `<button class="text-link" type="button" data-chat-log="${e(message.run_id)}">查看执行日志 ${icon('arrow')}</button>`
    : '';
  return `<article class="chat-message agent">
    <span class="chat-avatar" style="--agent-color:${tone}">${e(memberInitial(member))}</span>
    <div class="chat-body">
      <div class="chat-meta"><strong>${e(member.name)}</strong>
        <span class="tag ${busy ? 'warm' : message.status === 'succeeded' ? '' : 'error'}">${e(status)}</span>
        ${seconds ? `<span class="muted">${e(seconds)}</span>` : ''}
        <span class="muted">${e(shortTime(message.created_at))}</span></div>
      ${busy ? '<div class="chat-typing"><span></span><span></span><span></span></div>' : ''}
      ${message.text ? `<div class="chat-bubble">${e(message.text)}</div>` : ''}
      ${message.status === 'error' && message.error ? `<p class="chat-error">${e(message.error)}</p>` : ''}
      ${open}
    </div></article>`;
}

function taskList(conv) {
  const tasks = (conv.messages || []).filter((m) => m.role === 'assistant');
  if (!tasks.length) return '<p class="muted">这条会话还没有任务。</p>';
  return `<div class="chat-task-list">${tasks
    .map((message, index) => {
      const { member, status, seconds } = memberBubbleMeta(message, conv);
      return `<div class="chat-task">
        <span class="chat-avatar" style="--agent-color:${memberTone(member)}">${e(memberInitial(member))}</span>
        <div class="chat-task-body"><strong>${e(member.name)} · 第 ${index + 1} 个回复</strong>
          <small>${e(status)}${seconds ? ` · ${e(seconds)}` : ''} · ${e(shortTime(message.created_at))}</small></div>
        ${message.run_id ? `<button class="text-link" type="button" data-chat-log="${e(message.run_id)}">日志</button>` : ''}
      </div>`;
    })
    .join('')}</div>`;
}

function openTaskList() {
  const conv = active();
  if (!conv) return;
  showModal(
    '会话任务列表',
    `<p class="muted">每条回复对应一次真实的客户端调用，日志保存在本机执行记录里。</p>${taskList(conv)}`,
  );
}

function emptyState() {
  const runners = (state.runners || []).filter((r) => r.available);
  return `<div class="chat-empty">
    <div class="chat-empty-mark">${icon('chat')}</div>
    <h2>开始一个多 Agent 会话</h2>
    <p>选好工作区和成员后，你的每条消息会依次交给每个 Agent，回复留在同一段对话里。</p>
    <button class="btn primary" type="button" data-chat-new>${icon('plus')} 新建会话</button>
    ${runners.length ? `<p class="muted">当前可执行的客户端：${runners.map((r) => e(r.name)).join(' · ')}</p>` : '<p class="muted">还没有找到可执行的客户端，请先在总览确认程序入口。</p>'}
  </div>`;
}

function conversationHeader(conv) {
  return `<div class="page-head" data-page="chat">
    <div class="page-avatar">${icon('chat')}</div>
    <div class="page-titles"><h1>${e(conv.name)}</h1>
      <p class="page-handle">MULTI-AGENT CHAT · ${e(conv.workspace || '未设置工作区')}</p></div>
    <div class="page-actions">
      <button class="btn" type="button" data-chat-edit>${icon('settings')} 成员与工作区</button>
      <button class="icon-button" type="button" data-chat-delete aria-label="删除会话" title="删除会话">${icon('trash')}</button>
    </div>
  </div>
  <div class="chat-statusbar">
    <span class="chat-members">${(conv.members || [])
      .map(
        (m) =>
          `<span class="chat-member-pill" title="${e(m.name)}" style="--agent-color:${memberTone(m)}">${e(memberInitial(m))}</span>`,
      )
      .join('')}<span class="muted">${(conv.members || []).length} 个成员</span></span>
    <span class="chat-statusbar-right"><span class="muted">${runningCount(conv) ? `本轮 ${runningCount(conv)} 个正在回复` : `${doneCount(conv)} 个回复已完成`}</span>
      <button class="btn small" type="button" data-chat-tasks>${icon('list')} 任务列表</button></span>
  </div>`;
}

function composerBar(conv) {
  const busy = Boolean(conv.running) || runningCount(conv) > 0 || sending.has(conv.id);
  const targets = turnTargets || new Set((conv.members || []).map((m) => m.id));
  const pickers = (conv.members || [])
    .map(
      (m) =>
        `<button class="chat-target ${targets.has(m.id) ? 'on' : ''}" type="button" data-chat-target="${e(m.id)}" aria-pressed="${targets.has(m.id)}" ${busy ? 'disabled' : ''}>${e(m.name)}</button>`,
    )
    .join('');
  return `<form id="chat-form" class="chat-composer">
    <input type="hidden" name="cwd" value="${e(conv.workspace)}">
    <input type="hidden" name="permission" value="${e(permissionScope(conv))}">
    <div class="chat-targets" role="group" aria-label="本轮回答的成员">${pickers}</div>
    <textarea id="chat-input" data-conversation-id="${e(conv.id)}" aria-label="会话消息" rows="1" maxlength="20000" placeholder="和 ${e((conv.members || []).map((m) => m.name).join('、') || '成员')} 说点什么…" ${busy ? 'disabled' : ''}></textarea>
    ${cursorTrustMarkup}
    <div class="chat-composer-actions">
      <span class="chat-hint">${busy ? '正在依次回复，可取消本轮' : '消息会依次发给选中的成员；回复会留在同一段对话里'}</span>
      ${conv.running || runningCount(conv) > 0 ? `<button class="btn" type="button" data-chat-cancel>${icon('close')} 取消本轮</button>` : `<button class="btn primary" type="button" data-chat-send ${busy ? 'disabled' : ''}>${icon('arrow')} 发送</button>`}
    </div>
  </form>`;
}

function applyTargets() {
  const conv = active();
  if (!conv) return;
  const targets = turnTargets || new Set((conv.members || []).map((m) => m.id));
  for (const button of document.querySelectorAll('[data-chat-target]')) {
    const on = targets.has(button.dataset.chatTarget);
    button.classList.toggle('on', on);
    button.setAttribute('aria-pressed', String(on));
  }
  const form = byId('chat-form');
  if (form) form.elements.permission.value = permissionScope(conv);
  workspaceTrust?.sync();
}

function toggleTarget(memberId) {
  const conv = active();
  if (!conv) return;
  const all = (conv.members || []).map((m) => m.id);
  const next = turnTargets ? new Set(turnTargets) : new Set(all);
  if (next.has(memberId)) next.delete(memberId);
  else next.add(memberId);
  if (!next.size) {
    toast('至少保留一个本轮回复的成员', true);
    return;
  }
  turnTargets = next.size === all.length ? null : next;
  applyTargets();
}

export function chatPage() {
  const conv = active();
  if (!conv) {
    return `<section class="chat-page">${emptyState()}</section>`;
  }
  const messages = conv.messages || [];
  return `<section class="chat-page">
    ${conversationHeader(conv)}
    <div class="chat-stream" id="chat-stream" aria-live="polite">
      ${messages.length ? messages.map((m) => messageBubble(m, conv)).join('') : `<div class="chat-welcome"><strong>${e(conv.name)}</strong><p>这条会话还没有消息。写下第一句，成员会依次回复。</p></div>`}
    </div>
    ${composerBar(conv)}
  </section>`;
}

export function chatContextItems() {
  const rows = state.conversations || [];
  const items = [
    `<button class="context-item" data-ctx="new-conversation"><span class="ctx-icon">${icon('plus')}</span><div><strong>新建会话</strong><small>选工作区与成员</small></div></button>`,
  ];
  for (const row of rows) {
    items.push(
      `<button class="context-item ${active()?.id === row.id ? 'active' : ''}" data-ctx="conversation" data-value="${e(row.id)}">
        <span class="ctx-icon">${icon(row.running ? 'chat' : 'chat')}</span>
        <div><strong>${e(row.name)}</strong><small>${e(row.preview || `${row.message_count} 条消息`)}</small></div>
        ${row.running ? '<span class="context-dot"></span>' : '<span class="ctx-count">' + (row.message_count || 0) + '</span>'}
      </button>`,
    );
  }
  return { items, note: rows.length ? '' : '还没有会话' };
}

function scrollToBottom() {
  const stream = byId('chat-stream');
  if (stream) stream.scrollTop = stream.scrollHeight;
}

function paint() {
  const main = byId('main-content');
  if (!main) return;
  preserveFocus(() => {
    main.innerHTML = chatPage();
    bindComposer();
  });
  scrollToBottom();
}

function bindComposer() {
  const input = byId('chat-input');
  if (!input) return;
  if (input.dataset.bound) return;
  input.dataset.bound = 'true';
  for (const button of document.querySelectorAll('[data-chat-target]')) {
    button.addEventListener('click', () => toggleTarget(button.dataset.chatTarget));
  }
  const conversationId = input.dataset.conversationId;
  input.value = draftFor(conversationId);
  let composing = false;
  input.addEventListener('compositionstart', () => {
    composing = true;
  });
  input.addEventListener('compositionend', () => {
    composing = false;
  });
  input.addEventListener('input', () => {
    saveDraft(conversationId, input.value);
    input.style.height = 'auto';
    input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
  });
  input.addEventListener('keydown', (event) => {
    if (
      event.key === 'Enter' &&
      !event.shiftKey &&
      !composing &&
      !event.isComposing &&
      event.keyCode !== 229
    ) {
      event.preventDefault();
      sendMessage();
    }
  });
  const form = byId('chat-form');
  form.addEventListener('submit', (event) => event.preventDefault());
  workspaceTrust = mountCursorTrust(form, () => {
    const conv = active();
    return (
      conv?.id === conversationId &&
      !conv.running &&
      !sending.has(conv.id) &&
      selectedMembers(conv).some((member) => member.id === 'cursor')
    );
  });
}

export function applyConversation(conv) {
  if (state.activeConversation?.id !== conv.id) turnTargets = null;
  state.activeConversation = conv;
  state.conversations = (state.conversations || []).map((row) =>
    row.id === conv.id
      ? {
          ...row,
          name: conv.name,
          workspace: conv.workspace,
          members: conv.members,
          message_count: (conv.messages || []).length,
          running: Boolean(conv.running),
          updated_at: conv.updated_at,
        }
      : row,
  );
  if (mounted()) paint();
  document.dispatchEvent(new CustomEvent('awb:conversation-changed', { detail: { id: conv.id } }));
}

function mounted() {
  return state.tab === 'chat';
}

export async function selectConversation(conversationId) {
  const request = ++selectionRequest;
  try {
    turnTargets = null;
    const conv = await api(`conversations/${conversationId}`);
    if (request !== selectionRequest || !mounted()) return;
    applyConversation(conv);
    startPolling();
  } catch (error) {
    toast(error.message, true);
  }
}

export async function refreshConversations() {
  const result = await api('conversations');
  state.conversations = result.items || [];
  const wanted = active()?.id;
  if (wanted) {
    const row = state.conversations.find((item) => item.id === wanted);
    if (!row) state.activeConversation = null;
  } else if (state.conversations.length) {
    state.activeConversation = null;
  }
}

function startPolling() {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    const conv = active();
    if (!conv || !mounted()) return;
    if (!conv.running && !runningCount(conv)) return;
    try {
      const updated = await api(`conversations/${conv.id}`);
      if (active()?.id === conv.id && mounted()) applyConversation(updated);
    } catch {
      /* The next tick retries. */
    }
  }, 1200);
}

export function mountChatUI() {
  bindComposer();
  startPolling();
  scrollToBottom();
  if (!active() && (state.conversations || []).length) {
    selectConversation(state.conversations[0].id);
  }
}

export function unmountChatUI() {
  clearInterval(pollTimer);
  pollTimer = null;
  workspaceTrust = null;
  selectionRequest += 1;
}

async function sendMessage() {
  const conv = active();
  const input = byId('chat-input');
  if (!conv || !input || input.disabled || sending.has(conv.id) || conv.running) return;
  const text = input.value.trim();
  if (!text) return;
  const submittedDraft = input.value;
  saveDraft(conv.id, submittedDraft);
  const trust = workspaceTrust;
  const payload = { id: conv.id, text, cursor_trusted_cwd: trust?.value() || '' };
  if (turnTargets) payload.members = [...turnTargets];
  sending.add(conv.id);
  input.disabled = true;
  const sendButton = document.querySelector('[data-chat-send]');
  if (sendButton) sendButton.disabled = true;
  try {
    const updated = await api('conversations/message', payload);
    sending.delete(conv.id);
    if (draftFor(conv.id) === submittedDraft) saveDraft(conv.id, '');
    trust?.reset();
    if (active()?.id === conv.id) applyConversation(updated);
    startPolling();
  } catch (error) {
    toast(error.message, true);
    if (active()?.id === conv.id && mounted()) {
      const current = byId('chat-input');
      if (current) {
        current.disabled = false;
        current.focus();
      }
      const button = document.querySelector('[data-chat-send]');
      if (button) button.disabled = false;
    }
  } finally {
    sending.delete(conv.id);
  }
}

async function cancelTurn() {
  const conv = active();
  if (!conv) return;
  try {
    const updated = await api('conversations/cancel', { id: conv.id });
    applyConversation(updated);
    startPolling();
  } catch (error) {
    toast(error.message, true);
  }
}

function openLog(runId) {
  api(`runs/${runId}`)
    .then((run) =>
      showModal(
        '执行日志',
        `<p class="muted">${e(run.title || '')} · ${e(run.status || '')} · ${e(timeLabel(run.created_at))}</p>
         <pre class="execution-log">${e((run.log || '').slice(-12000) || '暂无日志')}</pre>
         ${run.result ? `<h3>结果</h3><pre class="execution-result">${e(run.result.slice(0, 12000))}</pre>` : ''}
         <p class="muted execution-location">${e(run.directory || '')}</p>`,
      ),
    )
    .catch((error) => toast(error.message, true));
}

function runnerOptions(selected) {
  const runners = state.runners || [];
  const picked = new Map((selected || []).map((m) => [m.id, m]));
  return runners
    .map((runner) => {
      const modes = runner.modes || [];
      const chosen = picked.get(runner.id)?.sandbox || modes[0]?.id || '';
      const valid = modes.some((mode) => mode.id === chosen);
      return `<div class="chat-member-row ${runner.available ? '' : 'disabled'}"><label class="chat-member-option">
          <input type="checkbox" data-chat-member value="${e(runner.id)}" ${picked.has(runner.id) ? 'checked' : ''} ${runner.available ? '' : 'disabled'}>
          <span><strong>${e(runner.name)}</strong><small>${runner.available ? '可执行' : '没有调用入口'}</small></span>
        </label><label class="field">权限<select data-chat-mode="${e(runner.id)}" aria-label="${e(runner.name)} 的会话权限" ${runner.available ? '' : 'disabled'}>
          ${valid ? '' : `<option value="${e(chosen)}" selected disabled>原权限不可用，请重新选择</option>`}
          ${modes.map((mode) => `<option value="${e(mode.id)}" ${mode.id === chosen ? 'selected' : ''}>${e(mode.name)}</option>`).join('')}
        </select></label></div>`;
    })
    .join('');
}

function conversationDialog(conv) {
  const editing = Boolean(conv);
  const body = `<div class="chat-dialog">
    <label class="field">会话名称<input id="chat-name" maxlength="80" value="${e(conv?.name || '')}" placeholder="例如：周报协作"></label>
    <label class="field">工作区<input id="chat-workspace" maxlength="4096" value="${e(conv?.workspace || '')}" placeholder="选择已有项目目录"></label>
    ${directoryExtras('chat-workspace')}
    <p class="muted">所有成员的回复都在这个目录里执行；程序按各自客户端的沙箱与权限运行。</p>
    <fieldset class="chat-member-field"><legend>会话成员</legend>${runnerOptions(conv?.members)}</fieldset>
  </div>`;
  const footer = `<button class="btn" data-action="close-modal">取消</button>
    <button class="btn primary" data-chat-save="${editing ? 'edit' : 'new'}">${editing ? '保存' : '创建会话'}</button>`;
  showModal(editing ? '成员与工作区' : '新建会话', body, footer);
  modal.querySelector('[data-chat-save]').onclick = () => saveConversation(editing);
}

async function saveConversation(editing) {
  const name = byId('chat-name').value.trim();
  const workspace = byId('chat-workspace').value.trim();
  const members = [...modal.querySelectorAll('[data-chat-member]:checked')].map((input) => {
    const previous = editing ? active()?.members.find((member) => member.id === input.value) : null;
    return {
      id: input.value,
      model: previous?.model || '',
      sandbox: modal.querySelector(`[data-chat-mode="${CSS.escape(input.value)}"]`).value,
    };
  });
  if (!name) return toast('请填写会话名称', true);
  if (!workspace) return toast('请选择工作区目录', true);
  if (!members.length) return toast('请选择至少一个成员', true);
  try {
    const payload = editing
      ? { id: active().id, name, workspace, members }
      : { name, workspace, members };
    const conv = await api(`conversations/${editing ? 'update' : 'create'}`, payload);
    modal.close();
    applyConversation(conv);
    document.dispatchEvent(
      new CustomEvent('awb:conversations-changed', { detail: { id: conv.id } }),
    );
    startPolling();
  } catch (error) {
    toast(error.message, true);
  }
}

async function deleteConversation() {
  const conv = active();
  if (!conv) return;
  showModal(
    '删除会话',
    `<p class="subheading">将删除「${e(conv.name)}」的对话记录；各客户端的执行记录仍保留在执行记录里。</p>`,
    '<button class="btn" data-action="close-modal">取消</button><button class="btn danger" data-chat-confirm-delete>删除会话</button>',
  );
  modal.querySelector('[data-chat-confirm-delete]').onclick = async () => {
    try {
      await api('conversations/delete', { id: conv.id });
      saveDraft(conv.id, '');
      modal.close();
      state.activeConversation = null;
      await refreshConversations();
      paint();
      document.dispatchEvent(new CustomEvent('awb:conversations-changed'));
    } catch (error) {
      toast(error.message, true);
    }
  };
}

export function openConversationDialog(conv) {
  conversationDialog(conv || null);
}

let bound = false;

export function bindChatEvents() {
  if (bound) return;
  bound = true;
  document.addEventListener('click', (event) => {
    const target = event.target.closest(
      '[data-chat-send],[data-chat-cancel],[data-chat-new],[data-chat-edit],[data-chat-delete],[data-chat-log],[data-chat-save],[data-chat-tasks]',
    );
    if (!target) return;
    const d = target.dataset;
    if (d.chatSend !== undefined) sendMessage();
    else if (d.chatCancel !== undefined) cancelTurn();
    else if (d.chatNew !== undefined) conversationDialog(null);
    else if (d.chatEdit !== undefined) conversationDialog(active());
    else if (d.chatDelete !== undefined) deleteConversation();
    else if (d.chatTasks !== undefined) openTaskList();
    else if (d.chatLog) openLog(d.chatLog);
  });
}
