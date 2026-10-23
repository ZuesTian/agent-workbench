import { state, agentName } from '/state.js';
import { e, icon, timeLabel } from '/ui.js';
import { editor, roleMeta, descendants, visibleNodes, CARD, teamIssues } from '/team-model.js';
import { clientGroups, runnerStatus } from '/runner-status.js';

const action = (name, label, symbol, extra = '') =>
  `<button class="btn small" data-team-action="${name}" ${extra}>${icon(symbol)}${label}</button>`;
const selectedNode = () => editor.team.nodes.find((node) => node.id === editor.selected);

export function teamPage() {
  const team = editor.team;
  return `<section class="collaboration ${editor.mode === 'activity' ? 'activity-mode' : ''} ${editor.graphView === 'read' ? 'reading-nodes' : ''} ${editor.inspectorOpen === false ? 'inspector-collapsed' : ''}">
    <header class="team-header"><div class="team-title"><span class="team-mark">${icon('network')}</span>
      <div><input id="team-name" data-team-field="name" aria-label="协作方案名称" maxlength="80" value="${e(team.name)}">
      <span id="team-save-status" role="status">${saveStatus()}</span></div></div>
      <div class="team-view-switch" aria-label="协作视图">
        <button data-team-mode="canvas" class="${editor.mode === 'canvas' ? 'active' : ''}" aria-pressed="${editor.mode === 'canvas'}">${icon('network')}协作画布</button>
        <button data-team-mode="activity" class="${editor.mode === 'activity' ? 'active' : ''}" aria-pressed="${editor.mode === 'activity'}">${icon('chat')}流程动态</button>
      </div>
      <div class="team-actions">${action('saved', '我的方案', 'folder')}${action('save', '保存', 'save')}
        <details class="team-menu"><summary class="btn small">${icon('plus')} 新建</summary><div>
          <button data-team-template="software">产品研发组 <small>设计、实现与质量审核</small></button>
          <button data-team-template="research">研究协作组 <small>资料、分析与证据审核</small></button>
          <button data-team-template="blank">空白画布 <small>从一个负责人开始</small></button></div></details>
        <details class="team-menu"><summary class="btn small" aria-label="更多协作操作">${icon('more')}</summary><div>
          <button data-team-action="save-as">另存为新方案</button>
          <button data-team-action="export">导出协作 JSON</button>
          <button data-team-action="brief">导出任务说明</button>
          <button data-team-action="import">导入协作 JSON</button></div></details>
      </div><input type="file" id="team-file" accept=".json,application/json" hidden></header>
    <div class="team-goal"><label for="team-goal">${icon('target')} 总目标</label>
      <textarea id="team-goal" data-team-field="goal" rows="1" maxlength="5000" placeholder="这组 Agent 要一起完成什么？写下目标、交付物和验收条件。">${e(team.goal)}</textarea>
      <button class="btn small" data-team-action="preview">${icon('play')}流程预演</button>
      <button class="btn small primary" data-tab="workflows">${icon('play')}执行协作</button></div>
    <div class="team-workspace">
      <div class="graph-pane">
        <div class="graph-toolbar"><div class="graph-tools">
          ${action('add', '添加节点', 'plus')}
          <button class="btn small ${editor.graphView === 'read' ? 'active' : ''}" type="button" data-team-action="read-nodes" aria-pressed="${editor.graphView === 'read'}">阅读节点</button>
          <button class="btn small ${editor.graphView !== 'read' ? 'active' : ''}" type="button" data-team-action="show-map" aria-pressed="${editor.graphView !== 'read'}">查看全图</button>
          <button class="icon-button" data-team-action="layout" aria-label="自动排列节点" title="自动排列节点">${icon('layout')}</button>
          <button class="icon-button" data-team-action="direction" aria-label="切换横向或纵向布局" title="切换横向或纵向布局">${icon('rotate')}</button>
          <button class="icon-button" data-team-action="expand" aria-label="展开所有节点" title="展开所有节点">${icon('expand')}</button>
        </div><span class="graph-hint">拖动节点 · Shift 拖到节点可改上级</span></div>
        <div id="team-canvas" class="team-canvas" tabindex="0" aria-label="协作画布；可拖动节点，使用下方按钮缩放，或在节点设置中修改上下级">
          <div id="graph-stage" class="graph-stage"><svg id="graph-edges" aria-hidden="true"></svg><div id="graph-nodes"></div></div>
        </div>
        <div id="team-reading" class="reading-list" ${editor.graphView === 'read' ? '' : 'hidden'}>${readingList()}</div>
        <div class="graph-bottom"><details class="outline"><summary>${icon('list')}结构大纲 <span id="team-node-count">${team.nodes.length}</span></summary><div id="team-outline">${outline()}</div></details>
          <div class="zoom-controls"><button class="icon-button" data-team-action="zoom-out" aria-label="缩小画布">${icon('minus')}</button>
            <span id="graph-zoom">100%</span><button class="icon-button" data-team-action="zoom-in" aria-label="放大画布">${icon('plus')}</button>
            <button class="icon-button" data-team-action="fit" aria-label="居中显示全部节点" title="居中显示">${icon('focus')}</button></div></div>
        <div class="graph-empty-hint" id="graph-empty-hint" ${team.nodes.length > 1 ? 'hidden' : ''}>从这个节点开始，添加协作者或子 Agent。</div>
      </div>
      <section id="team-activity" class="team-activity" aria-label="对话式流程动态">${editor.mode === 'activity' ? activity() : ''}</section>
      <aside class="team-inspector" aria-label="节点设置">
        <div class="inspector-tabs"><button type="button" class="icon-button inspector-collapse" data-team-action="toggle-inspector" aria-expanded="${editor.inspectorOpen !== false}" aria-label="${editor.inspectorOpen === false ? '展开节点设置' : '收起节点设置'}">${icon(editor.inspectorOpen === false ? 'expand' : 'minus')}</button>
          <button data-team-panel="config" class="${editor.inspectorTab === 'config' ? 'active' : ''}">${icon('settings')}节点设置</button>
          <button data-team-panel="preview" class="${editor.inspectorTab === 'preview' ? 'active' : ''}">${icon('list')}流程预演</button></div>
        <div id="team-inspector-body">${inspector()}</div></aside>
    </div>
    <footer class="team-statusbar"><span>${icon('lock')}协作方案保存在本机</span><span>全部节点绑定可执行客户端后可自动调度</span></footer>
  </section>`;
}

