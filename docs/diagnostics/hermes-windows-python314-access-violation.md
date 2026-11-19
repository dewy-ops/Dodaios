# Hermes Windows Python access violation

## Status

Observed with Hermes 0.21.5 on Windows on 2026-11-19. This is separate from
the session-initialization failure caused by a dependency path longer than
Windows' default `MAX_PATH`: keeping the E2E profile root short resolves that
initialization failure.

The bundled Python 3.14.7 can still terminate with `0xC0000005` during a
later ACP session recovery. Windows Error Reporting identifies `python314.dll`
at offsets `0x1cf859` and `0x1cf86c`; the bundled symbols resolve those offsets
to `_PyTemplate_Build` (`+0x29` and `+0x3c`). It has been seen both while
building Hermes' full CLI parser after a dependency sync and after a warm ACP
process loads a persisted session.

## Evidence retained

The original `session/new` server traceback was captured before restoring the
installed runtime instrumentation. Its failure is:

```text
ModuleNotFoundError: No module named
'openai.types.responses.response_computer_tool_call_output_screenshot_param'
```

On the affected system that module existed under the prepared profile but was
inaccessible through ordinary Windows paths because the full path was 260
characters and `LongPathsEnabled` was disabled. The integration probe keeps
the Windows Hermes root short and leaves failed probe directories in place,
including `prepare.log` and `runtime-stderr.log`, for future upstream reports.

The preserved local raw captures from this investigation are under the system
temporary directory with the `he-` prefix and the original traceback is in
`prometheus-hermes-trace-*`. They are intentionally not source controlled
because they include machine-specific paths and generated runtime state.

## Current containment and upstream reproduction

`HermesRuntime.prepare_command()` uses `UV_NATIVE_TLS=1` on Windows and asks
Hermes to run ACP's self-check rather than constructing the full CLI parser.
The real E2E probe treats a native process exit before an ACP result as an
error. For the known Hermes access violation it starts one fresh process,
loads the same persisted vendor session, and requires the real third response
and native file-tool result before it can pass. No response is synthesized.

For an upstream report, reproduce with Hermes 0.21.5 and its bundled Windows
Python 3.14.7, then attach the retained `prepare.log`, `runtime-stderr.log`,
and Windows Error Reporting entry. Include whether the crash followed a
dependency sync, `session/load`, or both.
