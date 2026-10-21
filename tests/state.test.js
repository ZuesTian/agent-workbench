import test, { beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import {
  state,
  clearSelection,
  loadProfile,
  importProfile,
  missingResources,
  toggleItem,
  groupResources,
  selection,
  persist,
  restoreDraft,
} from '../agent_workbench/static/state.js';

const catalog = {
  agents: [{ id: 'codex', name: 'Codex' }],
  skills: [
    {
      id: 'a',
      name: '实验记录',
      slug: 'lab',
      path: '/a/lab',
      description: '整理实验笔记',
      sources: [{ agent: 'codex', label: '本地 Skill' }],
    },
    {
      id: 'b',
      name: '实验记录',
      slug: 'lab',
      path: '/b/lab',
      description: '旧版笔记',
      sources: [{ agent: 'library', label: '我的资源库' }],
    },
    {
      id: 'c',
      name: '文献',
      slug: 'papers',
      path: '/papers',
      description: 'Read papers',
      sources: [{ agent: 'library', label: '我的资源库' }],
    },
  ],
  mcps: [
    {
      id: 'm',
      name: 'tools',
      agent: 'codex',
      scope: '全局',
      source: '/config',
      summary: { transport: 'stdio' },
    },
  ],
};
beforeEach(() => {
  clearSelection();
  Object.assign(state, {
    catalog: structuredClone(catalog),
    query: '',
    source: 'all',
    transport: 'all',
    onlySelected: false,
    group: true,
    sort: 'name',
  });
});

test('import keeps unresolved references and targets in replace mode', () => {
  const p = importProfile(
    {
      format: 'agent-workbench-profile',
      version: 1,
      name: 'Shared',
      skills: [{ id: 'a', name: '实验记录' }],
      mcps: [{ id: 'unknown', name: 'missing tools', source: '/elsewhere' }],
      agents: ['codex', 'absent-agent'],
      mode: 'replace_mcp',
    },
    catalog,
  );
  state.overwrite = true;
  loadProfile(p);
  assert.equal(state.mode, 'replace_mcp');
  assert.equal(state.mcps.size, 1);
  assert.equal(state.agents.size, 2);
  assert.equal(missingResources().length, 2);
  assert.equal(state.overwrite, false);
});

test('malformed import never partly replaces the current draft', () => {
  state.skills.add('a');
  assert.throws(() =>
    importProfile(
      {
        format: 'agent-workbench-profile',
        version: 1,
        skills: [{ id: 'c', name: '文献' }],
        mcps: [null],
        agents: ['codex'],
      },
      catalog,
    ),
  );
  assert.deepEqual([...state.skills], ['a']);
});

test('source selection replaces the same install slug, not unrelated skills', () => {
  toggleItem('skills', 'a');
  toggleItem('skills', 'c');
  toggleItem('skills', 'b');
  assert.deepEqual([...state.skills], ['c', 'b']);
  assert.equal(groupResources('skills').find((g) => g.item.name === '实验记录').item.id, 'b');
});

test('search combines Chinese text and source without changing selection', () => {
  state.skills.add('c');
  state.query = '实验 Codex';
  assert.equal(groupResources('skills').length, 1);
  assert.equal(groupResources('skills')[0].item.id, 'a');
  assert.deepEqual([...state.skills], ['c']);
});

test('selected filter and source filter compose', () => {
  state.skills.add('a');
  state.onlySelected = true;
  assert.equal(groupResources('skills').length, 1);
  state.source = 'library';
  assert.equal(groupResources('skills').length, 0);
});

test('saved profile with missing references stays blocked after loading', () => {
  loadProfile({
    id: 'profile',
    name: 'Saved',
    skills: ['removed'],
    mcps: [],
    agents: ['codex'],
    mode: 'replace_mcp',
  });
  assert.equal(missingResources()[0].id, 'removed');
  assert.equal(state.skills.size, 1);
});

test('invalid profile lists and modes are rejected before mutation', () => {
  state.skills.add('a');
  for (const bad of [
    { skills: 'abc', mcps: [], agents: [] },
    { skills: [], mcps: [], agents: [], mode: 'destructive-unknown' },
  ])
    assert.throws(() => loadProfile(bad));
  assert.deepEqual([...state.skills], ['a']);
});

test('local target choices survive drafts and profiles but not portable imports', () => {
  const saved = new Map();
  globalThis.localStorage = {
    setItem: (key, value) => saved.set(key, value),
    getItem: (key) => saved.get(key) ?? null,
  };
  loadProfile({
    name: 'Local',
    skills: [],
    mcps: ['m'],
    agents: ['codex'],
    config_targets: { codex: '/local/config.toml' },
  });
  persist();
  clearSelection();
  restoreDraft();
  assert.deepEqual(selection().config_targets, { codex: '/local/config.toml' });
  state.agents.clear();
  assert.deepEqual(selection().config_targets, {});
  loadProfile(
    importProfile(
      {
        format: 'agent-workbench-profile',
        version: 1,
        skills: [],
        mcps: [],
        agents: ['codex'],
        config_targets: { codex: '/another-machine/config.toml' },
      },
      catalog,
    ),
  );
  assert.deepEqual(selection().config_targets, {});
  delete globalThis.localStorage;
});

test('invalid target maps cannot replace a valid local draft', () => {
  state.skills.add('a');
  for (const targets of [null, [], { other: '/file' }, { codex: 7 }, { codex: '' }])
    assert.throws(() =>
      loadProfile({ skills: [], mcps: [], agents: ['codex'], config_targets: targets }),
    );
  assert.deepEqual([...state.skills], ['a']);
});
