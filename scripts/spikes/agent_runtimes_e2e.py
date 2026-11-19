"""Smoke: the real runtime drivers launch real Hermes / OpenClaw turns.

Unlike ``agent_runtimes_probe.py`` this goes through Jarvis' own drivers
(``dodaios.agent_runtimes.hermes`` / ``openclaw``): profile and config
writing, process environment, the OpenClaw Gateway supervisor, and the ACP
turn. The model is ``tests/fakes/fake_openai_server.py``; no paid key.
All runtime state lands in a temporary folder.

    python scripts/spikes/agent_runtimes_e2e.py hermes
    python scripts/spikes/agent_runtimes_e2e.py openclaw
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dodaios.agent_runtimes import base, driver  # noqa: E402
from dodaios.agent_runtimes.acp import AcpTurn, frame_line  # noqa: E402
from dodaios.agent_runtimes.base import RuntimeTurn  # noqa: E402
from dodaios.agent_runtimes.model_map import ModelRoute  # noqa: E402
from dodaios.core.control_key import get_control_key  # noqa: E402
from dodaios.core.process_tree import make_process_tree  # noqa: E402
from dodaios.core.process_utils import NO_WINDOW_CREATIONFLAGS  # noqa: E402
from tests.fakes.fake_openai_server import MODEL_ID, FakeOpenAIServer  # noqa: E402


class _IO:
    def __init__(self, proc: Any) -> None:
        self.proc = proc
        self.events: list[dict[str, Any]] = []
        self.started = time.monotonic()
        #: Seconds from the turn's start to its first answer text.
        self.first_text_s: float | None = None
        self.exit_code: int | None = None

    async def write(self, frame: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(frame_line(frame).encode("utf-8"))
        await self.proc.stdin.drain()

    async def emit(self, event: dict[str, Any]) -> None:
        if self.first_text_s is None and event.get("kind") in ("text_delta", "assistant_text"):
            self.first_text_s = time.monotonic() - self.started
        self.events.append(event)

    async def ask(self, call_id: str, name: str, args: dict[str, Any], summary: str) -> str:
        return "allow"


async def _run(name: str, turn: RuntimeTurn, text: str) -> tuple[AcpTurn, _IO]:
    """One turn the way the CLI runner drives it: on the agent's warm process
    when the driver hands one over (``launch.proc``), else a fresh one."""
    from dodaios.agent_runtimes import gateway

    started = time.monotonic()
    runtime = driver(name)
    launch = await runtime.launch(turn)
    acp = AcpTurn(
        turn_id="e2e",
        cwd=str(launch.cwd),
        prompt_text=text,
        resume=launch.acp_resume,
        mcp_servers=launch.mcp_servers,
        auto_allow=True,
        report_session=launch.vendor_session,
        session_meta=launch.session_meta,
        prompt_meta=launch.prompt_meta,
    )
    if launch.proc is not None:
        acp.initialized = launch.proc.initialized
        acp.open_session = launch.proc.open_session
        launch.proc.turn = acp
    tree = make_process_tree("runtime-e2e")
    proc = launch.proc
    failure = None
    token = turn.route.api_key or ""
    try:
        if gateway.verify(token) is not None:
            # Match the chat runner's ownership of each model-gateway turn.
            failure = gateway.watch_failure(token, effort=turn.effort or "")
        with (turn.workspace.parent / "runtime-stderr.log").open("ab") as stderr:
            if proc is None:
                proc = await asyncio.create_subprocess_exec(
                    *launch.argv,
                    cwd=str(launch.cwd),
                    env=launch.env,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=stderr,
                    limit=16 * 1024 * 1024,
                    creationflags=NO_WINDOW_CREATIONFLAGS,
                    start_new_session=os.name != "nt",
                )
                tree.assign(proc.pid)
            io = _IO(proc)
            io.started = started
            assert proc.stdin is not None and proc.stdout is not None
            proc.stdin.write(acp.opening_frame().encode("utf-8"))
            await proc.stdin.drain()

            async def pump() -> None:
                assert proc.stdout is not None and proc.stdin is not None
                while raw := await proc.stdout.readline():
                    try:
                        obj = json.loads(raw)
                    except ValueError:
                        continue  # upstream diagnostic output is not an ACP frame
                    if isinstance(obj, dict):
                        await acp.on_message(obj, io)
                    if acp.saw_result and not proc.stdin.is_closing():
                        proc.stdin.close()

            async def pump_warm_stderr() -> None:
                if launch.proc is not None:
                    while raw := await launch.proc.stderr.readline():
                        stderr.write(raw)

            await asyncio.wait_for(asyncio.gather(pump(), pump_warm_stderr()), timeout=180)
            io.exit_code = await asyncio.wait_for(proc.wait(), timeout=30)
            if not acp.saw_result:
                acp.status = "error"
                acp.error = f"Runtime exited ({io.exit_code}) before returning an ACP result."
            return acp, io
    finally:
        if failure is not None:
            gateway.unwatch_failure(token, failure)
        tree.close()
        try:
            if proc is not None:
                if proc.returncode is None:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass  # the process tree already reaped it
                await asyncio.wait_for(proc.wait(), timeout=10)
        finally:
            if launch.release is not None:
                launch.release()


def _report(label: str, io: _IO) -> None:
    first = f"{io.first_text_s:.2f} s" if io.first_text_s is not None else "never"
    print(f"{label}: first answer text after {first}")


async def recovered_turn(name: str, turn: RuntimeTurn, text: str) -> tuple[bool, AcpTurn, _IO]:
    """Reopen the persisted session in a new process before sending the probe."""
    runtime = driver(name)
    await runtime.stop()
    started = time.monotonic()
    ready = await runtime.warm(turn)
    print(f"warm-up: {ready} in {time.monotonic() - started:.1f} s")
    answer, io = await _run(name, turn, text)
    if name == "hermes" and os.name == "nt" and io.exit_code == 0xC0000005 and not answer.saw_result:
        # This probe only asks for a reply. After a native process fault, give
        # Hermes one more process in which to load the same session and answer.
        print("Hermes exited with Windows access violation 0xC0000005. Reopening the session once.")
        await runtime.stop()
        ready = await runtime.warm(turn)
        answer, io = await _run(name, turn, text)
    return ready, answer, io


def _key_on_disk(root: Path, key: str) -> bool:
    for path in root.rglob("*") if root.exists() else ():
        if "installs" in path.parts or not path.is_file():
            continue
        try:
            if path.stat().st_size < 5_000_000 and key in path.read_text(
                encoding="utf-8", errors="replace"
            ):
                return True
        except OSError:  # locked by a process the runtime left: reported below
            print("  unreadable:", path.name)
    return False


def keep_user_path() -> None:
    """A spike never edits the machine's PATH: the app's one-time cleanup of
    stale Hermes entries (``path_cleanup``) stays the app's job."""
    from dodaios.agent_runtimes import path_cleanup

    path_cleanup._done = True  # noqa: SLF001 — the spike opts out of an app duty


