"""Durable execution boundaries for unattended coding threads.

An instruction in a prompt is not an authorization boundary.  This module
creates a separate workspace for a delegated Codex thread, records the exact
authority granted to it, and revalidates that record before every process
launch (including a resumed turn after an application restart).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final
from uuid import uuid4

from dodaios.core.paths import user_data_dir
from dodaios.core.process_utils import NO_WINDOW_CREATIONFLAGS

AUTHORIZATION_VERSION: Final[int] = 1
_SUPPORTED_RUNNERS: Final[frozenset[str]] = frozenset({"codex-cli"})
_SECRET_ENV_MARKERS: Final[tuple[str, ...]] = (
    "GITHUB",
    "GH_",
    "GITLAB",
    "BITBUCKET",
    "AZURE_",
    "AWS_",
    "GOOGLE_",
    "SLACK_",
    "STRIPE_",
    "NPM_TOKEN",
    "PYPI_TOKEN",
    "DOCKER_",
    "SSH_",
    "JARVIS_",
    "DODAIOS_",
    "MCP_",
    "CONTROL_KEY",
    "SESSION_KEY",
)
_EXACT_ENV_DROPS: Final[frozenset[str]] = frozenset(
    {
        "GIT_ASKPASS",
        "SSH_ASKPASS",
        "GCM_CREDENTIAL_STORE",
        "GCM_HTTP_PROXY",
        "GCM_PROVIDER",
    }
)


class AuthorizationViolation(RuntimeError):
    """A delegated worker attempted to start outside its recorded envelope."""


@dataclass(frozen=True, slots=True)
class AuthorizationEnvelope:
    """Authority fixed to one task, worker and isolated workspace."""

    version: int
    authorization_id: str
    task_id: str
    worker: str
    issued_at_ms: int
    source_root: str
    workspace_root: str
    runtime_home: str
    filesystem: str = "workspace-write"
    network: bool = False
    credentials: tuple[str, ...] = ("provider:codex",)
    commit: bool = False
    push: bool = False
    pull_request: bool = False
    merge: bool = False
    delegated: bool = True

    def dumps(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @classmethod
    def loads(cls, raw: str) -> AuthorizationEnvelope:
        try:
            value = json.loads(raw)
            if isinstance(value.get("credentials"), list):
                value["credentials"] = tuple(value["credentials"])
            envelope = cls(**value)
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AuthorizationViolation("The worker authorization record is invalid.") from exc
        if envelope.version != AUTHORIZATION_VERSION:
            raise AuthorizationViolation("The worker authorization version is not supported.")
        return envelope


def is_delegated(session: Any) -> bool:
    return bool(str(getattr(session, "authorization_json", "") or "").strip())


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, OSError):
        return path.is_symlink()
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _assert_plain_root(path: Path, *, label: str) -> Path:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except OSError as exc:
        raise AuthorizationViolation(f"The permitted {label} is unavailable.") from exc
    if not resolved.is_dir() or _is_reparse_point(path):
        raise AuthorizationViolation(f"The permitted {label} must be a real directory.")
    return resolved


def _assert_no_reparse_descendants(root: Path) -> None:
    """Refuse non-Git copies that would dereference an external link."""

    pending = [root]
    while pending:
        current = pending.pop()
        try:
            entries = list(current.iterdir())
        except OSError as exc:
            raise AuthorizationViolation("The source workspace could not be inspected.") from exc
        for entry in entries:
            if _is_reparse_point(entry):
                raise AuthorizationViolation(
                    f"The source workspace contains a link or junction: {entry.name}"
                )
            if entry.is_dir():
                pending.append(entry)


def _git_root(source: Path) -> Path | None:
    result = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=NO_WINDOW_CREATIONFLAGS,
        check=False,
    )
    if result.returncode:
        return None
    try:
        return Path(result.stdout.strip()).resolve(strict=True)
    except OSError:
        return None


def prepare_delegated_workspace(
    source: str | Path,
    *,
    task_id: str,
    worker: str,
    isolation_root: Path | None = None,
) -> tuple[Path, AuthorizationEnvelope]:
    """Create a detached worktree/copy and its deny-by-default envelope."""

    source_path = _assert_plain_root(Path(source), label="source workspace")
    repository = _git_root(source_path)
    boundary = repository or source_path
    proposed_root = (
        isolation_root or (user_data_dir() / "agent_chat" / "delegated-workspaces")
    ).resolve()
    try:
        proposed_root.relative_to(boundary)
    except ValueError:
        root = proposed_root
    else:
        if isolation_root is not None:
            raise AuthorizationViolation(
                "The delegated isolation root cannot be inside the source workspace."
            )
        source_key = hashlib.sha256(str(source_path).encode()).hexdigest()[:16]
        root = boundary.parent / ".smith-delegated-workspaces" / source_key
    authorization_id = uuid4().hex
    container = root / authorization_id
    workspace = container / "workspace"
    container.mkdir(parents=True, exist_ok=False)
    try:
        if repository is not None:
            relative = source_path.relative_to(repository)
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "worktree",
                    "add",
                    "--detach",
                    str(workspace),
                    "HEAD",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=NO_WINDOW_CREATIONFLAGS,
                check=False,
            )
            if result.returncode:
                raise AuthorizationViolation(
                    "The isolated Git worktree could not be created: "
                    + (result.stderr.strip() or "git worktree add failed")
                )
            permitted = workspace / relative
        else:
            _assert_no_reparse_descendants(source_path)
            shutil.copytree(source_path, workspace, symlinks=False)
            permitted = workspace
        permitted = _assert_plain_root(permitted, label="isolated workspace")
        home = container / "home"
        home.mkdir()
        envelope = AuthorizationEnvelope(
            version=AUTHORIZATION_VERSION,
            authorization_id=authorization_id,
            task_id=task_id,
            worker=worker,
            issued_at_ms=int(time.time() * 1000),
            source_root=str(source_path),
            workspace_root=str(permitted),
            runtime_home=str(home),
        )
        (container / "authorization.json").write_text(
            envelope.dumps() + "\n", encoding="utf-8"
        )
        return permitted, envelope
    except Exception:
        if repository is not None and workspace.exists():
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "worktree",
                    "remove",
                    "--force",
                    str(workspace),
                ],
                capture_output=True,
                creationflags=NO_WINDOW_CREATIONFLAGS,
                check=False,
            )
        shutil.rmtree(container, ignore_errors=True)
        raise


def validate_launch(session: Any, runner: str, cwd: Path) -> AuthorizationEnvelope | None:
    """Revalidate a session envelope immediately before a worker starts."""

    raw = str(getattr(session, "authorization_json", "") or "").strip()
    if not raw:
        return None
    envelope = AuthorizationEnvelope.loads(raw)
    if runner not in _SUPPORTED_RUNNERS or envelope.worker != runner:
        raise AuthorizationViolation(
            "This delegated worker has no enforceable authorization adapter; launch denied."
        )
    permitted = _assert_plain_root(Path(envelope.workspace_root), label="worker workspace")
    actual = _assert_plain_root(cwd, label="worker working directory")
    if actual != permitted:
        raise AuthorizationViolation("The worker working directory is outside its authorization.")
    runtime_home = _assert_plain_root(Path(envelope.runtime_home), label="worker runtime home")
    sidecar = runtime_home.parent / "authorization.json"
    try:
        durable = AuthorizationEnvelope.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, AuthorizationViolation) as exc:
        raise AuthorizationViolation(
            "The durable worker authorization record is missing or invalid."
        ) from exc
    if durable != envelope:
        raise AuthorizationViolation("The worker authorization records do not match.")
    if envelope.filesystem not in {"read-only", "workspace-write"}:
        raise AuthorizationViolation("The worker filesystem grant is not recognized.")
    elevated = (
        envelope.network
        or envelope.commit
        or envelope.push
        or envelope.pull_request
        or envelope.merge
    )
    if elevated:
        raise AuthorizationViolation(
            "Unsupported elevated worker authority was recorded; launch denied."
        )
    return envelope


def harden_codex_plan(
    argv: list[str], env: dict[str, str], envelope: AuthorizationEnvelope
) -> None:
    """Apply the enforceable Codex boundary after normal CLI planning."""

    forbidden = {"--dangerously-bypass-approvals-and-sandbox", "--yolo"}
    argv[:] = [value for value in argv if value not in forbidden]
    for flag in ("--ignore-user-config", "--ignore-rules"):
        if flag not in argv:
            argv.append(flag)
    argv += [
        "-c",
        'sandbox_mode="read-only"',
        "-c",
        'approval_policy="never"',
        "-c",
        "sandbox_workspace_write.network_access=false",
        "-c",
        'web_search="disabled"',
    ]
    if os.name == "nt":
        argv += ["-c", 'windows.sandbox="unelevated"']
    disabled = {argv[index + 1] for index, value in enumerate(argv[:-1]) if value == "--disable"}
    for feature in (
        "shell_tool",
        "apps",
        "browser_use",
        "computer_use",
        "hooks",
        "multi_agent",
        "multi_agent_v2",
        "plugins",
    ):
        if feature not in disabled:
            argv += ["--disable", feature]

    for key in list(env):
        upper = key.upper()
        if upper in _EXACT_ENV_DROPS or any(marker in upper for marker in _SECRET_ENV_MARKERS):
            env.pop(key, None)
    home = envelope.runtime_home
    env.update(
        {
            "HOME": home,
            "USERPROFILE": home,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "never",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "credential.helper",
            "GIT_CONFIG_VALUE_0": "",
            # The trusted stdio file adapter is loaded from the running SMITH
            # installation, not from the untrusted detached worktree.
            "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        }
    )


def codex_stdio_config_args(envelope: AuthorizationEnvelope) -> list[str]:
    """Mount the authorization-specific, credential-free file adapter."""

    authorization_file = Path(envelope.runtime_home).parent / "authorization.json"
    command = sys.executable
    args = [
        str(Path(__file__).with_name("authorized_mcp_server.py")),
        "--authorization-file",
        str(authorization_file),
    ]
    return [
        "-c",
        f"mcp_servers.smith_authorized_files.command={json.dumps(command)}",
        "-c",
        f"mcp_servers.smith_authorized_files.args={json.dumps(args)}",
        "-c",
        'mcp_servers.smith_authorized_files.env={"PYTHONPATH"='
        + json.dumps(str(Path(__file__).resolve().parents[2]))
        + "}",
        "-c",
        "mcp_servers.smith_authorized_files.required=true",
        "-c",
        'mcp_servers.smith_authorized_files.default_tools_approval_mode="approve"',
    ]


def audit_payload(envelope: AuthorizationEnvelope) -> dict[str, Any]:
    """A credential-free event payload suitable for the session timeline."""

    return {
        "authorization_id": envelope.authorization_id,
        "task_id": envelope.task_id,
        "worker": envelope.worker,
        "workspace_root": envelope.workspace_root,
        "source_root": envelope.source_root,
        "filesystem": envelope.filesystem,
        "network": envelope.network,
        "git": {
            "commit": envelope.commit,
            "push": envelope.push,
            "pull_request": envelope.pull_request,
            "merge": envelope.merge,
        },
    }


__all__ = [
    "AuthorizationEnvelope",
    "AuthorizationViolation",
    "audit_payload",
    "codex_stdio_config_args",
    "harden_codex_plan",
    "is_delegated",
    "prepare_delegated_workspace",
    "validate_launch",
]
