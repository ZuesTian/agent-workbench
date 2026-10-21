import { api } from '/api.js';
import {
  state,
  names,
  selection,
  missingResources,
  clearSelection,
  loadProfile,
  importProfile,
  toggleItem,
  persist,
  restoreDraft,
  getItem,
  agentStatus,
} from '/state.js';
import { e, icon, byId, modal, showModal, run, toast } from '/ui.js';
import {
  render,
  renderSelection,
  renderResults,
  syncComposer,
  compactLayout,
  renderCatalogUpdate,
  renderScanStatus,
  paintSession,
  renderContext,
  syncContextChrome,
} from '/views.js';
import { openExecution } from '/execution.js';
import { openWorkflow } from '/workflows.js';
import { initTeamEvents } from '/team-controller.js';
import { restoreTeamDraft, preserveTeamDraftBeforeClose, setTeam, editor } from '/team-model.js';
import {
  details,
  saveDialog,
  addMcpDialog,
  importSkillDialog,
  rootDialog,
  installRootDialog,
  agentDialog,
  showPreview,
  downloadProfile,
} from '/dialogs.js';
import { applyRecentDirectory, chooseDirectory } from '/directories.js';
import {
  bindChatEvents,
  openConversationDialog,
  refreshConversations,
  selectConversation,
} from '/conversations.js';

let scanTimer = null;
let scanNotice = false;
let scanWatch = 0;

function watchScan() {
  clearTimeout(scanTimer);
  const watch = ++scanWatch;
  if (!state.catalog?.scan?.running) return;
  scanTimer = setTimeout(async () => {
    try {
      const status = await api('scan-status');
      if (watch !== scanWatch) return;
      if (status.revision !== state.catalog.scan?.revision || !status.running) {
        const catalog = await api('catalog');
        if (watch !== scanWatch) return;
        state.catalog = catalog;
        renderCatalogUpdate();
        if (!state.catalog.scan.running && scanNotice) {
          scanNotice = false;
          toast(status.error || '扫描完成');
        }
      } else {
        state.catalog.scan = status;
        renderScanStatus();
      }
      watchScan();
    } catch (error) {
      if (watch !== scanWatch) return;
      state.catalog.scan = {
        ...state.catalog.scan,
        running: false,
        error: '暂时无法获取扫描进度，请点击重新扫描重试。',
      };
      scanNotice = false;
      renderScanStatus();
    }
  }, 500);
}

async function refresh() {
  const [catalog, profiles, history, teams, desktop, runners, conversations] = await Promise.all([
    api('catalog'),
    api('profiles'),
    api('history'),
    api('teams'),
    api('desktop/status'),
    api('runners').catch(() => null),
    api('conversations').catch(() => ({ items: [] })),
  ]);
  Object.assign(state, {
    catalog,
    profiles,
    history,
    teams,
    desktop,
    runners,
    conversations: conversations.items || [],
  });
  render();
  watchScan();
}

function switchTab(tab, source = 'all') {
  if (!Object.hasOwn(names, tab)) return;
  if (tab === 'overview') state.selectedAgent = '';
  Object.assign(state, { tab, query: '', source, transport: 'all', page: 1, onlySelected: false });
  if (compactLayout.matches) state.composerOpen = false;
  if (tab === 'teams') state.composerOpen = false;
  render();
  window.scrollTo({ top: 0, behavior: 'instant' });
  // Move focus into the new view so keyboard and screen-reader users keep their
  // place; the title change is the page announcement for assistive technology.
  byId('main-content')?.focus({ preventScroll: true });
}