export function saveStatus() {
  if (editor.draftSaved === false) return '草稿未能保存到本机浏览器 · 请导出或保存方案';
  return editor.dirty
    ? '有未保存的更改'
    : editor.team.id
      ? `已保存 · ${timeLabel(editor.team.updated_at)}`
      : '示例模板 · 可编辑后保存';
}

export function graphNodes() {
  return visibleNodes(editor.team)
    .map((node) => {
      const role = roleMeta[node.role];
      const children = editor.team.nodes.filter((item) => item.parentId === node.id);
      const missing = ['skills', 'mcps'].some((kind) =>
        node[kind].some((ref) => !state.catalog[kind].some((item) => item.id === ref.id)),
      );
      const client = node.agentId ? agentName(node.agentId) : '未绑定客户端';
      const status = runnerStatus(node.agentId, state.runners);
      return `<article class="graph-node ${node.id === editor.selected ? 'selected' : ''}" data-node="${e(node.id)}" style="left:${node.x}px;top:${node.y}px">
      <button id="team-graph-node-${e(node.id)}" class="node-hit" data-team-select="${e(node.id)}" aria-label="选择节点 ${e(node.name)}" aria-pressed="${node.id === editor.selected}">
        <span class="node-eyebrow"><span>${icon(role.icon)} ${role.label}</span><span class="node-client" title="${e(status.detail)}">${e(client)}${node.agentId && status.kind !== 'ready' ? ` · ${e(status.label)}` : ''}</span></span>
        <strong>${e(node.name)}</strong><span class="node-prompt">${e(node.prompt || '点击填写职责与交付要求')}</span>
        <span class="node-resources">${icon('skill')}${node.skills.length} Skills <span>·</span>${icon('plug')}${node.mcps.length} MCP ${missing ? '<i title="存在未匹配资源">!</i>' : ''}</span>
      </button>
      ${children.length ? `<button class="node-collapse" data-team-collapse="${e(node.id)}" aria-label="${node.collapsed ? '展开' : '折叠'} ${e(node.name)} 的下级">${icon(node.collapsed ? 'plus' : 'minus')}<span>${children.length}</span></button>` : ''}
    </article>`;
    })
    .join('');
}

