import { api } from '/api.js';
import { state, loadProfile, persist, agentName, filterResources } from '/state.js';
import { e, icon, byId, modal, showModal, toast, run, preserveFocus } from '/ui.js';
import {
  editor,
  createTeam,
  newNode,
  setTeam,
  changed,
  storeTeamDraft,
  preserveTeamDraftBeforeClose,
  exportTeam,
  importTeam,
  reparent,
  removeNode,
  autoLayout,
  previewSteps,
  taskBrief,
} from '/team-model.js';
import { teamPage, inspector, outline, readingList, saveStatus, activity } from '/team-view.js';
import { mountModelPicker } from '/model-picker.js';
import {
  mountCanvas,
  unmountCanvas,
  paintGraph,
  fitGraph,
  focusNode,
  zoomGraph,
  readGraph,
  showOverview,
  syncGraphChrome,
} from '/team-canvas.js';

let pendingReplacement = null;
let picker = null;
const node = () => editor.team.nodes.find((item) => item.id === editor.selected);
function settleGraph() {
  if (editor.graphView === 'read' || editor.viewport.userSet) return;
  fitGraph({ shrink: true });
}

function mountNodeModels() {
  const selected = node();
  if (selected)
    mountModelPicker(
      byId('team-node-model'),
      byId('team-model-note'),
      byId('team-model-refresh'),
      selected.agentId,
      selected.model,
    );
}

function updateStatus() {
  if (byId('team-save-status')) byId('team-save-status').textContent = saveStatus();
}
function updateInspector() {
  preserveFocus(() => {
    if (byId('team-inspector-body')) byId('team-inspector-body').innerHTML = inspector();
    if (byId('team-outline')) byId('team-outline').innerHTML = outline();
    if (byId('team-reading')) byId('team-reading').innerHTML = readingList();
    if (editor.mode === 'activity' && byId('team-activity')) {
      byId('team-activity').innerHTML = activity();
    }
  });
  document.querySelectorAll('[data-team-panel]').forEach((element) => {
    element.classList.toggle('active', element.dataset.teamPanel === editor.inspectorTab);
  });
  updateStatus();
  mountNodeModels();
  if (editor.mode === 'activity' && editor.preview && byId('activity-scroll')) {
    byId('activity-scroll').scrollTop = byId('activity-scroll').scrollHeight;
  }
}
function select(id) {
  editor.selected = id;
  editor.inspectorTab = 'config';
  preserveFocus(() => {
    paintGraph();
    updateInspector();
  });
  storeTeamDraft();
}
function renderTeam() {
  byId('main-content').innerHTML = teamPage();
  mountTeamUI();
}
export function mountTeamUI() {
  if (editor.mode === 'activity') {
    unmountCanvas();
    return;
  }
  mountCanvas({
    select,
    update: updateInspector,
    error: (message) => toast(message, true),
    busy: () => state.busy,
  });
  mountNodeModels();
}

function download(content, suffix, type) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download =
    (editor.team.name.replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').slice(0, 70) || 'team') + suffix;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function replaceDraft(next) {
  if (!editor.dirty) return next();
  pendingReplacement = next;
  showModal(
    '替换当前草稿',
    '<p>当前协作方案有未保存的更改。继续后将载入另一个方案。</p><p class="muted">可取消并先保存，或导出当前协作 JSON。</p>',
    '<button class="btn" data-action="close-modal">取消</button><button class="btn primary" data-team-action="confirm-replace">替换草稿</button>',
  );
}

async function save(asNew = false) {
  await run('正在保存协作方案…', async () => {
    const document = exportTeam(editor.team);
    if (!asNew && editor.team.id)
      Object.assign(document, { id: editor.team.id, revision: editor.team.revision });
    const saved = await api('teams/save', document);
    const selected = editor.selected;
    setTeam(saved, true);
    editor.selected = selected;
    storeTeamDraft();
    state.teams = await api('teams');
    paintGraph();
    updateInspector();
    toast('协作方案已保存到本机');
  });
}

async function showSaved() {
  await run('正在读取协作方案…', async () => {
    state.teams = await api('teams');
    showModal(
      '我的协作方案',
      state.teams.length
        ? `<div class="saved-teams">${state.teams
            .map(
              (
                team,
              ) => `<article><div><h3>${e(team.name)}</h3><p>${e(team.error || `${team.nodes.length} 个节点 · ${team.updated_at.replace('T', ' ').slice(0, 19)}`)}</p></div>
        ${team.error ? '' : `<button class="btn small" data-team-load="${e(team.id)}">载入</button><button class="icon-button" data-team-delete="${e(team.id)}" aria-label="删除 ${e(team.name)}">${icon('trash')}</button>`}</article>`,
            )
            .join('')}</div>`
        : '<div class="inspector-empty">还没有保存的协作方案。编辑当前画布后，点击“保存”。</div>',
    );
  });
}

