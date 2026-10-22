import {
  state,
  names,
  agentName,
  agentStatus,
  getItem,
  groupResources,
  missingResources,
} from '/state.js';
import { e, icon, byId, timeLabel, preserveFocus } from '/ui.js';
import { teamPage } from '/team-view.js';
import { mountTeamUI } from '/team-controller.js';
import { unmountCanvas } from '/team-canvas.js';
import { executionPage, mountExecutionUI, unmountExecutionUI } from '/execution.js';
import { workflowsPage, mountWorkflowsUI, unmountWorkflowsUI } from '/workflows.js';
import { chatPage, chatContextItems, mountChatUI, unmountChatUI } from '/conversations.js';

const PAGE_SIZE = 24;
export const compactLayout = window.matchMedia('(max-width: 1100px)');

function nav(tab, symbol, label, count) {
  return `<button class="nav-button ${state.tab === tab ? 'active' : ''}" data-tab="${tab}"
    ${state.tab === tab ? 'aria-current="page"' : ''} title="${label}"><span class="nav-icon">${icon(symbol)}</span>
    <span>${label}</span>${count !== undefined ? `<span class="nav-count">${count}</span>` : ''}</button>`;
}

function ensureShell() {
  if (byId('main-content')) return;
  byId('app').innerHTML = `<div class="app-shell">
    <aside class="sidebar" aria-label="主导航">
      <a class="brand" href="#main-content"><img src="/icon.svg" alt=""><div><strong>Agent 工坊</strong><small>资源 · 装配 · 协作</small></div></a>
      <button class="sidebar-search" data-action="focus-search">${icon('search')}<span>搜索资源</span><kbd>Ctrl K</kbd></button>
      <nav id="navigation"></nav>
      <div class="sidebar-footer"><p><span class="local-dot"></span><span id="desktop-state">本地运行</span></p><p>关闭窗口后仍在后台运行</p>
        <div class="window-actions"><button id="minimize-window" data-action="minimize" title="最小化窗口，任务继续运行">${icon('minus')} 最小化</button>
        <button data-action="shutdown" title="关闭窗口和后台服务">${icon('power')} 完全退出</button></div>
        <span class="version">Agent 工坊 · 1.0.0</span></div>
    </aside>
    <aside id="context-column" class="context-column" aria-label="当前页面的对象列表"></aside>
    <div class="workspace"><header class="topbar"><div class="breadcrumb">本机工作空间 <span>/</span> <b id="breadcrumb-name"></b></div>
      <div class="topbar-actions"><button type="button" id="context-toggle" class="btn context-toggle" data-action="toggle-context" aria-controls="context-column" aria-expanded="false">对象列表</button><details class="session-menu"><summary id="session-summary" aria-label="后台状态、运行中任务和完全退出"><span class="local-dot"></span><span id="session-summary-text">本地运行</span></summary><div>
        <p id="session-label">本地运行</p>
        <p id="session-tasks">当前无运行中任务。关闭窗口后程序仍在后台运行。</p>
        <button type="button" data-tab="execution">查看运行中任务</button>
        <button type="button" data-action="minimize" id="session-minimize">最小化</button>
        <button type="button" data-action="shutdown">完全退出</button>
      </div></details><button class="icon-button" data-action="theme" aria-label="切换明暗主题" title="切换明暗主题">${icon('sun')}</button>
      <button class="icon-button" data-action="scan" aria-label="重新扫描本机资源" title="重新扫描">${icon('refresh')}</button>
      <button id="composer-trigger" class="btn" data-action="toggle-composer" aria-controls="composer-panel">${icon('layers')}<span>装配台</span><b id="draft-count">0</b></button></div></header>
      <div id="scan-status" class="scan-status" role="status" aria-live="polite" hidden></div>
      <main class="main" id="main-content" tabindex="-1"></main>
    </div><aside id="composer-panel" class="composer" aria-label="装配工作台"></aside>
    <button type="button" id="context-backdrop" class="context-backdrop" data-action="close-context" hidden aria-label="关闭对象列表"></button>
  </div>`;
}

function renderChrome() {
  const counts = state.catalog.counts;
  byId('navigation').innerHTML = `<p class="nav-caption">工作空间</p>
    ${nav('overview', 'grid', '总览')}${nav('chat', 'chat', '会话')}
    ${nav('teams', 'network', '协作编排')}
    ${nav('execution', 'terminal', '执行任务')}
    ${nav('workflows', 'play', '自动调度')}
    <p class="nav-caption">能力资源</p>${nav('skills', 'skill', 'Skills', counts.skill_names)}
    ${nav('mcps', 'plug', 'MCP', counts.mcp_names)}${nav('roles', 'robot', '子 Agent', counts.roles)}
    <p class="nav-caption">管理</p>${nav('profiles', 'layers', '装配方案', state.profiles.length)}
    ${nav('history', 'history', '备份记录')}${nav('settings', 'settings', '设置')}`;
  byId('breadcrumb-name').textContent = names[state.tab];
  document.title = `${names[state.tab]} · Agent 工坊`;
  byId('draft-count').textContent = state.skills.size + state.mcps.size;
  byId('composer-trigger').setAttribute('aria-expanded', String(state.composerOpen));
  byId('composer-trigger').classList.toggle('active', state.composerOpen);
  byId('desktop-state').textContent = state.desktop.available ? '后台运行 · 托盘可用' : '本地运行';
  byId('minimize-window').disabled = !state.desktop.available;
  paintSession();
  renderScanStatus();
  renderContext();
  syncContextChrome();
}

export function paintSession() {
  const active = (state.activity?.runs || 0) + (state.activity?.workflows || 0);
  const summary = byId('session-summary-text');
  const label = byId('session-label');
  const tasks = byId('session-tasks');
  const minimize = byId('session-minimize');
  const running = state.desktop?.available ? '后台运行 · 托盘可用' : '本地运行';
  if (summary) summary.textContent = active ? `运行中 ${active}` : running;
  if (label) label.textContent = running;
  if (tasks) {
    tasks.textContent = active
      ? `有 ${active} 个任务仍在运行。关闭窗口后它们会继续；完全退出会取消它们。`
      : '当前无运行中任务。关闭窗口后程序仍在后台运行。';
  }
  if (minimize) minimize.disabled = !state.desktop?.available;
}

export function renderScanStatus() {
  const banner = byId('scan-status');
  if (!banner) return;
  const scan = state.catalog.scan || {};
  const message = scan.running
    ? scan.cached || scan.ready
      ? '正在后台更新，当前显示上次扫描结果。'
      : '正在发现本机资源，可以先使用协作与任务功能。'
    : scan.error || scan.cache_warning || '';
  banner.textContent = message;
  banner.hidden = !message;
  banner.classList.toggle('error', Boolean(scan.error));
  document.querySelector('[data-action="scan"]').disabled = Boolean(scan.running);
}

