import { api } from '/api.js';
import { e, toast } from '/ui.js';

const kinds = { run: '单任务', workflow: '协作调度', conversation: '会话' };
const statuses = {
  succeeded: '执行已完成',
  failed: '失败',
  running: '运行中',
  starting: '正在启动',
  cancelling: '正在取消',
  cancelled: '已取消',
  interrupted: '已中断',
  timed_out: '已超时',
  idle: '未运行',
};
let epoch = 0;
let pageNumber = 1;

export function taskLibraryPage() {
  return `<section class="execution-page"><div class="page-head"><div class="page-titles"><h1>任务库</h1><p>统一管理单任务、协作调度和会话。删除后进入回收站，执行日志和工作目录文件保留。</p></div></div>
    <section class="settings-card">
      <form id="task-filter" class="task-library-toolbar">
        <label class="field">查找任务<input name="q" type="search" maxlength="200" placeholder="名称、目录或日期"></label>
        <label class="field">位置<select name="scope"><option value="active">当前任务</option><option value="archived">已归档</option><option value="trash">回收站</option><option value="all">全部记录</option></select></label>
        <label class="field">分组<select name="group"><option value="">全部分组</option></select></label>
        <button class="btn" type="submit">查询 / 刷新</button>
      </form>
      <form id="task-group-form" class="task-library-toolbar"><label class="field">新分组名称<input name="name" required maxlength="60" placeholder="例如：电解液研究"></label><button class="btn" type="submit">创建分组</button><button class="btn" type="button" id="task-group-rename">重命名所选分组</button><button class="btn" type="button" id="task-group-delete">删除所选分组</button></form>
      <p class="muted" id="task-library-status" role="status"></p><div id="task-library-rows"></div>
      <div class="history-pager"><button class="btn" id="task-library-prev">上一页</button><span id="task-library-page"></span><button class="btn" id="task-library-next">下一页</button></div>
    </section></section>`;
}

export function unmountTaskLibrary() {
  epoch++;
}

export function mountTaskLibrary() {
  const current = ++epoch;
  const form = document.getElementById('task-filter');
  let ticket = 0;
  let rows = [];
  let groups = [];
  let busy = false;
  async function refresh() {
    const request = ++ticket;
    try {
      const result = await api(
        `tasks?${new URLSearchParams({ q: form.elements.q.value, scope: form.elements.scope.value, group: form.elements.group.value, page: String(pageNumber), page_size: '20' })}`,
      );
      if (current !== epoch || request !== ticket) return;
      rows = result.items;
      groups = result.groups;
      pageNumber = result.page;
      const selected = form.elements.group.value;
      form.elements.group.innerHTML =
        '<option value="">全部分组</option>' +
        groups.map((g) => `<option value="${e(g.id)}">${e(g.name)}</option>`).join('');
      form.elements.group.value = selected;
      document.getElementById('task-group-rename').disabled = !selected;
      document.getElementById('task-group-delete').disabled = !selected;
      document.getElementById('task-library-status').textContent =
        `共 ${result.total} 条记录。运行中的任务需完成或取消后再整理。`;
      document.getElementById('task-library-page').textContent =
        `第 ${result.page} / ${result.pages} 页`;
      document.getElementById('task-library-prev').disabled = result.page <= 1;
      document.getElementById('task-library-next').disabled = result.page >= result.pages;
      document.getElementById('task-library-rows').innerHTML =
        rows
          .map((r, index) => {
            const locked = ['starting', 'running', 'cancelling'].includes(r.status) || r.running;
            return `<article class="task-library-record" data-task-index="${index}"><div><h3>${e(r.title)}</h3><p class="muted">${e(kinds[r.kind])} · ${e(statuses[r.status] || r.status)} · ${e(r.created_at)} · ${r.location === 'trash' ? '回收站' : r.location === 'archived' ? '已归档' : '当前任务'}</p><p class="execution-location">${e(r.cwd)}</p></div>
        <div class="task-library-toolbar"><button class="btn small" data-task-action="open">查看记录</button><label>分组 <select data-task-group ${locked ? 'disabled' : ''}><option value="">未分组</option>${groups.map((g) => `<option value="${e(g.id)}" ${r.group_id === g.id ? 'selected' : ''}>${e(g.name)}</option>`).join('')}</select></label>
        ${r.location === 'trash' ? '<button class="btn small" data-task-action="restore">恢复</button>' : `<button class="btn small" data-task-action="${r.archived ? 'unarchive' : 'archive'}" ${locked ? 'disabled' : ''}>${r.archived ? '取消归档' : '归档'}</button><button class="btn small danger" data-task-action="delete" ${locked ? 'disabled' : ''}>删除</button>`}</div></article>`;
          })
          .join('') || '<p class="muted">这里还没有记录。</p>';
    } catch (error) {
      if (current === epoch) toast(error.message, true);
    }
  }
  async function mutate(operation) {
    if (busy) return;
    busy = true;
    try {
      await operation();
      if (current === epoch) await refresh();
    } catch (error) {
      toast(error.message, true);
    } finally {
      busy = false;
    }
  }
  form.onsubmit = (event) => {
    event.preventDefault();
    pageNumber = 1;
    refresh();
  };
  form.onchange = () => {
    pageNumber = 1;
    refresh();
  };
  document.getElementById('task-library-prev').onclick = () => {
    pageNumber--;
    refresh();
  };
  document.getElementById('task-library-next').onclick = () => {
    pageNumber++;
    refresh();
  };
  document.getElementById('task-group-form').onsubmit = (event) => {
    event.preventDefault();
    const input = event.target.elements.name;
    mutate(async () => {
      await api('task-groups/save', { name: input.value });
      input.value = '';
    });
  };
  document.getElementById('task-group-rename').onclick = () => {
    const group = groups.find((g) => g.id === form.elements.group.value);
    if (!group) return;
    const name = window.prompt('分组的新名称', group.name);
    if (name) mutate(() => api('task-groups/save', { id: group.id, name }));
  };
  document.getElementById('task-group-delete').onclick = () => {
    const id = form.elements.group.value;
    if (id && window.confirm('删除这个分组？组内任务将变为未分组，记录会保留。'))
      mutate(async () => {
        await api('task-groups/save', { id, action: 'delete' });
        form.elements.group.value = '';
      });
  };
  const records = document.getElementById('task-library-rows');
  records.onclick = (event) => {
    const button = event.target.closest('[data-task-action]');
    if (!button) return;
    const record = rows[Number(button.closest('[data-task-index]').dataset.taskIndex)];
    const action = button.dataset.taskAction;
    if (action === 'open') {
      document.dispatchEvent(new CustomEvent('awb:open-task', { detail: record }));
      return;
    }
    if (
      action === 'delete' &&
      !window.confirm('将这条记录移入回收站？可从任务库恢复，工作目录中的文件不会删除。')
    )
      return;
    mutate(() => api('tasks/update', { kind: record.kind, id: record.id, action }));
  };
  records.onchange = (event) => {
    if (!event.target.matches('[data-task-group]')) return;
    const record = rows[Number(event.target.closest('[data-task-index]').dataset.taskIndex)];
    const group_id = event.target.value;
    mutate(() =>
      api('tasks/update', { kind: record.kind, id: record.id, action: 'group', group_id }),
    );
  };
  refresh();
}
