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
`scripts/zj-agent-mob-hook.sh` is a compatible POSIX shell entry point. Agent
hook systems provide one JSON object on stdin and set `ZJ_AGENT_TOOL` to identify
the caller:

```sh
env ZJ_AGENT_TOOL=claude \
  python3 ~/.config/zj-agent-mob/zj-agent-mob-hook.py
# Or: env ZJ_AGENT_TOOL=claude \
#   ~/.config/zj-agent-mob/zj-agent-mob-hook.sh
```

Both hooks normalize the same common event, session, workspace, and tool-call
fields, construct a bounded key/value payload, and exit `0` on malformed input,
missing tools, or transport failures. Fail-open behavior is deliberate: status
monitoring must never block an agent turn. Events without a non-empty tool label
are ignored. The Python hook additionally accepts optional usage and context
fields.

Only agents inside Zellij are monitored. The hook requires a numeric
`ZELLIJ_PANE_ID` and a `ZELLIJ_SESSION_NAME`; without that pane context it
returns without writing status. (When present, `ZELLIJ` must be `0`.) A pane id
is unique only within a session, so row identity is `(session, pane_id)`.

## Status transport

For each recognized event, the hook sends a payload through the Zellij pipe:

```sh
zellij pipe --name agent-status \
  --plugin file:~/.config/zellij/plugins/zj-agent-mob.wasm \
  --args "pane_id=3,tool=claude,status=working,..."
```

`zellij pipe --plugin` starts the plugin when necessary. There is no daemon or
socket. Values are sanitized because `--args` is comma-separated; task and tool
text is truncated to a bounded length and commas, equals signs, and control
characters are removed.

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

The panel reads the spool while scanning process environments. A foreign row is
polled every five seconds while it is visible. `waiting`, `failed`, and `done`
are also fanned out directly to open panels so an urgent state does not wait for
the next poll. Set `ZJ_AGENT_FANOUT=0` to use polling only.

The hooks cache Git repository/worktree identity per agent and cache in-flight
tool start times to add a duration to slow tool calls. Cache and spool writes
are atomic and best-effort; an unwritable temporary directory degrades to
same-session pipe status.

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
hook event; `gone` is a session that has exited.

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
`~/.config/zj-agent-mob/approve.rules` short-circuits the wait.

A timed-out hook prints no decision and returns control to the agent's own
prompt. This is safer than leaving a turn blocked. The panel only offers
<kbd>a</kbd>/<kbd>r</kbd> while the verdict is still live. `plan`, `question`,
and `idle` notifications are shown but cannot be answered as yes/no decisions
from the panel.

## Follow-ups and peer context

<kbd>f</kbd> writes a follow-up file for the selected agent. At `Stop`, the hook
consumes the file and returns a blocking follow-up decision, allowing the next
instruction to continue the turn. Set `ZJ_AGENT_FOLLOWUP=0` to disable it.

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

Every recognized event performs at most one `zellij pipe` and one atomic spool
write. `PreToolUse` and `PostToolUse` additionally update the in-flight timing
record. Turn boundaries may read a bounded transcript tail; `UserPromptSubmit`
may scan sibling records; `PermissionRequest` may read the rules file. Urgent
cross-session fan-out runs only for `waiting`, `failed`, `idlewait`, and `done`.

`ZJ_AGENT_HEARTBEAT=0` skips per-tool and counter events, reducing hook volume
but making mid-turn status less precise. `ZJ_AGENT_SPOOL=0` removes the spool
write and cross-session visibility. `ZJ_AGENT_FANOUT=0` removes urgent
subprocesses and relies on the five-second poll.

## Limits

- The plugin has no direct filesystem or `$HOME` access; all agent integration
  and cross-session persistence are performed by the host command and one of
  the hooks.
- Pipe arguments use a bounded, sanitized key/value format, so task text is
  intentionally truncated and cannot contain arbitrary commas or newlines.
- Hook support depends on the event schema and synchronous-hook behavior of the
  host agent. Unsupported events simply produce no transition.
- The plugin can answer only permission decisions that the host agent accepts
  from a synchronous hook. Questions and plans still require the agent pane.
- Zellij keeps loaded plugin instances in memory. Replacing a wasm file takes a
  new plugin instance, normally by starting a new session.
