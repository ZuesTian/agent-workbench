import { api } from '/api.js';
import { e, toast } from '/ui.js';
import { directoryExtras } from '/directories.js';

export function dataSettings(path) {
  return `<h2>本地数据</h2><p>已保存的方案、资源库、配置备份、会话与任务记录位于：</p><code class="data-path">${e(path)}</code>
  <form id="data-migration-form"><label class="field">目标空目录<input id="data-target" name="path" required maxlength="4096" placeholder="例如 D:\\AgentWorkbenchData"></label>${directoryExtras('data-target')}
  <label class="field">操作<select name="mode"><option value="move">复制校验后切换数据目录</option><option value="backup">仅导出一份本地备份</option></select></label>
  <p class="muted">逐文件校验 SHA256，迁移时更新已保存方案的资源库引用，原目录保留。切换后须完全退出并重新打开工坊。外部项目文件、客户端账号与配置不随此迁移；浏览器草稿请先保存为方案。</p>
  <button class="btn" id="data-migrate" type="submit">开始迁移 / 备份</button><p id="data-migration-status" role="status" class="muted"></p></form>`;
}

export function mountDataSettings() {
  const form = document.getElementById('data-migration-form');
  const status = document.getElementById('data-migration-status');
  const button = document.getElementById('data-migrate');
  function show(result) {
    status.textContent = `已校验 ${result.files} 个文件：${result.target}。${result.restart_required ? '原数据已保留。请右键右下角托盘图标并选择“完全退出”，再从桌面入口打开工坊，新目录才会生效。' : '备份完成，当前目录未切换。'}`;
    button.disabled = result.restart_required;
  }
  api('data/status')
    .then((result) => {
      if (status.isConnected && result.migration) show(result.migration);
    })
    .catch((error) => toast(error.message, true));
  form.onsubmit = async (event) => {
    event.preventDefault();
    if (button.disabled) return;
    const backup_only = form.elements.mode.value === 'backup';
    if (
      !window.confirm(
        `${backup_only ? '复制备份到' : '复制并切换到'} ${form.elements.path.value}？原目录会保留。${backup_only ? '' : '完成后需要完全退出并重新打开工坊。'}`,
      )
    )
      return;
    button.disabled = true;
    status.textContent = '正在复制并校验文件，请勿关闭工坊…';
    try {
      show(await api('data/migrate', { path: form.elements.path.value, backup_only }));
    } catch (error) {
      status.textContent = error.message;
      button.disabled = false;
    }
  };
}
