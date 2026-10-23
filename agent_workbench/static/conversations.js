import { api } from '/api.js';
import { state } from '/state.js';
import { byId, e, icon, modal, preserveFocus, showModal, timeLabel, toast } from '/ui.js';
import { directoryExtras } from '/directories.js';
import { cursorTrustMarkup, mountCursorTrust } from '/cursor-trust.js';

const STATUS = {
  queued: '排队中',
  starting: '启动中',
  running: '正在回复',
  cancelling: '正在取消',
  succeeded: '已完成',
  failed: '失败',
  error: '出错',
  cancelled: '已取消',
  interrupted: '已中断',
  timed_out: '超时',
  timeout: '超时',
  skipped: '未执行',
};
const RUNNING = new Set(['queued', 'starting', 'running', 'cancelling']);
const UNFINISHED = new Set(['failed', 'error', 'cancelled', 'interrupted', 'timed_out']);
const NOT_EXECUTED_TEXT = '这一轮没有返回结果。';
// Mirrors MAX_MESSAGES in agent_workbench/conversations.py.
const MAX_MESSAGES = 400;
const NEAR_LIMIT = MAX_MESSAGES - 20;
const POLL_INTERVAL = 1200;
const POLL_BACKOFF = [1200, 2000, 4000, 4000];
const POLL_FAILURE_LIMIT = 3;
const NEAR_BOTTOM_GAP = 80;
const LONG_MESSAGE_LIMIT = 2000;
const TASK_LIST_LIMIT = 30;
const OFFLINE_TEXT = '暂时无法获取会话状态，正在重试…';
const SIGNATURE_SEPARATOR = String.fromCharCode(1);

let pollTimer = null;
let pollInFlight = false;
let pollSeq = 0;
let visibilityBound = false;
let busyNoticeAt = 0;
// null means every member answers this turn; a Set limits the replies.
let turnTargets = null;
let workspaceTrust = null;
let selectionRequest = 0;
const sending = new Set();
const drafts = new Map();
let draftStorageWarning = false;

// The chat skeleton is built once when a conversation is opened and then
// patched in place. Polling must never replace #main-content, the message
// stream or the composer: rebuilding them yanked the scroll position back to
// the bottom and broke IME composition, focus and text selection.
const chat = {
  conversationId: '',
  messageEls: new Map(),
  signatures: new Map(),
  statuses: new Map(),
  expanded: new Set(),
  headerSignature: '',
  membersSignature: '',
  rowSignature: '',
  followBottom: true,
  pendingNew: 0,
  pollFailures: 0,
  retrying: false,
};

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

function mounted() {
  return state.tab === 'chat';
}

function memberFor(message, conv) {
  const id = message.member || '';
  return (
    (conv?.members || []).find((member) => member.id === id) || {
      id,
      name: message.name || id || '成员',
    }
  );
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

function fullTime(value) {
  return value && shortTime(value) ? timeLabel(value) : '';
}

function elapsedSeconds(message) {
  if (!message.started_at) return null;
  const start = new Date(message.started_at).getTime();
  if (Number.isNaN(start)) return null;
  return Math.max(0, Math.floor((Date.now() - start) / 1000));
}

function durationLabel(message) {
  return message.duration_ms ? `${(message.duration_ms / 1000).toFixed(1)}s` : '';
}

// The turn is everything after the last user message: retry, progress and
// typing indicators are all scoped to that turn.
function latestRound(conv) {
  const messages = conv?.messages || [];
  let start = 0;
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index].role === 'user') {
      start = index + 1;
      break;
    }
  }
  return messages.slice(start).filter((message) => message.role === 'assistant');
}

// A reply that was already retried is superseded by its new attempt, so the
// failed original must not count as "still unfinished" for progress or retry.
function effectiveRound(conv) {
  const round = latestRound(conv);
  const retried = new Set(round.map((message) => message.retry_of).filter(Boolean));
  return round.filter((message) => !retried.has(message.id));
}

function unfinishedTargets(conv) {
  return effectiveRound(conv).filter((message) => UNFINISHED.has(message.status));
}

function isRetryable(message, conv) {
  if (message.role !== 'assistant' || !UNFINISHED.has(message.status)) return false;
  return effectiveRound(conv).some((item) => item.id === message.id);
}

function previewFor(conv) {
  const messages = conv.messages || [];
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const value = messages[index].text;
    if (!value) continue;
    const clean = String(value).split(/\s+/).filter(Boolean).join(' ');
    const chars = Array.from(clean);
    return chars.length <= 80 ? clean : chars.slice(0, 79).join('') + '…';
  }
  return '';
}

function conversationRowSignature(conv) {
  return JSON.stringify([
    conv.id,
    conv.name,
    conv.workspace,
    (conv.members || []).map((member) => [member.id, member.name]),
    previewFor(conv),
    (conv.messages || []).length,
    Boolean(conv.running),
    conv.updated_at || '',
  ]);
}

function memberSignature(conv) {
  return JSON.stringify((conv.members || []).map((member) => [member.id, member.name]));
}

function headerSignature(conv) {
  return JSON.stringify([
    conv.name,
    conv.workspace,
    (conv.members || []).map((member) => [member.id, member.name]),
  ]);
}

function isBusy(conv) {
  return Boolean(conv.running) || runningCount(conv) > 0 || sending.has(conv.id) || chat.retrying;
}

function hintText(conv) {
  if (chat.retrying) return '正在重试未完成的回复…';
  if (sending.has(conv.id)) return '正在发送…';
  if (conv.running || runningCount(conv) > 0) {
    return '正在依次回复，可继续编辑下一条消息；完成或取消后发送';
  }
  return '消息会依次发给选中的成员；回复会留在同一段对话里';
}

function placeholderFor(conv) {
  const names = (conv.members || []).map((member) => member.name).join('、') || '成员';
  return `和 ${names} 说点什么…`;
}