function sourceLabel(kind, item) {
  if (kind === 'skills') return item.path || '';
  return `${agentName(item.agent)} · ${item.scope || item.source || ''}`;
}
function openPicker(kind) {
  picker = {
    nodeId: node().id,
    kind,
    page: 1,
    refs: new Map(node()[kind].map((ref) => [ref.id, { ...ref }])),
  };
  const sources = state.catalog.agents
    .map((agent) => `<option value="${e(agent.id)}">${e(agent.name)}</option>`)
    .join('');
  showModal(
    `为 ${node().name} 绑定 ${kind === 'skills' ? 'Skills' : 'MCP'}`,
    `<label class="search">${icon('search')}<input id="team-resource-search" type="search" placeholder="搜索名称或用途…" aria-label="搜索可绑定资源"></label>
      <div class="picker-filters"><select id="team-resource-source" aria-label="筛选来源"><option value="all">所有来源</option>${sources}<option value="library">我的资源库</option>${kind === 'skills' ? '<option value="custom">自定义目录</option>' : ''}</select>
      <label><input id="team-resource-group" type="checkbox" checked>合并同名来源</label></div>
      <div id="team-picked-summary"></div><div id="team-resource-options"></div><nav id="team-resource-pages" class="history-pager" aria-label="资源分页"></nav>
      <p class="muted">这里只保存资源引用。同名资源可以比较来源后再绑定。实际配置在装配台预览后应用；多个节点绑定同一客户端时共享该客户端的全局配置。</p>`,
    '<button class="btn" data-action="close-modal">取消</button><button class="btn primary" data-team-action="confirm-bind">保存绑定</button>',
  );
  renderPicker();
  byId('team-resource-search').focus();
}
function renderPicker() {
  if (!picker || !byId('team-resource-options')) return;
  const search = byId('team-resource-search');
  const source = byId('team-resource-source');
  const group = byId('team-resource-group');
  const groups = filterResources(picker.kind, {
    query: search?.value || '',
    source: source?.value || 'all',
    group: group ? group.checked : true,
  }).map((entry) => {
    const chosen = entry.variants.find((item) => picker.refs.has(item.id));
    return chosen ? { ...entry, item: chosen } : entry;
  });
  const pageSize = 24;
  const pages = Math.max(1, Math.ceil(groups.length / pageSize));
  picker.page = Math.min(Math.max(1, picker.page), pages);
  const visible = groups.slice((picker.page - 1) * pageSize, picker.page * pageSize);
  preserveFocus(() => {
    byId('team-picked-summary').innerHTML =
      `<span>已选 ${picker.refs.size} 项 · 匹配 ${groups.length} 项</span><button class="text-link" data-team-action="clear-bind">清空选择</button>`;
    byId('team-resource-options').innerHTML =
      visible
        .map((entry, index) => {
          const item = entry.item;
          const variants =
            entry.variants.length > 1
              ? `<div class="picker-variants">${entry.variants
                  .map(
                    (variant) =>
                      `<label class="picker-variant"><input type="radio" name="team-variant-${index}" id="team-variant-${e(variant.id)}" data-team-variant="${e(variant.id)}" ${variant.id === item.id ? 'checked' : ''}><span><strong>${e(picker.kind === 'skills' ? [...new Set((variant.sources || []).map((row) => agentName(row.agent)))].join(' · ') : agentName(variant.agent))}</strong><code>${e(sourceLabel(picker.kind, variant))}</code></span></label>`,
                  )
                  .join('')}</div>`
              : `<code>${e(sourceLabel(picker.kind, item))}</code>`;
          return `<article class="resource-choice-row"><input type="checkbox" id="team-bind-${e(item.id)}" data-team-resource="${e(item.id)}" data-focus-key="${e(item.id)}" ${picker.refs.has(item.id) ? 'checked' : ''}><div><label for="team-bind-${e(item.id)}"><strong>${e(item.name)}</strong><small>${e(item.description || (item.summary?.transport?.toUpperCase() ?? ''))}</small></label>${variants}</div></article>`;
        })
        .join('') || '<p class="inspector-empty">没有匹配的资源。</p>';
    byId('team-resource-pages').innerHTML =
      `<button type="button" class="btn small" data-team-page="${picker.page - 1}" ${picker.page === 1 ? 'disabled' : ''}>上一页</button><span>第 ${picker.page} / ${pages} 页</span><button type="button" class="btn small" data-team-page="${picker.page + 1}" ${picker.page === pages ? 'disabled' : ''}>下一页</button>`;
  });
}