export function renderCatalogUpdate() {
  if (['overview', 'skills', 'mcps', 'roles', 'settings'].includes(state.tab)) {
    render();
  } else {
    // A background refresh must not replace task forms or an edited team canvas.
    preserveFocus(() => {
      renderChrome();
      syncComposer();
    });
  }
}

export function render() {
  if (!state.catalog) return;
  preserveFocus(() => {
    ensureShell();
    renderChrome();
    const pages = {
      overview,
      chat: chatPage,
      teams: teamPage,
      execution: executionPage,
      workflows: workflowsPage,
      skills: () => resources('skills'),
      mcps: () => resources('mcps'),
      roles,
      profiles,
      history: historyPage,
      settings: settingsPage,
    };
    byId('main-content').classList.toggle('collaboration-page', state.tab === 'teams');
    byId('main-content').innerHTML = pages[state.tab]();
    syncComposer();
    if (state.tab === 'teams') mountTeamUI();
    else unmountCanvas();
    if (state.tab === 'execution') mountExecutionUI();
    else unmountExecutionUI();
    if (state.tab === 'workflows') mountWorkflowsUI();
    else unmountWorkflowsUI();
    if (state.tab === 'chat') mountChatUI();
    else unmountChatUI();
  });
}

export function renderSelection() {
  preserveFocus(() => {
    renderChrome();
    syncComposer();
    if (['skills', 'mcps'].includes(state.tab)) renderResults();
    if (state.tab === 'overview') {
      const cards = byId('agent-cards');
      if (cards) cards.innerHTML = agentSections(byId('config-only-list')?.open);
    }
  });
}

export function syncComposer() {
  const shell = document.querySelector('.app-shell');
  const panel = byId('composer-panel');
  const drawer = byId('composer-dialog');
  shell.classList.toggle('with-composer', state.composerOpen && !compactLayout.matches);
  panel.hidden = !state.composerOpen || compactLayout.matches;
  if (compactLayout.matches) {
    panel.innerHTML = '';
    if (state.composerOpen) {
      drawer.innerHTML = composer();
      if (!drawer.open && !byId('modal').open) drawer.showModal();
    } else if (drawer.open) drawer.close();
  } else {
    if (drawer.open) drawer.close();
    drawer.innerHTML = '';
    panel.innerHTML = state.composerOpen ? composer() : '';
  }
}

const pageSymbols = {
  overview: 'grid',
  chat: 'chat',
  teams: 'network',
  execution: 'terminal',
  workflows: 'play',
  skills: 'skill',
  mcps: 'plug',
  roles: 'robot',
  profiles: 'layers',
  history: 'history',
  settings: 'settings',
};

function heading(kicker, title, subtitle, action = '') {
  return `<div class="page-head" data-page="${state.tab}">
    <div class="page-avatar">${icon(pageSymbols[state.tab] || 'box')}</div>
    <div class="page-titles"><h1>${title}</h1><p class="page-handle">${e(kicker)}${subtitle ? ` · ${subtitle}` : ''}</p></div>
    ${action ? `<div class="page-actions">${action}</div>` : ''}</div>`;
}

function contextItem({
  action,
  value,
  symbol,
  text,
  tone,
  title,
  sub,
  count,
  countLabel,
  active,
  id,
}) {
  const value_ = value === undefined ? '' : String(value);
  const icon_ = text
    ? `<span class="ctx-initial" ${tone ? `style="--agent-color:${e(tone[0])}"` : ''}>${e(text)}</span>`
    : `<span class="ctx-icon" ${tone ? `style="--tone-bg:${tone[0]};--tone-ink:${tone[1]}"` : ''}>${icon(symbol || 'box')}</span>`;
  const countText = countLabel || (count === undefined || count === null ? '' : String(count));
  return `<button ${id ? `id="${e(id)}"` : ''} class="context-item ${active ? 'active' : ''}" data-ctx="${action}" data-value="${e(value_)}">${icon_}<div><strong>${e(title)}</strong>${sub ? `<small>${e(sub)}</small>` : ''}</div>${count !== undefined && count !== null ? `<span class="ctx-count" title="${e(countText)}">${e(String(count))}</span>` : ''}</button>`;
}

function clientKind(agent) {
  if (agent.kind === 'codex') return 'TOML 客户端';
  if (agent.kind === 'dsh') return 'Harness 客户端';
  if (agent.kind === 'zcode') return 'Zcode 客户端';
  return 'JSON 客户端';
}

function contextPanel(title, note, items, extra = '') {
  return `<div class="context-head"><h2>${e(title)}</h2><span>${e(note || '')}</span></div>
    <div class="context-list">${items.join('')}</div>${extra}`;
}

export function renderContext() {
  const column = byId('context-column');
  if (!column || !state.catalog) return;
  preserveFocus(() => {
    column.innerHTML = contextColumn();
  });
}

export function syncContextChrome() {
  const shell = document.querySelector('.app-shell');
  const narrow = window.matchMedia('(max-width: 1180px)').matches;
  const open = Boolean(state.contextOpen);
  shell?.classList.toggle('context-open', open);
  byId('context-column')?.setAttribute('aria-hidden', String(narrow && !open));
  const toggle = byId('context-toggle');
  if (toggle) {
    toggle.setAttribute('aria-expanded', String(open));
    toggle.hidden = !narrow;
  }
  const backdrop = byId('context-backdrop');
  if (backdrop) backdrop.hidden = !(narrow && open);
}

window.matchMedia('(max-width: 1180px)').addEventListener('change', () => {
  if (state.catalog) syncContextChrome();
});

