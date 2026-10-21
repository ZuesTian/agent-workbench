// Serializable collaboration blueprints; no network, DOM, or agent execution.
export const roleMeta = {
  tutor: {
    name: '导师',
    label: 'TUTOR',
    icon: 'shield',
    policy: '审核成果并列出需修改项。',
  },
  employee: {
    name: '协作者',
    label: 'EMPLOYEE',
    icon: 'robot',
    policy: '按里程碑汇报，遇到阻碍及时上报。',
  },
  sub: {
    name: '子 Agent',
    label: 'SUB-AGENT',
    icon: 'terminal',
    policy: '完成指定任务，附上产物和验证依据。',
  },
};
export const CARD = { width: 240, height: 164 };
const clone = (value) => JSON.parse(JSON.stringify(value));

export function newNode(role = 'employee', parentId = null) {
  return {
    id: 'n-' + crypto.randomUUID().replaceAll('-', ''),
    name: `新${roleMeta[role].name}`,
    role,
    parentId,
    agentId: '',
    model: '',
    policy: roleMeta[role].policy,
    prompt: '',
    skills: [],
    mcps: [],
    x: 0,
    y: 0,
    collapsed: false,
  };
}

export function createTeam(template = 'software') {
  const team = {
    format: 'agent-workbench-team',
    version: 1,
    name:
      template === 'blank' ? '新的协作方案' : template === 'research' ? '研究协作组' : '产品研发组',
    goal: '',
    layout: 'horizontal',
    nodes: [],
  };
  const root = newNode('tutor');
  Object.assign(root, { name: '任务负责人', prompt: '拆解目标、协调分工，审核成果与验证依据。' });
  team.nodes.push(root);
  if (template !== 'blank') {
    const research = template === 'research';
    const work = newNode('employee', root.id);
    const review = newNode('employee', root.id);
    Object.assign(work, {
      name: research ? '资料与分析' : '设计与实现',
      prompt: research
        ? '整理来源、分析数据，区分已验证结果和待确认问题。'
        : '形成设计方案，实现约定功能并记录关键决策。',
    });
    Object.assign(review, {
      name: research ? '证据审核' : '质量审核',
      prompt: research
        ? '核对来源与论据，检查结论是否超出证据范围。'
        : '检查功能、交互和异常情况，给出具体修改意见。',
    });
    const helper = newNode('sub', work.id);
    const verifier = newNode('sub', review.id);
    Object.assign(helper, {
      name: research ? '资料整理' : '实现助手',
      prompt: '完成上级分配的具体子任务，交付可复核的产物。',
    });
    Object.assign(verifier, {
      name: research ? '引用核验' : '测试助手',
      prompt: '核验产物，记录复现步骤、检查结果和未解决的问题。',
    });
    team.nodes.push(work, review, helper, verifier);
  }
  autoLayout(team);
  return team;
}

export function descendants(team, id) {
  const result = new Set();
  const queue = [id];
  while (queue.length) {
    const parent = queue.shift();
    for (const node of team.nodes) {
      if (node.parentId === parent && !result.has(node.id)) {
        result.add(node.id);
        queue.push(node.id);
      }
    }
  }
  return result;
}

export function reparent(team, id, parentId) {
  const node = team.nodes.find((item) => item.id === id);
  if (!node) throw Error('找不到节点');
  if (parentId !== null && !team.nodes.some((item) => item.id === parentId))
    throw Error('找不到上级节点');
  if (id === parentId || descendants(team, id).has(parentId))
    throw Error('不能把节点挂到自身或自己的下级');
  node.parentId = parentId;
  const parent = team.nodes.find((item) => item.id === parentId);
  if (parent) parent.collapsed = false;
}

export function removeNode(team, id) {
  if (team.nodes.length === 1) throw Error('至少保留一个节点');
  const node = team.nodes.find((item) => item.id === id);
  if (!node) return;
  for (const child of team.nodes.filter((item) => item.parentId === id))
    child.parentId = node.parentId;
  team.nodes = team.nodes.filter((item) => item.id !== id);
}

export function visibleNodes(team) {
  const hidden = new Set();
  for (const node of team.nodes) {
    if (node.collapsed) for (const id of descendants(team, node.id)) hidden.add(id);
  }
  return team.nodes.filter((node) => !hidden.has(node.id));
}

