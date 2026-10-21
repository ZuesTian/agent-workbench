// Installation discovery and automatic execution are separate capabilities.
export function runnerStatus(agentId, runners) {
  if (!agentId)
    return {
      kind: 'unbound',
      label: '未绑定',
      detail: '请选择执行客户端；负责人和审核节点也需要绑定。',
    };
  if (!runners)
    return { kind: 'unknown', label: '待检查', detail: '暂未获取执行能力，请重新检查。' };
  const runner = runners.find((item) => item.id === agentId);
  if (!runner)
    return {
      kind: 'unsupported',
      label: '仅装配',
      detail: '支持资源装配，尚未接入自动执行。请更换可执行客户端。',
    };
  if (!runner.available)
    return {
      kind: 'unavailable',
      label: '入口不可用',
      detail: '已接入执行，但未找到本机 CLI 入口。请检查安装或更换客户端。',
    };
  return {
    kind: 'ready',
    label: '可执行',
    detail: '执行入口可用，运行时使用该客户端的账号与配置。',
  };
}

export function clientGroups(agents, runners) {
  const clients = [...agents];
  for (const runner of runners || []) {
    if (!clients.some((item) => item.id === runner.id))
      clients.push({ id: runner.id, name: runner.name });
  }
  const groups = [
    { kind: 'ready', label: '可自动执行' },
    { kind: 'unavailable', label: '已接入执行 · 本机入口不可用' },
    { kind: 'unsupported', label: '仅支持装配 · 尚未接入执行' },
    { kind: 'unknown', label: '执行能力待检查' },
  ];
  return groups
    .map((group) => ({
      ...group,
      clients: clients.filter((client) => runnerStatus(client.id, runners).kind === group.kind),
    }))
    .filter((group) => group.clients.length);
}

export function executionBindings(team, runners) {
  return team.nodes.map((node) => ({ node, ...runnerStatus(node.agentId, runners) }));
}