function messageTag(message) {
  const text = (message.text || '').trim();
  if (message.status === 'error' && text === NOT_EXECUTED_TEXT) {
    return { label: '未执行', tone: 'subtle' };
  }
  const label = STATUS[message.status] || '未知状态';
  if (RUNNING.has(message.status)) return { label, tone: 'warm' };
  if (message.status === 'cancelled' || message.status === 'interrupted') {
    return { label, tone: 'subtle' };
  }
  return { label, tone: message.status === 'succeeded' ? '' : 'error' };
}

function progressText(conv) {
  if (chat.retrying) return '正在重试未完成的回复…';
  const round = effectiveRound(conv);
  const current =
    round.find((message) => message.status === 'running') ||
    round.find((message) => message.status === 'starting') ||
    round.find((message) => message.status === 'queued') ||
    round.find((message) => message.status === 'cancelling');
  if (current) {
    const index = round.indexOf(current) + 1;
    const name = current.name || memberFor(current, conv).name;
    const seconds = elapsedSeconds(current);
    const suffix = seconds === null ? '' : ` · 已用 ${seconds} 秒`;
    return `正在回复：${name} · 第 ${index}/${round.length} 个${suffix}`;
  }
  const unfinished = round.filter((message) => UNFINISHED.has(message.status));
  const accumulated = doneCount(conv);
  if (unfinished.length) {
    return conv.error
      ? `本轮失败：${conv.error}（${unfinished.length} 个回复未完成）`
      : `本轮有 ${unfinished.length} 个回复未完成，可重试`;
  }
  if (conv.error && conv.status === 'failed') return `本轮失败：${conv.error}`;
  if (round.length) {
    const completed = round.filter((message) => message.status === 'succeeded').length;
    return completed === round.length
      ? `本轮 ${round.length} 个回复已完成（累计 ${accumulated} 个）`
      : `本轮 ${completed}/${round.length} 个回复已完成（累计 ${accumulated} 个）`;
  }
  return `累计 ${accumulated} 个回复已完成`;
}

function memberBubbleMeta(message, conv) {
  return {
    member: memberFor(message, conv),
    tag: messageTag(message),
    typing: message.status === 'running',
    seconds: durationLabel(message),
  };
}

function isLongMessage(message) {
  return (message.text || '').length > LONG_MESSAGE_LIMIT;
}

function limitText(conv) {
  const count = (conv.messages || []).length;
  const base = `已用 ${count}/${MAX_MESSAGES} 条消息`;
  return count >= NEAR_LIMIT ? base + ' · 接近上限，请新建会话继续' : base;
}

function bubbleMarkup(message) {
  const text = message.text || '';
  const classes = ['chat-bubble'];
  if (text.length > LONG_MESSAGE_LIMIT) {
    classes.push('long');
    classes.push(chat.expanded.has(message.id) ? 'open' : 'collapsed');
  }
  return `<div class="${classes.join(' ')}">${e(text)}</div>`;
}

function messageActionButtons(message, conv) {
  const buttons = [];
  if (message.text) {
    buttons.push(
      `<button class="text-link" type="button" data-chat-copy="${e(
        message.id,
      )}" aria-label="复制这条消息">复制</button>`,
    );
  }
  if (isLongMessage(message)) {
    const open = chat.expanded.has(message.id);
    buttons.push(
      `<button class="text-link" type="button" data-chat-expand="${e(
        message.id,
      )}" aria-expanded="${open}">${open ? '收起' : '展开全部'}</button>`,
    );
  }
  if (isRetryable(message, conv)) {
    buttons.push(
      `<button class="text-link" type="button" data-chat-retry="${e(message.id)}"${
        chat.retrying ? ' disabled' : ''
      }>${chat.retrying ? '正在重试…' : '重试'}</button>`,
    );
  }
  return buttons.length ? `<div class="chat-message-actions">${buttons.join('')}</div>` : '';
}

function messageMarkup(message, conv, live) {
  const liveAttr = live ? '' : ' aria-live="off"';
  if (message.role === 'user') {
    const actions = messageActionButtons(message, conv);
    return `<article class="chat-message user" data-message-id="${e(
      message.id,
    )}"${liveAttr}>${bubbleMarkup(
      message,
    )}<div class="chat-message-foot"><small class="chat-time" title="${e(
      fullTime(message.created_at),
    )}">${e(shortTime(message.created_at))}</small>${actions}</div></article>`;
  }
  const { member, tag, typing, seconds } = memberBubbleMeta(message, conv);
  const tone = memberTone(member);
  const duplicated =
    message.status === 'error' &&
    Boolean(message.error) &&
    Boolean(message.text) &&
    message.text.trim() === message.error.trim();
  const log = message.run_id
    ? `<button class="text-link" type="button" data-chat-log="${e(
        message.run_id,
      )}">查看执行日志 ${icon('arrow')}</button>`
    : '';
  const actions = messageActionButtons(message, conv);
  const foot = log || actions ? `<div class="chat-message-foot">${log}${actions}</div>` : '';
  const error =
    message.status === 'error' && message.error && !duplicated
      ? `<p class="chat-error">${e(message.error)}</p>`
      : '';
  return `<article class="chat-message agent" data-message-id="${e(message.id)}"${liveAttr}>
    <span class="chat-avatar" style="--agent-color:${tone}">${e(memberInitial(member))}</span>
    <div class="chat-body">
      <div class="chat-meta"><strong>${e(member.name)}</strong>
        <span class="tag ${tag.tone}">${e(tag.label)}</span>
        ${seconds ? `<span class="muted">${e(seconds)}</span>` : ''}
        <span class="muted" title="${e(fullTime(message.created_at))}">${e(
          shortTime(message.created_at),
        )}</span></div>
      ${typing ? '<div class="chat-typing" aria-hidden="true"><span></span><span></span><span></span></div>' : ''}
      ${message.text ? bubbleMarkup(message) : ''}
      ${error}
      ${foot}
    </div></article>`;
}

