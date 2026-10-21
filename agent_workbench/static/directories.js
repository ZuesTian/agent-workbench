import { api } from '/api.js';
import { byId, e, toast } from '/ui.js';

const storageKey = 'awb-recent-directories';

export function recentDirectories() {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) || '[]');
    return Array.isArray(saved) ? saved.filter((item) => typeof item === 'string' && item) : [];
  } catch {
    return [];
  }
}

export function rememberDirectory(path) {
  const value = path.trim();
  if (!value) return;
  const next = [value, ...recentDirectories().filter((item) => item !== value)].slice(0, 8);
  try {
    localStorage.setItem(storageKey, JSON.stringify(next));
  } catch {
    /* The chosen path is still in the field. */
  }
  refreshDirectorySelects();
}

export function refreshDirectorySelects() {
  for (const select of document.querySelectorAll('[data-recent-directory]')) {
    const current = select.value;
    select.innerHTML = `<option value="">最近使用的目录</option>${recentDirectories()
      .map((path) => `<option value="${e(path)}">${e(path)}</option>`)
      .join('')}`;
    if ([...select.options].some((option) => option.value === current)) select.value = current;
  }
}

export function directoryExtras(inputId) {
  return `<div class="directory-actions"><button type="button" class="btn small" data-browse-directory="${e(inputId)}">选择本机目录</button>
    <label>最近目录<select data-recent-directory="${e(inputId)}" aria-label="最近使用的目录"><option value="">最近使用的目录</option>${recentDirectories()
      .map((path) => `<option value="${e(path)}">${e(path)}</option>`)
      .join('')}</select></label></div>`;
}

export function applyRecentDirectory(select) {
  const input = byId(select.dataset.recentDirectory);
  if (!input || !select.value) return;
  input.value = select.value;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  rememberDirectory(select.value);
}

export async function chooseDirectory(inputId) {
  toast('请在弹出的窗口中选择目录');
  let result;
  try {
    result = await api('browse-directory', {});
  } catch (error) {
    toast(error.message, true);
    return;
  }
  const input = byId(inputId);
  if (!input || !result?.path) return;
  input.value = result.path;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  rememberDirectory(result.path);
}
