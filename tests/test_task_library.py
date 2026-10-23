import uuid
import threading

import pytest

from agent_workbench.catalog import Catalog
from agent_workbench.service import WorkbenchService
from agent_workbench.storage import write_json


@pytest.fixture
def service(tmp_path):
    catalog = Catalog(home=tmp_path / "home", roaming=tmp_path / "roaming", data=tmp_path / "data", system_scan=False)
    service = WorkbenchService(catalog)
    yield service
    catalog.close()
    service.runs.active = None
    service.workflows.close()
    service.conversations.close()
    service.runs.close()


def record(service, kind="run", **extra):
    ident = uuid.uuid4().hex
    manager = {"run": service.runs, "workflow": service.workflows, "conversation": service.conversations}[kind]
    row = dict(id=ident, title="test task", name="test chat", status="succeeded", created_at="2026-10-23", finished_at="2026-10-23", steps=[], error="", **extra)
    write_json(manager._path(ident) / f"{kind}.json", row)
    return ident


def test_group_archive_trash_restore_and_restart(service):
    ident = record(service)
    group = service.post('/api/task-groups/save', {"name": "研究"})['id']
    def update(action, **fields):
        return service.post('/api/tasks/update', dict(id=ident, kind="run", action=action, **fields))
    update("group", group_id=group)
    assert service.get('/api/tasks', f'group={group}')['total'] == 1
    update("archive")
    assert not service.get('/api/runs')['items']
    assert service.get('/api/tasks', 'scope=archived')['items'][0]['id'] == ident
    update("delete")
    assert service.get('/api/tasks', 'scope=archived')['total'] == 0
    assert service.get('/api/tasks', 'scope=trash')['total'] == 1
    assert (service.runs._path(ident) / 'run.json').is_file()
    update("restore")
    assert service.get('/api/runs')['items'][0]['id'] == ident
    service.post('/api/task-groups/save', {'id': group, 'action': 'delete'})
    assert service.get('/api/tasks')['items'][0]['group_id'] == ''
    assert not service.get('/api/tasks')['groups']


def test_group_names_and_invalid_references_are_guarded(service):
    ident = record(service)
    service.post('/api/task-groups/save', {'name': 'Study'})
    with pytest.raises(ValueError, match="已存在"):
        service.post('/api/task-groups/save', {'name': 'study'})
    with pytest.raises(ValueError, match="不存在"):
        service.post('/api/tasks/update', {'kind': 'run', 'id': ident, 'action': 'group', 'group_id': 'unknown'})


def test_parent_organization_hides_steps_and_blocks_archived_resume(service):
    parent = record(service, "workflow")
    child = record(service, "run", workflow_id=parent)
    assert len(service.get('/api/tasks')['items']) == 1
    with pytest.raises(ValueError, match="父任务"):
        service.post('/api/tasks/update', dict(kind='run', id=child, action='delete'))
    service.post('/api/tasks/update', dict(kind='workflow', id=parent, action='archive'))
    assert service.get('/api/runs')['total'] == 0
    assert service.get('/api/workflows')['total'] == 0
    with pytest.raises(ValueError, match="恢复"):
        service.post('/api/workflows/resume', dict(id=parent, request_id=uuid.uuid4().hex))


def test_active_records_cannot_be_reorganized(service):
    ident = record(service)
    service.runs.active = ident
    with pytest.raises(ValueError, match="运行中"):
        service.post('/api/tasks/update', dict(kind='run', id=ident, action='delete'))
    service.runs.active = None


def test_chat_delete_is_recoverable_and_chat_children_remain_available(service):
    ident = record(service, "conversation", messages=[], workspace="project")
    service.post('/api/conversations/delete', {'id': ident})
    assert not service.get('/api/conversations')['items']
    assert service.get('/api/conversations/' + ident)['id'] == ident
    assert service.get('/api/tasks', 'scope=trash')['items'][0]['id'] == ident
    service.post('/api/tasks/update', dict(kind='conversation', id=ident, action='restore'))
    assert service.get('/api/conversations')['items'][0]['id'] == ident


def test_directory_picker_does_not_block_other_requests(service, monkeypatch):
    entered, release, saved = threading.Event(), threading.Event(), threading.Event()
    def picker():
        entered.set()
        release.wait(5)
        return ""
    monkeypatch.setattr('agent_workbench.service.browse_directory', picker)
    browse = threading.Thread(target=lambda: service.post('/api/browse-directory', {}))
    def save():
        service.post('/api/task-groups/save', {'name': 'while picker is open'})
        saved.set()
    writer = threading.Thread(target=save)
    browse.start()
    try:
        assert entered.wait(2)
        writer.start()
        assert saved.wait(2), 'Picker blocked mutation/cancellation requests'
    finally:
        release.set()
        browse.join(5)
        if writer.ident:
            writer.join(5)