export function graphEdges() {
  const nodes = visibleNodes(editor.team);
  const set = new Map(nodes.map((node) => [node.id, node]));
  return nodes
    .filter((node) => set.has(node.parentId))
    .map((node) => {
      const parent = set.get(node.parentId);
      let path;
      if (editor.team.layout === 'horizontal') {
        const x = parent.x + CARD.width,
          y = parent.y + CARD.height / 2;
        const tx = node.x,
          ty = node.y + CARD.height / 2;
        const bend = Math.max(40, Math.abs(tx - x) * 0.5);
        path = `M${x} ${y} C${x + bend} ${y}, ${tx - bend} ${ty}, ${tx} ${ty}`;
      } else {
        const x = parent.x + CARD.width / 2,
          y = parent.y + CARD.height;
        const tx = node.x + CARD.width / 2,
          ty = node.y;
        const bend = Math.max(35, Math.abs(ty - y) * 0.5);
        path = `M${x} ${y} C${x} ${y + bend}, ${tx} ${ty - bend}, ${tx} ${ty}`;
      }
      return `<path d="${path}" class="${node.id === editor.selected || parent.id === editor.selected ? 'selected-edge' : ''}"/>`;
    })
    .join('');
}

export function outline() {
  const visit = (parent, depth = 0) =>
    editor.team.nodes
      .filter((node) => node.parentId === parent)
      .map(
        (node) =>
          `<button data-team-focus="${e(node.id)}" data-focus-key="${e(node.id)}" style="padding-left:${12 + depth * 14}px" class="${editor.selected === node.id ? 'active' : ''}">${icon(roleMeta[node.role].icon)}<span>${e(node.name)}</span></button>${visit(node.id, depth + 1)}`,
      )
      .join('');
  return visit(null);
}

export function readingList() {
  const visit = (parent, depth = 0) =>
    editor.team.nodes
      .filter((node) => node.parentId === parent)
      .map((node) => {
        const role = roleMeta[node.role];
        return `<button type="button" id="reading-node-${e(node.id)}" class="reading-node ${editor.selected === node.id ? 'active' : ''}" data-team-focus="${e(node.id)}" data-focus-key="read-${e(node.id)}" style="--depth:${depth}">
          <span class="reading-node-title">${icon(role.icon)}${e(node.name)}</span>
          <span class="reading-node-role">${e(role.name)}</span>
          <span class="reading-node-prompt">${e(node.prompt || '尚未填写职责与交付要求')}</span>
        </button>${visit(node.id, depth + 1)}`;
      })
      .join('');
  return `<p class="reading-note">按节点阅读分工。这里的文字和按钮保持原始大小，不随拓扑缩小。要看整张关系图，使用“查看全图”。</p>${visit(null)}`;
}

function resourceChips(node, kind) {
  const label = kind === 'skills' ? 'Skills' : 'MCP';
  return `<section class="node-bindings"><div class="inspector-section-title"><h3>${icon(kind === 'skills' ? 'skill' : 'plug')}${label}<span>${node[kind].length}</span></h3>
    <button class="text-link" data-team-bind="${kind}">${icon('plus')}选择</button></div>
    <div class="binding-chips">${node[kind].map((ref) => `<span class="binding-chip ${state.catalog[kind].some((item) => item.id === ref.id) ? '' : 'missing'}"><span title="${e(ref.name)}">${e(ref.name)}</span><button data-team-unbind="${kind}" data-ref="${e(ref.id)}" aria-label="移除 ${e(ref.name)}">${icon('close')}</button></span>`).join('') || `<span class="binding-empty">从本机 ${label} 库绑定能力</span>`}</div></section>`;
}

