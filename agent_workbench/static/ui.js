import { state } from '/state.js';

const paths = {
  chat: '<path d="M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l-3 3V11.5A8.5 8.5 0 0 1 9.5 3h3a8.5 8.5 0 0 1 8.5 8.5Z"/><path d="M6 9h10M6 13h7"/>',
  network:
    '<rect x="8" y="2" width="8" height="6" rx="1.5"/><rect x="2" y="16" width="7" height="6" rx="1.5"/><rect x="15" y="16" width="7" height="6" rx="1.5"/><path d="M12 8v4M5.5 16v-4h13v4"/>',
  shield: '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8 12 3 3 5-6"/>',
  terminal: '<rect x="2" y="4" width="20" height="16" rx="3"/><path d="m6 9 3 3-3 3m6 0h5"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>',
  folder: '<path d="M3 6a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v10H3V6Z"/>',
  more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
  target:
    '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
  play: '<path d="m7 3 14 9-14 9V3Z"/>',
  layout:
    '<rect x="2" y="8" width="6" height="8" rx="1"/><rect x="16" y="2" width="6" height="8" rx="1"/><rect x="16" y="14" width="6" height="8" rx="1"/><path d="M8 12h4V6h4m-4 6v6h4"/>',
  rotate: '<path d="M4 9V4h5M4 4l6 6m10 5v5h-5m5 0-6-6"/><path d="M14 4h6v6M4 14v6h6"/>',
  expand: '<path d="M8 3H3v5m18 0V3h-5M3 16v5h5m8 0h5v-5"/>',
  focus: '<path d="M8 3H3v5m18 0V3h-5M3 16v5h5m8 0h5v-5"/><circle cx="12" cy="12" r="3"/>',
  minus: '<path d="M5 12h14"/>',
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  skill: '<path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9L12 3Z"/>',
  plug: '<path d="M8 3v5m8-5v5M6 8h12v3a6 6 0 0 1-12 0V8Zm6 9v4"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5"/>',
  robot:
    '<rect x="4" y="7" width="16" height="13" rx="4"/><path d="M12 3v4M8 12h.01M16 12h.01M8 16h8M1 11v5m22-5v5"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10H3V7Z"/>',
  history: '<path d="M3 11a9 9 0 1 1 2.7 7M3 4v7h7m2-5v6l4 2"/>',
  settings:
    '<path d="m9 3-1 3-3 1-2 4 2 3 1 4 4 3 3-1 4-1 3-4-1-3 1-4-3-2-4-1-4-2Z"/><circle cx="12" cy="12" r="3"/>',
  search: '<circle cx="10.8" cy="10.8" r="6.8"/><path d="m16 16 4.5 4.5"/>',
  arrow: '<path d="M4 12h16m-6-6 6 6-6 6"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  refresh: '<path d="M20 7a9 9 0 0 0-15-2L2 8m0-6v6h6m-4 9a9 9 0 0 0 15 2l3-3m0 6v-6h-6"/>',
  lock: '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V6a4 4 0 0 1 8 0v4m-4 4v3"/>',
  save: '<path d="M4 3h13l4 4v14H3V3h1Zm3 0v6h10V3M7 21v-8h10v8"/>',
  box: '<path d="m12 3 9 5v10l-9 5-9-5V8l9-5Zm0 10v10M3 8l9 5 9-5M8 5l9 5"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  book: '<path d="M12 5C8 2 4 3 2 4v15c4-2 7-1 10 1 3-2 6-3 10-1V4c-3-1-7-2-10 1Zm0 0v15"/>',
  monitor: '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8m-4-4v4"/>',
  list: '<path d="M9 6h12M9 12h12M9 18h12M3 6h.01M3 12h.01M3 18h.01"/>',
  trash: '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/>',
};
export const icon = (name) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.box}</svg>`;
const esc = (value) =>
  String(value ?? '').replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
  );
export const e = esc;
export const byId = (id) => document.getElementById(id);
export const timeLabel = (value) =>
  value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '';

export const modal = byId('modal');

// A dialog must hand focus back to whatever opened it. Native <dialog> usually
// does, but the opener is often re-rendered away, so remember it explicitly and
// fall back to the main landmark when it no longer exists.
let opener = null;
modal.addEventListener('close', () => {
  const target = opener;
  opener = null;
  if (target && target !== document.body && target.isConnected) {
    target.focus({ preventScroll: true });
  } else {
    byId('main-content')?.focus({ preventScroll: true });
  }
});

export function showModal(title, body, footer = '') {
  if (modal.open) modal.close();
  // A disabled opener has already lost focus by now (run() disables controls
  // before awaiting), so only fall back to the live element when nothing was
  // recorded earlier in this interaction.
  opener = opener || document.activeElement;
  modal.innerHTML = `
    <div class="modal-head"><h2 id="modal-title">${e(title)}</h2>
      <button class="icon-button" data-action="close-modal" aria-label="关闭对话框">${icon('close')}</button></div>
    <div class="modal-body">${body}</div>
    <div class="modal-footer">${footer || '<button class="btn" data-action="close-modal">关闭</button>'}</div>`;
  modal.showModal();
}

export function toast(message, error = false) {
  const node = document.createElement('div');
  node.className = (modal.open ? 'notice' : 'toast') + (error ? ' error' : '');
  node.setAttribute('role', error ? 'alert' : 'status');
  node.textContent = message;
  if (modal.open) {
    modal.querySelector('.modal-body').prepend(node);
    modal.querySelector('.modal-body').scrollTop = 0;
  } else {
    byId('toasts').append(node);
  }
  setTimeout(() => node.remove(), error ? 15000 : 4500);
}

export async function run(label, task) {
  if (state.busy) return;
  // Remember the opener before controls are disabled, otherwise a dialog opened
  // from this task has no live element to return focus to.
  opener = opener || document.activeElement;
  state.busy = true;
  document.body.setAttribute('aria-busy', 'true');
  const overlay = document.createElement('div');
  overlay.className = 'busy-overlay';
  overlay.setAttribute('role', 'status');
  overlay.innerHTML = `<div class="loader"></div><p>${e(label)}</p>`;
  const parent = modal.open ? modal : document.body;
  parent.append(overlay);
  const locked = [
    ...document.querySelectorAll(
      'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled)',
    ),
  ];
  locked.forEach((element) => {
    element.disabled = true;
  });
  try {
    return await task();
  } catch (error) {
    toast(error.message, true);
  } finally {
    locked.forEach((element) => {
      element.disabled = false;
    });
    overlay.remove();
    state.busy = false;
    document.body.removeAttribute('aria-busy');
    // The interaction ended without a dialog to hand focus back from.
    if (!modal.open) opener = null;
  }
}

export function preserveFocus(update) {
  const active = document.activeElement;
  const id = active?.id;
  const start = active?.selectionStart;
  const end = active?.selectionEnd;
  update();
  if (id) {
    const next = byId(id);
    if (next && next !== active) {
      next.focus({ preventScroll: true });
      if (typeof next.setSelectionRange === 'function' && start !== null) {
        try {
          next.setSelectionRange(start, end);
        } catch {
          /* Non-text input. */
        }
      }
    }
  }
}

export function syncMarkup(container, signature, html) {
  if (!container) return;
  if (container.dataset.markup === signature) return;
  const active = document.activeElement;
  const key =
    active && container.contains(active) ? active.getAttribute('data-focus-key') || active.id : '';
  container.dataset.markup = signature;
  container.innerHTML = html;
  if (!key) return;
  const next = [...container.querySelectorAll('[data-focus-key], [id]')].find(
    (element) => element.getAttribute('data-focus-key') === key || element.id === key,
  );
  next?.focus({ preventScroll: true });
}
