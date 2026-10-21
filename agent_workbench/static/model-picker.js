import { api } from './api.js';

const catalogs = new Map();
const bindings = new WeakMap();
const supported = new Set(['codex', 'deepseek-harness', 'zcode', 'cursor']);

export function modelOptions(catalog, selected = '') {
  const rows = catalog?.models || [];
  const current = rows.find((row) => row.id === catalog?.current_model);
  const currentName = current
    ? `${current.name}${current.provider ? ` · ${current.provider}` : ''}`
    : catalog?.current_model;
  const options = [
    { value: '', label: `沿用客户端当前模型${currentName ? `（${currentName}）` : ''}` },
    ...rows.map((row) => ({
      value: row.id,
      label: `${row.name}${row.provider ? ` · ${row.provider}` : ''}`,
    })),
  ];
  const missing = !!selected && !rows.some((row) => row.id === selected);
  if (missing)
    options.push({ value: selected, label: `已保存，当前列表未找到：${selected}`, disabled: true });
  return { options, missing };
}

function load(agent, refresh) {
  if (refresh || !catalogs.has(agent)) {
    const request = api(`runners/${encodeURIComponent(agent)}/models${refresh ? '/refresh' : ''}`);
    catalogs.set(agent, request);
    request.catch(() => {
      if (catalogs.get(agent) === request) catalogs.delete(agent);
    });
  }
  return catalogs.get(agent);
}

// A response belongs to one element + one agent. Late responses must not replace
// a different node's choices or a model the user selected while refreshing.
export function mountModelPicker(select, hint, refreshButton, agent, selected, onReady = () => {}) {
  if (!select || !hint) return;
  const binding = {};
  bindings.set(select, binding);
  let selection = selected;
  select.onchange = () => {
    selection = select.value;
    select.setCustomValidity('');
  };
  const render = (catalog) => {
    const { options, missing } = modelOptions(catalog, selection);
    select.replaceChildren(
      ...options.map((item) => {
        const option = new Option(item.label, item.value);
        option.disabled = !!item.disabled;
        return option;
      }),
    );
    select.value = selection;
    select.setCustomValidity(missing ? '已保存的模型不在当前 Agent 列表中，请重新选择。' : '');
  };
  render(null);
  const refresh = async (force = false) => {
    select.disabled = true;
    select.dataset.loading = 'true';
    refreshButton.disabled = true;
    hint.textContent = '正在读取该 Agent 的本机模型目录…';
    try {
      const catalog = await load(agent, force);
      if (!select.isConnected || bindings.get(select) !== binding) return;
      render(catalog);
      hint.textContent = catalog.note;
    } catch (error) {
      if (!select.isConnected || bindings.get(select) !== binding) return;
      hint.textContent = error.message;
    } finally {
      if (select.isConnected && bindings.get(select) === binding) {
        select.disabled = false;
        select.dataset.loading = 'false';
        refreshButton.disabled = false;
        onReady();
      }
    }
  };
  refreshButton.onclick = () => refresh(true);
  if (!supported.has(agent)) {
    select.disabled = true;
    refreshButton.disabled = true;
    hint.textContent = agent ? '该客户端尚未接入执行。' : '先选择执行 Agent。';
    return;
  }
  refresh();
}
