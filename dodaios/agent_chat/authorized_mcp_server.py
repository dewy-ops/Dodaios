"""Credential-free stdio file tools for one delegated authorization envelope."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any
from uuid import UUID

from dodaios.agent_chat.authorization import AuthorizationEnvelope, AuthorizationViolation
from dodaios.agent_chat.folder_tools import authorized_delegation_tools
from dodaios.core.protocols import ExecutionContext


def _load(path: Path) -> tuple[AuthorizationEnvelope, Path]:
    try:
        envelope = AuthorizationEnvelope.loads(path.read_text(encoding="utf-8"))
        workspace = Path(envelope.workspace_root).resolve(strict=True)
        expected = (Path(envelope.runtime_home).parent / "authorization.json").resolve(strict=True)
    except (OSError, AuthorizationViolation) as exc:
        raise AuthorizationViolation(
            "The delegated file adapter authorization is invalid."
        ) from exc
    if path.resolve(strict=True) != expected or not workspace.is_dir():
        raise AuthorizationViolation("The delegated file adapter authorization path is invalid.")
    if (
        envelope.worker != "codex-cli"
        or envelope.network
        or envelope.commit
        or envelope.push
        or envelope.pull_request
        or envelope.merge
        or envelope.filesystem not in {"read-only", "workspace-write"}
    ):
        raise AuthorizationViolation("The delegated file adapter refuses elevated authority.")
    return envelope, workspace


def _audit(path: Path, envelope: AuthorizationEnvelope, tool: str, success: bool) -> None:
    record = {
        "at_ms": int(time.time() * 1000),
        "authorization_id": envelope.authorization_id,
        "task_id": envelope.task_id,
        "tool": tool,
        "success": success,
    }
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")


def build_server(authorization_file: Path) -> Any:
    import mcp.types as types
    from mcp.server.lowlevel import Server

    envelope, workspace = _load(authorization_file)
    tools = authorized_delegation_tools(workspace)
    if envelope.filesystem == "read-only":
        tools = {name: tool for name, tool in tools.items() if name not in {"Write", "Edit"}}
    audit_file = authorization_file.parent / "audit.jsonl"
    server: Any = Server("smith-authorized-files")

    @server.list_tools()  # type: ignore[misc, no-untyped-call]
    async def list_tools() -> list[Any]:
        return [
            types.Tool(
                name=tool.name,
                description=tool.description,
                inputSchema=tool.schema,
                annotations=types.ToolAnnotations(readOnlyHint=tool.name not in {"Write", "Edit"}),
            )
            for tool in tools.values()
        ]

    @server.call_tool()  # type: ignore[misc, no-untyped-call]
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> Any:
        tool = tools.get(name)
        if tool is None:
            _audit(audit_file, envelope, name, False)
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="Authorization denied this tool.")],
                isError=True,
            )
        result = await tool.execute(
            dict(arguments or {}),
            ExecutionContext(UUID(hex=envelope.authorization_id), "delegated worker", {}, None),
        )
        _audit(audit_file, envelope, name, result.success)
        text = str(result.output if result.success else result.error or result.output)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)], isError=not result.success
        )

    return server


async def serve(authorization_file: Path) -> None:
    from mcp.server.stdio import stdio_server

    server = build_server(authorization_file)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="smith-authorized-files")
    parser.add_argument("--authorization-file", type=Path, required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        asyncio.run(serve(args.authorization_file))
    except AuthorizationViolation as exc:
        print(f"Authorization violation: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - persist a crash without protocol corruption
        try:
            (args.authorization_file.parent / "server-error.txt").write_text(
                "".join(traceback.format_exception(exc)), encoding="utf-8"
            )
        except OSError:
            pass
        print(f"Authorized file adapter failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
