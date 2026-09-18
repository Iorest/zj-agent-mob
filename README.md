# Zellij Agent Mob (zj-agent-mob)

[![CI](https://github.com/mohseenrm/zj-agent-mob/actions/workflows/ci.yml/badge.svg)](https://github.com/mohseenrm/zj-agent-mob/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/mohseenrm/zj-agent-mob?sort=semver)](https://github.com/mohseenrm/zj-agent-mob/releases/latest)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
![Zellij 0.44+](https://img.shields.io/badge/zellij-0.44%2B-green.svg)

**Keep track of every coding agent you have running, from one floating panel.**

Agents spread across panes, tabs, and Zellij sessions. This panel shows their
status, task, project, and the pane where each agent runs. Press <kbd>Enter</kbd>
to jump to an agent, including one in another session.

![Seven agents across three Zellij sessions in one list](demo/tour.gif)

- **Every agent at once**, across sessions, with live status and task text.
- **Jump to any pane** with <kbd>Enter</kbd>, across tabs and sessions.
- **Fuzzy find** with <kbd>/</kbd> by task, worktree, path, session, tool, or status.
- **Desktop notifications** when an agent waits for input or fails.
- **Answer in place** for supported permission prompts and queue a follow-up.
- **No daemon or socket.** Hooks pipe status to the plugin and write a per-user
  status spool for other sessions.

The plugin is agent-agnostic. A hook integration only needs to pass a JSON event
on stdin and set `ZJ_AGENT_TOOL` to a stable tool name (for example `claude`,
`codex`, or `codebuddy`). The recommended entry point is
`scripts/zj-agent-mob-hook.py`; `scripts/zj-agent-mob-hook.sh` is also a
supported, compatible entry point.

**Docs:** [setup](docs/setup.md) · [how it works](docs/how-it-works.md) ·
[development](docs/development.md) · [troubleshooting](docs/troubleshooting.md)

## Requirements

| Requirement | Why |
|---|---|
| Zellij 0.44+ | Plugin API (`LaunchOrFocusPlugin`, pipes, `RunCommandResult`) |
| Python 3.12+ | Recommended Python hook runtime |
| POSIX shell + `jq` | Optional shell hook runtime |
| `zellij` on `PATH` | The hook uses `zellij pipe` when the agent runs in Zellij |
| Rust + `wasm32-wasip1` target | Only to build from source; releases contain the wasm |

## Quick start

### 1. Put the plugin and hook in place

Download `zj-agent-mob.wasm`, `zj-agent-mob-hook.py`, and
`zj-agent-mob-hook.sh` from the [releases page](https://github.com/mohseenrm/zj-agent-mob/releases),
then copy the plugin and one hook to stable paths:

```sh
mkdir -p ~/.config/zellij/plugins ~/.config/zj-agent-mob
cp zj-agent-mob.wasm ~/.config/zellij/plugins/zj-agent-mob.wasm
cp zj-agent-mob-hook.py ~/.config/zj-agent-mob/zj-agent-mob-hook.py
chmod +x ~/.config/zj-agent-mob/zj-agent-mob-hook.py
# Or use the POSIX shell hook instead:
# cp zj-agent-mob-hook.sh ~/.config/zj-agent-mob/zj-agent-mob-hook.sh
# chmod +x ~/.config/zj-agent-mob/zj-agent-mob-hook.sh
```

From a source checkout, use either script under `scripts/` as the hook source.
The Python hook is recommended for new integrations and supports the broader JSON
field set. The shell hook is a lighter POSIX implementation that shares the core
status, pipe, spool, permission, follow-up, and peer-context protocol; it also
requires `jq` on `PATH`.

To build the plugin yourself:

```sh
rustup target add wasm32-wasip1
cargo build --release --target wasm32-wasip1
cp target/wasm32-wasip1/release/zj-agent-mob.wasm \
  ~/.config/zellij/plugins/zj-agent-mob.wasm
```

### 2. Bind a key in Zellij

Add this to `~/.config/zellij/config.kdl`:

```kdl
keybinds {
    session {
        bind "c" {
            LaunchOrFocusPlugin "file:~/.config/zellij/plugins/zj-agent-mob.wasm" {
                floating true
                move_to_focused_tab true
            }
            SwitchToMode "Normal"
        }
    }
}
```

Press <kbd>Ctrl</kbd>+<kbd>s</kbd> then <kbd>c</kbd> to open the panel. Use
`zellij setup --check` to validate the KDL file.

### 3. Wire your agent's events to the hook

The hook reads one JSON object from stdin. Configure the agent's event-hook
facility to run this command:

```sh
env ZJ_AGENT_TOOL=<agent-name> \
  python3 "$HOME/.config/zj-agent-mob/zj-agent-mob-hook.py"
# Or: env ZJ_AGENT_TOOL=<agent-name> \
#   "$HOME/.config/zj-agent-mob/zj-agent-mob-hook.sh"
```

`<agent-name>` must be non-empty and should be consistent for one tool. The
smallest useful integration sends `SessionStart`, `UserPromptSubmit`,
`PreToolUse`, `PostToolUse`, `PermissionRequest`, `Stop`, `StopFailure`, and
`SessionEnd`. See [setup](docs/setup.md#agent-hook-integration) for minimal
snippets for Claude Code, Codex, and CodeBuddy.

Restart agents after changing their hook configuration; hook settings are read
when an agent session starts.

## Panel keys

| Key | Action |
|---|---|
| <kbd>j</kbd> / <kbd>k</kbd>, <kbd>↓</kbd> / <kbd>↑</kbd> | Move selection |
| <kbd>Enter</kbd> | Jump to the selected agent and hide the panel |
| <kbd>1</kbd>–<kbd>9</kbd> | Jump to agent N |
| <kbd>/</kbd> | Fuzzy find |
| <kbd>g</kbd> / <kbd>G</kbd> | Start a counted jump; `gg` goes to the first row and `G` to the last |
| <kbd>s</kbd> | Cycle urgency, project, and session ordering |
| <kbd>x</kbd> | Send SIGINT; press again to close the pane |
| <kbd>a</kbd> / <kbd>r</kbd> | Approve / reject a parked tool or plan permission prompt |
| <kbd>A</kbd> | Approve and add an allow rule |
| <kbd>f</kbd> | Queue a follow-up for the end of the turn |
| <kbd>y</kbd> / <kbd>m</kbd> | Answer a generic `question` notification waiting on pane input |
| <kbd>o</kbd> | Expand or collapse subagents |
| <kbd>d</kbd> / <kbd>D</kbd> | Dismiss one or all `done` badges |
| <kbd>n</kbd> | Open a new agent in a floating pane |
| <kbd>t</kbd> | Open a floating login shell |
| <kbd>q</kbd> / <kbd>Esc</kbd> | Hide the panel |

## Statuses

The hook wire values include `failed`, `waiting`, `idlewait`, `done`, `compact`,
`working`, `idle`, and `ended`. The panel renders `idlewait` as `idle-wait`.
`found` is the panel label for a process-discovered agent before its first hook
event. `unknown` means a row has not received fresh status recently; when its
Zellij session has exited, the panel renders that row as `gone`. `gone` is a
presentation label, not a hook status. See [troubleshooting](docs/troubleshooting.md)
for recovery steps.

## Configuration

Plugin options go in the `LaunchOrFocusPlugin` block. Common options are:

```kdl
LaunchOrFocusPlugin "file:~/.config/zellij/plugins/zj-agent-mob.wasm" {
    floating true
    move_to_focused_tab true
    popup_on_waiting true
    notify "waiting,failed"
    summary_file "/tmp/zj-agent-mob.summary"
}
```

`popup_on_waiting`, `discover`, `notify`, `notify_cooldown`, `notify_sound`, and
`summary_file` are documented in [setup](docs/setup.md#configuration).

## Known limitations

- Hooks only report agents running inside Zellij. Without `ZELLIJ_PANE_ID`, the
  hook exits without writing status.
- Cross-session status uses `$TMPDIR/zj-agent-mob-<uid>/status` when spool is
  enabled. Urgent wire states (`waiting`, `idlewait`, `failed`, and `done`) are
  fanned out immediately; visible live foreign rows are refreshed by a
  five-second poll. Foreign rows reliably identify session and pane; tab
  information is only available for panes in the panel's current session.
- The spool contains task summaries and is created with mode `0700`. Set
  `ZJ_AGENT_SPOOL=0` to opt out of cross-session records.
- Claude has no permission-granted event. With `ZJ_AGENT_HEARTBEAT=0`, a
  `waiting` row may remain until the turn ends.
- Notifications need `terminal-notifier`, `osascript`, or `notify-send` on
  `PATH`; the panel and status transport work without one.
- The hook is fail-open: malformed input, missing tools, or transport errors do
  not fail an agent turn, but they also cannot produce a status update.

## Releases

Prebuilt `zj-agent-mob.wasm` binaries and changelogs are on the
[releases page](https://github.com/mohseenrm/zj-agent-mob/releases). Pushing a
`v*` tag builds the wasm, verifies its Zellij exports, and publishes it.

## License

[Apache-2.0](LICENSE)