function assemble() {
  const selected = node();
  const missing = ['skills', 'mcps'].flatMap((kind) =>
    selected[kind].filter((ref) => !state.catalog[kind].some((item) => item.id === ref.id)),
  );
  if (missing.length) throw Error('此节点包含来源未匹配的资源，请先移除或重新绑定');
  const load = () => {
    loadProfile({
      skills: selected.skills.map((ref) => ref.id),
      mcps: selected.mcps.map((ref) => ref.id),
      agents: [selected.agentId],
      mode: 'merge',
      name: `${editor.team.name} · ${selected.name}`.slice(0, 80),
      description: selected.prompt.slice(0, 1000),
    });
    persist();
    modal.close();
    document.dispatchEvent(new CustomEvent('team-assembly'));
    toast('节点能力已载入装配台，请预览客户端配置变更');
  };
  if (state.skills.size || state.mcps.size || state.agents.size) {
    pendingReplacement = load;
    showModal(
      '替换装配台组合',
      '<p>装配台已有组合。载入此节点将用它的资源和客户端替换当前装配草稿。</p>',
      '<button class="btn" data-action="close-modal">取消</button><button class="btn primary" data-team-action="confirm-replace">载入节点组合</button>',
    );
  } else load();
}

function importRole() {
  showModal(
    '引用本机角色定义',
    `<label class="team-field">选择角色<select id="team-role-source"><option value="">请选择…</option>${state.catalog.roles.map((role) => `<option value="${e(role.id)}">${e(role.name)}</option>`).join('')}</select></label><p class="muted">将角色文件内容复制到当前节点的职责中，原文件保留。角色过长时会提示。</p>`,
    '<button class="btn" data-action="close-modal">取消</button><button class="btn primary" data-team-action="confirm-role">引用职责</button>',
  );
}