function messagesMarkup(conv) {
  const messages = conv.messages || [];
  if (!messages.length) return welcomeMarkup(conv);
  return messages.map((message) => messageMarkup(message, conv, false)).join('');
}

function welcomeMarkup(conv) {
  return `<div class="chat-welcome"><strong>${e(
    conv.name,
  )}</strong><p>这条会话还没有消息。写下第一句，成员会依次回复。</p></div>`;
}

function taskListMarkup(tasks, conv) {
  if (!tasks.length) return '<p class="muted">这条会话还没有任务。</p>';
  const replies = (conv.messages || []).filter((message) => message.role === 'assistant');
  const numbers = new Map(replies.map((message, index) => [message.id, index + 1]));
  return `<div class="chat-task-list">${tasks
    .map((task, index) => {
      const member = memberFor(task, conv);
      const status = STATUS[task.status] || '未知状态';
      const seconds = durationLabel(task);
      const number = numbers.get(task.id) || replies.length - tasks.length + index + 1;
      return `<div class="chat-task" data-chat-task="${e(task.id)}">
        <span class="chat-avatar" style="--agent-color:${memberTone(member)}">${e(
          memberInitial(member),
        )}</span>
        <div class="chat-task-body"><strong>${e(member.name)} · 回复 #${number}</strong>
          <small>${e(status)}${seconds ? ` · ${e(seconds)}` : ''} · ${e(
            shortTime(task.created_at),
          )}</small></div>
        ${
          task.run_id
            ? `<button class="text-link" type="button" data-chat-log="${e(
                task.run_id,
              )}">日志</button>`
            : ''
        }
      </div>`;
    })
    .join('')}</div>`;
}

function fallbackTasks(conv) {
  return (conv.messages || [])
    .filter((message) => message.role === 'assistant')
    .slice(-TASK_LIST_LIMIT)
    .map((message) => ({
      id: message.id,
      member: message.member,
      name: message.name,
      status: message.status,
      run_id: message.run_id,
      created_at: message.created_at,
      duration_ms: message.duration_ms,
      error: message.error,
    }));
}

function openTaskList() {
  const conv = active();
  if (!conv) return;
  const tasks = Array.isArray(conv.task_list)
    ? conv.task_list.slice(-TASK_LIST_LIMIT)
    : fallbackTasks(conv);
  showModal(
    '会话任务列表',
    `<p class="muted">每条回复对应一次真实的客户端调用，日志保存在本机执行记录里。这里仅显示最近 ${TASK_LIST_LIMIT} 条回复。</p>${taskListMarkup(
      tasks,
      conv,
    )}`,
  );
}

function emptyState() {
  const runners = (state.runners || []).filter((runner) => runner.available);
  const clients = runners.map((runner) => e(runner.name)).join(' · ');
  return `<div class="chat-empty">
    <div class="chat-empty-mark">${icon('chat')}</div>
    <h2>开始一个多 Agent 会话</h2>
    <p>选好工作区和成员后，你的每条消息会依次交给每个 Agent，回复留在同一段对话里。</p>
    <button class="btn primary" type="button" data-chat-new>${icon('plus')} 新建会话</button>
    ${
      runners.length
        ? `<p class="muted">当前可执行的客户端：${clients}</p>`
        : '<p class="muted">还没有找到可执行的客户端，请先在总览确认程序入口。</p>'
    }
  </div>`;
}

