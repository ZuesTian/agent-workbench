// Selection and filtering are independent of the DOM and HTTP transport.
export const names = {
  overview: '工作空间',
  chat: '会话',
  teams: '协作编排',
  execution: '执行任务',
  workflows: '自动调度',
  skills: 'Skill 库',
  mcps: 'MCP 库',
  roles: '子 Agent',
  profiles: '装配方案',
  history: '备份记录',
  settings: '设置',
};

export const state = {
  catalog: null,
  profiles: [],
  history: [],
  teams: [],
  runners: null,
  desktop: { available: false },
  tab: 'overview',
  selectedAgent: '',
  contextOpen: false,
  conversations: [],
  activeConversation: null,
  executionRows: [],
  executionSelected: '',
  workflowRows: [],
  workflowSelected: '',
  query: '',
  source: 'all',
  transport: 'all',
  group: true,
  page: 1,
  sort: 'name',
  view: 'grid',
  onlySelected: false,
  composerOpen: false,
  skills: new Set(),
  mcps: new Set(),
  agents: new Set(),
  configTargets: {},
  mode: 'merge',
  overwrite: false,
  profileId: null,
  profileName: '',
  profileDescription: '',
  plan: null,
  busy: false,
  unresolved: {},
};

export function getItem(kind, id) {
  return state.catalog?.[kind]?.find((item) => item.id === id);
}

export function agentName(id) {
  return getItem('agents', id)?.name || { library: '我的资源库', custom: '自定义目录' }[id] || id;
}

export function agentStatus(agent) {
  if (agent.installation_status === 'installed') return agent.running_hint ? '运行中' : '找到程序';
  return agent.installation_status === 'custom' ? '手动接入' : '仅发现配置';
}

export function selection() {
  return {
    skills: [...state.skills],
    mcps: [...state.mcps],
    agents: [...state.agents],
    config_targets: Object.fromEntries(
      Object.entries(state.configTargets).filter(([agent]) => state.agents.has(agent)),
    ),
    mode: state.mode,
    overwrite: state.overwrite,
    name: state.profileName || '自由装配',
    description: state.profileDescription,
  };
}

export function missingResources() {
  return ['skills', 'mcps', 'agents'].flatMap((kind) =>
    [...state[kind]]
      .filter((id) => !getItem(kind, id))
      .map((id) => ({
        kind,
        id,
        name: state.unresolved[id] || (kind === 'agents' ? agentName(id) : '来源已失效'),
      })),
  );
}

export function toggleItem(kind, id, force = false) {
  const item = getItem(kind, id);
  if (!item || !['skills', 'mcps'].includes(kind)) return;
  if (state[kind].has(id) && !force) {
    state[kind].delete(id);
  } else {
    const key = (x) => (kind === 'skills' ? x.slug.toLowerCase() : x.name);
    for (const selected of state[kind]) {
      const other = getItem(kind, selected);
      if (other && key(other) === key(item)) state[kind].delete(selected);
    }
    state[kind].add(id);
    state.composerOpen = true;
  }
  state.plan = null;
}

export function clearSelection() {
  for (const kind of ['skills', 'mcps', 'agents']) state[kind].clear();
  Object.assign(state, {
    profileId: null,
    profileName: '',
    profileDescription: '',
    mode: 'merge',
    overwrite: false,
    plan: null,
    unresolved: {},
    configTargets: {},
  });
}

export function loadProfile(profile) {
  const parsed = validateSelection(profile);
  for (const kind of ['skills', 'mcps', 'agents']) state[kind] = new Set(parsed[kind]);
  Object.assign(state, {
    mode: parsed.mode,
    configTargets: { ...parsed.config_targets },
    overwrite: false,
    profileId: profile.id || null,
    profileName: String(profile.name || ''),
    profileDescription: String(profile.description || ''),
    plan: null,
    unresolved: profile.unresolved || {},
    composerOpen: true,
  });
}

function validIds(value) {
  return (
    Array.isArray(value) &&
    value.length <= 5000 &&
    value.every((id) => typeof id === 'string' && id.length > 0 && id.length <= 200)
  );
}

function validateSelection(value) {
  if (!value || typeof value !== 'object') throw Error('方案内容不正确');
  for (const kind of ['skills', 'mcps', 'agents']) {
    if (!validIds(value[kind])) throw Error('方案中的资源列表不正确');
  }
  if (!['merge', 'replace_mcp'].includes(value.mode || 'merge'))
    throw Error('方案的应用方式不正确');
  const targets = value.config_targets === undefined ? {} : value.config_targets;
  if (
    typeof targets !== 'object' ||
    targets === null ||
    Array.isArray(targets) ||
    Object.entries(targets).some(
      ([agent, path]) =>
        !value.agents.includes(agent) ||
        typeof path !== 'string' ||
        !path.trim() ||
        path.length > 4096,
    )
  )
    throw Error('方案中的配置目标不正确');
  return { ...value, mode: value.mode || 'merge', config_targets: targets };
}