const actions = {
  save: () => save(),
  'save-as': () => save(true),
  saved: showSaved,
  export: () =>
    download(JSON.stringify(exportTeam(editor.team), null, 2), '.team.json', 'application/json'),
  brief: () =>
    download(
      taskBrief(exportTeam(editor.team), state.catalog),
      '.md',
      'text/markdown;charset=utf-8',
    ),
  import: () => {
    byId('team-file').value = '';
    byId('team-file').click();
  },
  'confirm-replace': () => {
    const next = pendingReplacement;
    pendingReplacement = null;
    modal.close();
    next?.();
  },
  add: () => {
    if (editor.team.nodes.length >= 100) throw Error('一个方案最多 100 个节点');
    const parent = node();
    const added = newNode(parent?.role === 'tutor' ? 'employee' : 'sub', parent?.id || null);
    editor.team.nodes.push(added);
    if (parent) parent.collapsed = false;
    editor.selected = added.id;
    editor.inspectorTab = 'config';
    autoLayout(editor.team);
    changed();
    renderTeam();
    byId('team-node-name')?.focus();
    byId('team-node-name')?.select();
  },
  layout: () => {
    autoLayout(editor.team);
    changed();
    paintGraph();
    updateInspector();
    settleGraph();
  },
  direction: () => {
    autoLayout(editor.team, editor.team.layout === 'horizontal' ? 'vertical' : 'horizontal');
    changed();
    paintGraph();
    updateInspector();
    settleGraph();
  },
  expand: () => {
    editor.team.nodes.forEach((item) => {
      item.collapsed = false;
    });
    changed();
    paintGraph();
    updateInspector();
    settleGraph();
  },
  fit: showOverview,
  'read-nodes': readGraph,
  'show-map': showOverview,
  'toggle-inspector': () => {
    editor.inspectorOpen = !editor.inspectorOpen;
    syncGraphChrome();
  },
  'zoom-in': () => zoomGraph(1.15),
  'zoom-out': () => zoomGraph(1 / 1.15),
  'delete-node': () =>
    showModal(
      '删除当前节点',
      `<p>删除“${e(node().name)}”的方案记录。它的直接下级将连接到原上级，绑定的资源和客户端配置保留。</p>`,
      '<button class="btn" data-action="close-modal">取消</button><button class="btn danger" data-team-action="confirm-delete-node">删除节点</button>',
    ),
  'confirm-delete-node': () => {
    const parent = node().parentId;
    removeNode(editor.team, node().id);
    editor.selected = parent || editor.team.nodes[0].id;
    autoLayout(editor.team);
    changed();
    modal.close();
    renderTeam();
  },
  'clear-bind': () => {
    picker.refs.clear();
    renderPicker();
  },
  'confirm-bind': () => {
    const target = editor.team.nodes.find((item) => item.id === picker.nodeId);
    if (picker.refs.size > 200) throw Error('每个节点最多绑定 200 项同类资源');
    target[picker.kind] = [...picker.refs.values()];
    picker = null;
    changed();
    modal.close();
    paintGraph();
    updateInspector();
  },
  assemble,
  'import-role': importRole,
  'confirm-role': () =>
    run('正在读取角色定义…', async () => {
      const id = byId('team-role-source').value;
      if (!id) throw Error('请先选择角色');
      const result = await api('roles/' + encodeURIComponent(id));
      if (result.content.length > 10000)
        throw Error('角色定义超过 10000 字符，请在职责中手动摘录所需部分');
      node().prompt = result.content;
      changed();
      modal.close();
      paintGraph();
      updateInspector();
    }),
  preview: () => {
    editor.inspectorTab = 'preview';
    updateInspector();
  },
  'start-preview': () => {
    editor.preview = { steps: previewSteps(exportTeam(editor.team)), index: 0, log: [] };
    editor.inspectorTab = 'preview';
    updateInspector();
  },
  'next-preview': () => {
    const preview = editor.preview;
    const step = preview?.steps[preview.index];
    if (!step) return;
    preview.log.push({
      id: step.id,
      name: step.name,
      text: step.kind === 'review' ? '预演：模拟审核通过，继续交接。' : `预演：${step.text}`,
    });
    preview.index++;
    updateInspector();
  },
  'reject-preview': () => {
    const preview = editor.preview;
    const step = preview?.steps[preview.index];
    if (step?.kind !== 'review') return;
    if (preview.log.length >= 1000) throw Error('预演记录已达上限，请重新开始');
    preview.log.push({
      id: step.id,
      name: step.name,
      text: '预演：审核未通过，需修改项记在本步。自动调度不会因此打回或重试；要改结果请调整任务后再次手动执行。',
    });
    updateInspector();
  },
  'confirm-delete-team': () =>
    run('正在删除方案记录…', async () => {
      const team = state.teams.find((item) => item.id === modal.dataset.teamDelete);
      await api('teams/delete', { id: team.id, revision: team.revision });
      if (editor.team.id === team.id) {
        delete editor.team.id;
        delete editor.team.revision;
        delete editor.team.updated_at;
        changed();
        updateStatus();
      }
      state.teams = await api('teams');
      modal.close();
      toast('协作方案记录已删除');
    }),
};

