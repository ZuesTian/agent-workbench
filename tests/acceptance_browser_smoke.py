"""File acceptance from editor to real fixture CLI, persisted evidence and reload."""

import json

from playwright.sync_api import expect, sync_playwright

from conversations_browser_smoke import CLI_FIXTURE, main, wait_record


FIXTURE = CLI_FIXTURE.replace("const finish = () => {", """
const finish = () => {
  if (prompt.includes('WRITE_ARTIFACT')) fs.writeFileSync('handoff.json', JSON.stringify({result:'HANDOFF_TOKEN'}));
""")


def exercise(client, base, token, root, agents):
    workspace = root / "workspace"
    workspace.mkdir()
    team = {
        "format": "agent-workbench-team", "version": 1, "name": "交付验收演示", "goal": "生成并检查交付文件",
        "layout": "horizontal", "nodes": [{
            "id": "worker", "parentId": None, "role": "employee", "name": "执行者",
            "prompt": "WRITE_ARTIFACT", "agentId": "codex", "x": 0, "y": 0,
        }],
    }
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.add_init_script("if (!localStorage.getItem('awb-team-draft-v1')) localStorage.setItem('awb-team-draft-v1', " + json.dumps(json.dumps({"team": team, "dirty": True})) + ");")
            page.goto(f"{base}/#token={token}")
            page.locator('.nav-button[data-tab="workflows"]').click()
            page.locator('#workflow-cwd').fill(str(workspace))
            page.locator('select[name="permission"]').select_option('edit')
            for kind, expected in [('json', ''), ('contains', 'HANDOFF_TOKEN')]:
                page.locator('#workflow-add-check').click()
                row = page.locator('.acceptance-rule').last
                row.locator('[data-check="path"]').fill('handoff.json')
                row.locator('[data-check="kind"]').select_option(kind)
                if expected:
                    row.locator('[data-check="expected"]').fill(expected)
            draft = page.evaluate("JSON.parse(localStorage.getItem('awb-team-draft-v1')).team")
            assert len(draft['acceptance']) == 2 and draft['acceptance'][1]['expected'] == 'HANDOFF_TOKEN'
            saved = client.post('/api/teams/save', json=draft)
            saved.raise_for_status()
            assert saved.json()['acceptance'] == draft['acceptance']

            def submit():
                with page.expect_response(lambda response: response.url.endswith('/api/workflows/start')) as response:
                    page.locator('#workflow-start').click()
                started = response.value.json()
                assert 'id' in started, started
                result = wait_record(client, f"/api/workflows/{started['id']}", lambda r: r['status'] not in {'starting', 'running', 'cancelling'})
                page.locator('#workflow-refresh').click()
                expect(page.locator('#workflow-status')).to_have_text('执行已完成')
                return result

            first = submit()
            assert first['acceptance']['status'] == 'passed'
            expect(page.locator('#workflow-acceptance-result h3')).to_have_text('文件验收通过')
            assert first['steps'][0]['runtime']['entrypoints']
            with page.expect_download() as download:
                page.locator('#workflow-evidence-download').click()
            download.value.save_as(root / 'evidence-download.json')
            evidence = json.loads((root / 'evidence-download.json').read_text(encoding='utf-8'))
            assert evidence['acceptance'] == first['acceptance']
            assert evidence['steps'][0]['runtime']['model_source'] == 'client_default'
            for width in (1440, 390):
                page.set_viewport_size({'width': width, 'height': 1000})
                page.evaluate('window.scrollTo(0, 0)')
                assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
                assert page.locator('#workflow-form .execution-submit').evaluate("el => getComputedStyle(el).position") == 'static'
                page.screenshot(path=str(root / f'acceptance-{width}.png'), full_page=True)
            page.set_viewport_size({'width': 1440, 'height': 1000})
            second = submit()
            assert second['status'] == 'succeeded' and second['acceptance']['status'] == 'failed'
            expect(page.locator('#workflow-acceptance-result h3')).to_have_text('文件验收未通过')
            expect(page.locator('#workflow-acceptance-result')).to_contain_text('文件内容与启动前相同')
            # Explicitly permitting existing artifacts enables read-only audits.
            for box in page.locator('[data-check="changed"]').all():
                box.uncheck()
            third = submit()
            assert third['acceptance']['status'] == 'passed'
            page.reload()
            page.locator('.nav-button[data-tab="workflows"]').click()
            expect(page.locator('#workflow-detail')).to_have_attribute('data-id', third['id'])
            expect(page.locator('#workflow-acceptance-result h3')).to_have_text('文件验收通过')
            assert page.locator('.acceptance-rule').count() == 2
            assert not page.locator('[data-check="changed"]').first.is_checked()
            assert errors == [], errors
            return {'feature': 'artifact-acceptance', 'agents_executed': ['codex'],
                    'checks': ['editor', 'saved_scheme', 'real_fixture_artifact', 'freshness_failure',
                               'allow_existing', 'exact_evidence_download', 'reload', 'responsive'],
                    'workflow_ids': [r['id'] for r in (first, second, third)], 'browser_errors': errors}
        finally:
            browser.close()


if __name__ == '__main__':
    main(exercise, FIXTURE, 'acceptance')
