from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from dodaios.agent_chat.authorization import (
    codex_stdio_config_args,
    prepare_delegated_workspace,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_authorized_stdio_adapter_writes_only_inside_workspace(tmp_path: Path) -> None:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    source = tmp_path / "source"
    source.mkdir()
    workspace, envelope = prepare_delegated_workspace(
        source,
        task_id="stdio-boundary-test",
        worker="codex-cli",
        isolation_root=tmp_path / "isolated",
    )
    server_file = PROJECT_ROOT / "dodaios" / "agent_chat" / "authorized_mcp_server.py"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            str(server_file),
            "--authorization-file",
            str(Path(envelope.runtime_home).parent / "authorization.json"),
        ],
        env=env,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            assert {tool.name for tool in listed.tools} == {
                "Read",
                "Write",
                "Edit",
                "Ls",
                "Glob",
                "Grep",
            }
            allowed = await session.call_tool(
                "Write", {"file_path": "allowed.txt", "content": "ALLOWED"}
            )
            denied = await session.call_tool(
                "Write",
                {"file_path": str(tmp_path / "escaped.txt"), "content": "ESCAPED"},
            )

    assert allowed.isError is False
    assert (workspace / "allowed.txt").read_text(encoding="utf-8") == "ALLOWED"
    assert denied.isError is True
    assert not (tmp_path / "escaped.txt").exists()
    audit = Path(envelope.runtime_home).parent / "audit.jsonl"
    assert audit.read_text(encoding="utf-8").count("\n") == 2


def test_codex_stdio_config_contains_no_control_credential(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _, envelope = prepare_delegated_workspace(
        source,
        task_id="config-test",
        worker="codex-cli",
        isolation_root=tmp_path / "isolated",
    )
    rendered = " ".join(codex_stdio_config_args(envelope))
    assert "authorized_mcp_server.py" in rendered
    assert "CONTROL" not in rendered.upper()
    assert "TOKEN" not in rendered.upper()
