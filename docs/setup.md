# Setup

This project has two independent pieces:

1. a Zellij WASI plugin (`zj-agent-mob.wasm`), and
2. a process hook that translates agent events into plugin status updates.

`scripts/zj-agent-mob-hook.py` is the recommended entry point for new
integrations. `scripts/zj-agent-mob-hook.sh` remains a supported POSIX shell
entry point with the same behavior. Hooks are configured manually so either
entry point can be used by any coding agent that sends JSON on stdin.

- [Install the plugin and hook](#install-the-plugin-and-hook)
- [Register the plugin with Zellij](#register-the-plugin-with-zellij)
- [Agent hook integration](#agent-hook-integration)
  - [Claude Code](#claude-code)
  - [Codex](#codex)
  - [CodeBuddy](#codebuddy)
- [Configuration](#configuration)
- [The fleet summary in your status bar](#the-fleet-summary-in-your-status-bar)
- [Hook environment](#hook-environment)
- [Build from source](#build-from-source)

## Install the plugin and hook

Download `zj-agent-mob.wasm`, `zj-agent-mob-hook.py`, and
`zj-agent-mob-hook.sh` from the [releases page](https://github.com/mohseenrm/zj-agent-mob/releases).
Copy the plugin and one hook to paths that your Zellij config and agent
settings can share:

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
The Python hook is recommended for new integrations; both hooks expose the same
status, spool, notification, permission, follow-up, and peer-context behavior.

The Python hook requires Python 3.9 or newer; the shell hook requires a POSIX
shell. Both require `zellij` on `PATH` when an agent is running inside Zellij.
The Python hook does not require `jq`.

## Register the plugin with Zellij

Add a keybinding to `~/.config/zellij/config.kdl`:

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

Press <kbd>Ctrl</kbd>+<kbd>s</kbd> then <kbd>c</kbd>. To use one chord from every
mode, put the same `LaunchOrFocusPlugin` action in `shared_except "locked"`.
Validate the configuration with:

```sh
zellij setup --check
```

A layout can open the panel at session start:

```kdl
layout {
    pane size=1 borderless=true { plugin location="zellij:tab-bar"; }
    pane
    floating_panes {
        pane {
            plugin location="file:~/.config/zellij/plugins/zj-agent-mob.wasm"
            width "80%"
            height "50%"
        }
    }
}
```

## Agent hook integration

The hook reads one JSON object from stdin and exits `0` in every failure case.
Set `ZJ_AGENT_TOOL` explicitly; an empty value is ignored so that an unlabelled
integration cannot create misleading rows.

Use this command, with an absolute path if the agent does not expand `$HOME`:

```sh
env ZJ_AGENT_TOOL=<agent-name> \
  python3 "$HOME/.config/zj-agent-mob/zj-agent-mob-hook.py"
# Or: env ZJ_AGENT_TOOL=<agent-name> \
#   "$HOME/.config/zj-agent-mob/zj-agent-mob-hook.sh"
```

Register the selected command for the agent's lifecycle and tool events. The hook recognizes
`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`,
`PostToolUseFailure`, `PermissionRequest`, `Notification`, `Stop`,
`StopFailure`, `PreCompact`, `PostCompact`, `SubagentStart`, `SubagentStop`,
`TaskCreated`, `TaskCompleted`, and `SessionEnd`. Unknown events are ignored.

The following snippets show only the command integration. Keep your existing
agent settings and merge the event entries according to that agent's current
official settings schema. The examples use Python; replace the command with the
shell entry point above when preferred.

### Claude Code

In `~/.claude/settings.json`, add the selected hook command to the events you
want to report. The example uses Python; replace it with the shell command if
preferred:

```json
{
  "hooks": {
    "SessionStart": [{
      "hooks": [{
        "type": "command",
        "command": "env ZJ_AGENT_TOOL=claude python3 $HOME/.config/zj-agent-mob/zj-agent-mob-hook.py",
        "async": true
      }]
    }]
  }
}
```

Repeat the entry for the other lifecycle events. Permission decisions and
follow-ups use synchronous `UserPromptSubmit`, `PermissionRequest`, or `Stop`
hooks where the host supports synchronous hooks; do not make every event
synchronous.

### Codex

In `~/.codex/hooks.json`, use the selected command with
`ZJ_AGENT_TOOL=codex`. The example uses Python; replace it with the shell
command if preferred:

```json
{
  "hooks": {
    "SessionStart": [{
      "hooks": [{
        "type": "command",
        "command": "env ZJ_AGENT_TOOL=codex python3 $HOME/.config/zj-agent-mob/zj-agent-mob-hook.py"
      }]
    }]
  }
}
```

Repeat the entry for `UserPromptSubmit`, `PreToolUse`, `PostToolUse`,
`PermissionRequest`, `Stop`, and `SessionEnd` when those events are available
in the installed Codex version.

### CodeBuddy

CodeBuddy integrations are manual. In `~/.codebuddy/settings.json`, add the
selected hook command in the event-hook section provided by your CodeBuddy
version. The example uses Python; replace it with the shell command if
preferred:

```json
{
  "hooks": {
    "SessionStart": {
      "command": "env ZJ_AGENT_TOOL=codebuddy python3 $HOME/.config/zj-agent-mob/zj-agent-mob-hook.py"
    }
  }
}
```

Keep the command shape and `ZJ_AGENT_TOOL=codebuddy`; adapt only the surrounding
key names to the current CodeBuddy hook schema. Consult each agent's current
official documentation for its settings path, event names, and schema. CodeBuddy
is process-discovered
by the plugin, but it cannot report live status until its hook calls this
entry point.

### Verify an integration

Start a new agent session inside a Zellij pane, then check that the panel shows
an `idle` or `working` row. For a direct smoke test, send a representative
payload from the agent's pane:

```sh
printf '%s\n' '{"event":"SessionStart","session_id":"test","cwd":"'$PWD'"}' \
  | env ZJ_AGENT_TOOL=test python3 ~/.config/zj-agent-mob/zj-agent-mob-hook.py
```

The hook silently does nothing outside Zellij or when required environment
variables are absent. Set `ZJ_AGENT_DEBUG=1` to write a diagnostic line per
event to `~/.cache/zj-agent-mob/hook.log`.

## Configuration

Plugin options go in the same block as `LaunchOrFocusPlugin`:

```kdl
LaunchOrFocusPlugin "file:~/.config/zellij/plugins/zj-agent-mob.wasm" {
    floating true
    move_to_focused_tab true
    popup_on_waiting true
    discover true
    notify "waiting,failed"
    notify_cooldown 60
    notify_sound false
}
```

| Key | Default | Meaning |
|---|---|---|
| `popup_on_waiting` | `true` | Show the panel when an agent needs input |
| `discover` | `true` | Scan process environments for agents that have not fired a hook |
| `notify` | `waiting,failed` | Comma-separated statuses that raise notifications; `""` disables them |
| `notify_cooldown` | `60` | Seconds before one agent may notify again |
| `notify_sound` | `false` | Play a sound with notifications |
| `summary_file` | unset | Publish a prose and `.kv` fleet summary for status bars |

Permission prompts are enabled by default when the integrated agent supports
synchronous permission hooks. Set `ZJ_AGENT_APPROVE=0` in that agent's
environment to disable panel approval. The `A` key appends allow-only rules to
`~/.config/zj-agent-mob/approve.rules`.

## The fleet summary in your status bar

Set `summary_file` in the plugin block:

```kdl
summary_file "/tmp/zj-agent-mob.summary"
```

The plugin atomically writes:

| File | Contents |
|---|---|
| `$summary_file` | `2 waiting · 1 working`, empty when nothing needs attention |
| `$summary_file.kv` | `failed=0 waiting=2 working=1 done=0 found=0 total=3` |

The prose line is also sent as the `zj-agent-mob-summary` Zellij pipe. Neither
file exists until the first publish, so consumers should handle a missing file.

For example, a Starship module can stay hidden when the file is empty:

```toml
[custom.agents]
command = "cat /tmp/zj-agent-mob.summary 2>/dev/null"
when = "test -s /tmp/zj-agent-mob.summary"
format = "[$output]($style) "
style = "bold yellow"
shell = ["sh", "-c"]
```

## Hook environment

Set these variables in the environment inherited by the agent, not in the
plugin block:

| Variable | Default | Meaning |
|---|---|---|
| `ZJ_AGENT_TOOL` | empty | Required tool label, such as `claude`, `codex`, or `codebuddy` |
| `ZJ_AGENT_HEARTBEAT` | `1` | Set `0` to skip per-tool heartbeat events |
| `ZJ_AGENT_APPROVE` | `1` | Set `0` to disable panel permission decisions |
| `ZJ_AGENT_APPROVE_TIMEOUT` | `30` | Seconds a parked permission waits |
| `ZJ_AGENT_APPROVE_RULES` | `~/.config/zj-agent-mob/approve.rules` | Allow-only rules file |
| `ZJ_AGENT_FOLLOWUP` | `1` | Set `0` to disable queued follow-ups |
| `ZJ_AGENT_CONTEXT` | `1` | Set `0` to disable same-directory peer context |
| `ZJ_AGENT_SLOW_TOOL` | `10` | Seconds before a tool duration is shown |
| `ZJ_AGENT_SPOOL` | `1` | Set `0` to disable cross-session records |
| `ZJ_AGENT_SPOOL_DIR` | `$TMPDIR/zj-agent-mob-<uid>/status` | Override the status directory |
| `ZJ_AGENT_FANOUT` | `1` | Set `0` to disable urgent cross-session fan-out |
| `ZJ_AGENT_PLUGIN` | `file:~/.config/zellij/plugins/zj-agent-mob.wasm` | Override plugin path |
| `ZJ_AGENT_DEBUG` | `0` | Set `1` to log hook events |

The Python hook also accepts usage and context fields when present. The current
plugin may ignore optional usage fields; they remain available to future plugin
versions and other consumers.

## Build from source

```sh
rustup target add wasm32-wasip1
cargo build --release --target wasm32-wasip1
cp target/wasm32-wasip1/release/zj-agent-mob.wasm \
  ~/.config/zellij/plugins/zj-agent-mob.wasm
```

Zellij needs the hyphenated **binary** artifact. The underscored library
artifact is not a loadable plugin and causes `could not find exported function`.
Start a new Zellij session after replacing the wasm if an existing session still
shows the old behavior; Zellij keeps loaded plugin instances in memory.