// The context column mirrors the Marvis "team list": it selects the object that
// the main panel shows without leaving the current section.
function handleContext(action, value) {
  const finish = () => closeContextIfNarrow();
  if (action === 'overview') {
    state.selectedAgent = '';
    finish();
    render();
    return;
  }
  if (action === 'agent') {
    state.selectedAgent = value || '';
    finish();
    render();
    return;
  }
  if (action === 'source') {
    state.source = value || 'all';
    state.page = 1;
    finish();
    render();
    return;
  }
  if (action === 'profile') {
    const profile = state.profiles.find((p) => p.id === value);
    if (profile && !profile.error) {
      loadProfile(profile);
      persist();
      renderSelection();
      toast(
        missingResources().length
          ? '组合已加载，请处理装配台中的失效来源'
          : '组合已加载，可调整后预览',
      );
    }
    finish();
    return;
  }
  if (action === 'team') {
    const team = (state.teams || []).find((t) => t.id === value);
    if (team && !team.error) {
      if (editor.dirty && !window.confirm('当前画布有未保存的改动，载入方案会替换它。是否继续？'))
        return;
      setTeam(team, true);
      finish();
      render();
      toast('已载入协作方案');
    }
    return;
  }
  if (action === 'click') {
    document.querySelector(value)?.click();
    finish();
    return;
  }
  if (action === 'run') {
    openExecution(value);
    finish();
    return;
  }
  if (action === 'workflow-run') {
    openWorkflow(value);
    finish();
    return;
  }
  if (action === 'conversation') {
    finish();
    selectConversation(value);
    return;
  }
  if (action === 'new-conversation') {
    finish();
    openConversationDialog(null);
    return;
  }
  if (action === 'scroll') {
    const target = document.querySelector(value);
    if (!target) return;
    target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    target.focus?.({ preventScroll: true });
    finish();
  }
}

function closeContextIfNarrow() {
  if (!window.matchMedia('(max-width: 1180px)').matches) return;
  state.contextOpen = false;
  syncContextChrome();
}

// Execution modules publish their history so the context column can list it.
document.addEventListener('awb:execution-rows', (event) => {
  state.executionRows = event.detail.rows || [];
  state.executionSelected = event.detail.selectedId || '';
  if (state.tab === 'execution' && state.catalog) renderContext();
});
document.addEventListener('awb:workflow-rows', (event) => {
  state.workflowRows = event.detail.rows || [];
  state.workflowSelected = event.detail.selectedId || '';
  if (state.tab === 'workflows' && state.catalog) renderContext();
});
document.addEventListener('awb:conversations-changed', async () => {
  try {
    await refreshConversations();
    if (state.catalog) renderContext();
  } catch {
    /* The next refresh retries. */
  }
});
document.addEventListener('awb:conversation-changed', () => {
  if (state.tab === 'chat' && state.catalog) renderContext();
});

