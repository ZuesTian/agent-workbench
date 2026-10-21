import json

from agent_workbench.model_catalog import cursor_models
from agent_workbench.runners import cursor_command, prepare_command, decode_event, cursor_workspace_untrusted


def test_cursor_resolves_only_complete_official_versions(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("agent_workbench.runners.shutil.which", lambda _: None)
    versions = tmp_path / "cursor-agent/versions"
    for name in ("2026.9.30-abc", "2026.10.19-def", "2026.10.20-bbb.new", "2026.10.21-ccc"):
        folder = versions / name
        folder.mkdir(parents=True)
        (folder / "node.exe").touch()
        if name != "2026.10.21-ccc":
            (folder / "index.js").touch()
    command = cursor_command()
    assert command == [str((versions / "2026.10.19-def/node.exe").resolve()),
                       str((versions / "2026.10.19-def/index.js").resolve())]


def test_cursor_models_use_account_catalog_and_strip_terminal_formatting(monkeypatch):
    calls = []
    def output(command, *args, environment=None):
        calls.append(args)
        if args[0] == "status":
            return json.dumps({"isAuthenticated": True, "accessToken": "must-not-return"})
        return "\x1b[2mAvailable models\x1b[0m\n\n" \
            "auto - Auto (default)\n" \
            "\x1b[32mexample[effort=high]\x1b[0m - Example High (current)\n" \
            "another - Another Model\n\nTip: use --model <id> to switch.\n"
    monkeypatch.setattr("agent_workbench.model_catalog.cli_output", output)
    rows, current, note = cursor_models(["fixture"], {})
    assert [row["id"] for row in rows] == ["auto", "example[effort=high]", "another"]
    assert current == "example[effort=high]" and rows[1]["name"] == "Example High"
    assert calls == [("status", "--format", "json"), ("models",)]
    assert "must-not-return" not in json.dumps((rows, current, note))


def test_cursor_login_requirement_does_not_trigger_inference_or_return_credentials(monkeypatch):
    def output(command, *args, **kwargs):
        assert args == ("status", "--format", "json")
        return '{"isAuthenticated":false,"message":"private"}'
    monkeypatch.setattr("agent_workbench.model_catalog.cli_output", output)
    rows, current, note = cursor_models(["fixture"], {})
    assert rows == [] and current == "" and "尚未登录" in note and "private" not in note


def test_cursor_command_keeps_prompt_on_stdin_and_existing_approval_rules(tmp_path):
    record = {"agent": "cursor", "cwd": str(tmp_path), "sandbox": "plan", "model": "example[effort=high]"}
    command = prepare_command(["node", "cursor/index.js"], record, tmp_path, {})
    assert command[-4:] == ["--mode", "plan", "--model", "example[effort=high]"]
    assert command[command.index("--workspace") + 1] == str(tmp_path)
    assert not {"--force", "--yolo", "--approve-mcps", "--trust"} & set(command)
    record.update(sandbox="edit", model="")
    command = prepare_command(["node", "cursor/index.js"], record, tmp_path, {})
    assert "--mode" not in command and "--model" not in command


def test_cursor_requires_explicit_success_and_does_not_log_thinking():
    assert decode_event("cursor", {"type": "assistant", "message": {"content": [
        {"type": "thinking", "thinking": "private"}, {"type": "text", "text": "answer"},
    ]}})["log"] == "answer"
    assert not decode_event("cursor", {"type": "assistant"}).get("completed")
    failed = decode_event("cursor", {"type": "result", "subtype": "error", "is_error": True, "result": "failed"})
    assert failed["failed"] and not failed["completed"]
    succeeded = decode_event("cursor", {"type": "result", "subtype": "success", "is_error": False,
                                        "result": "answer", "session_id": "fixture"})
    assert succeeded["completed"] and not succeeded["failed"] and succeeded["thread_id"] == "fixture"


def test_cursor_trust_flag_is_bound_to_exact_confirmed_workspace(tmp_path):
    record = dict(agent="cursor", cwd=str(tmp_path), sandbox="plan", model="",
                  cursor_trusted_cwd=str(tmp_path))
    command = prepare_command(["node", "cursor/index.js"], record, tmp_path, {})
    assert "--trust" in command and "--mode" in command
    assert not {"--force", "--yolo", "--approve-mcps"} & set(command)
    record["cursor_trusted_cwd"] = str(tmp_path.parent)
    assert "--trust" not in prepare_command(["node", "cursor/index.js"], record, tmp_path, {})
    record["cursor_trusted_cwd"] = str(tmp_path / ".")
    assert "--trust" in prepare_command(["node", "cursor/index.js"], record, tmp_path, {})
    record["cursor_trusted_cwd"] = str(tmp_path).replace("\\", "/")
    assert "--trust" in prepare_command(["node", "cursor/index.js"], record, tmp_path, {})


def test_cursor_trust_detection_accepts_color_codes_and_nearby_wording():
    assert cursor_workspace_untrusted("\x1b[33mWorkspace\x1b[0m Trust Required")
    assert cursor_workspace_untrusted("Do you trust the contents of this directory?")
    assert cursor_workspace_untrusted("Pass --trust if you trust this directory")
    assert not cursor_workspace_untrusted("model is not available")