function contextColumn() {
  const c = state.catalog;
  if (state.tab === 'chat') {
    const { items, note } = chatContextItems();
    return contextPanel(
      '会话',
      `${(state.conversations || []).length} 个`,
      items,
      note ? `<p class="context-note">${e(note)}</p>` : '',
    );
  }
  if (state.tab === 'overview') {
    const items = [
      contextItem({
        action: 'overview',
        value: '',
        symbol: 'layers',
        title: '工作空间概览',
        sub: `${c.counts.installed_agents} 个程序 · ${c.counts.skill_names} 个 Skill`,
        active: !state.selectedAgent,
      }),
      ...c.agents.map((a) =>
        contextItem({
          action: 'agent',
          value: a.id,
          text: a.name[0],
          tone: [a.color, '#2b2b2b'],
          title: a.name,
          sub: clientKind(a),
          count: a.skill_count,
          countLabel: `${a.skill_count} 份 Skill 来源`,
          active: state.selectedAgent === a.id,
        }),
      ),
    ];
    return contextPanel('我的 Agents', `${c.agents.length} 个`, items);
  }
  if (state.tab === 'skills' || state.tab === 'mcps') {
    const kind = state.tab;
    const sources = [
      { id: 'all', name: '全部来源', count: c[kind].length, sub: '未合并的来源份数' },
      ...c.agents
        .filter((a) => (kind === 'skills' ? a.skill_count : a.mcp_count) > 0)
        .map((a) => ({
          id: a.id,
          name: a.name,
          count: kind === 'skills' ? a.skill_count : a.mcp_count,
          sub: '该客户端上的来源份数',
        })),
      {
        id: 'library',
        name: '我的资源库',
        count: c[kind].filter((x) =>
          kind === 'skills' ? x.sources.some((s) => s.agent === 'library') : x.agent === 'library',
        ).length,
        sub: '本地导入与自定义',
      },
    ];
    if (kind === 'skills') {
      sources.push({
        id: 'custom',
        name: '自定义目录',
        count: c.skills.filter((x) => x.sources.some((s) => s.agent === 'custom')).length,
        sub: '手动添加的扫描目录',
      });
    }
    return contextPanel(
      kind === 'skills' ? 'Skill 来源' : 'MCP 来源',
      `${c[kind].length} 份来源`,
      sources.map((s) =>
        contextItem({
          action: 'source',
          value: s.id,
          symbol: s.id === 'all' ? 'layers' : 'plug',
          title: s.name,
          sub: s.sub,
          count: s.count,
          countLabel: `${s.count} 份来源`,
          active: state.source === s.id,
        }),
      ),
      '<p class="context-note">这里的数字是来源份数。主区打开「合并同名来源」后，显示的是名称个数。</p>',
    );
  }
  if (state.tab === 'roles') {
    const byAgent = new Map();
    for (const role of c.roles) byAgent.set(role.agent, (byAgent.get(role.agent) || 0) + 1);
    const items = [
      contextItem({
        action: 'source',
        value: 'all',
        symbol: 'robot',
        title: '全部角色',
        sub: '只读浏览',
        count: c.roles.length,
        active: state.source === 'all',
      }),
      ...[...byAgent.entries()].map(([agent, count]) =>
        contextItem({
          action: 'source',
          value: agent,
          symbol: 'robot',
          title: agentName(agent),
          sub: '角色定义来源',
          count,
          active: state.source === agent,
        }),
      ),
    ];
    return contextPanel('角色来源', `${c.roles.length} 个`, items);
  }
  if (state.tab === 'teams') {
    const teams = (state.teams || []).filter((t) => !t.error);
    return contextPanel(
      '协作方案',
      `${teams.length} 份`,
      [
        contextItem({
          action: 'click',
          value: '.team-view-switch button',
          symbol: 'network',
          title: '协作画布',
          sub: '节点、职责与汇报关系',
        }),
        contextItem({
          action: 'click',
          value: '.team-view-switch button:nth-child(2)',
          symbol: 'chat',
          title: '流程动态',
          sub: '交接顺序与审核预演',
        }),
        ...teams.map((t) =>
          contextItem({
            action: 'team',
            value: t.id,
            symbol: 'layers',
            title: t.name || '未命名方案',
            sub: `${(t.nodes || []).length} 个节点`,
            count: (t.nodes || []).length,
          }),
        ),
        contextItem({
          action: 'click',
          value: '[data-team-template="software"]',
          symbol: 'plus',
          title: '产品研发组',
          sub: '新建示例：设计、实现与质量审核',
        }),
        contextItem({
          action: 'click',
          value: '[data-team-template="blank"]',
          symbol: 'plus',
          title: '空白画布',
          sub: '从一个负责人开始',
        }),
        contextItem({
          action: 'click',
          value: '[data-team-action="saved"]',
          symbol: 'folder',
          title: '我的方案',
          sub: '载入已保存的协作方案',
        }),
      ],
      '<p class="context-note">画布编辑会保存为本机方案。</p>',
    );
  }
  if (state.tab === 'execution') {
    const rows = state.executionRows || [];
    return contextPanel(
      '执行',
      rows.length ? `${rows.length} 条` : '',
      [
        contextItem({
          action: 'click',
          value: '#execution-cwd',
          symbol: 'plus',
          title: '新建任务',
          sub: '选择目录并写下任务',
        }),
        ...rows.map((row) =>
          contextItem({
            action: 'run',
            value: row.id,
            symbol: row.workflow ? 'layers' : 'terminal',
            title: (row.title || '').replace(/^#\s*/, ''),
            sub: `${row.workflow ? '协作步骤' : '单任务'} · ${row.status}${row.created_at ? ` · ${timeLabel(row.created_at)}` : ''}`,
            active: state.executionSelected === row.id,
            id: `ctx-run-${row.id}`,
          }),
        ),
      ],
      rows.length
        ? '<p class="context-note">更早的记录可在主区搜索或翻页查看。</p>'
        : '<p class="context-note">任务完成后，记录会出现在这里。</p>',
    );
  }
  if (state.tab === 'workflows') {
    const rows = state.workflowRows || [];
    return contextPanel(
      '自动调度',
      rows.length ? `${rows.length} 条` : '',
      [
        ...rows.map((row) =>
          contextItem({
            action: 'workflow-run',
            value: row.id,
            symbol: 'layers',
            title: (row.title || '').replace(/^#\s*/, ''),
            sub: `${row.status} · ${row.steps} 步`,
            active: state.workflowSelected === row.id,
            id: `ctx-workflow-${row.id}`,
          }),
        ),
        contextItem({
          action: 'scroll',
          value: '#workflow-bindings',
          symbol: 'play',
          title: '节点执行检查',
          sub: '绑定、入口与权限',
        }),
        contextItem({
          action: 'scroll',
          value: '#workflow-result',
          symbol: 'layers',
          title: '汇总结果',
          sub: '完成后生成',
        }),
      ],
      rows.length
        ? '<p class="context-note">点击记录在主区查看每一步的日志。</p>'
        : '<p class="context-note">开始调度后，记录会出现在这里。</p>',
    );
  }
  if (state.tab === 'profiles') {
    return contextPanel(
      '装配方案',
      `${state.profiles.length} 份`,
      state.profiles.length
        ? state.profiles.map((p) =>
            contextItem({
              action: 'profile',
              value: p.id,
              symbol: 'layers',
              title: p.name,
              sub: `${p.skills.length} Skills · ${p.mcps.length} MCP`,
              active: state.profileId === p.id,
            }),
          )
        : [
            contextItem({
              action: 'click',
              value: '[data-action="save-profile"]',
              symbol: 'plus',
              title: '保存当前组合',
              sub: '暂无已保存方案',
            }),
          ],
      '<p class="context-note">加载方案会填入装配台，需预览后应用。</p>',
    );
  }
  if (state.tab === 'history') {
    return contextPanel(
      '备份记录',
      `${state.history.length} 条`,
      state.history.length
        ? state.history.map((m) =>
            contextItem({
              action: 'scroll',
              value: `#history-${m.id}`,
              symbol: 'history',
              title: m.name || '自由装配',
              sub: timeLabel(m.created_at),
              count: m.entries?.length || 0,
            }),
          )
        : [
            contextItem({
              action: 'click',
              value: '[data-action="refresh-history"]',
              symbol: 'refresh',
              title: '刷新记录',
              sub: '暂无变更记录',
            }),
          ],
    );
  }
  return contextPanel('设置分区', '', [
    contextItem({
      action: 'scroll',
      value: '#settings-data',
      symbol: 'folder',
      title: '本地数据',
      sub: '保存位置与监听地址',
    }),
    contextItem({
      action: 'scroll',
      value: '#settings-install',
      symbol: 'monitor',
      title: '程序查找目录',
      sub: '免安装与自选位置',
    }),
    contextItem({
      action: 'scroll',
      value: '#settings-skills',
      symbol: 'skill',
      title: 'Skill 目录',
      sub: '递归查找 SKILL.md',
    }),
    contextItem({
      action: 'scroll',
      value: '#settings-agents',
      symbol: 'robot',
      title: '自定义 Agent',
      sub: '接入其他客户端',
    }),
    contextItem({
      action: 'scroll',
      value: '#settings-scan',
      symbol: 'refresh',
      title: '扫描记录',
      sub: '上次扫描与问题',
    }),
    contextItem({
      action: 'scroll',
      value: '#settings-scope',
      symbol: 'shield',
      title: '装配范围',
      sub: '写入与备份边界',
    }),
  ]);
}

function overview() {
  const selected = state.catalog.agents.find((a) => a.id === state.selectedAgent);
  if (selected) return agentDetail(selected);
  const c = state.catalog.counts;
  return (
    heading(
      'LOCAL WORKSPACE',
      '你的 Agent 工作空间',
      '把合适的能力，交给合适的 Agent。',
      `<button class="btn primary" data-tab="teams">${icon('network')} 协作编排</button>`,
    ) +
    `<div class="stats">
      ${stat('找到的程序', c.installed_agents, `${c.config_only_agents} 项仅发现配置`, 'monitor', 'overview')}
      ${stat('Skill 名称', c.skill_names, `${c.skills} 份本地文件`, 'skill', 'skills')}
      ${stat('MCP 名称', c.mcp_names, `${c.mcps} 份来源配置`, 'plug', 'mcps')}
      ${stat('子 Agent', c.roles, '本地角色定义', 'robot', 'roles')}
    </div>
    <section class="hero-banner"><div class="hero-copy"><span class="hero-eyebrow">从能力到协作</span>
      <h2>绑定 Skill 与 MCP，设计分工，然后开始工作</h2>
      <p>预览装配、导出任务说明，或把节点交给自动调度。</p></div>
      <div class="hero-actions"><button class="btn hero-btn" data-tab="teams">打开画布 ${icon('arrow')}</button></div></section>
    <div class="section-title"><h2>快捷入口</h2>
      <button class="text-link" data-action="add-install-root">${icon('plus')} 添加程序目录</button></div>
    <div class="capability-grid">
      <button class="capability-card" data-tab="execution"><span class="capability-symbol">${icon('terminal')}</span><strong>执行任务</strong><small>选择客户端，写下任务并查看日志</small></button>
      <button class="capability-card" data-tab="workflows"><span class="capability-symbol">${icon('play')}</span><strong>自动调度</strong><small>按画布层级串行执行并汇总</small></button>
      <button class="capability-card" data-tab="skills"><span class="capability-symbol">${icon('skill')}</span><strong>Skill 库</strong><small>${c.skill_names} 个名称，挑选能力</small></button>
      <button class="capability-card" data-tab="mcps"><span class="capability-symbol">${icon('plug')}</span><strong>MCP 库</strong><small>${c.mcp_names} 个名称，连接工具</small></button>
      <button class="capability-card" data-tab="profiles"><span class="capability-symbol">${icon('layers')}</span><strong>装配方案</strong><small>保存常用组合，下次直接加载</small></button>
      <button class="capability-card" data-tab="history"><span class="capability-symbol">${icon('history')}</span><strong>备份记录</strong><small>查看变更，恢复到装配之前</small></button>
    </div>
    ${state.catalog.issues.length ? `<div class="notice"><strong>${state.catalog.issues.length} 项扫描提示</strong><button class="text-link" data-tab="settings">查看详情 ${icon('arrow')}</button></div>` : ''}
    <p class="page-note">通过 Windows 安装记录、程序文件、PATH 与扩展入口查找客户端，覆盖各本地磁盘的常用安装目录。<br>资源包含插件目录与历史版本；数量不代表已启用或连接成功。${state.catalog.scanned_at ? `上次扫描 ${timeLabel(state.catalog.scanned_at)}` : '等待首次扫描完成。'}</p>`
  );
}

function namedResources(items) {
  const seen = new Map();
  for (const item of items) {
    if (!seen.has(item.name)) seen.set(item.name, item);
  }
  return [...seen.values()];
}

function resourceChips(kind, items, agentId) {
  const unique = namedResources(items);
  if (!items.length) return '<p class="muted">还没有发现来源。</p>';
  const shown = unique.slice(0, 8);
  return `<div class="agent-resource-list">${shown
    .map(
      (item) =>
        `<button type="button" class="agent-resource-chip" data-detail="${kind}" data-id="${e(item.id)}">${e(item.name)}</button>`,
    )
    .join(
      '',
    )}${unique.length > shown.length ? `<button type="button" class="text-link" data-kind="${kind}" data-agent-view="${e(agentId)}">查看全部 ${unique.length} 个名称</button>` : ''}</div>
    <p class="muted">${items.length} 份来源${unique.length === items.length ? '' : `，合并同名后 ${unique.length} 个名称`}</p>`;
}

function agentDetail(agent) {
  const kindLabel = agent.kind === 'codex' ? 'TOML' : agent.kind === 'dsh' ? 'Cordis YAML' : 'JSON';
  const skills = state.catalog.skills.filter((item) =>
    item.sources?.some((source) => source.agent === agent.id),
  );
  const mcps = state.catalog.mcps.filter((item) => item.agent === agent.id);
  return `<div class="page-head" data-page="overview">
    <div class="page-avatar agent-avatar-lg" style="--agent-color:${e(agent.color)}">${e(agent.name[0])}</div>
    <div class="page-titles"><h1>${e(agent.name)}</h1>
      <p class="page-handle">@${e(agent.id)} · ${e(agentStatus(agent))} · ${kindLabel} 配置</p></div>
    <div class="page-actions"><button class="btn" data-agent-detail="${e(agent.id)}">${icon('folder')} 发现与配置</button></div>
  </div>
  <div class="agent-stat-row">
    <span><b>${agent.skill_count}</b> 份 Skill 来源</span><span><b>${agent.mcp_count}</b> 份 MCP 来源</span>
    <span><b>${agent.installations.length}</b> 程序入口</span><span><b>${agent.configuration_evidence.length}</b> 配置依据</span>
  </div>
  <p class="page-intro">${e(agent.note || '本地客户端')}</p>
  <button class="capability-card hero" data-kind="skills" data-agent-view="${e(agent.id)}">
    <span class="capability-eyebrow">下一步</span>
    <strong>在 Skill 库查看这些来源</strong>
    <small>按这个客户端筛选。来源份数和合并后的名称个数会分开显示。</small>
    <span class="capability-arrow">${icon('arrow')}</span>
  </button>
  <section class="agent-resource-section"><h2>已发现的 Skill</h2>${resourceChips('skills', skills, agent.id)}</section>
  <section class="agent-resource-section"><h2>已发现的 MCP</h2>${resourceChips('mcps', mcps, agent.id)}</section>
  <div class="capability-grid">
    <button class="capability-card" data-agent-detail="${e(agent.id)}"><span class="capability-symbol">${icon('folder')}</span><strong>配置文件</strong><small>${e(agent.config || '未配置')}</small></button>
    <button class="capability-card" data-tab="execution"><span class="capability-symbol">${icon('terminal')}</span><strong>执行任务</strong><small>用该客户端运行任务</small></button>
    <button class="capability-card" data-tab="teams"><span class="capability-symbol">${icon('network')}</span><strong>协作节点</strong><small>把它绑定到协作画布</small></button>
    <button class="capability-card" data-assemble="${e(agent.id)}"><span class="capability-symbol">${icon('layers')}</span><strong>装配到 ${e(agent.name)}</strong><small>设为装配目标并预览变更</small></button>
  </div>
  ${agent.error ? `<div class="notice error">${e(agent.error)}</div>` : ''}
  <p class="page-note">程序存在不表示已启动或连接成功；装配只写入该客户端的全局配置，不创建独立运行环境。</p>`;
}

function stat(label, value, note, symbol, tab) {
  return `<button class="stat" data-tab="${tab}"><span class="stat-top">${label}${icon(symbol)}</span>
    <strong>${value}</strong><small>${note}</small></button>`;
}

function agentSections(open = false) {
  if (!state.catalog.scanned_at && state.catalog.scan?.running) {
    return empty('正在发现本机 Agent', '扫描完成后会自动显示；现在可以切换到其他页面。');
  }
  const primary = state.catalog.agents.filter((a) => a.installation_status !== 'config_only');
  const configs = state.catalog.agents.filter((a) => a.installation_status === 'config_only');
  return `<div class="agent-grid">${primary.map(agentCard).join('') || empty('尚未找到程序', '可添加安装目录，或在设置中接入自定义 Agent。')}</div>
    ${configs.length ? `<details id="config-only-list" class="config-only-list" ${open ? 'open' : ''}><summary>仅发现配置 / 待检查 <span class="count">${configs.length}</span></summary><p class="subheading">未找到程序入口，可能是卸载残留，也可能装在尚未覆盖的位置。这些记录不计入上方程序数量，已有 Skill 与 MCP 仍可作为来源。</p><div class="agent-grid">${configs.map(agentCard).join('')}</div></details>` : ''}`;
}

function agentCard(agent) {
  const path = agent.installations[0]?.path || agent.config;
  return `<article class="agent-card">
    <div class="agent-head"><div class="agent-avatar" style="--agent-color:${e(agent.color)}">${e(agent.name[0])}</div>
      <div><h3>${e(agent.name)}</h3><span class="agent-id">${e(agent.kind === 'codex' ? 'TOML' : agent.kind === 'dsh' ? 'Cordis YAML · MCP 仅盘点' : 'JSON')} 配置</span></div>
      <span class="status ${agent.error ? 'error' : agent.running_hint ? 'running' : ''}">${agent.error ? '配置待检查' : e(agentStatus(agent))}</span></div>
    <div class="agent-counts"><button data-agent-view="${e(agent.id)}" data-kind="skills"><b>${agent.skill_count}</b> Skills ${icon('arrow')}</button>
      <button data-agent-view="${e(agent.id)}" data-kind="mcps"><b>${agent.mcp_count}</b> MCP ${icon('arrow')}</button></div>
    <p class="agent-path" title="${e(path)}">${e(path)}</p>
    <div class="agent-actions"><button class="text-link" data-agent-detail="${e(agent.id)}">发现与配置 ${icon('arrow')}</button>
      <label><input id="card-target-${e(agent.id)}" type="checkbox" data-target="${e(agent.id)}" ${agent.error ? 'disabled' : ''} ${state.agents.has(agent.id) ? 'checked' : ''}>设为目标</label></div>
  </article>`;
}

function toolbar(kind) {
  return `<div class="toolbar"><label class="search">${icon('search')}
    <input id="resource-search" data-filter="query" type="search" placeholder="搜索名称、用途或来源…" value="${e(state.query)}" aria-label="搜索资源"><kbd>Ctrl K</kbd></label>
    ${kind === 'mcps' ? `<select id="transport-filter" data-filter="transport" aria-label="筛选连接方式"><option value="all">所有连接</option>${['stdio', 'http', 'sse'].map((t) => `<option value="${t}" ${state.transport === t ? 'selected' : ''}>${t.toUpperCase()}</option>`).join('')}</select>` : ''}
    ${kind !== 'roles' ? `<div class="view-switch" aria-label="显示方式"><button data-view="grid" class="${state.view === 'grid' ? 'active' : ''}" aria-label="卡片视图" aria-pressed="${state.view === 'grid'}">${icon('grid')}</button><button data-view="list" class="${state.view === 'list' ? 'active' : ''}" aria-label="列表视图" aria-pressed="${state.view === 'list'}">${icon('list')}</button></div>` : ''}
  </div>`;
}

function resources(kind) {
  const skill = kind === 'skills';
  return (
    heading(
      skill ? 'SKILL LIBRARY' : 'MCP LIBRARY',
      skill ? '为 Agent 挑选能力' : '连接工具与服务',
      skill ? '查看完整说明，选定来源，再加入装配台。' : '查看连接配置与来源，组合自己的工具集。',
      `<button class="btn primary" data-action="${skill ? 'import-skill' : 'add-mcp'}">${icon('plus')}${skill ? '导入 Skill' : '新增 MCP'}</button>`,
    ) +
    toolbar(kind) +
    `<div class="filters-line"><div class="quick-filters">
      <button id="selected-filter" class="filter-pill ${state.onlySelected ? 'active' : ''}" data-action="selected-filter" aria-pressed="${state.onlySelected}">只看已选 <span>${state[kind].size}</span></button>
      <label><input id="group-filter" type="checkbox" data-filter="group" ${state.group ? 'checked' : ''}>合并同名来源</label></div>
      <select id="sort-filter" data-filter="sort" aria-label="排序"><option value="name" ${state.sort === 'name' ? 'selected' : ''}>按名称</option><option value="sources" ${state.sort === 'sources' ? 'selected' : ''}>按来源数量</option></select></div>
      <div id="resource-results">${resourceResults(kind)}</div>`
  );
}

export function renderResults() {
  const container = byId('resource-results');
  if (!container) return;
  container.innerHTML = state.tab === 'roles' ? roleResults() : resourceResults(state.tab);
  const button = byId('selected-filter');
  if (button) {
    button.classList.toggle('active', state.onlySelected);
    button.setAttribute('aria-pressed', String(state.onlySelected));
    button.innerHTML = `只看已选 <span>${state[state.tab].size}</span>`;
  }
}

function resourceResults(kind) {
  const all = groupResources(kind);
  const pages = Math.max(1, Math.ceil(all.length / PAGE_SIZE));
  state.page = Math.min(Math.max(1, state.page), pages);
  const filtered =
    state.query || state.source !== 'all' || state.transport !== 'all' || state.onlySelected;
  return `<div class="result-summary"><span role="status">${all.length} ${state.group ? '个名称' : '份资源'}${state.source !== 'all' ? ` · ${e(agentName(state.source))}` : ''}</span>
    ${filtered ? '<button class="text-link" data-action="reset-filters">清除筛选</button>' : '<span>同名来源可在详情中比较</span>'}</div>
    ${
      all.length
        ? `<div class="resource-grid ${state.view === 'list' ? 'list-view' : ''}">${all
            .slice((state.page - 1) * PAGE_SIZE, state.page * PAGE_SIZE)
            .map((group) => resourceCard(kind, group))
            .join('')}</div>`
        : empty(
            state.onlySelected ? '当前筛选下没有已选资源' : '没有找到匹配的资源',
            '调整关键词或来源，也可以清除筛选后重新浏览。',
            'search',
          )
    }
    ${
      pages > 1
        ? `<nav class="pagination" aria-label="资源分页"><button class="btn small" data-page="${state.page - 1}" ${state.page === 1 ? 'disabled' : ''}>上一页</button>
      <span>第 ${state.page} / ${pages} 页</span><button class="btn small" data-page="${state.page + 1}" ${state.page === pages ? 'disabled' : ''}>下一页</button></nav>`
        : ''
    }`;
}

function resourceCard(kind, group) {
  const x = group.item,
    skill = kind === 'skills',
    selected = state[kind].has(x.id);
  const sources = [
    ...new Set(
      skill
        ? group.variants.flatMap((v) => v.sources.map((s) => agentName(s.agent)))
        : group.variants.map((v) => agentName(v.agent)),
    ),
  ];
  const description = skill
    ? x.description
    : `${x.summary.transport.toUpperCase()} · ${x.summary.launcher || x.summary.host || '远程服务'}`;
  return `<article class="resource-card ${selected ? 'selected' : ''}">
    <div class="resource-top"><div class="resource-symbol ${skill ? 'skill' : 'mcp'}">${icon(skill ? 'skill' : 'plug')}</div>
      <button class="resource-name" data-detail="${kind}" data-id="${x.id}" title="${e(x.name)}">${e(x.name)}</button>
      <button class="check-button" id="resource-toggle-${kind}-${e(x.id)}" data-toggle="${kind}" data-id="${e(x.id)}" data-focus-key="${e(x.id)}" aria-label="${selected ? '移除' : '选择'} ${e(x.name)}" aria-pressed="${selected}" title="${selected ? '移出装配台' : '加入装配台'}">${icon(selected ? 'check' : 'plus')}</button></div>
    <p class="resource-description" title="${e(description)}">${e(description || '暂无说明')}</p>
    <div class="resource-footer"><div class="tags"><span class="tag">${e(sources.slice(0, 2).join(' · '))}${sources.length > 2 ? ` +${sources.length - 2}` : ''}</span>
      ${group.variants.length > 1 ? `<span class="tag subtle">${group.variants.length} 个来源</span>` : ''}
      ${!skill && !x.summary.enabled ? '<span class="tag warm">已禁用</span>' : ''}
      ${!skill && x.scope.startsWith('插件') ? '<span class="tag subtle">插件缓存</span>' : ''}</div>
      <button class="text-link" data-detail="${kind}" data-id="${x.id}">详情 ${icon('arrow')}</button></div></article>`;
}

function composer() {
  const total = state.skills.size + state.mcps.size;
  const missing = missingResources();
  const ready = state.agents.size && !missing.length && (total || state.mode === 'replace_mcp');
  const chips = (kind) =>
    [...state[kind]]
      .map((id) => {
        const item = getItem(kind, id);
        return `<div class="selected-chip ${item ? '' : 'invalid'}"><span>${icon(kind === 'skills' ? 'skill' : 'plug')}${e(item?.name || state.unresolved[id] || '来源已失效')}</span>
      <button class="icon-button" data-remove="${kind}" data-id="${e(id)}" aria-label="移出 ${e(item?.name || '失效来源')}">${icon('close')}</button></div>`;
      })
      .join('');
  return `<div class="composer-header"><div class="composer-mark">${icon('layers')}</div><div><h2>装配工作台</h2><p>${e(state.profileName || '当前组合')}</p></div>
    <button class="icon-button" data-action="toggle-composer" aria-label="收起装配台">${icon('close')}</button></div>
    <div class="composer-body">
      ${
        missing.length
          ? `<div class="notice error"><strong>${missing.length} 项资源或目标已失效</strong><p>请重新选择来源或移出失效项，处理后才能预览。</p>${missing
              .filter((x) => x.kind === 'agents')
              .map(
                (x) =>
                  `<button class="text-link" data-remove="agents" data-id="${e(x.id)}">移出 ${e(x.name)}</button>`,
              )
              .join('')}</div>`
          : ''
      }
      <div class="step-label"><span>1</span><h3>选择能力</h3>${total ? `<button class="text-link" data-action="clear">清空组合</button>` : ''}</div>
      ${
        total
          ? `${state.skills.size ? `<p class="selection-label">Skills <b>${state.skills.size}</b></p><div class="selected-items">${chips('skills')}</div>` : ''}
        ${state.mcps.size ? `<p class="selection-label">MCP <b>${state.mcps.size}</b></p><div class="selected-items">${chips('mcps')}</div>` : ''}`
          : `<div class="composition-empty">${icon('box')}<strong>从一项能力开始</strong><p>在资源卡片上点击 +，加入组合。</p><button class="btn small" data-tab="skills">浏览 Skill 库 ${icon('arrow')}</button></div>`
      }
      <div class="step-label"><span>2</span><h3>装配给谁</h3><small>可多选</small></div>
      <div class="target-list">${
        state.catalog.agents
          .filter(
            (a) => !a.error && (a.installation_status !== 'config_only' || state.agents.has(a.id)),
          )
          .map(
            (a) =>
              `<label class="target-pill"><input id="compose-target-${e(a.id)}" type="checkbox" data-target="${e(a.id)}" ${state.agents.has(a.id) ? 'checked' : ''}><span>${e(a.name)}</span></label>`,
          )
          .join('') || '<p class="muted">先添加程序目录；也可在总览展开配置记录，明确选择目标。</p>'
      }</div>
      <div class="step-label"><span>3</span><h3>应用方式</h3></div>
      <select id="compose-mode" class="compose-mode" data-compose="mode" aria-label="应用方式"><option value="merge" ${state.mode === 'merge' ? 'selected' : ''}>合并添加 · 保留其他 MCP</option><option value="replace_mcp" ${state.mode === 'replace_mcp' ? 'selected' : ''}>同步 MCP · 仅保留所选</option></select>
      <p class="compose-hint ${state.mode === 'replace_mcp' ? 'warning-text' : ''}">${state.mode === 'replace_mcp' ? (state.mcps.size ? '目标全局配置中未选的 MCP 将被移除。' : '当前未选 MCP：将清空目标的全局 MCP 配置。') : '向目标添加所选能力，保留其他 MCP。'}</p>
      <label class="overwrite-label"><input id="compose-overwrite" type="checkbox" data-compose="overwrite" ${state.overwrite ? 'checked' : ''}><span>允许覆盖不同的同名资源</span></label>
      <p class="compose-hint">Skill 仅安装或替换；取消选择不会卸载。</p></div>
    <div class="composer-footer"><div class="compose-totals"><span><b>${total}</b> 项能力</span><span><b>${state.agents.size}</b> 个目标</span></div>
      <button class="btn primary" data-action="preview" ${ready ? '' : 'disabled'}>预览变更 ${icon('arrow')}</button>
      <p class="preview-note">${missing.length ? '请处理失效资源' : !state.agents.size ? '先选择至少一个目标 Agent' : !ready ? '先选择需要的能力' : '预览确认后写入，并自动备份'}</p>
      <div class="secondary-actions"><button data-action="save-profile">${icon('save')} 保存方案</button><button data-action="export-profile">导出</button></div></div>`;
}

function roles() {
  return (
    heading('AGENT DEFINITIONS', '你的专长角色', '浏览本机的子 Agent 定义，查看其指令与用途。') +
    toolbar('roles') +
    `<div id="resource-results">${roleResults()}</div>`
  );
}

function roleResults() {
  const items = state.catalog.roles
    .filter((x) => state.source === 'all' || x.agent === state.source)
    .filter((x) =>
      (x.name + ' ' + x.description).toLowerCase().includes(state.query.toLowerCase().trim()),
    );
  return `<div class="result-summary"><span role="status">${items.length} 个角色</span><span>只读查看</span></div>
    <div class="resource-grid">${items
      .map(
        (
          x,
        ) => `<article class="resource-card"><div class="resource-top"><div class="resource-symbol role">${icon('robot')}</div>
      <button class="resource-name" data-detail="roles" data-id="${x.id}">${e(x.name)}</button></div><p class="resource-description">${e(x.description || '本地角色定义')}</p>
      <div class="resource-footer"><span class="tag">${e(agentName(x.agent))}</span><button class="text-link" data-detail="roles" data-id="${x.id}">查看定义 ${icon('arrow')}</button></div></article>`,
      )
      .join('')}</div>
    ${items.length ? '' : empty('没有匹配的子 Agent', '调整筛选条件，或检查客户端的 agents 目录。', 'robot')}`;
}

function profiles() {
  return (
    heading(
      'SAVED WORKFLOWS',
      '把顺手的组合留下来',
      '保存常用能力与目标，下次直接加载。',
      `<button class="btn primary" data-action="save-profile">${icon('plus')} 保存当前组合</button>`,
    ) +
    `<div class="toolbar"><button class="btn" data-action="import-profile">${icon('folder')} 导入方案</button><span class="muted">方案保存资源引用，导出不含密钥。</span><input id="profile-file" type="file" accept=".json" class="hidden"></div>` +
    (state.profiles.length
      ? `<div class="profile-grid">${state.profiles
          .map(
            (
              p,
            ) => `<article class="profile-card"><div class="profile-symbol">${icon('layers')}</div>
      <h2>${e(p.name)}</h2><p>${e(p.description || '你的本机能力组合')}</p>
      ${p.error ? `<div class="notice error">${e(p.error)}</div>` : `<div class="profile-meta"><span>${p.skills.length} Skills</span><span>${p.mcps.length} MCP</span></div><p class="profile-targets">${e(p.agents.map(agentName).join(' · ') || '尚未选择目标')}</p>`}
      <div class="row-actions"><button class="btn small primary" data-load="${e(p.id)}" ${p.error ? 'disabled' : ''}>加载组合 ${icon('arrow')}</button><button class="btn small" data-export="${e(p.id)}" ${p.error ? 'disabled' : ''}>导出</button>
      <button class="icon-button" data-delete-profile="${e(p.id)}" aria-label="删除方案 ${e(p.name)}">${icon('trash')}</button></div><small class="muted">更新于 ${timeLabel(p.updated_at) || '—'}</small></article>`,
          )
          .join('')}</div>`
      : empty(
          '保存你的第一份组合',
          '选择 Skill、MCP 与目标 Agent，然后点击右上角“保存当前组合”。',
          'layers',
        ))
  );
}

function historyPage() {
  const labels = {
    applied: '已应用',
    restored: '已恢复',
    rolled_back: '失败后已回滚',
    recovery_needed: '需要恢复',
    applying: '应用被中断',
    restoring: '恢复被中断',
    preparing: '准备被中断',
    unreadable: '清单无法读取',
  };
  return (
    heading(
      'CHANGE HISTORY',
      '每次变更，都可以追溯',
      '查看原文件备份，并恢复到本次装配之前。',
      `<button class="btn" data-action="refresh-history">${icon('refresh')} 刷新记录</button>`,
    ) +
    (state.history.length
      ? `<div class="row-list">${state.history
          .map(
            (m) => `<article class="history-card" id="history-${e(m.id)}">
      <div class="section-title"><h2>${e(m.name || '自由装配')}</h2><span class="tag ${['recovery_needed', 'unreadable'].includes(m.status) ? 'error' : ''}">${e(labels[m.status] || m.status)}</span></div>
      <p class="muted">${timeLabel(m.created_at)} · ${m.entries?.length || 0} 个资源</p>
      ${(m.recovery_errors || []).map((item) => (typeof item === 'string' ? `<p class="notice error">未恢复 <code>${e(item)}</code></p>` : `<p class="notice error">未恢复 <code>${e(item.target || '')}</code>${item.previous_path ? `；原件暂存于 <code>${e(item.previous_path)}</code>` : ''}${item.stage ? `；暂存写入 <code>${e(item.stage)}</code>` : ''}${item.phase ? `；阶段 ${e(item.phase)}` : ''}</p>`)).join('')}
      ${(m.entries || []).map((x) => `<div class="path-row"><span class="tag">${x.kind === 'skill' ? 'Skill' : 'MCP'}</span><code>${e(x.target)}</code></div>${x.restore_phase === 'prepared' ? `<div class="path-row"><span class="tag">恢复待完成</span><code>${e(x.restore_path || '')}</code></div>` : x.phase && x.phase !== 'committed' && x.restore_phase !== 'committed' ? `<div class="path-row"><span class="tag">${e(x.phase)}</span><code>${e(x.previous_path || x.stage || '')}</code></div>` : ''}`).join('')}
      ${m.restorable === false ? '<p class="notice">此备份由其他版本生成，请使用原版本恢复。</p>' : ['applied', 'applying', 'recovery_needed', 'restoring'].includes(m.status) ? `<div class="row-actions"><button class="btn small" data-restore="${e(m.id)}">${icon('history')}恢复变更前的内容</button></div>` : ''}</article>`,
          )
          .join('')}</div>`
      : empty('还没有修改过配置', '应用装配方案后，这里会保留原文件及变更记录。', 'history'))
  );
}

function settingsPage() {
  const c = state.catalog;
  return (
    heading(
      'WORKSPACE SETTINGS',
      '管理你的工作空间',
      '接入更多本机资源，查看扫描结果与保存位置。',
    ) +
    `<div class="settings-grid"><section class="settings-card" id="settings-data"><h2>本地数据</h2><p>方案、资源库、备份与执行记录保存在当前 Windows 用户目录。</p><code class="data-path">${e(c.data_dir)}</code><p>仅监听 127.0.0.1。扫描不启动 MCP；执行任务时由 Agent 调用模型与工具。</p></section>
    <section class="settings-card" id="settings-install"><div class="section-title"><h2>程序查找目录</h2><button class="btn small" data-action="add-install-root">${icon('plus')} 添加目录</button></div>
      <p>自动读取 Windows 安装记录并核对程序文件。免安装或自选位置可加在这里，向下查找三层；目前支持 ${state.catalog.agents.some((a) => a.id === 'zcode') ? 'Zcode、DeepSeek Harness 等内置客户端' : '内置客户端'}。</p>
      ${c.install_roots.map((path) => `<div class="path-row"><code>${e(path)}</code><button class="icon-button" data-remove-install-root="${e(path)}" aria-label="移除程序目录 ${e(path)}">${icon('close')}</button></div>`).join('') || '<p class="muted">尚未添加自选程序目录。</p>'}
      <details><summary>自动检查的目录</summary><div class="scan-paths">${c.discovery.roots
        .filter((p) => !c.install_roots.includes(p))
        .map((p) => `<code>${e(p)}</code>`)
        .join('')}</div></details></section>
    <section class="settings-card" id="settings-skills"><div class="section-title"><h2>额外的 Skill 目录</h2><button class="btn small" data-action="add-root">${icon('plus')} 添加目录</button></div>
      <p>递归查找 SKILL.md。可添加项目目录或个人 Skill 库。</p>
      ${c.roots.map((path) => `<div class="path-row"><code>${e(path)}</code><button class="icon-button" data-remove-root="${e(path)}" aria-label="移除扫描目录 ${e(path)}">${icon('close')}</button></div>`).join('') || '<p class="muted">尚未添加额外目录。</p>'}</section>
    <section class="settings-card" id="settings-agents"><div class="section-title"><h2>自定义 Agent</h2><button class="btn small" data-action="add-agent">${icon('plus')} 接入 Agent</button></div>
      <p>指定配置文件与 Skill 目录。移除接入只删除工坊记录。</p>
      ${c.agents
        .filter((a) => a.id.startsWith('custom-'))
        .map(
          (a) =>
            `<div class="path-row"><span>${e(a.name)}</span><code>${e(a.config)}</code><button class="icon-button" data-remove-agent="${e(a.id)}" aria-label="移除接入 ${e(a.name)}">${icon('close')}</button></div>`,
        )
        .join('')}</section>
    <section class="settings-card" id="settings-scan"><div class="section-title"><h2>扫描记录</h2><button class="btn small" data-action="scan">${icon('refresh')} 重新扫描</button></div><p>${timeLabel(c.scanned_at)}</p>
      ${c.issues.length ? `<div class="notice"><ul>${c.issues.map((x) => `<li>${e(x)}</li>`).join('')}</ul></div>` : '<p class="scan-success"><span class="local-dot"></span> 本次扫描没有读取错误</p>'}</section>
    <section class="settings-card" id="settings-scope"><h2>装配范围</h2><ul class="help-list"><li>只写入目标的全局 MCP 配置；项目配置与云端连接器仍由各客户端管理。</li><li>插件缓存仅表示文件存在，启用情况与依赖需要在目标客户端确认。</li><li>Skill 完整复制目录内容；超过 100 MB 或 10000 个条目时停止装配。</li><li>配置应用后，请重启或重新加载目标 Agent；MCP 数量不代表连接成功。</li></ul></section></div>`
  );
}

function empty(title, description, symbol = 'box') {
  return `<div class="empty">${icon(symbol)}<h2>${title}</h2><p>${description}</p></div>`;
}
