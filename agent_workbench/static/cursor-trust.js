export const cursorTrustMarkup = `<fieldset class="cursor-workspace-trust" data-cursor-trust hidden>
  <legend>Cursor 工作目录确认</legend>
  <code data-cursor-trust-path></code>
  <label><input type="checkbox" data-cursor-trust-check>信任此工作目录（Cursor 将记住）</label>
  <small>Cursor 将保存该目录的信任记录，后续可直接访问并加载其中的项目配置。文件操作仍按所选权限执行，工具操作仍遵守 Cursor 的批准规则。</small>
  <small>若 Cursor 已信任该目录，可不勾选。更换目录、客户端或权限后需重新确认。</small>
</fieldset>`;

export function mountCursorTrust(form, enabled) {
  const field = form.querySelector('[data-cursor-trust]');
  const checkbox = field.querySelector('[data-cursor-trust-check]');
  const path = field.querySelector('[data-cursor-trust-path]');
  let scope = '';
  const sync = () => {
    const cwd = form.elements.cwd.value.trim();
    const active = enabled();
    const next = JSON.stringify([
      active,
      cwd,
      form.elements.sandbox?.value || form.elements.permission?.value || '',
    ]);
    if (next !== scope) checkbox.checked = false;
    scope = next;
    field.hidden = !active;
    checkbox.disabled = !active || !cwd;
    path.textContent = cwd || '请先填写工作目录';
  };
  form.addEventListener('input', sync);
  sync();
  return {
    sync,
    value() {
      sync();
      return !checkbox.disabled && checkbox.checked ? form.elements.cwd.value.trim() : '';
    },
    reset() {
      checkbox.checked = false;
    },
  };
}
