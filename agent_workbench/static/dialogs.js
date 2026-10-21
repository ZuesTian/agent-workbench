import { state, getItem, agentName } from '/state.js';
import { api } from '/api.js';
import { e, icon, showModal } from '/ui.js';
import { directoryExtras } from '/directories.js';

function formField(label, id, placeholder = '', value = '', extra = '') {
  return `<label class="field">${label}<input id="${id}" placeholder="${e(placeholder)}" value="${e(value)}" ${extra}></label>`;
}
export async function details(kind, id) {
  const x = getItem(kind, id);
  if (!x) return;
  if (kind === 'roles') {
    const detail = await api('roles/' + id);
    showModal(
      x.name,
      `<dl class="detail-grid"><dt>来源</dt><dd>${e(agentName(x.agent))}</dd><dt>定义文件</dt><dd>${e(x.path)}</dd></dl><pre class="content-pre">${e(detail.content)}</pre>`,
    );
    return;
  }
  const variants = state.catalog[kind].filter((v) => v.name === x.name),
    skill = kind === 'skills';
  const info = skill ? await api('skills/' + id) : x;
  const summary = skill
    ? `<p class="subheading" style="margin-bottom:20px">${e(x.description)}</p><dl class="detail-grid"><dt>目录</dt><dd>${e(x.path)}</dd><dt>正文标识</dt><dd>${e(x.signature)}（仅 SKILL.md，不用于断言整个目录相同）</dd></dl><pre class="content-pre">${e(info.content)}</pre>`
    : `<dl class="detail-grid"><dt>连接方式</dt><dd>${e(x.summary.transport)}</dd><dt>启动器 / 主机</dt><dd>${e(x.summary.launcher || x.summary.host || '—')}</dd><dt>来源配置</dt><dd>${e(x.source)}</dd><dt>作用域</dt><dd>${e(x.scope)}</dd><dt>配置状态</dt><dd>${x.summary.enabled ? '配置未标记禁用' : '配置已禁用'}；尚未检查连通性</dd><dt>环境变量</dt><dd>${e(x.summary.environment_keys.join(', ') || '无显式变量')}</dd><dt>HTTP 请求头</dt><dd>${e(x.summary.header_keys.join(', ') || '无')}</dd><dt>配置字段</dt><dd>${e(x.summary.fields.join(', '))}</dd></dl><div class="notice info">装配时在本机直接读取原配置中的凭据；界面与导出的方案不展示密钥值。选择被禁用的 MCP 后装配，会尝试在目标启用它。</div>`;
  showModal(
    x.name,
    summary +
      `<div class="section-title"><h2>选择具体来源 <span class="count">${variants.length}</span></h2></div>${variants.map((v) => `<div class="variant"><div><strong>${e(skill ? [...new Set(v.sources.map((s) => agentName(s.agent)))].join(' · ') : agentName(v.agent))}${v.id === id ? ' · 当前查看' : ''}</strong><p>${e(skill ? v.path : v.source)}</p><p>${e(skill ? v.sources.map((s) => s.label).join(' · ') : v.scope + ' · ' + v.summary.transport)}</p></div><button class="btn small ${state[kind].has(v.id) ? '' : 'primary'}" data-choose="${kind}" data-id="${v.id}">${state[kind].has(v.id) ? '已选择' : '采用此来源'}</button></div>`).join('')}`,
    `<button class="btn" data-action="close-modal">关闭</button><button class="btn primary" data-choose="${kind}" data-id="${id}">${icon('plus')}加入装配台</button>`,
  );
}
export function saveDialog() {
  showModal(
    '保存装配方案',
    `${formField('方案名称', 'profile-name', '例如：文献阅读与写作', state.profileName)}<label class="field">用途说明（可选）<textarea id="profile-description" placeholder="记下这组能力适合做什么…">${e(state.profileDescription)}</textarea></label><p class="subheading">${state.skills.size} Skills · ${state.mcps.size} MCP · ${state.agents.size} 个目标</p>`,
    `<button class="btn" data-action="close-modal">取消</button>${state.profileId ? '<button class="btn" data-action="save-as-profile">另存为</button>' : ''}<button class="btn primary" data-action="confirm-save">${icon('save')}${state.profileId ? '更新方案' : '保存方案'}</button>`,
  );
}
export function addMcpDialog() {
  showModal(
    '添加 MCP 到资源库',
    `${formField('MCP 名称', 'mcp-name', '例如：my-research-tools')}<label class="field">来源格式<select id="mcp-kind"><option value="json">通用 JSON / Claude / Cursor</option><option value="codex">Codex 字段（用 JSON 对象填写）</option><option value="opencode">OpenCode</option><option value="gemini">Gemini CLI</option></select></label><label class="field">单个 MCP 的 JSON 配置<textarea id="mcp-config" class="code" spellcheck="false" placeholder='{"command":"npx","args":["-y","your-mcp-package"],"env":{}}'></textarea><small>不包含 mcpServers 外层。这里只保存定义，装配后由目标客户端启动服务。配置与密钥保存在本机资源库文件中。</small></label>`,
    '<button class="btn" data-action="close-modal">取消</button><button class="btn primary" data-action="confirm-mcp">保存到资源库</button>',
  );
}
export function importSkillDialog() {
  showModal(
    '导入本地 Skill',
    `${formField('包含 SKILL.md 的目录', 'skill-path', 'C:\\Users\\你\\my-skills\\my-skill')}${directoryExtras('skill-path')}<div class="notice info">会完整复制该 Skill 目录到工坊资源库，包含脚本与参考文件。也可在设置中添加目录，直接盘点现有文件。</div>`,
    '<button class="btn" data-action="close-modal">取消</button><button class="btn primary" data-action="confirm-import-skill">导入 Skill</button>',
  );
}
export function rootDialog() {
  showModal(
    '添加 Skill 扫描目录',
    formField('目录的绝对路径', 'root-path', 'D:\\Projects\\my-skills') +
      directoryExtras('root-path'),
    '<button class="btn" data-action="close-modal">取消</button><button class="btn primary" data-action="confirm-root">添加并扫描</button>',
  );
}
export function installRootDialog() {
  showModal(
    '添加程序查找目录',
    `${formField('程序所在目录（绝对路径）', 'install-root-path', 'G:\\\\Zcode')}${directoryExtras('install-root-path')}<p class="subheading">查找已支持客户端的程序文件，最多向下三层。Skill 库请使用设置里的「额外的 Skill 目录」。</p>`,
    '<button class="btn" data-action="close-modal">取消</button><button class="btn primary" data-action="confirm-install-root">添加并扫描</button>',
  );
}
export function agentDialog() {
  showModal(
    '接入自定义 Agent',
    `${formField('Agent 名称', 'agent-name', '例如：我的本地助手')}${formField('MCP 配置文件（绝对路径）', 'agent-config', 'C:\\Users\\你\\.myagent\\mcp.json')}<div class="form-row"><label class="field">配置格式<select id="agent-kind"><option value="json">通用 JSON</option><option value="codex">Codex TOML</option><option value="opencode">OpenCode JSON</option><option value="vscode">VS Code JSON</option><option value="gemini">Gemini JSON</option></select></label>${formField('MCP 所在的根字段', 'agent-key', '', 'mcpServers')}</div>${formField('Skill 安装目录（可选）', 'agent-skills', 'C:\\Users\\你\\.myagent\\skills')}<p class="subheading">配置文件尚不存在时，其父目录需已存在；字段由目标软件的规范决定。</p>`,
    '<button class="btn" data-action="close-modal">取消</button><button class="btn primary" data-action="confirm-agent">接入并扫描</button>',
  );
}
export function showPreview(plan) {
  state.plan = plan;
  const choices = (plan.config_choices || [])
    .map(
      (choice) =>
        `<label class="field">${e(choice.agent)} · 本次写入的配置文件<select data-config-target="${e(choice.agent_id)}"><option value="" disabled ${choice.selected ? '' : 'selected'}>请选择配置文件</option>${choice.paths.map((path) => `<option value="${e(path)}" ${choice.selected === path ? 'selected' : ''}>${e(path)}</option>`).join('')}</select><small>其余配置文件会保留。更改选择后请重新预览。</small></label>`,
    )
    .join('');
  showModal(
    '检查这次装配',
    `${plan.errors.length ? `<div class="notice error"><strong>请先处理以下问题</strong><ul>${plan.errors.map((x) => `<li>${e(x)}</li>`).join('')}</ul></div>` : ''}${choices}${plan.operations.length ? plan.operations.map((op) => `<div class="plan-row"><div><span class="tag ${op.action === '替换' ? 'warm' : ''}">${e(op.action)}</span><strong>${e(op.agent)}</strong><span>${e(op.name || op.kind)}</span></div><code>${e(op.target)}</code>${op.changes ? `<div class="plan-changes">${op.changes.map((x) => `<span class="tag ${x.action === '移除' ? 'error' : ''}">${e(x.action)} ${e(x.name)}</span>`).join('')}</div>` : ''}</div>`).join('') : `<div class="notice info">${plan.errors.length ? '本次预览尚未通过，请处理问题后重新预览。' : '所选资源与目标一致，没有需要写入的文件。'}</div>`}<div class="notice"><ul>${plan.warnings.map((x) => `<li>${e(x)}</li>`).join('')}</ul></div>`,
    `<small>${plan.operation_count} 个实际写入 · 预览 15 分钟内有效</small><button class="btn" data-action="close-modal">返回调整</button>${choices ? '<button class="btn" data-action="preview">重新预览</button>' : ''}<button class="btn primary" data-action="confirm-apply" ${plan.errors.length || !plan.operation_count ? 'disabled' : ''}>${icon('check')}应用并备份</button>`,
  );
}
export function downloadProfile(profile) {
  const enrich = (kind, ids) =>
    ids.map((id) => {
      const x = getItem(kind, id);
      return { id, name: x?.name || '', source: kind === 'skills' ? x?.path : x?.source };
    });
  const doc = {
    format: 'agent-workbench-profile',
    version: 1,
    name: profile.name,
    description: profile.description || '',
    agents: profile.agents,
    mode: profile.mode || 'merge',
    skills: enrich('skills', profile.skills),
    mcps: enrich('mcps', profile.mcps),
  };
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(doc, null, 2)], { type: 'application/json' }),
  );
  const a = document.createElement('a');
  a.href = url;
  a.download = (profile.name || 'agent-profile').replace(/[<>:"/\\|?*]/g, '_') + '.json';
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
