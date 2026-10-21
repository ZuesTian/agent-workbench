import test from 'node:test';
import assert from 'node:assert/strict';
import { modelOptions } from '../agent_workbench/static/model-picker.js';

test('models with the same name keep distinct provider identifiers', () => {
  const result = modelOptions(
    {
      models: [
        { id: 'a/same', name: 'Same', provider: 'Provider A' },
        { id: 'b/same', name: 'Same', provider: 'Provider B' },
      ],
      current_model: 'b/same',
    },
    'a/same',
  );
  assert.match(result.options[0].label, /Provider B/);
  assert.deepEqual(
    result.options.map((row) => row.value),
    ['', 'a/same', 'b/same'],
  );
  assert.equal(result.missing, false);
});

test('unavailable saved model is preserved visibly and requires a new selection', () => {
  const result = modelOptions({ models: [{ id: 'new', name: 'New' }] }, 'old');
  assert.equal(result.missing, true);
  assert.equal(result.options.at(-1).value, 'old');
  assert.match(result.options.at(-1).label, /当前列表未找到/);
  assert.equal(modelOptions({ models: [] }).missing, false);
});