function conversationHeader(conv) {
  const locked = Boolean(conv.running) || runningCount(conv) > 0;
  const editLock = locked ? ' disabled title="本轮结束后可修改"' : '';
  const deleteLock = locked ? ' disabled title="本轮结束后可删除"' : ' title="删除会话"';
  return `<div class="page-head" data-page="chat">
    <div class="page-avatar">${icon('chat')}</div>
    <div class="page-titles"><h1>${e(conv.name)}</h1>
      <p class="page-handle">MULTI-AGENT CHAT · ${e(conv.workspace || '未设置工作区')}</p></div>
    <div class="page-actions">
      <button class="btn" type="button" data-chat-edit${editLock}>${icon(
        'settings',
      )} 成员与工作区</button>
      <button class="icon-button" type="button" data-chat-delete aria-label="删除会话"${deleteLock}>${icon(
        'trash',
      )}</button>
    </div>
  </div>
  <div class="chat-statusbar">
    <span class="chat-members" data-chat-members>${(conv.members || [])
      .map(
        (member) =>
          `<span class="chat-member-pill" title="${e(member.name)}" style="--agent-color:${memberTone(
            member,
          )}">${e(memberInitial(member))}</span>`,
      )
      .join('')}<span class="muted">${(conv.members || []).length} 个成员</span></span>
    <span class="chat-statusbar-right"><span class="muted chat-progress" data-chat-progress>${e(
      progressText(conv),
    )}</span>
      <button class="btn small" type="button" data-chat-tasks>${icon('list')} 任务列表</button></span>
  </div>`;
}

function targetButtons(conv, busy) {
  const targets = turnTargets || new Set((conv.members || []).map((member) => member.id));
  return (conv.members || [])
    .map(
      (member) =>
        `<button class="chat-target ${targets.has(member.id) ? 'on' : ''}" type="button" data-chat-target="${e(
          member.id,
        )}" aria-pressed="${targets.has(member.id)}"${
          busy ? ' disabled' : ''
        }>${e(member.name)}</button>`,
    )
    .join('');
}

function cancelButtonMarkup() {
  return `<button class="btn" type="button" data-chat-cancel>${icon('close')} 取消本轮</button>`;
}

function actionButtonsMarkup(conv) {
  const running = Boolean(conv.running) || runningCount(conv) > 0;
  const sendingNow = sending.has(conv.id);
  const label = running ? '本轮回复中…' : sendingNow ? '发送中…' : '发送';
  const send = `<button class="btn primary" type="button" data-chat-send${
    isBusy(conv) ? ' disabled' : ''
  } aria-busy="${sendingNow}">${icon('arrow')} <span data-chat-send-label>${e(
    label,
  )}</span></button>`;
  return running ? send + cancelButtonMarkup() : send;
}

function composerBar(conv) {
  const busy = isBusy(conv);
  return `<form id="chat-form" class="chat-composer" data-conversation-id="${e(conv.id)}">
    <input type="hidden" name="cwd" value="${e(conv.workspace)}">
    <input type="hidden" name="permission" value="${e(permissionScope(conv))}">
    <div class="chat-targets" role="group" aria-label="本轮回答的成员">${targetButtons(
      conv,
      busy,
    )}</div>
    <textarea id="chat-input" data-conversation-id="${e(
      conv.id,
    )}" aria-label="会话消息" rows="1" maxlength="20000" placeholder="${e(
      placeholderFor(conv),
    )}"></textarea>
    <p class="chat-limit" data-chat-limit>${e(limitText(conv))}</p>
    ${cursorTrustMarkup}
    <div class="chat-composer-actions">
      <span class="chat-hint" data-chat-hint>${e(hintText(conv))}</span>
      ${actionButtonsMarkup(conv)}
    </div>
  </form>`;
}

function htmlElement(markup) {
  const template = document.createElement('template');
  template.innerHTML = markup.trim();
  return template.content.firstElementChild;
}

function toggleTarget(memberId) {
  const conv = active();
  if (!conv) return;
  const all = (conv.members || []).map((member) => member.id);
  const next = turnTargets ? new Set(turnTargets) : new Set(all);
  if (next.has(memberId)) next.delete(memberId);
  else next.add(memberId);
  if (!next.size) {
    toast('至少保留一个本轮回复的成员', true);
    return;
  }
  turnTargets = next.size === all.length ? null : next;
  const current = active();
  if (current) syncComposerState(current);
}

export function chatPage() {
  const conv = active();
  if (!conv) {
    return `<section class="chat-page">${emptyState()}</section>`;
  }
  return `<section class="chat-page" data-chat-page data-conversation-id="${e(conv.id)}">
    <div data-chat-header>${conversationHeader(conv)}</div>
    <div class="chat-main">
      <div class="chat-stream" id="chat-stream" role="log" aria-live="polite" aria-relevant="additions">${messagesMarkup(
        conv,
      )}</div>
      <div class="chat-offline" data-chat-offline role="status" hidden>${e(OFFLINE_TEXT)}</div>
      <button class="chat-scroll-bottom" type="button" data-chat-scroll-bottom aria-label="跳到最新回复" hidden>有新回复 ↓</button>
    </div>
    <div class="chat-retry-bar" data-chat-retry-bar role="status" hidden></div>
    ${composerBar(conv)}
  </section>`;
}

export function chatContextItems() {
  const rows = state.conversations || [];
  const items = [
    `<button class="context-item" data-ctx="new-conversation"><span class="ctx-icon">${icon(
      'plus',
    )}</span><div><strong>新建会话</strong><small>选工作区与成员</small></div></button>`,
  ];
  for (const row of rows) {
    const count = row.running
      ? '<span class="context-dot"></span>'
      : '<span class="ctx-count">' + (row.message_count || 0) + '</span>';
    items.push(
      `<button class="context-item ${active()?.id === row.id ? 'active' : ''}" data-ctx="conversation" data-value="${e(
        row.id,
      )}">
        <span class="ctx-icon">${icon('chat')}</span>
        <div><strong>${e(row.name)}</strong><small>${e(
          row.preview || row.message_count + ' 条消息',
        )}</small></div>
        ${count}
      </button>`,
    );
  }
  return { items, note: rows.length ? '' : '还没有会话' };
}

function streamGap(stream) {
  return stream.scrollHeight - stream.scrollTop - stream.clientHeight;
}

function isNearBottom(stream) {
  return !stream || streamGap(stream) <= NEAR_BOTTOM_GAP;
}

function scrollToBottom() {
  const stream = byId('chat-stream');
  if (!stream) return;
  stream.scrollTop = stream.scrollHeight;
  chat.followBottom = true;
  chat.pendingNew = 0;
  updateNewReplyButton();
}

function updateNewReplyButton() {
  const button = document.querySelector('[data-chat-scroll-bottom]');
  if (!button) return;
  button.hidden = chat.pendingNew === 0;
  if (!button.hidden) button.textContent = `有新回复 ${chat.pendingNew} 条 ↓`;
}

function bindStreamEvents(stream) {
  if (!stream || stream.dataset.chatScrollBound) return;
  stream.dataset.chatScrollBound = '1';
  stream.addEventListener(
    'scroll',
    () => {
      chat.followBottom = isNearBottom(stream);
      if (chat.followBottom && chat.pendingNew) {
        chat.pendingNew = 0;
        updateNewReplyButton();
      }
    },
    { passive: true },
  );
}

function textFingerprint(value) {
  const text = value || '';
  return `${text.length}:${text.slice(0, 48)}:${text.slice(-48)}`;
}

function messageSignature(message, conv) {
  const member = message.role === 'assistant' ? memberFor(message, conv) : null;
  return [
    message.role,
    message.status,
    textFingerprint(message.text),
    textFingerprint(message.error),
    message.duration_ms || 0,
    message.created_at || '',
    message.run_id || '',
    member ? member.id + ':' + member.name : '',
    chat.expanded.has(message.id) ? 1 : 0,
    chat.retrying ? 1 : 0,
    isRetryable(message, conv) ? 1 : 0,
  ].join(SIGNATURE_SEPARATOR);
}

function indexMessages() {
  const stream = byId('chat-stream');
  const conv = active();
  if (!stream) return;
  chat.messageEls.clear();
  chat.signatures.clear();
  chat.statuses.clear();
  for (const element of stream.querySelectorAll('[data-message-id]')) {
    const id = element.getAttribute('data-message-id');
    element.setAttribute('aria-live', 'off');
    chat.messageEls.set(id, element);
    const message = (conv?.messages || []).find((item) => item.id === id);
    if (!message) continue;
    chat.signatures.set(id, messageSignature(message, conv));
    chat.statuses.set(id, message.status);
  }
}

function placeAfter(stream, element, previous) {
  const expected = previous ? previous.nextElementSibling : stream.firstElementChild;
  if (expected === element) return;
  if (previous) previous.after(element);
  else stream.prepend(element);
}

function createMessageElement(message, conv, live) {
  return htmlElement(messageMarkup(message, conv, live));
}

function updateMessageElement(element, message, conv) {
  const fresh = htmlElement(messageMarkup(message, conv, false));
  element.className = fresh.className;
  element.replaceChildren(...Array.from(fresh.childNodes));
  element.setAttribute('aria-live', 'off');
}

function updateMessages(conv) {
  const stream = byId('chat-stream');
  if (!stream) return;
  const messages = conv.messages || [];
  if (!messages.length) {
    for (const element of chat.messageEls.values()) element.remove();
    chat.messageEls.clear();
    chat.signatures.clear();
    chat.statuses.clear();
    if (!stream.querySelector('.chat-welcome')) stream.innerHTML = welcomeMarkup(conv);
    return;
  }
  const welcome = stream.querySelector('.chat-welcome');
  if (welcome) welcome.remove();
  let previous = null;
  let added = 0;
  let completed = 0;
  const seen = new Set();
  for (const message of messages) {
    seen.add(message.id);
    let element = chat.messageEls.get(message.id);
    if (element && !element.isConnected) element = null;
    const signature = messageSignature(message, conv);
    if (!element) {
      element = createMessageElement(message, conv, true);
      chat.messageEls.set(message.id, element);
      chat.signatures.set(message.id, signature);
      chat.statuses.set(message.id, message.status);
      placeAfter(stream, element, previous);
      added += 1;
    } else {
      const previousStatus = chat.statuses.get(message.id);
      if (previousStatus && RUNNING.has(previousStatus) && !RUNNING.has(message.status)) {
        completed += 1;
      }
      chat.statuses.set(message.id, message.status);
      if (chat.signatures.get(message.id) !== signature) {
        updateMessageElement(element, message, conv);
        chat.signatures.set(message.id, signature);
      } else if (element.getAttribute('aria-live') !== 'off') {
        element.setAttribute('aria-live', 'off');
      }
      placeAfter(stream, element, previous);
    }
    previous = element;
  }
  for (const [id, element] of Array.from(chat.messageEls)) {
    if (seen.has(id)) continue;
    element.remove();
    chat.messageEls.delete(id);
    chat.signatures.delete(id);
    chat.statuses.delete(id);
  }
  if (!chat.followBottom) chat.pendingNew += added + completed;
  updateNewReplyButton();
}

function updateHeader(conv) {
  const header = document.querySelector('[data-chat-header]');
  if (!header) return;
  const signature = headerSignature(conv);
  if (chat.headerSignature !== signature) {
    chat.headerSignature = signature;
    header.innerHTML = conversationHeader(conv);
  }
  const progress = header.querySelector('[data-chat-progress]');
  if (progress) {
    const text = progressText(conv);
    if (progress.textContent !== text) progress.textContent = text;
  }
}

function updateHeaderActions(conv) {
  const locked = Boolean(conv.running) || runningCount(conv) > 0;
  const edit = document.querySelector('[data-chat-edit]');
  if (edit) {
    edit.disabled = locked;
    if (locked) edit.title = '本轮结束后可修改';
    else edit.removeAttribute('title');
  }
  const remove = document.querySelector('[data-chat-delete]');
  if (remove) {
    remove.disabled = locked;
    remove.title = locked ? '本轮结束后可删除' : '删除会话';
  }
}

function applyRetryBusy() {
  for (const button of document.querySelectorAll('[data-chat-retry], [data-chat-retry-turn]')) {
    button.disabled = chat.retrying;
    if (button.hasAttribute('data-chat-retry-turn')) {
      button.textContent = chat.retrying ? '正在重试…' : '重试未完成成员';
    } else {
      button.textContent = chat.retrying ? '正在重试…' : '重试';
    }
  }
}

function updateRetryBar(conv) {
  const bar = document.querySelector('[data-chat-retry-bar]');
  if (!bar) return;
  const targets = unfinishedTargets(conv);
  if (!targets.length) {
    bar.hidden = true;
    bar.dataset.signature = '';
    return;
  }
  const signature = targets.map((message) => message.id).join(',');
  if (bar.dataset.signature !== signature) {
    bar.dataset.signature = signature;
    bar.innerHTML =
      '<span>本轮有 ' +
      targets.length +
      ' 个成员未完成</span><button class="btn small" type="button" data-chat-retry-turn>重试未完成成员</button>';
  }
  bar.hidden = false;
  applyRetryBusy();
}

function syncComposerState(conv) {
  const form = byId('chat-form');
  if (!form) return;
  const busy = isBusy(conv);
  if (form.elements.cwd) form.elements.cwd.value = conv.workspace || '';
  if (form.elements.permission) form.elements.permission.value = permissionScope(conv);
  // The textarea stays editable while a turn runs: only send and member
  // targets are locked, so IME, cursor and drafts survive a polling tick.
  const input = byId('chat-input');
  if (input) {
    const placeholder = placeholderFor(conv);
    if (input.placeholder !== placeholder) input.placeholder = placeholder;
  }
  const signature = memberSignature(conv);
  if (chat.membersSignature !== signature) {
    chat.membersSignature = signature;
    const container = form.querySelector('.chat-targets');
    if (container) container.innerHTML = targetButtons(conv, busy);
  } else {
    const targets = turnTargets || new Set((conv.members || []).map((member) => member.id));
    for (const button of form.querySelectorAll('[data-chat-target]')) {
      const on = targets.has(button.dataset.chatTarget);
      button.classList.toggle('on', on);
      button.setAttribute('aria-pressed', String(on));
      button.disabled = busy;
    }
  }
  const limit = form.querySelector('[data-chat-limit]');
  if (limit) {
    const text = limitText(conv);
    if (limit.textContent !== text) limit.textContent = text;
  }
  const actions = form.querySelector('.chat-composer-actions');
  if (actions) {
    const running = Boolean(conv.running) || runningCount(conv) > 0;
    const sendingNow = sending.has(conv.id);
    const send = actions.querySelector('[data-chat-send]');
    if (send) {
      send.disabled = busy;
      send.setAttribute('aria-busy', String(sendingNow));
      const label = send.querySelector('[data-chat-send-label]');
      const labelText = running ? '本轮回复中…' : sendingNow ? '发送中…' : '发送';
      if (label && label.textContent !== labelText) label.textContent = labelText;
    }
    const cancel = actions.querySelector('[data-chat-cancel]');
    if (running && !cancel) actions.append(htmlElement(cancelButtonMarkup()));
    else if (!running && cancel) cancel.remove();
    const hint = actions.querySelector('[data-chat-hint]');
    const text = hintText(conv);
    if (hint && hint.textContent !== text) hint.textContent = text;
  }
  workspaceTrust?.sync();
}

function updateOfflineBanner() {
  const banner = document.querySelector('[data-chat-offline]');
  if (!banner) return;
  banner.hidden = chat.pollFailures < POLL_FAILURE_LIMIT;
}

function syncChatView(conv) {
  if (!chatSkeleton(conv)) return false;
  updateHeader(conv);
  updateHeaderActions(conv);
  updateMessages(conv);
  updateRetryBar(conv);
  syncComposerState(conv);
  updateOfflineBanner();
  if (chat.followBottom) scrollToBottom();
  return true;
}

function chatSkeleton(conv) {
  const page = document.querySelector('#main-content .chat-page[data-chat-page]');
  if (!page || page.getAttribute('data-conversation-id') !== conv.id) return null;
  return byId('chat-input') ? page : null;
}

function rebuildChat(conv) {
  const main = byId('main-content');
  if (!main) return;
  preserveFocus(() => {
    main.innerHTML = chatPage();
  });
  chat.conversationId = conv.id;
  chat.headerSignature = '';
  chat.membersSignature = '';
  chat.messageEls.clear();
  chat.signatures.clear();
  chat.statuses.clear();
  chat.followBottom = true;
  chat.pendingNew = 0;
  indexMessages();
  bindComposer();
  syncChatView(conv);
  scrollToBottom();
}

function paint() {
  const main = byId('main-content');
  if (!main) return;
  const conv = active();
  if (!conv) {
    if (!main.querySelector('.chat-page[data-chat-page]')) {
      main.innerHTML = `<section class="chat-page">${emptyState()}</section>`;
    }
    return;
  }
  if (!chatSkeleton(conv)) {
    rebuildChat(conv);
    return;
  }
  syncChatView(conv);
}

function resizeInput(input) {
  input.style.height = 'auto';
  input.style.height = `${Math.max(28, Math.min(input.scrollHeight || 0, 160))}px`;
}

function nudgeBusy() {
  const now = Date.now();
  if (now - busyNoticeAt < 3000) return;
  busyNoticeAt = now;
  toast('本轮还在回复，完成或取消后再发送；正在输入的内容已保留');
}

function bindComposer() {
  const input = byId('chat-input');
  const conv = active();
  if (!input || !conv) return;
  const conversationId = conv.id;
  chat.conversationId = conversationId;
  chat.headerSignature = headerSignature(conv);
  chat.membersSignature = memberSignature(conv);
  bindStreamEvents(byId('chat-stream'));
  if (input.dataset.bound) return;
  input.dataset.bound = 'true';
  let composing = false;
  input.addEventListener('compositionstart', () => {
    composing = true;
  });
  input.addEventListener('compositionend', () => {
    composing = false;
  });
  input.addEventListener('input', () => {
    saveDraft(conversationId, input.value);
    resizeInput(input);
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
      const current = active();
      if (!current || current.id !== conversationId) return;
      if (isBusy(current)) {
        nudgeBusy();
        return;
      }
      sendMessage();
    }
  });
  const form = byId('chat-form');
  if (form) {
    form.addEventListener('submit', (event) => event.preventDefault());
    workspaceTrust = mountCursorTrust(form, () => {
      const current = active();
      return (
        current?.id === conversationId &&
        !current.running &&
        !sending.has(current.id) &&
        selectedMembers(current).some((member) => member.id === 'cursor')
      );
    });
  }
  input.value = draftFor(conversationId);
  resizeInput(input);
}

export function applyConversation(conv) {
  const previous = state.activeConversation;
  const signature = conversationRowSignature(conv);
  const changed = previous?.id !== conv.id || chat.rowSignature !== signature;
  if (previous?.id !== conv.id) turnTargets = null;
  state.activeConversation = conv;
  chat.rowSignature = signature;
  if (turnTargets) {
    const known = new Set((conv.members || []).map((member) => member.id));
    const next = new Set(Array.from(turnTargets).filter((id) => known.has(id)));
    turnTargets = next.size === known.size ? null : next.size ? next : null;
  }
  state.conversations = (state.conversations || []).map((row) =>
    row.id === conv.id
      ? {
          ...row,
          name: conv.name,
          workspace: conv.workspace,
          members: conv.members,
          preview: previewFor(conv) || row.preview,
          message_count: (conv.messages || []).length,
          running: Boolean(conv.running),
          updated_at: conv.updated_at,
        }
      : row,
  );
  if (mounted()) paint();
  if (!changed) return;
  document.dispatchEvent(new CustomEvent('awb:conversations-changed', { detail: { id: conv.id } }));
  document.dispatchEvent(new CustomEvent('awb:conversation-changed', { detail: { id: conv.id } }));
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

function pollDelay() {
  return POLL_BACKOFF[Math.min(chat.pollFailures, POLL_BACKOFF.length - 1)];
}

function schedulePoll(delay) {
  clearTimeout(pollTimer);
  if (!mounted() || document.visibilityState === 'hidden') {
    pollTimer = null;
    return;
  }
  pollTimer = setTimeout(pollOnce, delay);
}

function startPolling() {
  clearTimeout(pollTimer);
  pollTimer = null;
  pollOnce();
}

async function pollOnce() {
  const conv = active();
  if (!conv || !mounted() || document.visibilityState === 'hidden') {
    pollTimer = null;
    return;
  }
  const conversationId = conv.id;
  const shouldRun =
    Boolean(conv.running) || runningCount(conv) > 0 || chat.pollFailures >= POLL_FAILURE_LIMIT;
  if (!shouldRun) {
    pollTimer = null;
    return;
  }
  if (pollInFlight) return;
  pollInFlight = true;
  const seq = ++pollSeq;
  try {
    const updated = await api(`conversations/${conversationId}`);
    chat.pollFailures = 0;
    if (seq === pollSeq && active()?.id === conversationId && mounted()) {
      applyConversation(updated);
    }
  } catch {
    chat.pollFailures += 1;
    if (mounted() && active()?.id === conversationId) updateOfflineBanner();
  } finally {
    pollInFlight = false;
  }
  pollTimer = null;
  const current = active();
  const keep =
    current &&
    (current.running || runningCount(current) > 0 || chat.pollFailures >= POLL_FAILURE_LIMIT);
  if (keep) schedulePoll(pollDelay());
}

function bindVisibility() {
  if (visibilityBound) return;
  visibilityBound = true;
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') {
      clearTimeout(pollTimer);
      pollTimer = null;
      return;
    }
    if (mounted()) {
      clearTimeout(pollTimer);
      pollTimer = null;
      pollOnce();
    }
  });
}

export function mountChatUI() {
  const conv = active();
  const main = byId('main-content');
  if (main?.querySelector('.chat-page[data-chat-page]')) {
    if (conv && chatSkeleton(conv)) {
      chat.conversationId = conv.id;
      indexMessages();
      bindComposer();
      syncChatView(conv);
      scrollToBottom();
    }
  }
  bindVisibility();
  startPolling();
  if (!conv && (state.conversations || []).length) {
    selectConversation(state.conversations[0].id);
  }
}

export function unmountChatUI() {
  clearTimeout(pollTimer);
  pollTimer = null;
  workspaceTrust = null;
  selectionRequest += 1;
}

async function sendMessage() {
  const conv = active();
  const input = byId('chat-input');
  if (!conv || !input || sending.has(conv.id) || conv.running) return;
  const text = input.value.trim();
  if (!text) return;
  const submittedDraft = input.value;
  saveDraft(conv.id, submittedDraft);
  const trust = workspaceTrust;
  const payload = { id: conv.id, text, cursor_trusted_cwd: trust?.value() || '' };
  if (turnTargets) payload.members = Array.from(turnTargets);
  sending.add(conv.id);
  chat.followBottom = true;
  syncComposerState(conv);
  try {
    const updated = await api('conversations/message', payload);
    sending.delete(conv.id);
    if (draftFor(conv.id) === submittedDraft) saveDraft(conv.id, '');
    trust?.reset();
    if (active()?.id === conv.id) {
      applyConversation(updated);
      const current = byId('chat-input');
      if (current && current.value === submittedDraft) current.value = '';
      if (current) resizeInput(current);
      scrollToBottom();
    }
    startPolling();
  } catch (error) {
    toast(error.message, true);
    sending.delete(conv.id);
    if (active()?.id === conv.id && mounted()) {
      const current = active();
      if (current) syncComposerState(current);
      byId('chat-input')?.focus();
    }
  } finally {
    sending.delete(conv.id);
    if (active()?.id === conv.id && mounted()) syncComposerState(active());
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

async function retryMessages(messageIds) {
  const conv = active();
  if (!conv || chat.retrying) return;
  const ids = Array.from(new Set((messageIds || []).filter(Boolean)));
  if (!ids.length) return;
  chat.retrying = true;
  applyRetryBusy();
  syncComposerState(conv);
  try {
    const updated = await api('conversations/retry', { id: conv.id, message_ids: ids });
    if (active()?.id === conv.id) {
      chat.followBottom = true;
      applyConversation(updated);
      scrollToBottom();
    }
    startPolling();
    toast(`正在重试 ${ids.length} 条未完成的回复`);
  } catch (error) {
    toast(error.message, true);
  } finally {
    chat.retrying = false;
    const current = active();
    if (current?.id === conv.id && mounted()) syncChatView(current);
  }
}

function retryTurn() {
  const conv = active();
  if (!conv) return;
  retryMessages(unfinishedTargets(conv).map((message) => message.id));
}

async function copyMessage(messageId) {
  const conv = active();
  const message = (conv?.messages || []).find((item) => item.id === messageId);
  const text = message?.text || '';
  if (!text) {
    toast('这条消息没有可复制的正文', true);
    return;
  }
  try {
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
    else copyWithSelection(text);
    toast('已复制');
  } catch {
    try {
      copyWithSelection(text);
      toast('已复制');
    } catch {
      toast('复制失败，请手动选中文本复制', true);
    }
  }
}

function copyWithSelection(text) {
  const area = document.createElement('textarea');
  area.value = text;
  area.setAttribute('readonly', '');
  area.style.position = 'fixed';
  area.style.top = '-1000px';
  area.style.opacity = '0';
  document.body.append(area);
  area.select();
  const copied = document.execCommand('copy');
  area.remove();
  if (!copied) throw Error('复制失败');
}

function toggleExpand(messageId) {
  const conv = active();
  const message = (conv?.messages || []).find((item) => item.id === messageId);
  const element = chat.messageEls.get(messageId);
  if (!message || !element) return;
  if (chat.expanded.has(messageId)) chat.expanded.delete(messageId);
  else chat.expanded.add(messageId);
  updateMessageElement(element, message, conv);
  chat.signatures.set(messageId, messageSignature(message, conv));
  const button = element.querySelector(`[data-chat-expand="${messageId}"]`);
  button?.focus({ preventScroll: true });
}

function openLog(runId) {
  api(`runs/${runId}`)
    .then((run) =>
      showModal(
        '执行日志',
        `<p class="muted">${e(run.title || '')} · ${e(run.status || '')} · ${e(
          timeLabel(run.created_at),
        )}</p>
         <pre class="execution-log">${e((run.log || '').slice(-12000) || '暂无日志')}</pre>
         ${
           run.result
             ? `<h3>结果</h3><pre class="execution-result">${e(run.result.slice(0, 12000))}</pre>`
             : ''
         }
         <p class="muted execution-location">${e(run.directory || '')}</p>`,
      ),
    )
    .catch((error) => toast(error.message, true));
}

function runnerOptions(selected) {
  const runners = state.runners || [];
  const picked = new Map((selected || []).map((member) => [member.id, member]));
  return runners
    .map((runner) => {
      const modes = runner.modes || [];
      const chosen = picked.get(runner.id)?.sandbox || modes[0]?.id || '';
      const valid = modes.some((mode) => mode.id === chosen);
      return `<div class="chat-member-row ${runner.available ? '' : 'disabled'}"><label class="chat-member-option">
          <input type="checkbox" data-chat-member value="${e(runner.id)}"${
            picked.has(runner.id) ? ' checked' : ''
          }${runner.available ? '' : ' disabled'}>
          <span><strong>${e(runner.name)}</strong><small>${
            runner.available ? '可执行' : '没有调用入口'
          }</small></span>
        </label><label class="field">权限<select data-chat-mode="${e(
          runner.id,
        )}" aria-label="${e(runner.name)} 的会话权限"${runner.available ? '' : ' disabled'}>
          ${
            valid
              ? ''
              : `<option value="${e(chosen)}" selected disabled>原权限不可用，请重新选择</option>`
          }
          ${modes
            .map(
              (mode) =>
                `<option value="${e(mode.id)}"${
                  mode.id === chosen ? ' selected' : ''
                }>${e(mode.name)}</option>`,
            )
            .join('')}
        </select></label></div>`;
    })
    .join('');
}

function conversationDialog(conv) {
  const editing = Boolean(conv);
  const body = `<div class="chat-dialog">
    <label class="field">会话名称<input id="chat-name" maxlength="80" value="${e(
      conv?.name || '',
    )}" placeholder="例如：周报协作"></label>
    <label class="field">工作区<input id="chat-workspace" maxlength="4096" value="${e(
      conv?.workspace || '',
    )}" placeholder="选择已有项目目录"></label>
    ${directoryExtras('chat-workspace')}
    <p class="muted">所有成员的回复都在这个目录里执行；程序按各自客户端的沙箱与权限运行。</p>
    <fieldset class="chat-member-field"><legend>会话成员</legend>${runnerOptions(
      conv?.members,
    )}</fieldset>
  </div>`;
  const footer = `<button class="btn" data-action="close-modal">取消</button>
    <button class="btn primary" data-chat-save="${editing ? 'edit' : 'new'}">${
      editing ? '保存' : '创建会话'
    }</button>`;
  showModal(editing ? '成员与工作区' : '新建会话', body, footer);
  modal.querySelector('[data-chat-save]').onclick = () => saveConversation(editing);
}

async function saveConversation(editing) {
  const name = byId('chat-name').value.trim();
  const workspace = byId('chat-workspace').value.trim();
  const members = Array.from(modal.querySelectorAll('[data-chat-member]:checked')).map((input) => {
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
    `<p class="subheading">将删除「${e(
      conv.name,
    )}」移入回收站，可在任务库恢复；工作目录中的文件保留。</p>`,
    '<button class="btn" data-action="close-modal">取消</button><button class="btn danger" data-chat-confirm-delete>删除会话</button>',
  );
  modal.querySelector('[data-chat-confirm-delete]').onclick = async () => {
    try {
      await api('conversations/delete', { id: conv.id });
      saveDraft(conv.id, '');
      modal.close();
      state.activeConversation = null;
      chat.rowSignature = '';
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
      '[data-chat-send],[data-chat-cancel],[data-chat-new],[data-chat-edit],[data-chat-delete],[data-chat-log],[data-chat-save],[data-chat-tasks],[data-chat-target],[data-chat-copy],[data-chat-expand],[data-chat-retry],[data-chat-retry-turn],[data-chat-scroll-bottom]',
    );
    if (!target || target.disabled) return;
    const d = target.dataset;
    if (d.chatSend !== undefined) sendMessage();
    else if (d.chatCancel !== undefined) cancelTurn();
    else if (d.chatNew !== undefined) conversationDialog(null);
    else if (d.chatEdit !== undefined) conversationDialog(active());
    else if (d.chatDelete !== undefined) deleteConversation();
    else if (d.chatTasks !== undefined) openTaskList();
    else if (d.chatLog) openLog(d.chatLog);
    else if (d.chatTarget) toggleTarget(d.chatTarget);
    else if (d.chatCopy) copyMessage(d.chatCopy);
    else if (d.chatExpand) toggleExpand(d.chatExpand);
    else if (d.chatRetry) retryMessages([d.chatRetry]);
    else if (d.chatRetryTurn !== undefined) retryTurn();
    else if (d.chatScrollBottom !== undefined) scrollToBottom();
  });
}
