import test from 'node:test';
import assert from 'node:assert/strict';
import {
  clientGroups,
  executionBindings,
  runnerStatus,
} from '../agent_workbench/static/runner-status.js';

const runners = [
  { id: 'codex', name: 'Codex CLI', available: true },
  { id: 'zcode', name: 'Zcode', available: false },
  { id: 'deepseek-harness', name: 'DeepSeek Harness', available: true },
];

test('installation and execution capabilities stay distinct', () => {
  const groups = clientGroups(
    [
      { id: 'cursor', name: 'Cursor', installation_status: 'installed' },
      { id: 'codex', name: 'Codex' },
      { id: 'zcode', name: 'Zcode' },
    ],
    runners,
  );
  assert.deepEqual(
    groups.map((group) => [group.kind, group.clients.map((item) => item.id)]),
    [
      ['ready', ['codex', 'deepseek-harness']],
      ['unavailable', ['zcode']],
      ['unsupported', ['cursor']],
    ],
  );
  assert.equal(runnerStatus('cursor', runners).kind, 'unsupported');
  assert.equal(runnerStatus('zcode', runners).kind, 'unavailable');
});

test('missing bindings and unavailable capability data are not unsupported clients', () => {
  assert.equal(runnerStatus('', runners).kind, 'unbound');
  assert.equal(runnerStatus('cursor', null).kind, 'unknown');
  assert.equal(clientGroups([{ id: 'cursor', name: 'Cursor' }], null)[0].kind, 'unknown');
});

test('preflight checks every node including coordinators without replacing bindings', () => {
  const team = {
    nodes: [
      { id: 'root', agentId: 'codex' },
      { id: 'design', parentId: 'root', agentId: 'cursor' },
      { id: 'worker', parentId: 'design', agentId: 'deepseek-harness' },
      { id: 'review', parentId: 'root', agentId: '' },
    ],
  };
  const before = JSON.stringify(team);
  assert.deepEqual(
    executionBindings(team, runners).map(({ node, kind }) => [node.id, kind]),
    [
      ['root', 'ready'],
      ['design', 'unsupported'],
      ['worker', 'ready'],
      ['review', 'unbound'],
    ],
  );
  assert.equal(JSON.stringify(team), before);
});
