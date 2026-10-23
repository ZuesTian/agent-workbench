"""UI recovery, bounded repair, task organization and verified local relocation."""

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

from playwright.sync_api import expect, sync_playwright

from conversations_browser_smoke import CLI_FIXTURE, main, wait_record


FIXTURE = CLI_FIXTURE.replace("const finish = () => {", """
const finish = () => {
  if (prompt.includes('/ 验收返工')) fs.writeFileSync('out.json', JSON.stringify({result:'fixed'}));
""").replace("const current = prompt.split(/## 用户的新消息\\r?\\n/).at(-1);",
              "const current = prompt.replaceAll('\\r\\n', '\\n').split('## 本节点职责\\n').at(-1).split('\\n\\n## 当前步骤')[0];")


def exercise(client, base, token, root, agents):
    workspace = root / "workspace"
    workspace.mkdir()
    team = {
        "format": "agent-workbench-team", "version": 1, "name": "有限返工演示", "goal": "生成可验收的文件",
        "layout": "horizontal", "nodes": [{
            "id": "worker", "parentId": None, "role": "employee", "name": "执行者",
            "prompt": "生成 out.json", "agentId": "codex", "x": 0, "y": 0,
        }],
    }
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("dialog", lambda dialog: dialog.accept())
            page.add_init_script("if (!localStorage.getItem('awb-team-draft-v1')) localStorage.setItem('awb-team-draft-v1', " + json.dumps(json.dumps({"team": team, "dirty": True})) + ");")
            page.goto(f"{base}/#token={token}")
            page.locator('.nav-button[data-tab="workflows"]').click()
            page.locator('#workflow-cwd').fill(str(workspace))
            page.locator('select[name="permission"]').select_option('edit')
            page.locator('#workflow-add-check').click()
            row = page.locator('.acceptance-rule').last
            row.locator('[data-check="path"]').fill('out.json')
            row.locator('[data-check="kind"]').select_option('json')
            page.locator('[name="max_reworks"]').select_option('1')
            with page.expect_response(lambda r: r.url.endswith('/api/workflows/start')) as response:
                page.locator('#workflow-start').click()
            first = response.value.json()
            assert 'id' in first, first
            first = wait_record(client, '/api/workflows/' + first['id'], lambda r: r['status'] not in {'starting', 'running', 'cancelling'})
            assert first['rework_calls'] == 1 and first['acceptance']['status'] == 'passed'
            page.locator('#workflow-refresh').click()
            expect(page.locator('#workflow-location')).to_contain_text('返工已调用 1 / 1 次')
            page.screenshot(path=str(root / 'rework.png'), full_page=True)

            page.locator('.nav-button[data-tab="tasks"]').click()
            page.locator('#task-group-form [name="name"]').fill('研究任务')
            page.locator('#task-group-form button[type="submit"]').click()
            expect(page.locator('#task-filter [name="group"] option')).to_have_count(2)
            group = client.get('/api/tasks').json()['groups'][0]['id']
            task = page.locator('.task-library-record', has_text='有限返工演示')
            task.locator('[data-task-group]').select_option(group)
            page.locator('#task-filter [name="group"]').select_option(group)
            expect(task).to_have_count(1)
            task.locator('[data-task-action="archive"]').click()
            expect(task).to_have_count(0)
            assert client.get('/api/workflows').json()['total'] == 0
            page.locator('#task-filter [name="scope"]').select_option('archived')
            expect(task).to_have_count(1)
            task.locator('[data-task-action="delete"]').click()
            expect(task).to_have_count(0)
            page.locator('#task-filter [name="scope"]').select_option('trash')
            expect(task).to_have_count(1)
            task.locator('[data-task-action="restore"]').click()
            expect(task).to_have_count(0)
            page.locator('#task-filter [name="scope"]').select_option('active')
            expect(task).to_have_count(1)
            for width in (1440, 390):
                page.set_viewport_size({'width': width, 'height': 1000})
                assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
                page.screenshot(path=str(root / f'task-library-{width}.png'), full_page=True)
            page.set_viewport_size({'width': 1440, 'height': 1000})

            interrupted = json.loads(json.dumps(team))
            interrupted['name'] = '断点续跑演示'
            interrupted['nodes'].append({**interrupted['nodes'][0], 'id': 'child', 'parentId': 'worker', 'name': '第二步', 'prompt': 'FAIL_ONCE_NODE'})
            response = client.post('/api/workflows/start', json={'request_id': uuid.uuid4().hex, 'team': interrupted, 'cwd': str(workspace), 'permission': 'read-only', 'timeout_minutes': 1})
            response.raise_for_status()
            second = wait_record(client, '/api/workflows/' + response.json()['id'], lambda r: r['status'] == 'failed')
            first_run = second['steps'][0]['run_id']
            assert second['steps'][0]['status'] == 'succeeded'
            page.locator('#task-filter [name="group"]').select_option('')
            task = page.locator('.task-library-record', has_text='断点续跑演示')
            expect(task).to_have_count(1)
            task.locator('[data-task-action="open"]').click()
            expect(page.locator('#workflow-detail')).to_have_attribute('data-id', second['id'])
            page.locator('#workflow-resume').click()
            second = wait_record(client, '/api/workflows/' + second['id'], lambda r: r['status'] == 'succeeded')
            assert second['steps'][0]['run_id'] == first_run and len(second['resumptions']) == 1
            assert second['steps'][1]['attempts'][0]['status'] == 'failed'
            page.locator('#workflow-refresh').click()
            expect(page.locator('#workflow-location')).to_contain_text('续跑 1 次')
            page.screenshot(path=str(root / 'resume.png'), full_page=True)

            skill_source = root / 'portable-skill'
            skill_source.mkdir()
            (skill_source / 'SKILL.md').write_text('---\nname: portable-skill\n---\nFixture skill', encoding='utf-8')
            client.post('/api/library/skill', json={'path': str(skill_source)}).raise_for_status()
            imported = client.post('/api/library/mcp', json={'name': 'fixture-library', 'config': {'command': 'python'}})
            imported.raise_for_status()
            catalog = imported.json()
            skill_id = next(s['id'] for s in catalog['skills'] if any(source['agent'] == 'library' for source in s['sources']))
            mcp_id = next(m['id'] for m in catalog['mcps'] if m['agent'] == 'library')
            profile = client.post('/api/profiles/save', json={'name': '迁移引用验证', 'skills': [skill_id], 'mcps': [mcp_id]})
            profile.raise_for_status()
            team['nodes'][0].update(skills=[{'id': skill_id, 'name': 'portable-skill'}], mcps=[{'id': mcp_id, 'name': 'fixture-library'}])
            saved_team = client.post('/api/teams/save', json=team)
            saved_team.raise_for_status()

            page.locator('.nav-button[data-tab="settings"]').click()
            backup = root / 'backup-data'
            page.locator('#data-target').fill(str(backup))
            page.locator('#data-migration-form [name="mode"]').select_option('backup')
            page.locator('#data-migrate').click()
            expect(page.locator('#data-migration-status')).to_contain_text('备份完成', timeout=30000)
            source = root / 'local/AgentWorkbench'
            assert not (source / 'data-location.json').exists()
            assert (backup / f'workflows/{first["id"]}/workflow.json').is_file()
            target = root / 'migrated-data'
            page.locator('#data-target').fill(str(target))
            page.locator('#data-migration-form [name="mode"]').select_option('move')
            page.locator('#data-migrate').click()
            expect(page.locator('#data-migration-status')).to_contain_text('新目录才会生效', timeout=30000)
            assert json.loads((source / 'data-location.json').read_text(encoding='utf-8'))['path'] == str(target)
            assert (source / f'workflows/{first["id"]}/workflow.json').is_file()
            assert (target / f'workflows/{second["id"]}/workflow.json').is_file()
            assert client.post('/api/task-groups/save', json={'name': 'blocked'}).status_code == 400
            page.screenshot(path=str(root / 'migration.png'), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 1000})
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
            assert errors == [], errors
            return {'feature': 'rework-resume-tasks-migration', 'agents_executed': ['codex'],
                    'checks': ['bounded_rework', 'failed_reasons_handoff', 'group', 'archive', 'trash_restore',
                               'resume_without_repeating_success', 'local_backup', 'verified_migration',
                               'mutation_freeze', 'responsive'],
                    'workflow_ids': [first['id'], second['id']], 'profile_id': profile.json()['id'],
                    'team_id': saved_team.json()['id'], 'old_resource_ids': [skill_id, mcp_id], 'browser_errors': errors}
        finally:
            browser.close()


