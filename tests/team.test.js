import test from 'node:test';
import assert from 'node:assert/strict';
import {
  createTeam,
  reparent,
  descendants,
  removeNode,
  visibleNodes,
  autoLayout,
  validateTeam,
  importTeam,
  exportTeam,
  teamIssues,
  previewSteps,
  taskBrief,
  editor,
  setTeam,
  restoreTeamDraft,
  preserveTeamDraftBeforeClose,
} from '../agent_workbench/static/team-model.js';

test('hierarchy rejects cycles and invalid parents without changing the relationship', () => {
  const team = createTeam();
  const root = team.nodes[0],
    leaf = team.nodes[3];
  assert.throws(() => reparent(team, root.id, leaf.id), /下级/);
  assert.throws(() => reparent(team, leaf.id, leaf.id), /自身/);
  assert.throws(() => reparent(team, leaf.id, 'absent'), /找不到/);
  assert.equal(root.parentId, null);
  assert.equal(descendants(team, root.id).size, 4);
});

test('moving and deleting a parent retains its children and their resource bindings', () => {
  const team = createTeam();
  const parent = team.nodes[1],
    child = team.nodes[3],
    root = team.nodes[0];
  child.skills = [{ id: 'ref', name: 'Skill' }];
  removeNode(team, parent.id);
  assert.equal(child.parentId, root.id);
  assert.equal(child.skills[0].id, 'ref');
  assert.equal(validateTeam(team).nodes.length, 4);
  assert.throws(() => removeNode(createTeam('blank'), root.id), /至少保留/);
});

test('collapsed nodes only affect visibility; plans still include the whole team', () => {
  const team = createTeam();
  team.nodes[0].collapsed = true;
  assert.equal(visibleNodes(team).length, 1);
  assert.equal(new Set(previewSteps(team).map((step) => step.id)).size, 5);
  assert.equal(previewSteps(team).at(-1).kind, 'review');
});

test('both layout directions position children after parents without mutating bindings', () => {
  const team = createTeam();
  for (const direction of ['horizontal', 'vertical']) {
    autoLayout(team, direction);
    const axis = direction === 'horizontal' ? 'x' : 'y';
    for (const node of team.nodes.filter((item) => item.parentId)) {
      assert(node[axis] > team.nodes.find((item) => item.id === node.parentId)[axis]);
    }
  }
});

test('NexusMind exports are imported as blueprints with editable roles and positions', () => {
  const imported = importTeam({
    workbench: 'NexusMind Multi-Agent Orchestrator',
    layout: 'horizontal',
    agents: [
      {
        id: 't1',
        parentId: null,
        name: '导师',
        role: 'tutor',
        policy: '审核',
        prompt: '检查',
        model: 'example-model',
        position: { x: 110, y: 310 },
      },
      { id: 'e1', parentId: 't1', name: '员工', role: 'employee', position: { x: 510, y: 310 } },
    ],
  });
  assert.equal(imported.nodes[0].x, 110);
  assert.equal(imported.nodes[0].model, 'example-model');
  assert.equal(imported.nodes[1].parentId, 't1');
  assert.deepEqual(imported.nodes[0].skills, []);
});

test('untrusted imports reject invalid topology and oversized or malformed fields', () => {
  for (const edit of [
    (team) => {
      team.nodes[0].parentId = team.nodes[1].id;
    },
    (team) => {
      team.nodes[1].id = team.nodes[0].id;
    },
    (team) => {
      team.nodes[0].x = Infinity;
    },
    (team) => {
      team.nodes[0].role = '__proto__';
    },
    (team) => {
      team.nodes[0].collapsed = 'false';
    },
    (team) => {
      team.nodes[0].prompt = 'x'.repeat(10001);
    },
    (team) => {
      team.nodes[0].skills = [
        { id: 's', name: 'x' },
        { id: 's', name: 'y' },
      ];
    },
  ]) {
    const team = createTeam();
    edit(team);
    assert.throws(() => importTeam(team));
  }
});

test('portable export drops IDs, revisions, credentials, runtime states and file paths', () => {
  const team = createTeam();
  Object.assign(team, { id: 'saved', revision: 'old', token: 'secret' });
  Object.assign(team.nodes[0], { config: { env: { API_KEY: 'secret' } }, status: 'completed' });
  team.nodes[0].skills = [{ id: 's', name: 'skill', source: '/private-path' }];
  const output = JSON.stringify(exportTeam(team));
  assert(!output.includes('secret'));
  assert(!output.includes('private-path'));
  assert(!output.includes('completed'));
  assert(!output.includes('revision'));
  assert.equal(team.id, 'saved');
});

test('missing references are retained and shown as issues rather than silently reassigned', () => {
  const team = createTeam();
  team.nodes[0].agentId = 'absent-client';
  team.nodes[0].mcps = [{ id: 'missing', name: 'Tool' }];
  const catalog = { agents: [], skills: [], mcps: [{ id: 'different', name: 'Tool' }] };
  const imported = importTeam(exportTeam(team));
  assert.equal(imported.nodes[0].mcps[0].id, 'missing');
  assert(teamIssues(imported, catalog).some((issue) => issue.includes('来源未匹配')));
  assert(teamIssues(imported, catalog).some((issue) => issue.includes('客户端已失效')));
});

test('task brief contains real assignments and review policies but no configuration values', () => {
  const team = createTeam();
  team.goal = '构建本机管理工具';
  team.nodes[0].agentId = 'codex';
  team.nodes[0].mcps = [{ id: 'm', name: 'Research' }];
  const text = taskBrief(team, { agents: [{ id: 'codex', name: 'Codex', config: 'secret' }] });
  assert(text.includes('构建本机管理工具'));
  assert(text.includes('客户端：Codex'));
  assert(text.includes('MCP：Research'));
  assert(!text.includes('secret'));
});

test('closing retains an unsaved collaboration draft for the next launch', (t) => {
  const previous = globalThis.localStorage;
  const values = new Map();
  globalThis.localStorage = {
    setItem: (key, value) => values.set(key, value),
    getItem: (key) => values.get(key),
  };
  t.after(() => {
    globalThis.localStorage = previous;
  });
  const team = createTeam();
  team.goal = '退出后继续编排';
  setTeam(team);
  assert.equal(preserveTeamDraftBeforeClose(), true);
  setTeam(createTeam('blank'), true);
  restoreTeamDraft();
  assert.equal(editor.team.goal, '退出后继续编排');
  assert.equal(editor.dirty, true);
});

test('failed local draft storage still prevents silent loss on exit', (t) => {
  const previous = globalThis.localStorage;
  globalThis.localStorage = {
    setItem: () => {
      throw new Error('Storage full');
    },
  };
  t.after(() => {
    globalThis.localStorage = previous;
  });
  setTeam(createTeam());
  assert.equal(preserveTeamDraftBeforeClose(), false);
  assert.equal(editor.dirty, true);
  setTeam(createTeam(), true);
  assert.equal(preserveTeamDraftBeforeClose(), true);
});