const actions = {
  'focus-search': () => {
    switchTab('skills');
    byId('resource-search')?.focus();
  },
  theme: () => {
    const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem('awb-theme', theme);
    } catch {
      /* Keep the current theme. */
    }
  },
  'close-modal': () => modal.close(),
  'toggle-composer': () => {
    state.composerOpen = !state.composerOpen;
    renderSelection();
  },
  'toggle-context': () => {
    state.contextOpen = !state.contextOpen;
    syncContextChrome();
    if (state.contextOpen) byId('context-column')?.querySelector('button')?.focus();
  },
  'close-context': () => {
    state.contextOpen = false;
    syncContextChrome();
    byId('context-toggle')?.focus();
  },
  'selected-filter': () => {
    state.onlySelected = !state.onlySelected;
    state.page = 1;
    renderResults();
  },
  'reset-filters': () => {
    state.query = '';
    state.source = 'all';
    state.transport = 'all';
    state.onlySelected = false;
    state.page = 1;
    render();
  },
  scan: async () => {
    if (state.catalog.scan?.running) return;
    document.querySelector('[data-action="scan"]').disabled = true;
    try {
      state.catalog = await api('scan', {});
      scanNotice = true;
      renderScanStatus();
      watchScan();
      if (!state.catalog.scan.running) {
        scanNotice = false;
        renderCatalogUpdate();
        toast(state.catalog.scan.error || '扫描完成');
      }
    } catch (error) {
      renderScanStatus();
      toast(error.message, true);
    }
  },
  clear: () => {
    clearSelection();
    persist();
    renderSelection();
  },
  preview: () =>
    run('正在检查目标与资源内容…', async () => {
      if (missingResources().length) throw Error('请先处理失效的资源与目标');
      showPreview(await api('preview', selection()));
    }),
  'confirm-apply': () =>
    run('正在备份并应用装配…', async () => {
      if (!state.plan) throw Error('配置目标已变化，请重新预览');
      const result = await api('apply', { id: state.plan.id });
      modal.close();
      await refresh();
      toast(result.message);
    }),
  'save-profile': saveDialog,
  'save-as-profile': () => saveCurrent(true),
  'confirm-save': () => saveCurrent(false),
  'export-profile': () =>
    downloadProfile({
      ...selection(),
      name: state.profileName || '自由装配',
      description: state.profileDescription,
    }),
  'add-mcp': addMcpDialog,
  'confirm-mcp': () =>
    run('正在保存 MCP 定义…', async () => {
      let raw;
      try {
        raw = JSON.parse(byId('mcp-config').value);
      } catch {
        throw Error('JSON 格式不正确，请检查引号和逗号');
      }
      state.catalog = await api('library/mcp', {
        name: byId('mcp-name').value,
        kind: byId('mcp-kind').value,
        config: raw,
      });
      modal.close();
      switchTab('mcps', 'library');
      toast('MCP 已加入资源库');
    }),
  'import-skill': importSkillDialog,
  'confirm-import-skill': () =>
    run('正在复制并校验 Skill 资源…', async () => {
      state.catalog = await api('library/skill', { path: byId('skill-path').value });
      modal.close();
      switchTab('skills', 'library');
      toast('Skill 已导入资源库');
    }),
  'add-root': rootDialog,
  'add-install-root': installRootDialog,
  'confirm-install-root': () =>
    run('正在查找程序文件…', async () => {
      state.catalog = await api('install-roots/add', { path: byId('install-root-path').value });
      modal.close();
      render();
      toast('程序目录已添加，扫描完成');
    }),
  'confirm-root': () =>
    run('正在扫描新增目录…', async () => {
      state.catalog = await api('roots/add', { path: byId('root-path').value });
      modal.close();
      render();
      toast('扫描目录已添加');
    }),
  'add-agent': agentDialog,
  'confirm-agent': () =>
    run('正在接入 Agent…', async () => {
      state.catalog = await api('agents/add', {
        name: byId('agent-name').value,
        config: byId('agent-config').value,
        skills: byId('agent-skills').value,
        key: byId('agent-key').value,
        kind: byId('agent-kind').value,
      });
      modal.close();
      render();
      toast('Agent 已加入工作空间');
    }),
  'refresh-history': () =>
    run('正在读取备份记录…', async () => {
      state.history = await api('history');
      render();
    }),
  'confirm-restore': () =>
    run('正在校验并恢复备份…', async () => {
      const result = await api('restore', { id: modal.dataset.restoreId });
      modal.close();
      await refresh();
      toast(result.message);
    }),
  'confirm-delete-profile': () =>
    run('正在移除方案…', async () => {
      await api('profiles/delete', { id: modal.dataset.deleteId });
      state.profiles = await api('profiles');
      if (state.profileId === modal.dataset.deleteId) {
        state.profileId = null;
        persist();
      }
      modal.close();
      render();
    }),
  'import-profile': () => {
    byId('profile-file').value = '';
    byId('profile-file').click();
  },
  minimize: async () => {
    const result = await api('desktop/minimize', {});
    if (!result.minimized) toast(result.message || '当前没有可最小化的工坊窗口');
  },
  shutdown: () =>
    showModal(
      '完全退出 Agent 工坊',
      '<p class="subheading">将关闭工坊窗口、托盘图标和后台服务；正在运行的任务会被取消。若希望任务继续，请选择“最小化”。</p>',
      '<button class="btn" data-action="close-modal">取消</button><button class="btn primary" data-action="confirm-shutdown">完全退出</button>',
    ),
  'confirm-shutdown': async () => {
    if (!preserveTeamDraftBeforeClose())
      throw Error('无法保存本机草稿，请先保存或导出协作方案，再完全退出。');
    await api('shutdown', {});
    modal.close();
    byId('app').innerHTML =
      '<div class="boot"><div class="brand-symbol">a<span>·</span></div><h2>工坊已完全退出</h2><p>后台服务正在结束。从桌面入口可重新打开。</p></div>';
  },
};
async function saveCurrent(asNew) {
  return run('正在保存装配方案…', async () => {
    const profile = await api('profiles/save', {
      ...selection(),
      name: byId('profile-name').value,
      description: byId('profile-description').value,
      ...(!asNew && state.profileId ? { id: state.profileId } : {}),
    });
    state.profileId = profile.id;
    state.profileName = profile.name;
    state.profileDescription = profile.description;
    state.profiles = await api('profiles');
    modal.close();
    persist();
    render();
    toast('方案已保存');
  });
}