export function inspector() {
  if (editor.inspectorTab === 'preview') return previewPanel();
  const node = selectedNode();
  if (!node) return '<div class="inspector-empty">选择一个节点，设置它的职责和能力。</div>';
  const children = descendants(editor.team, node.id);
  const clientMissing =
    node.agentId && !state.catalog.agents.some((item) => item.id === node.agentId);
  const groups = clientGroups(state.catalog.agents, state.runners);
  const unknownClient =
    node.agentId && !groups.some((group) => group.clients.some((item) => item.id === node.agentId));
  const executionStatus = runnerStatus(node.agentId, state.runners);
  return `<div class="node-editor">
    <div class="inspector-title"><span class="node-avatar">${icon(roleMeta[node.role].icon)}</span><div><strong>${e(roleMeta[node.role].name)}</strong><small>${children.size} 个下级节点</small></div>
      <button class="icon-button" data-team-focus="${e(node.id)}" title="定位节点" aria-label="定位当前节点">${icon('focus')}</button></div>
    <label class="team-field">节点名称<input id="team-node-name" data-node-field="name" maxlength="80" value="${e(node.name)}"></label>
    <div class="team-field"><span>角色</span><div class="role-switch">${Object.entries(roleMeta)
      .map(
        ([role, meta]) =>
          `<button data-team-role="${role}" aria-pressed="${node.role === role}" class="${node.role === role ? 'active' : ''}">${icon(meta.icon)}${meta.name}</button>`,
      )
      .join('')}</div></div>
    <label class="team-field">汇报给<select id="team-node-parent" data-node-field="parentId"><option value="">用户 / 总目标</option>${editor.team.nodes
      .filter((item) => item.id !== node.id && !children.has(item.id))
      .map(
        (item) =>
          `<option value="${e(item.id)}" ${item.id === node.parentId ? 'selected' : ''}>${e(item.name)}</option>`,
      )
      .join('')}</select></label>
    <label class="team-field">绑定客户端<select id="team-node-client" data-node-field="agentId" aria-describedby="team-runner-note"><option value="">选择本机客户端</option>${unknownClient ? `<option value="${e(node.agentId)}" selected>来源未匹配 · ${e(node.agentId)}</option>` : ''}${groups.map((group) => `<optgroup label="${e(group.label)}">${group.clients.map((agent) => `<option value="${e(agent.id)}" ${agent.error ? 'disabled' : ''} ${node.agentId === agent.id ? 'selected' : ''}>${e(agent.name)} · ${e(runnerStatus(agent.id, state.runners).label)}</option>`).join('')}</optgroup>`).join('')}</select><small id="team-runner-note">${e(executionStatus.detail)}</small></label>
    <label class="team-field">执行模型 <span class="field-optional">可选</span><select id="team-node-model" data-node-field="model"><option value="">沿用客户端当前模型</option></select><small id="team-model-note">选择该节点所用的模型。</small></label>
    <button class="text-link" type="button" id="team-model-refresh">刷新模型列表</button>
    <label class="team-field">职责与交付要求<textarea id="team-node-prompt" data-node-field="prompt" maxlength="10000" rows="4" placeholder="它负责什么？向上级交付什么？">${e(node.prompt)}</textarea></label>
    <button class="text-link role-import" data-team-action="import-role">${icon('book')}引用本机角色定义</button>
    <label class="team-field">审核与汇报<textarea id="team-node-policy" data-node-field="policy" maxlength="500" rows="2">${e(node.policy)}</textarea></label>
    ${resourceChips(node, 'skills')}${resourceChips(node, 'mcps')}
    <button class="btn node-assemble" data-team-action="assemble" ${!node.agentId || clientMissing || (!node.skills.length && !node.mcps.length) ? 'disabled' : ''}>${icon('layers')}在装配台预览此节点</button>
    <p class="inspector-note">装配应用于所选客户端的全局配置。这里选择的模型仅用于该节点执行；也可沿用客户端当前模型。</p>
    <button class="text-link node-delete" data-team-action="delete-node" ${editor.team.nodes.length === 1 ? 'disabled' : ''}>${icon('trash')}删除节点，保留下级</button>
  </div>`;
}