export function initTeamEvents() {
  document.addEventListener('click', async (event) => {
    const button = event.target.closest('button');
    if (!button || button.disabled || state.busy) return;
    const data = button.dataset;
    try {
      if (data.teamMode) {
        editor.mode = data.teamMode;
        renderTeam();
      } else if (data.teamOpenNode) {
        editor.mode = 'canvas';
        editor.selected = data.teamOpenNode;
        editor.inspectorTab = 'config';
        renderTeam();
        focusNode(data.teamOpenNode);
      } else if (data.teamAction) await actions[data.teamAction]?.();
      else if (data.teamTemplate) {
        replaceDraft(() => {
          setTeam(createTeam(data.teamTemplate));
          storeTeamDraft();
          renderTeam();
        });
      } else if (data.teamLoad) {
        const saved = state.teams.find((item) => item.id === data.teamLoad);
        replaceDraft(() => {
          modal.close();
          setTeam(saved, true);
          storeTeamDraft();
          renderTeam();
        });
      } else if (data.teamDelete) {
        modal.dataset.teamDelete = data.teamDelete;
        showModal(
          '删除协作方案',
          '<p>删除本机保存的方案记录，客户端和资源文件保留。</p>',
          '<button class="btn" data-action="close-modal">取消</button><button class="btn danger" data-team-action="confirm-delete-team">删除方案</button>',
        );
      } else if (data.teamPage && picker) {
        picker.page = Number(data.teamPage);
        renderPicker();
      } else if (data.teamSelect || data.teamFocus) {
        select(data.teamSelect || data.teamFocus);
        if (data.teamFocus) {
          focusNode(data.teamFocus);
          updateStatus();
        }
      } else if (data.teamPanel) {
        editor.inspectorTab = data.teamPanel;
        updateInspector();
      } else if (data.teamCollapse) {
        const target = editor.team.nodes.find((item) => item.id === data.teamCollapse);
        target.collapsed = !target.collapsed;
        changed();
        paintGraph();
        updateInspector();
      } else if (data.teamRole) {
        node().role = data.teamRole;
        changed();
        paintGraph();
        updateInspector();
      } else if (data.teamBind) openPicker(data.teamBind);
      else if (data.teamUnbind) {
        node()[data.teamUnbind] = node()[data.teamUnbind].filter((ref) => ref.id !== data.ref);
        changed();
        paintGraph();
        updateInspector();
      }
      if (Object.keys(data).some((key) => key.startsWith('team')))
        document.querySelectorAll('.team-menu[open]').forEach((menu) => {
          menu.open = false;
        });
    } catch (error) {
      toast(error.message, true);
    }
  });
  document.addEventListener('input', (event) => {
    if (state.busy) return;
    const input = event.target;
    if (input.id === 'team-resource-search') {
      picker.page = 1;
      return renderPicker();
    }
    if (input.dataset.teamField) {
      editor.team[input.dataset.teamField] = input.value;
      changed();
      updateStatus();
      if (editor.inspectorTab === 'preview' || editor.mode === 'activity') updateInspector();
    } else if (
      input.dataset.nodeField &&
      !['parentId', 'agentId'].includes(input.dataset.nodeField)
    ) {
      node()[input.dataset.nodeField] = input.value;
      changed();
      updateStatus();
      paintGraph();
      if (byId('team-outline')) byId('team-outline').innerHTML = outline();
      if (byId('team-reading')) byId('team-reading').innerHTML = readingList();
    }
  });
  document.addEventListener('change', async (event) => {
    if (state.busy) return;
    const input = event.target;
    try {
      if (input.id === 'team-resource-source' || input.id === 'team-resource-group') {
        picker.page = 1;
        renderPicker();
      } else if (input.dataset.nodeField === 'parentId') {
        reparent(editor.team, node().id, input.value || null);
        autoLayout(editor.team);
        changed();
        paintGraph();
        updateInspector();
        settleGraph();
      } else if (input.dataset.nodeField === 'agentId') {
        node().agentId = input.value;
        node().model = '';
        changed();
        paintGraph();
        updateInspector();
      } else if (input.dataset.teamResource && picker) {
        const item = state.catalog[picker.kind].find(
          (ref) => ref.id === input.dataset.teamResource,
        );
        if (input.checked) {
          for (const [id] of picker.refs) {
            const existing = state.catalog[picker.kind].find((ref) => ref.id === id);
            if (
              existing &&
              (picker.kind === 'skills'
                ? existing.slug.toLowerCase() === item.slug.toLowerCase()
                : existing.name === item.name)
            )
              picker.refs.delete(id);
          }
          picker.refs.set(item.id, { id: item.id, name: item.name });
        } else picker.refs.delete(item.id);
        // Preserve the active checkbox while reflecting duplicate-source replacement.
        byId('team-picked-summary').querySelector('span').textContent =
          `已选 ${picker.refs.size} 项`;
        document.querySelectorAll('[data-team-resource]').forEach((checkbox) => {
          checkbox.checked = picker.refs.has(checkbox.dataset.teamResource);
        });
      } else if (input.dataset.teamVariant && picker) {
        const item = state.catalog[picker.kind].find((ref) => ref.id === input.dataset.teamVariant);
        const siblings = state.catalog[picker.kind].filter((other) => other.name === item.name);
        if (siblings.some((other) => picker.refs.has(other.id))) {
          for (const other of siblings) picker.refs.delete(other.id);
          picker.refs.set(item.id, { id: item.id, name: item.name });
        }
        renderPicker();
      } else if (input.id === 'team-file' && input.files[0]) {
        if (input.files[0].size > 2_000_000) throw Error('协作文件超过 2 MB');
        const imported = importTeam(JSON.parse(await input.files[0].text()));
        replaceDraft(() => {
          setTeam(imported);
          storeTeamDraft();
          renderTeam();
          toast('拓扑已导入，请核对本机客户端和资源绑定');
        });
      }
    } catch (error) {
      toast(error.message, true);
    }
  });
  document.addEventListener('keydown', (event) => {
    if (
      state.tab === 'teams' &&
      !modal.open &&
      (event.ctrlKey || event.metaKey) &&
      event.key.toLowerCase() === 's'
    ) {
      event.preventDefault();
      if (!state.busy) save();
    }
  });
  window.addEventListener('beforeunload', (event) => {
    if (!preserveTeamDraftBeforeClose()) {
      event.preventDefault();
      event.returnValue = '';
    }
  });
}
