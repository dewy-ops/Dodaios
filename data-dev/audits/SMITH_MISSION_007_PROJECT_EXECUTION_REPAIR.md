# SMITH Mission 007 — Project Execution Repair

Date: 2026-11-20
Branch: `repair/mission-007-project-execution`

## Root causes and repair

1. Long-horizon project IDs (`prj_*`) and Agentic IDE project IDs were separate namespaces. `delegate` passed the long-horizon ID directly to Agentic IDE, which could not resolve it. Projects now persist an explicit authorized workspace path and Agentic IDE identity. The `project workspace` action accepts only a reachable folder already owned by the active IDE library or durable resume store. Coding delegation translates a bound `prj_*` to that exact folder and refuses an unbound project before execution.
2. `DelegateTool._call` interpreted only `ToolResult.success`. A successful envelope containing `status=needs_clarification` therefore received a fabricated fallback job and `running` state. Clarification/refusal/failure now pass through as non-success states without a job. A coding-agent response without a real worker/job receipt is recorded as blocked, never running. Terminal cancellation/interruption/completion states are preserved.
3. Work Board did not read long-horizon projects. It now uses the existing `project` tool in detailed read mode, showing the real workspace association, all subgoals, blockers, job IDs and proof without inventing progress.
4. Windows process containment counted a console-backed tool's child process as a second requested tool. Descendants are still reaped, but the API now reports accepted root handles.

Changed modules: `dodaios/projects/models.py`, `dodaios/projects/engine.py`, `dodaios/brain/project_tool.py`, `dodaios/brain/delegate_tool.py`, `dodaios/core/work_board.py`, `dodaios/brain/work_board_tool.py`, and `dodaios/agentic_ide/resource_sampler.py`, plus focused regression tests.

## Preserved project and real acceptance

- Stock Trading Framework: `prj_73b9568114`; all six original subgoals preserved.
- Durable binding: `C:\Users\dwayn\PROJECT\SMITH`, Agentic IDE project `c276d6a2d12b404c`.
- Project state after acceptance: active, `0 of 6`. The bounded diagnostic did not satisfy any original subgoal, so progress was intentionally not incremented.
- Real Agentic IDE Codex session: `eeeaae3a2535413c9a23b27c73f9a0db` (`#eeeaae`), turn `473dbe3f8943465dbc96d245067825a0`, `openai-codex` / `gpt-6.2-sol`, exact SMITH cwd.
- Authoritative terminal event: `turn_finished`, `status=done`, duration 172,865 ms.
- Verified artifact: `data-dev/audits/STOCK_TRADING_FRAMEWORK_EXECUTION_DIAGNOSTIC_2026-11-20.md`, 4,363 bytes, SHA-256 `db784ef68b78fc11bc91832c1ea7f281a5e87e949045296736b4fc3eabfa386b`.
- After a full app restart, the session remained present and idle, the artifact remained present, the workspace binding remained `c276d6a2d12b404c`, and the project remained honestly at `0 of 6`.
- Earlier jobs `job_3c3c6ef3e2ef` and `job_27172285fc55` reconciled from fabricated/stale running state to `interrupted` on restart; neither produced project progress.

## Verification

- `.\.venv\Scripts\python.exe -m pytest tests/unit/brain/test_delegate_tool.py tests/unit/brain/test_work_board_tool.py tests/unit/projects -q` — exit 0, 79 passed.
- `.\.venv\Scripts\python.exe -m pytest tests/unit/agentic_ide tests/unit/orchestrator tests/unit/projects -q` — initial exit 1, 3,080 passed, 8 skipped, one reproducible Windows containment count failure. Root cause fixed.
- `.\.venv\Scripts\python.exe -m pytest tests/unit/agentic_ide/test_resource_sampler.py -q` — exit 0, 17 passed after the fix.
- `.\.venv\Scripts\python.exe -m pytest tests/unit/agent_runtimes tests/unit/brain/test_codex_brain.py tests/unit/plugins/brain/test_codex_brain.py -q` — exit 0, 577 passed.
- `.\.venv\Scripts\python.exe -m ruff check ... --ignore S108` over all changed source/tests — exit 0. `S108` is ignored only for two pre-existing `/tmp/web Chip` literals in `test_delegate_tool.py`; changed source passed normally.
- `git diff --check` — exit 0; only the preserved `scripts/config-soll.json` line-ending warning was emitted.
- No live Codex execution or Hermes ACP child remained after the acceptance turn.

An attempted broad `tests/unit -k hermes` collection was not counted as verification: it stopped during unrelated collection because two test modules share `test_http_pool.py` and optional `twilio` is not installed. Hermes/runtime coverage was instead run by explicit path in the 577-test command above.

## Alpaca status and limitations

No Alpaca-specific adapter, account probe, or Alpaca-specific regression test exists in the inspected SMITH checkout. Existing credential storage was not opened and no external broker request was made. Paper environment, authentication, account identity/status and balance therefore remain blocked and unverified. No order was placed, modified or cancelled.

The real acceptance was started from SMITH's Agentic IDE UI and exercised the authenticated Codex CLI. It proves native thread creation, execution events, file production, terminal success, and restart persistence. It does not prove live external brokerage access, and the report makes no such claim.
