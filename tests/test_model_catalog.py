import json
from pathlib import Path

import pytest

from agent_workbench.model_catalog import (
    ModelCatalog, codex_models, dsh_models, zcode_models, zcode_model_override,
)
from agent_workbench.runners import prepare_command


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_codex_respects_provider_profile_and_hides_internal_models(tmp_path):
    (tmp_path / "config.toml").write_text('model = "primary"\n', encoding="utf-8")
    write(tmp_path / "models_cache.json", {"models": [
        {"slug": "primary", "display_name": "Primary", "visibility": "list"},
        {"slug": "hidden", "visibility": "hide"},
        {"slug": "alternate", "visibility": "list", "api_key": "must-not-escape"},
    ]})
    env = {"CODEX_HOME": str(tmp_path)}
    rows, current, _ = codex_models(env)
    assert [row["id"] for row in rows] == ["primary", "alternate"]
    assert current == "primary" and "must-not-escape" not in json.dumps(rows)
    (tmp_path / "config.toml").write_text(
        'model = "primary"\nprofile = "custom"\n[profiles.custom]\n'
        'model_provider = "local"\nmodel = "local-model"\n', encoding="utf-8",
    )
    rows, current, _ = codex_models(env)
    assert [row["id"] for row in rows] == ["local-model"] and current == "local-model"


def test_codex_custom_catalog_path(tmp_path):
    (tmp_path / "config.toml").write_text(
        'model_provider = "custom"\nmodel_catalog_json = "catalog.json"\n', encoding="utf-8",
    )
    write(tmp_path / "catalog.json", {"models": [{"slug": "own-model"}]})
    rows, _, _ = codex_models({"CODEX_HOME": str(tmp_path)})
    assert [row["id"] for row in rows] == ["own-model"]


def test_dsh_uses_composed_headless_config_and_installed_schema(monkeypatch, tmp_path):
    document = [
        {"id": "agent-default-model", "name": "default", "config": {"provider": "deepseek-official", "model": "flash"}},
        {"name": "@deepseek-ai/dsh-llm-deepseek"},
        {"name": "@deepseek-ai/dsh-llm-pi-ai", "config": {"providers": {
            "own": {"displayName": "Own", "apiKey": "private", "models": [{"id": "vendor/model"}]},
        }}},
        {"name": "@deepseek-ai/dsh-llm-pi-ai", "disabled": True, "config": {"providers": {
            "disabled": {"models": [{"id": "bad"}]},
        }}},
    ]
    schema = {"$defs": {"unrelated": {"properties": {"models": {"default": []}}}, "example": {"properties": {"models": {"default": [
        {"id": "flash", "name": "Flash"}, {"id": "pro", "name": "Pro"},
    ]}}}}}
    calls = []
    def output(command, *args):
        calls.append(args)
        return json.dumps(schema if args[-1] == "--dump-config-schema" else document)
    monkeypatch.setattr("agent_workbench.model_catalog.cli_output", output)
    rows, current, _ = dsh_models(["fixture"])
    assert [r["id"] for r in rows] == ["deepseek-official/flash", "deepseek-official/pro", "own/vendor/model"]
    assert current == "deepseek-official/flash"
    assert all(args[:2] == ("--profile", "headless") for args in calls)
    assert "private" not in json.dumps(rows)
    prepare_command(["node", "fixture"], {
        "agent": "deepseek-harness", "sandbox": "read-only", "cwd": str(tmp_path), "model": "own/vendor/model",
    }, tmp_path, {})
    patch = json.loads((tmp_path / "headless.patch.json").read_text(encoding="utf-8"))
    assert patch[-1]["config"] == {"provider": "own", "model": "vendor/model"}


def zcode_config(path):
    write(path, {"schemaVersion": 1, "config": {
        "providerConfigRules": {"providerRules": [
            {"providerId": "one", "providerName": "One", "config": {"personalModelIds": ["same", "hidden", "org/large"]}},
            {"providerId": "two", "providerName": "Two", "config": {"personalModelIds": ["same"]}},
            {"providerId": "off", "config": {"enabled": False, "personalModelIds": ["hidden"]}},
        ]},
        "modelConfigRules": {"providerModelRules": [
            {"providerId": "one", "modelId": "hidden", "config": {"enabled": False}},
        ], "manualProviderModelRules": []},
        "defaultModelSelection": {"providerId": "two", "modelId": "same"},
    }})
    return {"ZCODE_PERSONAL_PROVIDER_CONFIG_FILE": str(path)}


def test_zcode_names_are_provider_qualified_and_override_is_run_local(tmp_path):
    path = tmp_path / "personal.json"
    env = zcode_config(path)
    original = path.read_bytes()
    rows, current, _ = zcode_models(env)
    assert [r["id"] for r in rows] == ["one/same", "one/org/large", "two/same"]
    assert current == "two/same"
    directory = tmp_path / "run"
    directory.mkdir()
    zcode_model_override(env, "one/org/large", directory)
    assert path.read_bytes() == original
    selection = json.loads(Path(env["ZCODE_PERSONAL_PROVIDER_CONFIG_FILE"]).read_text(encoding="utf-8"))["config"]["defaultModelSelection"]
    assert selection == {"providerId": "one", "modelId": "org/large"}
    with pytest.raises(ValueError, match="Zcode"):
        zcode_model_override(env, "off/hidden", directory)


def test_zcode_override_preserves_secret_fields_and_limits_the_copy(tmp_path):
    path = tmp_path / "personal.json"
    env = zcode_config(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["config"]["apiKey"] = "VERY_SECRET"
    path.write_text(json.dumps(document), encoding="utf-8")
    original = path.read_bytes()
    directory = tmp_path / "run"
    directory.mkdir()
    zcode_model_override(env, "one/org/large", directory)
    assert path.read_bytes() == original
    copy_path = Path(env["ZCODE_PERSONAL_PROVIDER_CONFIG_FILE"])
    copy = json.loads(copy_path.read_text(encoding="utf-8"))
    assert copy["config"]["apiKey"] == "VERY_SECRET"
    assert copy["config"]["defaultModelSelection"] == {"providerId": "one", "modelId": "org/large"}
    import win32security

    descriptor = win32security.GetFileSecurity(str(copy_path), win32security.DACL_SECURITY_INFORMATION)
    access = descriptor.GetSecurityDescriptorDacl()
    blocked = {"S-1-1-0", "S-1-5-32-545"}
    for index in range(access.GetAceCount()):
        sid = win32security.ConvertSidToStringSid(access.GetAce(index)[-1])
        assert sid not in blocked


def test_catalog_failure_does_not_expose_config_or_poison_retry(tmp_path):
    env = {"CODEX_HOME": str(tmp_path)}
    path = tmp_path / "config.toml"
    path.write_text('secret = "private\n', encoding="utf-8")
    catalog = ModelCatalog()
    failed = catalog.get("codex", ["unused"], env)
    assert failed["models"] == [] and "private" not in json.dumps(failed)
    path.write_text('model = "first"\n', encoding="utf-8")
    assert catalog.get("codex", ["unused"], env)["current_model"] == "first"
    path.write_text('model = "second"\n', encoding="utf-8")
    assert catalog.get("codex", ["unused"], env, refresh=True)["current_model"] == "second"
