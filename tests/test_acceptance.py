import hashlib
import json

import pytest

from agent_workbench.acceptance import rules, prepare, evaluate, MAX_BYTES


def rule(path="output.json", kind="json", **kwargs):
    return rules([dict(path=path, kind=kind, **kwargs)])[0]


@pytest.mark.parametrize("path", ["../secret", "/secret", "C:/secret", "C:secret", "a/../x", "a//b", "NUL.json", "x:stream", "file.", "a/ /b"])
def test_paths_cannot_leave_workspace_or_name_devices(path):
    with pytest.raises(ValueError):
        rule(path)


def test_json_and_text_checks_record_original_and_final_hashes(tmp_path):
    checks = [rule(), rule(kind="contains", expected="完成")]
    evidence = prepare(tmp_path, checks)
    content = json.dumps({"结果": "完成"}, ensure_ascii=False).encode()
    (tmp_path / "output.json").write_bytes(content)
    result = evaluate(tmp_path, evidence, "succeeded")
    assert result["status"] == "passed"
    assert result["checked_at"] and evidence["checked_at"] is None
    assert all(c["before"] == {"exists": False} for c in result["checks"])
    assert all(c["after"]["sha256"] == hashlib.sha256(content).hexdigest() for c in result["checks"])


def test_preexisting_content_is_not_new_delivery_even_if_timestamp_changes(tmp_path):
    path = tmp_path / "output.json"
    path.write_text('{"valid": true}')
    evidence = prepare(tmp_path, [rule()])
    path.write_text('{"valid": true}')
    result = evaluate(tmp_path, evidence, "succeeded")
    assert result["status"] == "failed"
    assert "启动前相同" in result["checks"][0]["reason"]
    evidence = prepare(tmp_path, [rule(changed=False)])
    assert evaluate(tmp_path, evidence, "succeeded")["status"] == "passed"


@pytest.mark.parametrize("content", [b"", b"invalid", b'{"x":NaN}', b'"\xff"'])
def test_bad_json_or_empty_files_never_pass(tmp_path, content):
    evidence = prepare(tmp_path, [rule()])
    (tmp_path / "output.json").write_bytes(content)
    assert evaluate(tmp_path, evidence, "succeeded")["status"] == "failed"


def test_missing_text_and_missing_file_fail_independently(tmp_path):
    evidence = prepare(tmp_path, [rule(kind="contains", expected="wanted"), rule("missing.txt", "file")])
    (tmp_path / "output.json").write_text("different")
    result = evaluate(tmp_path, evidence, "succeeded")
    assert [r["reason"] for r in result["checks"]] == ["未找到指定文本", "文件不存在"]


@pytest.mark.parametrize("status", ["cancelled", "failed", "timed_out", "interrupted"])
def test_incomplete_execution_cannot_claim_acceptance(tmp_path, status):
    evidence = prepare(tmp_path, [rule()])
    (tmp_path / "output.json").write_text("{}")
    result = evaluate(tmp_path, evidence, status)
    assert result["status"] == "not_run"
    assert result["checks"][0]["status"] == "not_run"
    assert "after" not in result["checks"][0]


def test_cancelled_checks_and_no_rules_are_not_passed(tmp_path):
    assert evaluate(tmp_path, prepare(tmp_path, []), "succeeded")["status"] == "not_configured"
    evidence = prepare(tmp_path, [rule()])
    assert evaluate(tmp_path, evidence, "succeeded", lambda: True)["status"] == "not_run"


def test_large_files_are_bounded_before_and_after_execution(tmp_path):
    evidence = prepare(tmp_path, [rule()])
    with (tmp_path / "output.json").open("wb") as stream:
        stream.truncate(MAX_BYTES + 1)
    result = evaluate(tmp_path, evidence, "succeeded")
    assert result["status"] == "failed" and "16 MiB" in result["checks"][0]["reason"]
    with pytest.raises(ValueError, match="基线"):
        prepare(tmp_path, [rule()])


def test_redirected_output_is_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "work"
    root.mkdir()
    evidence = prepare(root, [rule("redirect/out.json")])
    try:
        (root / "redirect").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation unavailable")
    (outside / "out.json").write_text("{}")
    result = evaluate(root, evidence, "succeeded")
    assert result["status"] == "failed" and "链接" in result["checks"][0]["reason"]
    assert "after" not in result["checks"][0]


def test_rules_limit_prompt_budget_and_ignore_untrusted_verdicts():
    assert "status" not in rule(status="passed")
    with pytest.raises(ValueError, match="4000"):
        rules([dict(path=f"{i}.txt", kind="contains", expected="x" * 1000) for i in range(5)])
    with pytest.raises(ValueError):
        rule(changed="false")