def user_path_snapshot() -> str:
    """The user's persistent PATH (Windows), to prove a run never changed it."""
    if sys.platform != "win32":
        return ""
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        try:
            return str(winreg.QueryValueEx(key, "Path")[0])
        except FileNotFoundError:
            return ""


async def prepare(name: str) -> float:
    """Run the runtime's setup preparation (Hermes: the shared data root) like
    the setup job does, before any turn; returns how long it took."""
    command = getattr(driver(name), "prepare_command", lambda: None)()
    if not command:
        return 0.0
    argv, env = command
    started = time.monotonic()
    tree = make_process_tree("runtime-e2e-prepare")
    with (base.runtimes_root().parent / "prepare.log").open("ab") as log:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            creationflags=NO_WINDOW_CREATIONFLAGS,
            start_new_session=os.name != "nt",
        )
        try:
            tree.assign(proc.pid)
            code = await asyncio.wait_for(proc.wait(), timeout=1800)
        finally:
            tree.close()
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await asyncio.wait_for(proc.wait(), timeout=10)
    elapsed = time.monotonic() - started
    print(f"prepare: exit {code} in {elapsed:.1f} s")
    if code != 0:
        raise RuntimeError(f"{name} preparation exited {code}. See {log.name}")
    driver(name).mark_prepared(driver(name).detect(refresh=True))
    return elapsed


def probe_directory(name: str, check: str) -> Path:
    # Hermes adds a generation and site-packages below this root. Windows
    # Python cannot import dependencies past MAX_PATH on a default system.
    prefix = "he-" if name == "hermes" and os.name == "nt" else f"jarvis-{name}-{check}-e2e-"
    return Path(tempfile.mkdtemp(prefix=prefix))


def probe_runtimes_root(tmp: Path) -> Path:
    return tmp / ("r" if os.name == "nt" else "agent_runtimes")


def finish_profile(tmp: Path, result: int) -> None:
    if result:
        print("Failed runtime profile kept:", tmp)
        return
    if os.name == "nt":
        # Dependency bytecode and caches may exceed MAX_PATH even when their
        # source files can be imported. Use Win32's extended filesystem path.
        path = str(tmp.absolute())
        path = "\\\\?\\UNC\\" + path[2:] if path.startswith("\\\\") else "\\\\?\\" + path
        shutil.rmtree(path)
    else:
        shutil.rmtree(tmp)


