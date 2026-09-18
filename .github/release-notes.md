# Release notes

## v0.13.0

This release presents zj-agent-mob as a general coding-agent monitor rather
than a client-specific integration.

### Highlights

- Added the recommended Python hook entry point at
  `scripts/zj-agent-mob-hook.py`; the compatible POSIX shell hook remains at
  `scripts/zj-agent-mob-hook.sh`.
- Normalized common JSON event, session, workspace, tool, model, usage, and
  context fields across coding-agent integrations for both hooks.
- Added `claude`, `codex`, and `codebuddy` tool labels without making the panel
  depend on one agent vendor.
- Preserved cross-session status, atomic per-user spool records, urgency fan-out,
  task summaries, tool timing, notifications, permission prompts, and queued
  follow-ups.
- Documented manual hook configuration and a minimal integration shape for each
  supported agent.

### Configuration

1. Download the release's `zj-agent-mob.wasm`, `zj-agent-mob-hook.py`, and
   `zj-agent-mob-hook.sh` assets.
2. Copy `zj-agent-mob.wasm` to
   `~/.config/zellij/plugins/zj-agent-mob.wasm`.
3. Copy one hook to `~/.config/zj-agent-mob/` and make it executable. Python is
   recommended for new integrations; the shell hook remains supported.
4. Add a `LaunchOrFocusPlugin` keybinding in Zellij.
5. Configure each agent to run one of:

   ```sh
   env ZJ_AGENT_TOOL=<agent-name> \
     python3 "$HOME/.config/zj-agent-mob/zj-agent-mob-hook.py"
   # Or: env ZJ_AGENT_TOOL=<agent-name> \
   #   "$HOME/.config/zj-agent-mob/zj-agent-mob-hook.sh"
   ```

   See [setup](../docs/setup.md#agent-hook-integration) for the smallest
   Claude Code, Codex, and CodeBuddy snippets.

Existing agent sessions must be restarted after changing hook settings. The
hook intentionally fails open: malformed events, missing tools, and pipe errors
never block an agent turn. It only reports agents running in a Zellij pane.
