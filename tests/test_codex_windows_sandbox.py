from types import SimpleNamespace

import pytest

from agent_workbench import runners


@pytest.mark.parametrize("os_name", ["nt", "posix"], ids=["windows", "linux"])
@pytest.mark.parametrize("sandbox", ["read-only", "workspace-write"])
def test_codex_prefers_mxc_only_on_windows_and_preserves_policy(tmp_path, monkeypatch, os_name, sandbox):
    monkeypatch.setattr(runners, "os", SimpleNamespace(name=os_name))
    prefix = ["node", "codex.js"]
    record = {"agent": "codex", "cwd": str(tmp_path), "sandbox": sandbox, "model": "example-model"}
    environment = {"EXISTING_SETTING": "preserved"}

    command = runners.prepare_command(prefix, record, tmp_path, environment)

    assert command[:len(prefix)] == prefix
    exec_index = command.index("exec")
    assert command[command.index("-a") + 1] == "never"
    assert command.index("-a") < exec_index
    if os_name == "nt":
        override_index = command.index("-c")
        assert command[override_index + 1] == "features.prefer_mxc=true"
        assert override_index < exec_index
    else:
        assert "-c" not in command
        assert not any("prefer_mxc" in argument for argument in command)
    assert command[command.index("--sandbox") + 1] == sandbox
    assert command[command.index("--cd") + 1] == record["cwd"]
    assert command[command.index("--model") + 1] == record["model"]
    assert command[command.index("--output-last-message") + 1] == str(tmp_path / "result.md")
    assert "--json" in command
    assert command[-1] == "-"
    assert not {"danger-full-access", "--dangerously-bypass-approvals-and-sandbox", "--full-auto"} & set(command)
    assert environment == {"EXISTING_SETTING": "preserved"}