async def main(name: str) -> int:
    tmp = probe_directory(name, "driver")
    previous_root = base.runtimes_root
    base.runtimes_root = lambda: probe_runtimes_root(tmp)
    result = 1
    try:
        result = await _check(name, tmp)
        return result
    finally:
        try:
            await driver(name).stop()
            finish_profile(tmp, result)
        finally:
            base.runtimes_root = previous_root


async def _check(name: str, tmp: Path) -> int:
    workspace = tmp / "workspace"
    workspace.mkdir()
    keep_user_path()  # before detect(): it would run the app's PATH cleanup
    status = driver(name).detect(refresh=True)
    print("detect:", status.to_dict())
    if not status.ready:
        return 2
    path_before = user_path_snapshot()
    await prepare(name)
    with FakeOpenAIServer() as server:
        key = "sk-e2e-" + uuid.uuid4().hex
        # The key travels like the gateway token: process environment only.
        route = ModelRoute(
            "local-openai", MODEL_ID, server.base_url, "chat_completions", key,
            context_window=131_072,  # Hermes refuses models below 64k
        )
        # JARVIS_E2E_MCP=<url>|<agent id>: hand the runtime a running Jarvis'
        # MCP server (a dev instance) and make the fake model call a real tool.
        mcp = os.environ.get("JARVIS_E2E_MCP", "")
        mcp_url, _, mcp_agent = mcp.partition("|")
        agent_id = mcp_agent or "e2e-agent"
        turn = RuntimeTurn(
            agent_id=agent_id,
            agent_name="Probe",
            session_id=f"society:{agent_id}",
            workspace=workspace,
            route=route,
            resume=None,
            auto_approve=True,
            mcp_url=mcp_url or None,
            control_key=get_control_key() if mcp_url else None,
        )
        tool_marker = "HERMES_TOOL_E2E"
        if mcp_url:
            opening = (
                'CALL_TOOL society_wiki_note {"kind": "memory", "target": "user", '
                '"text": "Favourite food: lasagne (runtime e2e)"}'
            )
        elif name == "hermes":
            fixture = workspace / "probe.txt"
            fixture.write_text(tool_marker, encoding="utf-8")
            opening = "CALL_TOOL read_file " + json.dumps({"path": str(fixture)})
        else:
            opening = "first message"
        started = time.monotonic()
        first, io1 = await _run(name, turn, opening)
        print(f"turn 1 took {time.monotonic() - started:.1f} s")
        if server.requests:
            offered = [
                (t.get("function") or {}).get("name", "")
                for t in server.requests[0].get("tools") or []
            ]
            print("  offered jarvis tools:", [n for n in offered if "jarvis" in n][:8])
            print("  offered total:", len(offered), "tool_search" in offered)
        calls = [e["payload"] for e in io1.events if e["kind"] in ("tool_call", "tool_result")]
        for call in calls:
            print("  ", {k: str(v)[-700:] for k, v in call.items() if k in ("name", "output")})
        print("turn 1:", first.status, first.error, repr(first.result_text[:80]))
        print("  vendor session:", first.vendor_session)
        tool_ok = (
            name != "hermes" or bool(mcp_url)
            or any(
                e["kind"] == "tool_result" and tool_marker in str(e["payload"].get("output", ""))
                for e in io1.events
            )
        )
        print("  native file tool verified:", tool_ok if name == "hermes" and not mcp_url else "n/a")
        turn.resume = first.vendor_session
        second, io2 = await _run(name, turn, "second message")
        print("turn 2:", second.status, second.error, repr(second.result_text[:80]))
        history = len(server.requests[-1].get("messages", [])) if server.requests else 0
        print("  model saw", history, "messages on turn 2")
        _report("turn 1 (cold)", io1)
        _report("turn 2 (warm process)", io2)
        # The app restarted (or the agent idled out): the chat is opened, the
        # runtime warms up while it is read, then the message is sent.
        turn.resume = second.vendor_session
        ready, third, io3 = await recovered_turn(name, turn, "third message")
        print("turn 3:", third.status, third.error, repr(third.result_text[:80]))
        _report("turn 3 (after warm-up)", io3)
    # Stopped first: a running Gateway holds some of its files open.
    await driver(name).stop()
    secret_leak = _key_on_disk(base.runtimes_root(), key)
    # Only a fixed word is printed: never anything derived from the key.
    print("  key written to disk:", "YES" if secret_leak else "no")
    path_kept = user_path_snapshot() == path_before
    print("  user PATH unchanged:", "yes" if path_kept else "NO")
    ok = (
        first.status == "done"
        and second.status == "done"
        and third.status == "done"
        and (name != "hermes" or (
            bool(first.vendor_session)
            and first.vendor_session == second.vendor_session == third.vendor_session
            and ready
            and "third message" in third.result_text
        ))
        and tool_ok
        and history > 2
        and not secret_leak
        and path_kept
    )
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "hermes")))