def verify_packaged_restart(result):
    root = Path(result['fixture'])
    executable = Path(sys.argv[sys.argv.index('--exe') + 1]).resolve()
    environment = os.environ.copy()
    environment.update(USERPROFILE=str(root / 'home'), HOME=str(root / 'home'),
                       APPDATA=str(root / 'roaming'), LOCALAPPDATA=str(root / 'local'),
                       PATH=str(root / 'bin') + os.pathsep + str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32'),
                       PYTHONUTF8='1')
    runtime = root / 'local/AgentWorkbench/runtime.json'
    assert not runtime.exists(), 'Previous fixture did not shut down'
    with (root / 'restart.log').open('wb') as log:
        process = subprocess.Popen([str(executable), '--serve'], env=environment, stdout=log, stderr=log,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            deadline = time.monotonic() + 40
            while not runtime.exists() and time.monotonic() < deadline:
                assert process.poll() is None, 'Restart exited early'
                time.sleep(0.1)
            info = json.loads(runtime.read_text(encoding='utf-8'))
            with httpx.Client(base_url=f"http://127.0.0.1:{info['port']}", trust_env=False,
                              headers={'X-Workbench': '1'}, timeout=30) as client:
                client.post('/api/auth', json={'token': info['token']}).raise_for_status()
                try:
                    status = client.get('/api/data/status').json()
                    assert Path(status['path']) == root / 'migrated-data' and status['migration'] is None
                    tasks = client.get('/api/tasks').json()
                    assert {r['id'] for r in tasks['items']} == set(result['workflow_ids'])
                    assert tasks['groups'][0]['name'] == '研究任务'
                    for ident in result['workflow_ids']:
                        flow = client.get('/api/workflows/' + ident).json()
                        assert flow['status'] == 'succeeded'
                        assert flow['directory'].startswith(str(root / 'migrated-data'))
                    catalog = wait_record(client, '/api/catalog', lambda c: bool(c.get('skills')) and not c.get('scan', {}).get('running'), timeout=40)
                    profile = next(p for p in client.get('/api/profiles').json() if p['id'] == result['profile_id'])
                    team = next(t for t in client.get('/api/teams').json() if t['id'] == result['team_id'])
                    assert profile['skills'][0] in {s['id'] for s in catalog['skills']}
                    assert profile['mcps'][0] in {m['id'] for m in catalog['mcps']}
                    assert [profile['skills'][0], profile['mcps'][0]] != result['old_resource_ids']
                    assert team['nodes'][0]['skills'][0]['id'] == profile['skills'][0]
                    assert team['nodes'][0]['mcps'][0]['id'] == profile['mcps'][0]
                    client.post('/api/task-groups/save', json={'name': '重启后可写'}).raise_for_status()
                    assert len(json.loads((root / 'migrated-data/task-library.json').read_text(encoding='utf-8'))['groups']) == 2
                    assert len(json.loads((root / 'local/AgentWorkbench/task-library.json').read_text(encoding='utf-8'))['groups']) == 1
                finally:
                    client.post('/api/shutdown', json={}).raise_for_status()
            assert process.wait(timeout=20) == 0
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    result['checks'].append('real_exe_restart_uses_new_data_and_preserves_old')
    result['checks'].append('saved_skill_and_mcp_references_resolve_after_migration')
    (root / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    result = main(exercise, FIXTURE, 'recovery')
    if result['mode'] == 'packaged':
        verify_packaged_restart(result)