export function autoLayout(team, direction = team.layout) {
  team.layout = direction;
  let leaf = 0;
  const horizontal = direction === 'horizontal';
  const visit = (node, depth) => {
    const children = team.nodes.filter((item) => item.parentId === node.id);
    const positions = children.map((child) => visit(child, depth + 1));
    const center = positions.length ? (positions[0] + positions.at(-1)) / 2 : leaf++;
    node.x = horizontal ? depth * 310 : center * 280;
    node.y = horizontal ? center * 208 : depth * 220;
    return center;
  };
  for (const node of team.nodes.filter((item) => item.parentId === null)) {
    visit(node, 0);
    leaf += 0.4;
  }
}

export function validateTeam(value) {
  if (!value || value.format !== 'agent-workbench-team' || value.version !== 1)
    throw Error('不是有效的协作方案');
  const text = (value, max, label, required = false) => {
    if (typeof value !== 'string' || value.length > max || (required && !value.trim()))
      throw Error(`${label}格式不正确`);
    return value;
  };
  const refs = (value) => {
    if (!Array.isArray(value) || value.length > 200) throw Error('资源引用格式不正确');
    const ids = new Set();
    return value.map((ref) => {
      const id = text(ref?.id, 200, '资源编号', true);
      if (ids.has(id)) throw Error('资源引用重复');
      ids.add(id);
      return { id, name: text(ref.name, 200, '资源名称', true) };
    });
  };
  const validId = (id) => typeof id === 'string' && /^[a-zA-Z0-9_-]{1,80}$/.test(id);
  if (!Array.isArray(value.nodes) || value.nodes.length < 1 || value.nodes.length > 100)
    throw Error('协作方案需要 1–100 个节点');
  if (!['horizontal', 'vertical'].includes(value.layout)) throw Error('画布布局不正确');
  const team = {
    format: value.format,
    version: 1,
    name: text(value.name, 80, '方案名称', true),
    goal: text(value.goal ?? '', 5000, '任务目标'),
    layout: value.layout,
    nodes: value.nodes.map((raw) => {
      if (
        !raw ||
        !validId(raw.id) ||
        !(raw.parentId === null || validId(raw.parentId)) ||
        !Object.hasOwn(roleMeta, raw.role)
      )
        throw Error('节点类型或编号不正确');
      if (
        ![raw.x, raw.y].every(
          (n) => typeof n === 'number' && Number.isFinite(n) && Math.abs(n) <= 100000,
        )
      )
        throw Error('节点坐标超出范围');
      if (raw.collapsed !== undefined && typeof raw.collapsed !== 'boolean')
        throw Error('折叠状态不正确');
      return {
        id: raw.id,
        parentId: raw.parentId,
        role: raw.role,
        name: text(raw.name, 80, '节点名称', true),
        agentId: text(raw.agentId ?? '', 200, '客户端编号'),
        model: text(raw.model ?? '', 120, '执行模型'),
        policy: text(raw.policy ?? '', 500, '审核策略'),
        prompt: text(raw.prompt ?? '', 10000, '职责说明'),
        skills: refs(raw.skills ?? []),
        mcps: refs(raw.mcps ?? []),
        x: raw.x,
        y: raw.y,
        collapsed: raw.collapsed ?? false,
      };
    }),
  };
  const parents = new Map(team.nodes.map((node) => [node.id, node.parentId]));
  if (parents.size !== team.nodes.length) throw Error('节点编号不能重复');
  for (const node of team.nodes) {
    const seen = new Set();
    let cursor = node.id;
    while (cursor !== null) {
      if (!parents.has(cursor)) throw Error('节点引用了不存在的上级');
      if (seen.has(cursor)) throw Error('上下级关系形成了循环');
      seen.add(cursor);
      cursor = parents.get(cursor);
    }
  }
  return team;
}

export function importTeam(value) {
  // Explicit import of the user's NexusMind topology export.
  if (value?.workbench === 'NexusMind Multi-Agent Orchestrator') {
    if (!Array.isArray(value.agents)) throw Error('拓扑中的 agents 列表不正确');
    value = {
      format: 'agent-workbench-team',
      version: 1,
      name: '导入的协作拓扑',
      goal: '',
      layout: value.layout,
      nodes: value.agents.map((node) => ({
        ...node,
        x: node.position?.x ?? 0,
        y: node.position?.y ?? 0,
        agentId: '',
        skills: [],
        mcps: [],
        collapsed: false,
      })),
    };
  }
  return validateTeam(value);
}

