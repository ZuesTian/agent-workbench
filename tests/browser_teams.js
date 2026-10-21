// Run against tests/ui_fixture.py. Real user Agent configurations are never used.
async (page) => {
  const errors = [], external = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    if (!request.url().startsWith('http://127.0.0.1:47832') && !request.url().startsWith('blob:')) external.push(request.url());
  });
  page.on('dialog', dialog => dialog.type() === 'beforeunload' ? dialog.accept() : dialog.dismiss());
  const check = (condition, message) => { if (!condition) throw Error(message); };
  const teamState = () => page.evaluate(async () => {
    const { editor } = await import('/team-model.js');
    return JSON.parse(JSON.stringify(editor));
  });
  const teamAction = async (action) => {
    let button = page.locator(`[data-team-action="${action}"]:visible`).first();
    if (!(await button.count())) button = page.locator(`[data-team-action="${action}"]`).first();
    if (!(await button.isVisible())) await page.locator('.team-menu').last().locator('summary').click();
    await button.click();
  };
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('about:blank');
  await page.goto('http://127.0.0.1:47832/#token=ui-fixture-session');
  await page.locator('.nav-button[data-tab="teams"]').waitFor();
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  check(await page.locator('html').getAttribute('data-theme') === 'light', 'Default theme must follow the Marvis reference');
  await page.locator('[data-action="theme"]').click();
  await page.locator('.nav-button[data-tab="teams"]').click();
  check(await page.locator('.graph-node').count() === 5, 'Default collaboration topology missing');
  await page.locator('#team-name').fill('协作集成验收');
  await page.locator('#team-goal').fill('组合本机能力，完成设计、实现与审核，并交付验证记录。');
  const originalInput = await page.locator('#team-node-name').elementHandle();
  await page.locator('#team-node-name').fill('项目负责人');
  check(await originalInput.evaluate(element => element.isConnected && element === document.activeElement), 'Node typing loses focus');
  await page.locator('#team-node-client').selectOption('codex');
  for (const kind of ['skills', 'mcps']) {
    await page.locator(`[data-team-bind="${kind}"]`).click();
    await page.locator('#team-resource-search').fill('lab');
    await page.locator('[data-team-resource]').first().check();
    await teamAction('confirm-bind');
  }
  await teamAction('save');
  await page.locator('#team-save-status').filter({ hasText: '已保存' }).waitFor();
  let saved = (await teamState()).team;
  check(saved.nodes[0].skills.length === 1 && saved.nodes[0].mcps.length === 1, 'Resource bindings not saved');
  await teamAction('assemble');
  if (await page.locator('#modal').isVisible()) await teamAction('confirm-replace');
  await page.getByRole('button', { name: '预览变更', exact: true }).click();
  await page.getByRole('heading', { name: '检查这次装配' }).waitFor();
  check(await page.locator('.plan-row').count() === 2, 'Node assembly did not reach the real preview');
  await page.locator('#modal [data-action="close-modal"]').first().click();
  await page.getByRole('button', { name: '收起装配台', exact: true }).click();

  // Move a parent: its entire subtree follows, but reporting relationships stay stable.
  let before = await teamState();
  const worker = before.team.nodes[1], child = before.team.nodes[3];
  const box = await page.locator(`[data-node="${worker.id}"]`).boundingBox();
  await page.mouse.move(box.x + 95, box.y + 55);
  await page.mouse.down();
  await page.mouse.move(box.x + 135, box.y + 85, { steps: 6 });
  await page.mouse.up();
  let after = await teamState();
  const movedWorker = after.team.nodes.find(n => n.id === worker.id);
  const movedChild = after.team.nodes.find(n => n.id === child.id);
  check(Math.abs((movedWorker.x - worker.x) - (movedChild.x - child.x)) < 0.1 && movedWorker.x !== worker.x, 'Dragging lost subtree positions');
  await page.locator(`[data-team-collapse="${worker.id}"]`).click();
  check(await page.locator('.graph-node').count() === 4, 'Collapse did not hide the child');
  await teamAction('expand');
  await teamAction('direction');
  check((await teamState()).team.layout === 'vertical', 'Vertical layout not applied');
  await teamAction('direction');
  await teamAction('add');
  await page.locator('#team-node-name').fill('新增测试角色');
  const newId = (await teamState()).selected;
  await page.locator('#team-node-parent').selectOption(saved.nodes[0].id);
  check((await teamState()).team.nodes.find(n => n.id === newId).parentId === saved.nodes[0].id, 'Parent selector did not update topology');
  await teamAction('delete-node');
  await teamAction('confirm-delete-node');
  check((await teamState()).team.nodes.length === 5, 'Node deletion changed unrelated nodes');
  await teamAction('save');
  await page.locator('#team-save-status').filter({ hasText: '已保存' }).waitFor();
  saved = (await teamState()).team;
  await page.reload();
  await page.locator('.nav-button[data-tab="teams"]').click();
  check((await teamState()).team.id === saved.id, 'Reload lost saved team identity');
  check((await teamState()).team.nodes[0].mcps.length === 1, 'Reload lost bindings');

  // Preflight is explicitly a demonstration, with a real manual review gate in the UI.
  await teamAction('preview');
  await teamAction('start-preview');
  for (let i = 0; i < 3; i++) await teamAction('next-preview');
  const reviewIndex = (await teamState()).preview.index;
  await teamAction('reject-preview');
  check((await teamState()).preview.index === reviewIndex, 'Reject must keep the review gate pending');
  for (let i = 0; i < 8; i++) {
    if (!(await page.locator('[data-team-action="next-preview"]').count())) break;
    await teamAction('next-preview');
  }
  await page.locator('.preview-complete').waitFor();
  check(await page.locator('.preview-panel').innerText().then(t => t.includes('不会调用模型')), 'Simulation boundary missing');
  const completePreview = (await teamState()).preview;
  await page.locator('[data-team-mode="activity"]').click();
  check(await page.locator('.activity-complete').isVisible(), 'Conversation view did not reflect completed preview');
  check(await page.locator('.activity-messages .activity-message').count() === completePreview.log.length, 'Conversation view lost messages');
  await teamAction('start-preview');
  for (let i = 0; i < 3; i++) await teamAction('next-preview');
  const activityReview = (await teamState()).preview.index;
  await teamAction('reject-preview');
  check((await teamState()).preview.index === activityReview, 'Conversation review did not stay pending');
  await page.locator('[data-team-mode="canvas"]').click();
  check((await teamState()).preview.index === activityReview, 'Switching views reset the preview');
  await page.locator('[data-team-panel="config"]').click();

  // Export through an actual browser download; schema never includes MCP configuration.
  const downloadPromise = page.waitForEvent('download');
  await teamAction('export');
  const download = await downloadPromise;
  await download.saveAs('D:/OneDrive/文档/Local/agent-workbench/.test-artifacts/team-export.json');
  check(download.suggestedFilename().endsWith('.team.json'), 'Wrong export name');
  const briefPromise = page.waitForEvent('download');
  await teamAction('brief');
  await (await briefPromise).saveAs('D:/OneDrive/文档/Local/agent-workbench/.test-artifacts/team-brief.md');

  // Both themes and small windows must remain usable, including native dialogs.
  for (const width of [1440, 1280, 1024, 768, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    check(!(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)), `Collaboration overflow at ${width}`);
    await page.locator('[data-team-mode="activity"]').click();
    check(!(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)), `Activity overflow at ${width}`);
    await page.locator('[data-team-mode="canvas"]').click();
  }
  await page.locator('[data-team-bind="skills"]').click();
  check(await page.locator('#modal').evaluate(element => element.contains(document.activeElement)), 'Binding dialog lost focus');
  await page.keyboard.press('Escape');
  await page.locator('#modal').waitFor({ state: 'hidden' });
  await page.evaluate(() => { document.activeElement?.blur(); window.scrollTo(0, 0); });
  await page.screenshot({ path: 'D:/OneDrive/文档/Local/agent-workbench/.test-artifacts/team-mobile.png', fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.locator('[data-action="theme"]').click();
  check(await page.locator('html').getAttribute('data-theme') === 'light', 'Light theme not applied');
  await page.waitForFunction(() => getComputedStyle(document.querySelector('[data-team-action="save"]')).backgroundColor === 'rgb(255, 255, 255)');
  await page.screenshot({ path: 'D:/OneDrive/文档/Local/agent-workbench/.test-artifacts/team-light.png' });
  await page.reload();
  check(await page.locator('html').getAttribute('data-theme') === 'light', 'Theme preference not retained');
  await page.locator('.nav-button[data-tab="teams"]').click();
  await page.locator('[data-action="theme"]').click();
  await page.waitForFunction(() => getComputedStyle(document.querySelector('[data-team-action="save"]')).backgroundColor === 'rgb(36, 36, 36)');
  await page.screenshot({ path: 'D:/OneDrive/文档/Local/agent-workbench/.test-artifacts/team-dark.png' });

  // Invalid topology does not partly replace the current draft.
  const bad = { ...saved, nodes: saved.nodes.map(n => ({ ...n })) };
  bad.nodes[0].parentId = bad.nodes[1].id;
  await page.locator('#team-file').setInputFiles({ name: 'cycle.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(bad)) });
  await page.getByText('上下级关系形成了循环', { exact: true }).waitFor();
  check((await teamState()).team.id === saved.id, 'Invalid import replaced current team');

  // The user's original export format is accepted, with explicit local binding afterwards.
  const nexus = { workbench: 'NexusMind Multi-Agent Orchestrator', layout: 'horizontal',
    agents: [
      { id: 't1', name: '案例导师', role: 'tutor', parentId: null, model: 'example', policy: '审核', prompt: '拆解目标', position: { x: 10, y: 40 } },
      { id: 'e1', name: '案例协作者', role: 'employee', parentId: 't1', model: '', policy: '', prompt: '完成模块', position: { x: 320, y: 40 } },
    ] };
  await page.locator('#team-file').setInputFiles({ name: 'nexus.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(nexus)) });
  check(await page.locator('.graph-node').count() === 2, 'NexusMind topology not imported');
  await teamAction('saved');
  await page.locator(`[data-team-load="${saved.id}"]`).click();
  await teamAction('confirm-replace');
  check((await teamState()).team.nodes.length === 5, 'Saved team failed to reload');
  check(errors.length === 0, 'Browser errors: ' + errors.join('; '));
  check(external.length === 0, 'Unexpected external request: ' + external.join('; '));
  return { topology: 'passed', subtreeDrag: 'passed', bindingAndPreview: 'passed', persistence: 'passed',
    reviewGate: 'passed', activityView: 'passed', export: 'passed', nexusImport: 'passed', themes: 'passed', mobile: 'passed', errors, external };
}