export function importProfile(document, catalog) {
  if (!document || document.format !== 'agent-workbench-profile' || document.version !== 1) {
    throw Error('不是工坊导出的有效方案');
  }
  const result = {
    name: typeof document.name === 'string' ? document.name.slice(0, 80) : '导入的方案',
    description:
      typeof document.description === 'string' ? document.description.slice(0, 1000) : '',
    agents: document.agents || [],
    mode: document.mode || 'merge',
    unresolved: {},
  };
  for (const kind of ['skills', 'mcps']) {
    if (!Array.isArray(document[kind]) || document[kind].length > 5000)
      throw Error('方案中的资源列表不正确');
    result[kind] = document[kind].map((ref, index) => {
      if (!ref || typeof ref !== 'object' || typeof ref.name !== 'string')
        throw Error('方案中的来源信息不正确');
      const match =
        catalog[kind].find((x) => x.id === ref.id) ||
        catalog[kind].find(
          (x) => x.name === ref.name && (kind === 'skills' ? x.path : x.source) === ref.source,
        );
      if (match) return match.id;
      const missing = `missing-${kind}-${index}`;
      result.unresolved[missing] = ref.name || '未匹配的资源';
      return missing;
    });
  }
  // Missing references stay in the draft. They must never become silent removals.
  return validateSelection(result);
}

export function persist() {
  try {
    localStorage.setItem(
      'awb-draft-v1',
      JSON.stringify({
        ...selection(),
        profileId: state.profileId,
        profileName: state.profileName,
        unresolved: state.unresolved,
      }),
    );
    localStorage.setItem('awb-view', JSON.stringify({ view: state.view, sort: state.sort }));
  } catch {
    /* The app remains usable when browser storage is unavailable. */
  }
}

export function restoreDraft() {
  try {
    const draft = JSON.parse(localStorage.getItem('awb-draft-v1') || 'null');
    if (draft) {
      loadProfile({
        ...draft,
        name: draft.profileName || '',
        id: draft.profileId,
        description: draft.description || '',
      });
      state.composerOpen = state.skills.size + state.mcps.size + state.agents.size > 0;
    }
    const prefs = JSON.parse(localStorage.getItem('awb-view') || '{}');
    if (['grid', 'list'].includes(prefs.view)) state.view = prefs.view;
    if (['name', 'sources'].includes(prefs.sort)) state.sort = prefs.sort;
  } catch {
    clearSelection();
  }
}

export function filterResources(kind, options = {}) {
  const saved = {
    query: state.query,
    source: state.source,
    transport: state.transport,
    onlySelected: state.onlySelected,
    group: state.group,
    sort: state.sort,
  };
  Object.assign(state, {
    query: options.query || '',
    source: options.source || 'all',
    transport: 'all',
    onlySelected: false,
    group: options.group !== false,
    sort: 'name',
  });
  try {
    return groupResources(kind);
  } finally {
    Object.assign(state, saved);
  }
}

export function groupResources(kind) {
  let items = state.catalog[kind];
  if (state.source !== 'all') {
    items = items.filter((x) =>
      kind === 'skills'
        ? x.sources.some((s) => s.agent === state.source)
        : x.agent === state.source,
    );
  }
  if (state.query.trim()) {
    const words = state.query.toLocaleLowerCase().trim().split(/\s+/);
    items = items.filter((x) => {
      const haystack = [
        x.name,
        x.description,
        x.path,
        x.source,
        x.scope,
        kind === 'skills' ? x.sources.map((s) => agentName(s.agent)).join(' ') : agentName(x.agent),
      ]
        .filter(Boolean)
        .join(' ')
        .toLocaleLowerCase();
      return words.every((word) => haystack.includes(word));
    });
  }
  if (kind === 'mcps' && state.transport !== 'all') {
    items = items.filter((x) => x.summary.transport === state.transport);
  }
  if (state.onlySelected) items = items.filter((x) => state[kind].has(x.id));
  const groups = new Map();
  for (const item of items) {
    const key = state.group ? item.name : item.id;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }
  const score = (item) =>
    kind === 'skills'
      ? item.sources.some((s) => s.agent === 'library')
        ? 0
        : item.sources.some((s) => s.label === '本地 Skill')
          ? 1
          : 3
      : item.agent === 'library'
        ? 0
        : item.scope === '全局'
          ? 1
          : 3;
  const result = [...groups.values()].map((variants) => {
    variants.sort((a, b) => score(a) - score(b) || a.id.localeCompare(b.id));
    return { item: variants.find((x) => state[kind].has(x.id)) || variants[0], variants };
  });
  return result.sort(
    (a, b) =>
      (state.sort === 'sources' ? b.variants.length - a.variants.length : 0) ||
      a.item.name.localeCompare(b.item.name, 'zh-CN', { numeric: true }),
  );
}