export function teamIssues(team, catalog) {
  const issues = [];
  if (!team.goal.trim()) issues.push('填写总目标，明确这组 Agent 要完成什么。');
  for (const node of team.nodes) {
    if (!node.prompt.trim()) issues.push(`${node.name}：尚未填写职责。`);
    if (node.agentId && !catalog.agents.some((item) => item.id === node.agentId))
      issues.push(`${node.name}：客户端已失效，请重新绑定。`);
    for (const kind of ['skills', 'mcps']) {
      for (const ref of node[kind]) {
        if (!catalog[kind].some((item) => item.id === ref.id))
          issues.push(`${node.name}：${ref.name} 的来源未匹配。`);
      }
    }
  }
  return issues;
}

export function previewSteps(team) {
  const steps = [];
  function visit(node) {
    const children = team.nodes.filter((item) => item.parentId === node.id);
    if (children.length)
      steps.push({
        id: node.id,
        kind: 'assign',
        name: node.name,
        text: `分解目标，交给 ${children.map((child) => child.name).join('、')}。`,
      });
    for (const child of children) visit(child);
    steps.push({
      id: node.id,
      name: node.name,
      kind: children.length || node.role === 'tutor' ? 'review' : 'deliver',
      text:
        children.length || node.role === 'tutor'
          ? node.policy || '审核成果和验证依据。'
          : '交付约定产物，附上验证依据。实际结果由执行端提供。',
    });
  }
  for (const node of team.nodes.filter((item) => item.parentId === null)) visit(node);
  return steps;
}

export function taskBrief(team, catalog) {
  const lines = [
    `# ${team.name}`,
    '',
    '## 总目标',
    '',
    team.goal || '待填写',
    '',
    '> 本文件是协作任务说明。客户端、模型为规划信息，需在执行端确认后开展工作。',
    '',
  ];
  for (const node of team.nodes) {
    const parent = team.nodes.find((item) => item.id === node.parentId);
    const agent = catalog.agents.find((item) => item.id === node.agentId);
    lines.push(
      `## ${node.name}`,
      '',
      `角色：${roleMeta[node.role].name}`,
      `汇报对象：${parent?.name || '用户 / 总目标'}`,
      `客户端：${agent?.name || (node.agentId ? '来源未匹配' : '待选择')}`,
      `执行模型：${node.model || '由客户端决定'}`,
      '',
      '职责：',
      '',
      node.prompt || '待填写',
      '',
      '审核与汇报：',
      '',
      node.policy || '待填写',
      '',
      `Skills：${node.skills.map((ref) => ref.name).join('、') || '未绑定'}`,
      `MCP：${node.mcps.map((ref) => ref.name).join('、') || '未绑定'}`,
      '',
    );
  }
  return lines.join('\n');
}

export const editor = {
  team: createTeam(),
  selected: null,
  dirty: false,
  viewport: { x: 40, y: 40, zoom: 1, userSet: true },
  preview: null,
  inspectorTab: 'config',
  inspectorOpen: true,
  graphView: 'read',
  graphChosen: false,
  mode: 'canvas',
};
editor.selected = editor.team.nodes[0].id;

export function setTeam(team, saved = false) {
  const clean = validateTeam(team);
  if (saved && team.id)
    Object.assign(clean, { id: team.id, revision: team.revision, updated_at: team.updated_at });
  editor.team = clean;
  editor.selected = clean.nodes[0].id;
  editor.dirty = !saved;
  editor.preview = null;
  editor.inspectorTab = 'config';
}

export function storeTeamDraft() {
  try {
    localStorage.setItem(
      'awb-team-draft-v1',
      JSON.stringify({ team: editor.team, dirty: editor.dirty, selected: editor.selected }),
    );
    return true;
  } catch {
    /* Explicit saving to disk remains available. */
    return false;
  }
}

export function preserveTeamDraftBeforeClose() {
  return !editor.dirty || storeTeamDraft();
}

export function restoreTeamDraft() {
  try {
    const stored = JSON.parse(localStorage.getItem('awb-team-draft-v1') || 'null');
    if (stored) {
      setTeam(stored.team, Boolean(stored.team.id));
      editor.dirty = Boolean(stored.dirty);
      if (editor.team.nodes.some((node) => node.id === stored.selected))
        editor.selected = stored.selected;
    }
  } catch {
    /* Keep the usable default template if the draft is corrupt. */
  }
}

export function changed() {
  editor.dirty = true;
  editor.preview = null;
  storeTeamDraft();
}

export function exportTeam(team) {
  return clone(validateTeam(team));
}
