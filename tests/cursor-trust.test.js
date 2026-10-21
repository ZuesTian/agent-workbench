import test from 'node:test';
import assert from 'node:assert/strict';
import { mountCursorTrust } from '../agent_workbench/static/cursor-trust.js';

function fixture() {
  const checkbox = { checked: true };
  const path = {};
  const field = {
    querySelector: (selector) => (selector.endsWith('check]') ? checkbox : path),
  };
  const form = {
    elements: { cwd: { value: 'D:\\project' }, permission: { value: 'read-only' } },
    querySelector: () => field,
    addEventListener: (_, handler) => {
      form.input = handler;
    },
  };
  const enabled = { value: true };
  const picker = mountCursorTrust(form, () => enabled.value);
  return { checkbox, path, field, form, enabled, picker };
}

test('trust requires an explicit check, is scoped to a directory, and resets after submission', () => {
  const { checkbox, path, picker } = fixture();
  assert.equal(picker.value(), '');
  assert.equal(path.textContent, 'D:\\project');
  checkbox.checked = true;
  assert.equal(picker.value(), 'D:\\project');
  picker.reset();
  assert.equal(picker.value(), '');
});

for (const change of ['cwd', 'permission', 'client']) {
  test(`changing ${change} invalidates existing trust even before input events arrive`, () => {
    const { checkbox, form, enabled, picker } = fixture();
    checkbox.checked = true;
    if (change === 'client') enabled.value = false;
    else form.elements[change].value = change === 'cwd' ? 'D:\\other' : 'edit';
    assert.equal(picker.value(), '');
    assert.equal(checkbox.checked, false);
  });
}
