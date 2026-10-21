// Run with the Playwright connector against tests/ui_fixture.py.
async (page) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const check = (condition, message) => { if (!condition) throw Error(message); };
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('about:blank');
  await page.goto('http://127.0.0.1:47832/#token=ui-fixture-session');
  await page.getByRole('heading', { name: '你的 Agent 工作空间' }).waitFor();
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await page.locator('.nav-button[data-tab="skills"]').click();
  const search = page.locator('#resource-search');
  const originalInput = await search.elementHandle();
  await search.fill('lab');
  check(await originalInput.evaluate(element => element === document.activeElement && element.isConnected), 'Search lost its original input/focus');
  check(await page.locator('.resource-card').count() === 1, 'Search result mismatch');
  await page.getByRole('button', { name: '列表视图', exact: true }).click();
  check(await page.locator('.resource-grid.list-view').count() === 1, 'List view not applied');
  await page.getByRole('button', { name: '选择 lab-notes', exact: true }).click();
  await page.locator('#compose-target-codex').check();
  await page.locator('.nav-button[data-tab="mcps"]').click();
  await page.getByRole('button', { name: '选择 lab-tools', exact: true }).click();
  await page.getByRole('button', { name: '保存方案', exact: true }).click();
  await page.locator('#profile-name').fill('新版装配回归');
  await page.locator('#profile-description').fill('隔离测试，不修改真实客户端。');
  await page.locator('#modal [data-action="confirm-save"]').click();
  await page.locator('#modal').waitFor({ state: 'hidden' });
  await page.getByRole('button', { name: '预览变更', exact: true }).click();
  await page.getByRole('heading', { name: '检查这次装配' }).waitFor();
  check(await page.locator('.plan-row').count() === 2, 'Expected skill + MCP plan');
  await page.getByRole('button', { name: '应用并备份', exact: true }).click();
  await page.locator('#modal').waitFor({ state: 'hidden' });
  await page.locator('.nav-button[data-tab="history"]').click();
  await page.getByText('已应用', { exact: true }).waitFor();
  await page.getByRole('button', { name: '恢复变更前的内容', exact: true }).click();
  await page.getByRole('button', { name: '检查并恢复', exact: true }).click();
  await page.locator('#modal').waitFor({ state: 'hidden' });
  await page.getByText('已恢复', { exact: true }).waitFor();
  await page.locator('.nav-button[data-tab="profiles"]').click();
  await page.getByRole('heading', { name: '新版装配回归', exact: true }).waitFor();
  const file = {
    format: 'agent-workbench-profile', version: 1, name: '缺失来源检查',
    skills: [], mcps: [{ id: 'no-longer-here', name: '离线资源', source: '/absent' }],
    agents: ['codex'], mode: 'replace_mcp',
  };
  await page.locator('#profile-file').setInputFiles({
    name: 'missing.json', mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(file)),
  });
  await page.getByText('1 项资源或目标已失效', { exact: true }).waitFor();
  check(await page.getByRole('button', { name: '预览变更', exact: true }).isDisabled(), 'Missing import should block sync');
  await page.getByRole('button', { name: '清空组合', exact: true }).click();
  await page.getByRole('button', { name: '收起装配台', exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('.nav-button[data-tab="skills"]').click();
  check(!(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)), 'Mobile library overflows');
  check(await page.locator('.resource-description').first().evaluate(element =>
    element.getBoundingClientRect().height >= parseFloat(getComputedStyle(element).lineHeight) - 1
  ), 'Mobile list description is clipped');
  await page.getByRole('button', { name: '选择 lab-notes', exact: true }).click();
  await page.locator('#composer-trigger').click();
  await page.locator('#composer-dialog').waitFor({ state: 'visible' });
  check(!(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)), 'Mobile composer overflows');
  await page.locator('#compose-target-codex').check();
  check(await page.locator('#composer-dialog').evaluate(element => element.contains(document.activeElement)), 'Drawer lost keyboard focus');
  await page.keyboard.press('Escape');
  await page.locator('#composer-dialog').waitFor({ state: 'hidden' });
  await page.screenshot({ path: 'D:/OneDrive/文档/Local/agent-workbench/.test-artifacts/ui-v02-mobile.png' });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.locator('.nav-button[data-tab="settings"]').click();
  await page.getByRole('heading', { name: '管理你的工作空间' }).waitFor();
  check(errors.length === 0, 'Browser errors: ' + errors.join('; '));
  return { errors, desktop: 'passed', mobile: 'passed', searchFocus: 'retained', applyRestore: 'passed', missingImport: 'blocked' };
}
