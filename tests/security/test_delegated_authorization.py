from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from dodaios.agent_chat.authorization import (
    AuthorizationViolation,
    harden_codex_plan,
    prepare_delegated_workspace,
    validate_launch,
)
from dodaios.agent_chat.folder_tools import authorized_delegation_tools
from dodaios.agent_chat.store import AgentChatStore


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout.strip()


def _repository(path: Path) -> None:
    path.mkdir()
    _git(path, "init")
    _git(path, "config", "user.email", "security-test@example.invalid")
    _git(path, "config", "user.name", "Security Test")
    (path / "inside.txt").write_text("inside\n", encoding="utf-8")
    _git(path, "add", "inside.txt")
    _git(path, "commit", "-m", "fixture")


def test_delegated_workspace_is_detached_and_authority_is_deny_by_default(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _repository(source)

    workspace, envelope = prepare_delegated_workspace(
        source,
        task_id="task-1",
        worker="codex-cli",
        isolation_root=tmp_path / "isolated",
    )

    assert workspace != source.resolve()
    assert (workspace / "inside.txt").read_text(encoding="utf-8") == "inside\n"
    assert _git(workspace, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"
    assert envelope.workspace_root == str(workspace)
    assert envelope.source_root == str(source.resolve())
    assert envelope.network is False
    assert not any((envelope.commit, envelope.push, envelope.pull_request, envelope.merge))
    saved = json.loads((workspace.parent / "authorization.json").read_text(encoding="utf-8"))
    assert saved["authorization_id"] == envelope.authorization_id


def test_isolation_root_cannot_be_inside_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(AuthorizationViolation, match="cannot be inside"):
        prepare_delegated_workspace(
            source,
            task_id="recursive-copy-test",
            worker="codex-cli",
            isolation_root=source / "isolated",
        )


def test_default_isolation_root_relocates_outside_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dodaios.agent_chat.authorization as authorization

    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.setattr(authorization, "user_data_dir", lambda: source / "data")
    workspace, envelope = prepare_delegated_workspace(
        source, task_id="relocation-test", worker="codex-cli"
    )
    with pytest.raises(ValueError):
        workspace.relative_to(source)
    assert Path(envelope.source_root) == source.resolve()


def test_non_git_source_link_is_not_dereferenced(tmp_path: Path) -> None:
    source = tmp_path / "source"
    outside = tmp_path / "outside"
    source.mkdir()
    outside.mkdir()
    link = source / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation is unavailable")
    with pytest.raises(AuthorizationViolation, match="link or junction"):
        prepare_delegated_workspace(
            source,
            task_id="linked-source-test",
            worker="codex-cli",
            isolation_root=tmp_path / "isolated",
        )


def test_launch_revalidation_rejects_workspace_and_worker_changes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _repository(source)
    workspace, envelope = prepare_delegated_workspace(
        source,
        task_id="task-2",
        worker="codex-cli",
        isolation_root=tmp_path / "isolated",
    )
    session = SimpleNamespace(authorization_json=envelope.dumps())

    assert validate_launch(session, "codex-cli", workspace) == envelope
    with pytest.raises(AuthorizationViolation, match="outside its authorization"):
        validate_launch(session, "codex-cli", source)
    with pytest.raises(AuthorizationViolation, match="no enforceable authorization adapter"):
        validate_launch(session, "claude-cli", workspace)
    elevated = replace(envelope, push=True)
    (Path(envelope.runtime_home).parent / "authorization.json").write_text(
        elevated.dumps(), encoding="utf-8"
    )
    with pytest.raises(AuthorizationViolation, match="elevated worker authority"):
        validate_launch(
            SimpleNamespace(authorization_json=elevated.dumps()), "codex-cli", workspace
        )


def test_launch_revalidation_rejects_sidecar_tampering(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    workspace, envelope = prepare_delegated_workspace(
        source,
        task_id="sidecar-test",
        worker="codex-cli",
        isolation_root=tmp_path / "isolated",
    )
    sidecar = Path(envelope.runtime_home).parent / "authorization.json"
    sidecar.write_text(replace(envelope, push=True).dumps(), encoding="utf-8")
    with pytest.raises(AuthorizationViolation, match="do not match"):
        validate_launch(
            SimpleNamespace(authorization_json=envelope.dumps()), "codex-cli", workspace
        )


def test_codex_plan_drops_publication_credentials_and_bypass_flags(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _repository(source)
    workspace, envelope = prepare_delegated_workspace(
        source,
        task_id="task-3",
        worker="codex-cli",
        isolation_root=tmp_path / "isolated",
    )
    argv = ["codex", "exec", "--dangerously-bypass-approvals-and-sandbox"]
    env = {
        "PATH": os.environ.get("PATH", ""),
        "GH_TOKEN": "synthetic-never-log",
        "GITHUB_TOKEN": "synthetic-never-log",
        "JARVIS_CONTROL_API_KEY": "synthetic-never-log",
        "OPENAI_API_KEY": "provider-only-fixture",
        "GIT_ASKPASS": "credential-helper",
    }

    harden_codex_plan(argv, env, envelope)

    assert "--dangerously-bypass-approvals-and-sandbox" not in argv
    assert "--ignore-user-config" in argv and "--ignore-rules" in argv
    rendered = " ".join(argv)
    assert 'sandbox_mode="read-only"' in rendered
    assert 'approval_policy="never"' in rendered
    assert "sandbox_workspace_write.network_access=false" in rendered
    assert "GH_TOKEN" not in env and "GITHUB_TOKEN" not in env and "GIT_ASKPASS" not in env
    assert "JARVIS_CONTROL_API_KEY" not in env
    assert env["OPENAI_API_KEY"] == "provider-only-fixture"
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_CONFIG_KEY_0"] == "credential.helper"
    assert Path(env["HOME"]) == Path(envelope.runtime_home)


def test_authorization_record_survives_store_reopen(tmp_path: Path) -> None:
    db = tmp_path / "chat.db"
    store = AgentChatStore(db)
    session = store.create_session(
        provider="openai-codex",
        model="",
        effort="",
        cwd=str(tmp_path),
        permission_mode="auto",
        authorization_json='{"version":1}',
        started_by="jarvis",
    )
    store.close()

    reopened = AgentChatStore(db)
    persisted = reopened.get_session(session.session_id)
    assert persisted is not None
    assert persisted.authorization_json == '{"version":1}'
    assert persisted.started_by == "jarvis"
    reopened.close()


@pytest.mark.asyncio
async def test_authorized_file_gateway_blocks_traversal_and_has_no_shell(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    tools = authorized_delegation_tools(workspace)

    assert set(tools) == {"Read", "Ls", "Glob", "Grep", "Write", "Edit"}
    assert "RunCommand" not in tools
    result = await tools["Write"].execute(
        {"file_path": str(outside), "content": "escaped"},
        SimpleNamespace(),
    )
    assert not result.success
    assert "outside the authorized workspace" in str(result.error)
    assert outside.read_text(encoding="utf-8") == "outside\n"


@pytest.mark.skipif(os.name != "nt", reason="Windows UNC path rule")
def test_authorized_file_gateway_rejects_unc_before_resolution(tmp_path: Path) -> None:
    from dodaios.agent_chat.tools import resolve_path

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(PermissionError, match="Network and device paths"):
        resolve_path(workspace, r"\\server\share\credential.txt")


@pytest.mark.skipif(os.name != "nt", reason="Windows junction behavior")
@pytest.mark.asyncio
async def test_authorized_file_gateway_blocks_junction_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    junction = workspace / "junction"
    made = subprocess.run(  # noqa: ASYNC221 - one bounded Windows fixture command
        ["cmd.exe", "/d", "/s", "/c", f'mklink /J "{junction}" "{outside}"'],
        capture_output=True,
        text=True,
    )
    if made.returncode:
        pytest.skip("Junction creation is unavailable")
    tools = authorized_delegation_tools(workspace)
    result = await tools["Write"].execute(
        {"file_path": "junction/escaped.txt", "content": "escaped"},
        SimpleNamespace(),
    )
    assert not result.success
    assert not (outside / "escaped.txt").exists()
