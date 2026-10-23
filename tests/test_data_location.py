from pathlib import Path

import pytest

from agent_workbench.data_location import control_root, data_root, inventory, relocate
from agent_workbench.storage import read_json, write_json
from agent_workbench.catalog import Catalog
from test_task_library import service as shared_service, record

service = shared_service


def test_verified_migration_switches_catalog_but_keeps_runtime_and_original(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    source = control_root()
    write_json(source / 'teams/example.json', {'name': '中文方案'})
    write_json(source / 'runtime.json', {'token': 'private'})
    write_json(source / 'cache/catalog.json', {'old': True})
    expected = inventory(source)
    target = tmp_path / '新目录'
    result = relocate(source, str(target))
    assert result['files'] == 1 and result['restart_required']
    assert data_root() == target
    assert not (target / 'runtime.json').exists() and not (target / 'cache').exists()
    assert inventory(source) == expected
    assert read_json(target / 'relocation-receipt.json', {})['sha256'] == expected
    catalog = Catalog(system_scan=False)
    try:
        assert catalog.data == target
    finally:
        catalog.close()
    again = tmp_path / 'second-move'
    relocate(target, str(again))
    receipt = read_json(again / 'relocation-receipt.json', {})
    assert receipt['target_sha256'] == inventory(again)
    assert 'relocation-receipt.json' not in receipt['target_sha256']


def test_backup_does_not_switch_and_nonempty_target_is_not_overwritten(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    source = control_root()
    write_json(source / 'settings.json', {'roots': [], 'agents': []})
    target = tmp_path / 'backup'
    result = relocate(source, str(target), switch=False)
    assert not result['restart_required'] and data_root() == source
    with pytest.raises(ValueError, match='为空'):
        relocate(source, str(target))
    assert read_json(target / 'settings.json', {}) == {'roots': [], 'agents': []}


def test_nested_and_relative_locations_are_rejected(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    for target in [source, source / 'child', tmp_path, Path('relative')]:
        with pytest.raises(ValueError):
            relocate(source, str(target), switch=False)


def test_copy_failure_never_switches_pointer(tmp_path, monkeypatch):
    import agent_workbench.data_location as location
    source, target, control = tmp_path / 'source', tmp_path / 'target', tmp_path / 'control'
    write_json(source / 'settings.json', {'important': True})
    write_json(control / 'data-location.json', {'path': str(source)})
    monkeypatch.setattr(location.shutil, 'copy2', lambda *args: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError):
        relocate(source, str(target), control=control)
    assert read_json(control / 'data-location.json', {}) == {'path': str(source)}
    assert (source / 'settings.json').is_file()


def test_source_change_during_copy_never_switches(tmp_path, monkeypatch):
    import agent_workbench.data_location as location
    source, target, control = tmp_path / 'source', tmp_path / 'target', tmp_path / 'control'
    write_json(source / 'settings.json', {'value': 'before'})
    original = location.shutil.copy2
    def changing_copy(src, dest):
        original(src, dest)
        write_json(src, {'value': 'changed'})
    monkeypatch.setattr(location.shutil, 'copy2', changing_copy)
    with pytest.raises(ValueError, match='发生变化'):
        relocate(source, str(target), control=control)
    assert not (control / 'data-location.json').exists()


def test_service_freezes_mutations_after_migration_and_rejects_busy(service, tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'control'))
    ident = record(service)
    service.runs.active = ident
    with pytest.raises(ValueError, match='运行'):
        service.post('/api/data/migrate', {'path': str(tmp_path / 'target')})
    service.runs.active = None
    result = service.post('/api/data/migrate', {'path': str(tmp_path / 'target')})
    assert result['restart_required']
    assert service.get('/api/data/status')['migration'] == result
    with pytest.raises(ValueError, match='退出'):
        service.post('/api/task-groups/save', {'name': 'must not write to stale source'})


def test_migration_rebinds_saved_library_resources_without_rewriting_history(tmp_path):
    from agent_workbench.profiles import Profiles
    from agent_workbench.teams import Teams
    from test_scheduling import body, node

    source, target = tmp_path / 'source', tmp_path / 'target'
    skill = source / 'library/skills/demo/SKILL.md'
    skill.parent.mkdir(parents=True)
    skill.write_text('---\nname: demo\n---\nTest skill', encoding='utf-8')
    write_json(source / 'library/mcps.json', [{'id': 'demo', 'name': 'demo', 'kind': 'json', 'config': {'command': 'python'}}])
    old = Catalog(tmp_path / 'home', tmp_path / 'roaming', source, system_scan=False)
    try:
        old.scan()
        skill_id = next(iter(old.skills))
        mcp_id = next(iter(old.mcps))
        profile = Profiles(source).save({'name': 'saved', 'skills': [skill_id], 'mcps': [mcp_id]})
        team = body(tmp_path, [node('codex', 'worker')])['team']
        team['nodes'][0].update(skills=[{'id': skill_id, 'name': 'demo'}], mcps=[{'id': mcp_id, 'name': 'demo'}])
        team = Teams(source).save(team)
        write_json(source / 'workflows/evidence/workflow.json', {'team': team})
        result = relocate(source, str(target), control=tmp_path / 'control')
        assert len(result['rewritten_schemes']) == 2
        assert read_json(source / f"profiles/{profile['id']}.json", {})['skills'] == [skill_id]
        assert read_json(target / 'workflows/evidence/workflow.json', {}) == {'team': team}
        new = Catalog(tmp_path / 'home', tmp_path / 'roaming', target, system_scan=False)
        try:
            new.scan()
            saved = Profiles(target).list()[0]
            assert saved['skills'] == list(new.skills)
            assert saved['mcps'] == list(new.mcps)
            saved_team = Teams(target).list()[0]
            assert saved_team['nodes'][0]['skills'][0]['id'] in new.skills
            assert saved_team['nodes'][0]['mcps'][0]['id'] in new.mcps
            assert saved_team['revision'] != team['revision']
        finally:
            new.close()
    finally:
        old.close()
