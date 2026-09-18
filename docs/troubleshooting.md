# Troubleshooting

Start with [the panel says no agents](#the-panel-says-no-agents). The hook is
fail-open: an error never blocks an agent turn, so inspect the hook's
environment and log when status is missing.

- [Zellij fails to load the plugin](#zellij-fails-to-load-the-plugin)
- [Changes to the plugin have no effect](#changes-to-the-plugin-have-no-effect)
- [The panel says no agents](#the-panel-says-no-agents)
- [A row is `found`, `unknown`, or `gone`](#a-row-is-found-unknown-or-gone)
- [An agent in another session is stale](#an-agent-in-another-session-is-stale)
- [No desktop notifications](#no-desktop-notifications)
- [The hook produces no status](#the-hook-produces-no-status)
- [`waiting` remains after answering](#waiting-remains-after-answering)
- [Approve / reject does nothing](#approve--reject-does-nothing)
- [The panel is cramped](#the-panel-is-cramped)
- [Task text is visible to another local user](#task-text-is-visible-to-another-local-user)

## Zellij fails to load the plugin

```text
could not find exported function
```

Zellij loads the WASI **binary** artifact. Use the hyphenated file from a
release or build it with:

```sh
cargo build --release --target wasm32-wasip1
cp target/wasm32-wasip1/release/zj-agent-mob.wasm \
  ~/.config/zellij/plugins/zj-agent-mob.wasm
```

The Cargo library target is for native tests and is not a loadable Zellij
plugin. Only the hyphenated WASI binary `zj-agent-mob.wasm` has the required
plugin exports. Also confirm the path in `LaunchOrFocusPlugin` points to the
copied file.

## Changes to the plugin have no effect

Zellij caches compiled plugins on disk and keeps loaded instances in the server
process. Replacing the file does not replace an instance already loaded in the
current session. Start a new session from outside Zellij:

```sh
zellij --session fresh
```

On macOS, an old on-disk cache may be under
`~/Library/Caches/org.Zellij-Contributors.Zellij/`; on Linux it is commonly
`~/.cache/zellij/`. Remove only the plugin's cache entry if you know the cache
layout. Avoid removing the whole cache directory unless losing Zellij's other
cache metadata is acceptable.

Compare the build and copied artifact before investigating cache state:

```sh
cmp -s target/wasm32-wasip1/release/zj-agent-mob.wasm \
  ~/.config/zellij/plugins/zj-agent-mob.wasm && echo "artifacts match"
```

## The panel says no agents

Work through these checks in order:

1. **Is the agent inside Zellij?** The hook requires `ZELLIJ_PANE_ID` and
   `ZELLIJ_SESSION_NAME`; outside a pane it intentionally exits.
2. **Is the hook registered for a new agent session?** Hook configuration is
   read at session start. Restart agents after changing settings.
3. **Is `ZJ_AGENT_TOOL` set?** The Python hook ignores events when it is empty;
   the shell hook defaults an unset value to `claude`, but explicit configuration
   avoids ambiguous labels.
4. **Is the selected hook runtime available?** For Python, run
   `python3 --version`; for the shell hook, run `command -v sh` and
   `command -v jq`. Both require `command -v zellij` in the agent's pane.
5. **Can the hook log an event?** Set `ZJ_AGENT_DEBUG=1`, start a new turn, and
   inspect `~/.cache/zj-agent-mob/hook.log`.
6. **Can the panel be driven directly?** This bypasses the agent integration:

   ```sh
   zellij pipe --name agent-status \
     --plugin file:~/.config/zellij/plugins/zj-agent-mob.wasm \
     --args "pane_id=$ZELLIJ_PANE_ID,tool=manual,status=waiting,task=manual test"
   ```

   If this creates a row, the plugin is working and the agent hook registration
   is the remaining problem.

## A row is `found`, `unknown`, or `gone`

- **`found`** means process discovery saw an agent before its first hook event.
  Start another turn; the next event should populate live status.
- **`unknown`** means the process is still present but no fresh status arrived
  for about 60 seconds. Check the hook log, `ZJ_AGENT_SPOOL`, and the Python
  runtime in that agent's environment.
- **`gone`** means the Zellij session has exited. There is no process left to
  report status; <kbd>Enter</kbd> can attach to the session if it is
  resurrectable, but <kbd>x</kbd> cannot signal it.

## An agent in another session is stale

Cross-session rows use one record per agent:

```text
${TMPDIR:-/tmp}/zj-agent-mob-$(id -u)/status/<session>.<pane_id>
```

Inspect the directory and one record:

```sh
ls -la "${TMPDIR:-/tmp}/zj-agent-mob-$(id -u)/status/"
head -n 1 "${TMPDIR:-/tmp}/zj-agent-mob-$(id -u)/status/"*
```

No record means the hook did not write (check `ZJ_AGENT_SPOOL`, `ZJ_AGENT_TOOL`,
and the Zellij environment). A record older than 60 seconds makes active
`working`/`compact` status decay to `unknown`; urgent statuses remain valid while
the process exists. The panel polls foreign rows every five seconds, while
`waiting`, `idlewait`, `failed`, and `done` are fanned out immediately unless
`ZJ_AGENT_FANOUT=0`.

## No desktop notifications

1. The plugin probes `terminal-notifier`, `osascript`, then `notify-send`.
2. Notifications are suppressed while the panel is visible.
3. The same agent is limited by `notify_cooldown` (60 seconds by default).
4. Check that `notify` contains the status you expect, such as
   `notify "waiting,failed,done"`.
5. Check OS notification permissions for the terminal application.

## The hook produces no status

Run these in the agent's Zellij pane:

```sh
printf 'pane=%s session=%s tool=%s\n' \
  "$ZELLIJ_PANE_ID" "$ZELLIJ_SESSION_NAME" "$ZJ_AGENT_TOOL"
python3 --version
command -v zellij
command -v python3
```

The direct event smoke test is:

```sh
printf '%s\n' '{"event":"SessionStart","session_id":"test","cwd":"'$PWD'"}' \
  | env ZJ_AGENT_TOOL=manual python3 ~/.config/zj-agent-mob/zj-agent-mob-hook.py
# Or:
printf '%s\n' '{"hook_event_name":"SessionStart"}' \
  | env ZJ_AGENT_TOOL=manual ~/.config/zj-agent-mob/zj-agent-mob-hook.sh
```

If the command exits successfully but the panel remains empty, run the hook
with logging enabled and inspect `~/.cache/zj-agent-mob/hook.log`:

```sh
env ZJ_AGENT_DEBUG=1 ZJ_AGENT_TOOL=manual \
  python3 ~/.config/zj-agent-mob/zj-agent-mob-hook.py <<'JSON'
{"event":"SessionStart"}
JSON
```

Malformed JSON, missing `ZJ_AGENT_TOOL`, missing pane identity, and unknown
events are intentionally ignored. The hook does not require `jq`.

## `waiting` remains after answering

Claude does not emit a permission-granted event. The row changes back to
`working` on the next heartbeat event. With `ZJ_AGENT_HEARTBEAT=0`, it may stay
`waiting` until the turn ends; this trades immediacy for fewer hook invocations.

## Approve / reject does nothing

The panel can answer only a parked `PermissionRequest`; `plan`, `question`, and
`idle` rows must be handled in the agent pane. Check:

- the integration registered `PermissionRequest` as a synchronous hook where
  the agent supports synchronous decisions;
- `ZJ_AGENT_APPROVE` is not `0` in the agent's environment;
- the prompt has not exceeded `ZJ_AGENT_APPROVE_TIMEOUT` (30 seconds by
  default); and
- the agent was restarted after changing its hook settings.

A matching `allow <tool> [arg-prefix]` line in
`~/.config/zj-agent-mob/approve.rules` answers immediately. Rules are
allow-only; the panel never writes an automatic deny.

## The panel is cramped

The project column is dropped below 50 columns. The detail line needs about 60
columns and two rows per agent. Resize the floating pane or set a larger
`width`/`height` in the layout. Group headings consume one row each.

## Task text is visible to another local user

The spool contains task summaries and is created as
`$TMPDIR/zj-agent-mob-<uid>/status` with mode `0700`. Check the directory mode:

```sh
chmod 700 "${TMPDIR:-/tmp}/zj-agent-mob-$(id -u)/status"
```

Set `ZJ_AGENT_SPOOL=0` to disable cross-session task records. Status for an
agent's own session still travels through the Zellij pipe.
