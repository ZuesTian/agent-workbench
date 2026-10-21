"""Merged AppData locations must work without accepting redirected record paths."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_workbench import storage


def test_record_locations_keep_existing_and_new_virtualized_records(tmp_path, monkeypatch):
    logical, physical, outside = (tmp_path / name for name in ("logical", "physical", "outside"))
    logical.mkdir()
    (logical / "old").mkdir()
    resolve = Path.resolve

    def mapped(path, strict=False):
        if path.parent == logical and (path.name.startswith(".awb-location-") or path.name == "new"):
            return physical / path.name
        if path == logical / "escaped":
            return outside / "escaped"
        return resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", mapped)
    roots = storage.record_roots(logical)
    assert roots == frozenset((logical, physical))
    assert list(logical.iterdir()) == [logical / "old"]
    for name in ("old", "new"):
        assert storage.record_directory(logical, name, roots, "任务") == logical / name
    with pytest.raises(ValueError, match="已被重定向"):
        storage.record_directory(logical, "escaped", roots, "任务")
    # Pin locations at initialization: a later new redirect is never learned.
    with pytest.raises(ValueError, match="已被重定向"):
        storage.record_directory(logical, "../outside", roots, "任务")


@pytest.mark.parametrize("link_kind", ["is_symlink", "is_junction"])
def test_links_are_rejected_even_if_they_point_inside_an_allowed_root(tmp_path, monkeypatch, link_kind):
    roots = storage.record_roots(tmp_path)
    original = getattr(Path, link_kind)
    monkeypatch.setattr(Path, link_kind, lambda p: p.name == "linked" or original(p))
    with pytest.raises(ValueError, match="已被重定向"):
        storage.record_directory(tmp_path, "linked", roots, "会话")


def test_probe_collision_never_removes_an_existing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(storage.uuid, "uuid4", lambda: SimpleNamespace(hex="collision"))
    existing = tmp_path / ".awb-location-collision.tmp"
    existing.write_text("preserve", encoding="utf-8")
    with pytest.raises(FileExistsError):
        storage.record_roots(tmp_path)
    assert existing.read_text(encoding="utf-8") == "preserve"