document.addEventListener('click', async (event) => {
  const button = event.target.closest('button');
  if (!button || button.disabled || state.busy) return;
  try {
    const d = button.dataset;
    if (d.tab) switchTab(d.tab);
    else if (d.ctx) handleContext(d.ctx, d.value);
    else if (d.assemble) {
      state.agents.add(d.assemble);
      state.composerOpen = true;
      state.plan = null;
      persist();
      renderSelection();
      toast('已加入装配目标，可在装配台预览');
    } else if (d.browseDirectory) await chooseDirectory(d.browseDirectory);
    else if (d.action) await actions[d.action]?.();
    else if (d.toggle || d.choose) {
      const open = state.composerOpen;
      toggleItem(d.toggle || d.choose, d.id, Boolean(d.choose));
      if (compactLayout.matches) state.composerOpen = open;
      if (d.choose) modal.close();
      persist();
      renderSelection();
      if (compactLayout.matches) toast('组合已更新，可打开装配台继续');
    } else if (d.remove) {
      state[d.remove].delete(d.id);
      state.plan = null;
      persist();
      renderSelection();
    } else if (d.detail) {
      await run('正在读取资源详情…', () => details(d.detail, d.id));
    } else if (d.page) {
      state.page = Number(d.page);
      renderResults();
      window.scrollTo({ top: 0, behavior: 'instant' });
      byId('resource-search')?.focus({ preventScroll: true });
    } else if (d.view) {
      state.view = d.view;
      persist();
      render();
    } else if (d.agentView) {
      switchTab(d.kind, d.agentView);
    } else if (d.agentDetail) {
      const agent = getItem('agents', d.agentDetail);
      showModal(
        agent.name,
        `<dl class="detail-grid"><dt>程序状态</dt><dd>${e(agentStatus(agent))}</dd><dt>程序入口</dt><dd>${agent.installations.map((x) => `${e(x.path)}<small> · ${e(x.source)}</small>`).join('<br>') || '未找到，可能已卸载或位于其他位置'}</dd><dt>MCP 配置</dt><dd>${e(agent.config)}${agent.mcp_writable ? '' : '（仅盘点）'}</dd><dt>Skill 目录</dt><dd>${e(agent.skills_path || '未配置')}</dd><dt>配置发现依据</dt><dd>${agent.configuration_evidence.map(e).join('<br>') || '尚无配置文件'}</dd></dl>${agent.error ? `<div class="notice error">${e(agent.error)}</div>` : ''}<p class="subheading">${e(agent.note)}</p><div class="notice info">程序文件存在不表示 MCP 已启用或连接成功；请在目标客户端确认。</div>`,
      );
    } else if (d.load) {
      const profile = state.profiles.find((p) => p.id === d.load);
      if (profile && !profile.error) {
        loadProfile(profile);
        persist();
        renderSelection();
        toast(
          missingResources().length
            ? '组合已加载，请处理装配台中的失效来源'
            : '组合已加载，可调整后预览',
        );
      }
    } else if (d.export) {
      downloadProfile(state.profiles.find((p) => p.id === d.export));
    } else if (d.deleteProfile) {
      modal.dataset.deleteId = d.deleteProfile;
      showModal(
        '删除装配方案',
        '<p class="subheading">仅删除这份方案记录，已经应用的客户端配置保留。</p>',
        '<button class="btn" data-action="close-modal">取消</button><button class="btn danger" data-action="confirm-delete-profile">删除方案</button>',
      );
    } else if (d.restore) {
      modal.dataset.restoreId = d.restore;
      showModal(
        '恢复变更前的内容',
        '<div class="notice">将恢复这次应用所改动的配置与 Skill。如果文件之后又被修改，会停止恢复并保留新的内容。</div>',
        '<button class="btn" data-action="close-modal">取消</button><button class="btn primary" data-action="confirm-restore">检查并恢复</button>',
      );
    } else if (d.removeRoot || d.removeAgent || d.removeInstallRoot) {
      await run('正在更新工作空间…', async () => {
        state.catalog = await api(
          d.removeInstallRoot
            ? 'install-roots/remove'
            : d.removeRoot
              ? 'roots/remove'
              : 'agents/remove',
          d.removeInstallRoot
            ? { path: d.removeInstallRoot }
            : d.removeRoot
              ? { path: d.removeRoot }
              : { id: d.removeAgent },
        );
        render();
        toast('接入记录已移除，原文件保留');
      });
    }
  } catch (error) {
    toast(error.message, true);
  }
});

