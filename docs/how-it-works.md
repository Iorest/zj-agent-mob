# How it works

- [The hook entry point](#the-hook-entry-point)
- [Status transport](#status-transport)
- [Cross-session status](#cross-session-status)
- [State ownership and stale records](#state-ownership-and-stale-records)
- [Notifications](#notifications)
- [Permission prompts](#permission-prompts)
- [Follow-ups and peer context](#follow-ups-and-peer-context)
- [Task summaries](#task-summaries)
- [Hook cost](#hook-cost)
- [Limits](#limits)

## The hook entry point

`scripts/zj-agent-mob-hook.py` is the recommended integration boundary, and
`scripts/zj-agent-mob-hook.sh` is a supported POSIX shell entry point. Agent
hook systems provide one JSON object on stdin and set `ZJ_AGENT_TOOL` to identify
the caller. The Python hook supports a broader set of field aliases and
usage/context extensions; the shell hook parses core fields and requires `jq`.

```sh
env ZJ_AGENT_TOOL=claude \
  python3 ~/.config/zj-agent-mob/zj-agent-mob-hook.py
# Or: env ZJ_AGENT_TOOL=claude \
#   ~/.config/zj-agent-mob/zj-agent-mob-hook.sh
```

Both hooks construct a bounded key/value payload and exit `0` on malformed
input, missing tools, or transport failures. Fail-open behavior is deliberate:
status monitoring must never block an agent turn. The Python hook ignores an
empty `ZJ_AGENT_TOOL`; the shell hook defaults an unset value to `claude`, though
explicitly setting the tool is recommended. The Python hook additionally accepts
common field aliases and optional usage/context fields.

Only agents inside Zellij are monitored. The hook requires a numeric
`ZELLIJ_PANE_ID`; without that pane context it returns without writing status.
`ZELLIJ_SESSION_NAME` is used for cross-session identity and addressing, but an
empty session name still permits the current-session pipe path. The Python hook
also ignores an explicit nonzero `ZELLIJ`; the shell hook relies on the pane id
check. A pane id is unique only within a session, so row identity is
`(session, pane_id)`.

## Status transport

For each recognized event, the hook sends a payload through the Zellij pipe:

```sh
zellij pipe --name agent-status \
  --plugin file:~/.config/zellij/plugins/zj-agent-mob.wasm \
  --args "pane_id=3,tool=claude,status=working,..."
```

`zellij pipe --plugin` starts the plugin when necessary. There is no daemon or
socket. Values are sanitized because `--args` is comma-separated; both hooks
bound task/detail text, while the Python adapter applies the broader escaping
rules for its supported fields.

The event-to-status mapping is intentionally small:

| Event | Status |
|---|---|
| `SessionStart` | `idle` |
| `UserPromptSubmit`, `PreToolUse`, `PostToolUse` | `working` |
| `PermissionRequest` | `waiting` |
| `Notification` | `waiting` or `idlewait` |
| `Stop` | `done` |
| `StopFailure` | `failed` |
| `PreCompact` | `compact` |
| `PostCompact` | `working` |
| `SessionEnd` | removes the record |

`SubagentStart`, `SubagentStop`, `TaskCreated`, and `TaskCompleted` carry
counter deltas instead of replacing the parent pane's status. Unknown events
are ignored.

Two notification kinds are ignored outright, because neither is a request the
panel can answer: `auth_success` is informational, and `elicitation_dialog` is
MCP input that CodeBuddy collects in its own pane. Writing them as `waiting`
with a `question` block marked an idle agent as blocked on you *and* offered the
<kbd>y</kbd>/<kbd>n</kbd> reply keys on a pane that was not reading stdin.

## Cross-session status

A pipe reaches the plugin in the agent's own session. For panels in other
sessions, the hook writes one atomic record per agent:

```text
$TMPDIR/zj-agent-mob-<uid>/status/<session>.<pane_id>
```

The record is written to a temporary file and renamed, so readers see either
the previous complete record or the new complete record. The directory is
created with mode `0700` because records contain task summaries. Set
`ZJ_AGENT_SPOOL=0` to disable this transport.

The panel reads the spool while scanning process environments. A live foreign
row is polled every five seconds while it is visible. `waiting`, `idlewait`,
`failed`, and `done` are also fanned out directly to open panels so an urgent
state does not wait for the next poll. Set `ZJ_AGENT_FANOUT=0` to use polling
only.

The hooks cache Git repository/worktree identity per agent and cache in-flight
tool start times to add a duration to slow tool calls. Cache and spool writes
are atomic and best-effort; an unwritable temporary directory degrades to
same-session pipe status.

Rows keep the full `(session, pane_id)` identity. The hook record also carries
`cwd`, `repo`, `wt`, and `branch`, so a foreign row can show its session or
repository/worktree identity and launch a shell in its reported directory. Tab
and pane titles come from the current session's `PaneUpdate`; a foreign row has
no cross-session pane manifest and therefore keeps tab unknown rather than
inferring it from the pane number or directory.

## State ownership and stale records

Three sources contribute to a row:

| Source | Owns |
|---|---|
| Same-session pipe | Status for an agent in the panel's session |
| Spool record | Status for an agent in another session |
| Process discovery | Whether an agent row exists |

A spool record never creates a row. This prevents a leftover file from
resurrecting an exited process. Records are accepted only when their filename,
`session`, `pane_id`, and session id agree and the timestamp is fresh. A
recycled pane id therefore cannot inherit another agent's status indefinitely.

`working` and `compact` decay to `unknown` after roughly 60 seconds without a
fresh event. A blocked or finished state can be re-confirmed by an unchanged
record while its process remains alive. `found` is process discovery without a
hook event. When an `unknown` row's session has exited, the panel displays it as
`gone`; `gone` is not a wire status.

## Notifications

The plugin owns notification policy because it has the complete fleet state.
It batches a burst, suppresses notifications while the panel is visible, and
rate-limits each agent by `notify_cooldown`. It probes `terminal-notifier`,
`osascript`, and `notify-send` once. If no notifier exists, notification is
disabled while status tracking continues.

Task summaries and tool arguments are passed as separate process arguments.
macOS notification text is passed through `osascript`'s `argv` rather than
being interpolated into AppleScript.

## Permission prompts

When an agent emits `PermissionRequest`, the hook sends an `agent-ask` pipe
with a verdict-file path. The plugin writes `allow` or `deny` to that path; the
hook polls it until `ZJ_AGENT_APPROVE_TIMEOUT` (30 seconds by default). A
matching `allow <tool> [arg-prefix]` rule in
`~/.config/zj-agent-mob/approve.rules` short-circuits the wait. The ask is also
fanned out to registered foreign panels, while the verdict file and expiry stay
bound to the original `(session, pane_id)`.

A timed-out hook prints no decision and returns control to the agent's own
prompt. This is safer than leaving a turn blocked. The panel only offers
<kbd>a</kbd>/<kbd>r</kbd>/<kbd>A</kbd> while the verdict is still live; <kbd>y</kbd>,
<kbd>n</kbd>, and <kbd>m</kbd> are reserved for a generic `question` notification and
never write into a parked permission or plan prompt. The question reply is typed into
the agent's pane through `zellij --session <name> action write-chars`, the same
command for a row in the panel's session and one in another: both transports go
through `RunCommands`, so there is no local-only path that can be silently
denied. Replies are still pane input, not a generic hook answer protocol.
CodeBuddy `Elicitation`/`ElicitationResult` has no documented hook answer
schema, so those interactions remain in CodeBuddy's native UI/pane.

## Follow-ups and peer context

<kbd>f</kbd> writes a follow-up file for the selected agent. At `Stop`, the hook
consumes the file and asks the agent to continue with that instruction. CodeBuddy
uses `{"continue":false,"reason":"..."}`; Claude/Codex retain their compatible
`decision:block` response. This is an agent continuation, not a synthetic user
message. Set `ZJ_AGENT_FOLLOWUP=0` to disable it.

At `UserPromptSubmit`, the hook can read active sibling records with the same
`cwd` and add a short informational note. It names at most three peers and is
never a veto. Set `ZJ_AGENT_CONTEXT=0` to disable it.

## Task summaries

The hook uses turn-boundary fields and bounded transcript reads when available:

- Claude Code may provide `ai-title` or `last-prompt` records.
- Codex may provide the first `event_msg` user message in its rollout.
- Other tools can provide `task` or `prompt` directly in the event payload.

Tool events leave the task unchanged rather than rereading a large transcript.
A `Stop` event prefers `last_assistant_message` so a completed row describes
what actually happened.

## Hook cost

A recognized event performs at most one current-session status `zellij pipe`
and one atomic spool write. `PermissionRequest` may also send an `agent-ask`
pipe, and urgent statuses may fan out an additional pipe to each registered
foreign panel. `PreToolUse` and `PostToolUse` additionally update the in-flight
timing record. Turn boundaries may read a bounded transcript tail;
`UserPromptSubmit` may scan sibling records; `PermissionRequest` may read the
rules file. Urgent cross-session fan-out runs only for `waiting`, `failed`,
`idlewait`, and `done`.

Each pipe is also bounded in time: both entry points give one `zellij` call half
a second (`ZJ_AGENT_PIPE_TIMEOUT`) and then abandon it, because blocking an agent
on the panel is worse than a stale row. The Python hook bounds it itself; the
shell hook runs the call under `timeout`/`gtimeout`, and where neither exists it
falls back to the unbounded call it always made. Process startup on a loaded
machine can exceed the budget, and then that one heartbeat is lost - the next
event, or the panel's five-second poll of the spool, repairs the row.

`ZJ_AGENT_HEARTBEAT=0` skips per-tool and counter events, reducing hook volume
but making mid-turn status less precise. `ZJ_AGENT_SPOOL=0` removes the spool
write and cross-session visibility. `ZJ_AGENT_FANOUT=0` removes urgent
subprocesses and relies on the five-second poll.

Claude and Codex detach the reporting hooks with `"async": true` in their
settings. CodeBuddy has no such field - its executor only detaches a hook that
prints `{"async": true}` itself, and only when that is the first JSON object to
reach stdout - so the hook writes that line before doing any work whenever
`ZJ_AGENT_TOOL=codebuddy`, for every event whose stdout nobody reads.
`PermissionRequest`, `Stop`, and `UserPromptSubmit` are exempt: detaching one of
those would drop the verdict, the queued follow-up, or the injected context the
event exists to deliver.

## Limits

- The plugin has no direct filesystem or `$HOME` access; all agent integration
  and cross-session persistence are performed by the host command and one of
  the hooks.
- Pipe arguments use a bounded, sanitized key/value format, so task text is
  intentionally truncated and cannot contain arbitrary commas or newlines.
- Hook support depends on the event schema and synchronous-hook behavior of the
  host agent. Unsupported events simply produce no transition.
- The plugin can answer permission decisions that the host agent accepts from a
  synchronous hook. Generic `question` notifications can receive best-effort
  `y`/`n`/`m` pane input; plans and CodeBuddy MCP elicitation still require the
  agent's native pane/UI.
- Zellij keeps loaded plugin instances in memory. Replacing a wasm file takes a
  new plugin instance, normally by starting a new session.