export function activity() {
  const preview = editor.preview;
  const step = preview?.steps[preview.index];
  const progress = preview
    ? `${preview.index} / ${preview.steps.length} 步`
    : `${editor.team.nodes.length} 个角色`;
  const messages = preview?.log || [];
  return `<div class="activity-scroll" id="activity-scroll">
    <div class="activity-intro"><span class="activity-title">与团队一起，把任务安排清楚</span>
      <p>流程预演 · 不调用模型 · ${e(progress)}</p></div>
    <div class="activity-thread">
      ${editor.team.goal.trim() ? `<article class="activity-goal"><span>你的目标</span><div>${e(editor.team.goal)}</div></article>` : ''}
      ${
        !preview
          ? `<div class="activity-welcome"><span class="activity-avatar">${icon('network')}</span><div><h2>团队已就位</h2>
        <p>先在画布设置分工和能力，再用预演检查任务如何交接。预演里的通过或打回不会启动模型，也不会让自动调度重试。</p>
        <div class="activity-roster">${editor.team.nodes.map((node) => `<button data-team-open-node="${e(node.id)}">${icon(roleMeta[node.role].icon)}${e(node.name)}</button>`).join('')}</div>
        <button class="btn primary" data-team-action="start-preview">${icon('play')}开始流程预演</button></div></div>`
          : ''
      }
      <div class="activity-messages" role="log" aria-label="协作预演消息">${messages
        .map((message) => {
          const member = editor.team.nodes.find((node) => node.id === message.id);
          const role = member ? roleMeta[member.role] : roleMeta.employee;
          return `<article class="activity-message"><span class="activity-avatar">${icon(role.icon)}</span><div>
          <div class="activity-message-meta"><strong>${e(message.name)}</strong><span>${e(role.name)}</span><span>预演记录</span></div>
          <div class="activity-bubble">${e(message.text)}</div></div></article>`;
        })
        .join('')}</div>
      ${
        step
          ? `<article class="activity-message current"><span class="activity-avatar">${icon(roleMeta[editor.team.nodes.find((node) => node.id === step.id).role].icon)}</span><div>
        <div class="activity-message-meta"><strong>${e(step.name)}</strong><span>${step.kind === 'review' ? '等待模拟审核' : '下一步交接'}</span></div>
        <div class="activity-bubble"><p>${e(step.text)}</p><div class="activity-step-actions">
          ${step.kind === 'review' ? '<button class="btn" data-team-action="reject-preview">模拟打回（仅预演）</button><button class="btn primary" data-team-action="next-preview">模拟通过（仅预演）</button>' : `<button class="btn primary" data-team-action="next-preview">演示下一步 ${icon('arrow')}</button>`}
        </div></div></div></article>`
          : preview
            ? `<div class="activity-complete">${icon('check')}流程预演结束，任务说明可以交给执行端使用。
          <div>${action('brief', '导出任务说明', 'book')}${action('start-preview', '重新预演', 'refresh')}</div></div>`
            : ''
      }
    </div></div>`;
}

function previewPanel() {
  const preview = editor.preview;
  const issues = teamIssues(editor.team, state.catalog);
  const step = preview?.steps[preview.index];
  return `<div class="preview-panel"><div class="preview-label">${icon('play')}流程预演</div>
    <h3>先检查分工，再开始工作</h3><p class="inspector-note">按拓扑演示任务交接与审核顺序。这里不会调用模型，也不会生成真实执行结果。模拟通过或打回只存在于预演；自动调度不会按审核结果打回或重试。需要修改时，请调整任务后再次手动执行。</p>
    ${issues.length ? `<details class="team-issues"><summary>${issues.length} 项待完善</summary><ul>${issues.map((issue) => `<li>${e(issue)}</li>`).join('')}</ul></details>` : '<p class="team-ready">目标、职责与资源引用已填写。</p>'}
    ${
      !preview
        ? `<button class="btn primary" data-team-action="start-preview">${icon('play')}开始流程预演</button>`
        : `
      <div class="preview-progress"><span>${preview.index} / ${preview.steps.length} 步</span><progress value="${preview.index}" max="${preview.steps.length}"></progress></div>
      ${
        step
          ? `<div class="preview-step"><span>${step.kind === 'review' ? '审核关口' : step.kind === 'assign' ? '任务分派' : '产物交接'}</span><h4>${e(step.name)}</h4><p>${e(step.text)}</p></div>
        <div class="preview-buttons">${step.kind === 'review' ? `<button class="btn" data-team-action="reject-preview">模拟打回（仅预演）</button><button class="btn primary" data-team-action="next-preview">模拟通过（仅预演）</button>` : `<button class="btn primary" data-team-action="next-preview">演示下一步 ${icon('arrow')}</button>`}</div>`
          : '<div class="preview-complete">流程预演结束。可导出任务说明，交给执行端使用。</div>'
      }
      <div class="preview-log" role="log" aria-label="预演记录">${preview.log.map((entry, index) => `<article><span>${String(index + 1).padStart(2, '0')}</span><div><strong>${e(entry.name)}</strong><p>${e(entry.text)}</p></div></article>`).join('')}</div>
      ${action('start-preview', '重新预演', 'refresh')}`
    }</div>`;
}