document.addEventListener('input', (event) => {
  if (event.target.dataset.filter === 'query' && !state.busy) {
    state.query = event.target.value;
    state.page = 1;
    renderResults();
  }
});

document.addEventListener('change', async (event) => {
  const input = event.target;
  if (state.busy) return;
  if (input.dataset.recentDirectory) {
    applyRecentDirectory(input);
    return;
  }
  if (input.dataset.target) {
    if (input.checked) state.agents.add(input.dataset.target);
    else {
      state.agents.delete(input.dataset.target);
      delete state.configTargets[input.dataset.target];
    }
    state.plan = null;
    persist();
    renderSelection();
  } else if (input.dataset.configTarget) {
    state.configTargets[input.dataset.configTarget] = input.value;
    state.plan = null;
    persist();
    modal.querySelector('[data-action="confirm-apply"]').disabled = true;
  } else if (input.dataset.filter) {
    const key = input.dataset.filter;
    if (key === 'query') return;
    state[key] = key === 'group' ? input.checked : input.value;
    state.page = 1;
    persist();
    renderResults();
  } else if (input.dataset.compose) {
    const key = input.dataset.compose;
    state[key] = key === 'overwrite' ? input.checked : input.value;
    state.plan = null;
    persist();
    renderSelection();
  } else if (input.id === 'agent-kind') {
    byId('agent-key').value =
      { codex: 'mcp_servers', opencode: 'mcp', vscode: 'servers' }[input.value] || 'mcpServers';
  } else if (input.id === 'profile-file' && input.files[0]) {
    try {
      if (input.files[0].size > 2_000_000) throw Error('方案文件超过 2 MB');
      const profile = importProfile(JSON.parse(await input.files[0].text()), state.catalog);
      loadProfile(profile);
      persist();
      render();
      const missing = missingResources().length;
      toast(
        missing ? `已保留 ${missing} 项失效资源或目标，请处理后再预览` : '方案已加载，可调整或保存',
        missing > 0,
      );
    } catch (error) {
      toast(error.message, true);
    }
  }
});

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && state.contextOpen && !modal.open) {
    state.contextOpen = false;
    syncContextChrome();
    byId('context-toggle')?.focus();
    return;
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault();
    if (modal.open) return;
    if (!['skills', 'mcps', 'roles'].includes(state.tab)) switchTab('skills');
    if (compactLayout.matches && state.composerOpen) {
      state.composerOpen = false;
      renderSelection();
    }
    byId('resource-search')?.focus();
  }
});

byId('composer-dialog').addEventListener('close', () => {
  if (compactLayout.matches && state.composerOpen && !byId('composer-dialog').open) {
    state.composerOpen = false;
    renderSelection();
  }
});
compactLayout.addEventListener('change', () => {
  if (state.catalog) syncComposer();
});
modal.addEventListener('close', () => {
  if (state.catalog) syncComposer();
});

async function boot() {
  try {
    const token = new URLSearchParams(location.hash.slice(1)).get('token');
    if (token) {
      await api('auth', { token });
      history.replaceState(null, '', location.pathname);
    }
    restoreDraft();
    restoreTeamDraft();
    await refresh();
    watchActivity();
  } catch (error) {
    byId('app').innerHTML = `<div class="boot"><div class="brand-symbol">a<span>·</span></div>
      <h1>暂时无法打开工坊</h1><p>${e(error.message)}</p><p>请从桌面上的「Agent 工坊」入口重新打开。</p></div>`;
  }
}
async function watchActivity() {
  try {
    state.activity = await api('activity');
    paintSession();
  } catch {
    /* The next poll retries. The current page stays usable. */
  }
  setTimeout(watchActivity, 4000);
}

document.addEventListener('team-assembly', renderSelection);
initTeamEvents();
bindChatEvents();
boot();
