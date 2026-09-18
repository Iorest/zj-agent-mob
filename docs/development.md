# Local development

- [Getting started](#getting-started)
- [Iterating against a live session](#iterating-against-a-live-session)
- [Cutting a release](#cutting-a-release)
- [Why a bin target](#why-a-bin-target)
- [Rendering note](#rendering-note)

## Getting started

```sh
git clone git@github.com:mohseenrm/zj-agent-mob.git
cd zj-agent-mob
rustup target add wasm32-wasip1
cargo test
```

The full check set is one command, running the same steps CI does in the same
order:

```sh
./scripts/check.sh          # everything, ~40s
./scripts/check.sh fast     # skips the wasm build, exports and panel e2e
./scripts/check.sh -l       # list the steps without running them
```

It does not stop at the first failure - a `cargo fmt` diff should not hide a
failing test - and prints which steps failed at the end.

The individual commands, if you want one on its own:

```sh
cargo fmt --all --check
cargo clippy --all-targets -- -D warnings
cargo test --all-targets
cargo build --release --target wasm32-wasip1
shellcheck --shell=sh scripts/zj-agent-mob-hook.sh scripts/check.sh tests/e2e-zellij.sh
python3 -m py_compile scripts/zj-agent-mob-hook.py
```

### Test layers

| Layer | Where | Covers |
|---|---|---|
| Unit | `src/*.rs`, beside the code | The state machine and layout, starting from an already-parsed pipe message |
| End-to-end (hook) | [`tests/hook_e2e.rs`](../tests/hook_e2e.rs) | The hook script: hook-event JSON in, `zellij pipe --args` out |
| End-to-end (panel) | [`tests/e2e-zellij.sh`](../tests/e2e-zellij.sh) | A real WASM plugin loaded in Zellij and fed by a configured hook |

Between them these cover the hook-to-plugin seam and the real panel boundary. The hook tests run without a Zellij server; the panel test uses a real Zellij session.

`tests/hook_e2e.rs` drives the Python hook with a stub `zellij` binary, then feeds it real-shaped event JSON: event-to-status mapping, malformed input, Claude transcript and Codex rollout summaries, CodeBuddy labels, sanitizing, permission verdicts, follow-ups, context injection, shell-injection resistance, fail-open behavior, and urgent fan-out. The shell hook remains a supported equivalent entry point and is checked with ShellCheck.

The hook configuration itself is deliberately outside this repository's runtime. Each agent is configured manually, with `ZJ_AGENT_TOOL` selecting its integration adapter; the hook never edits user settings.

```sh
cargo test --test hook_e2e
./tests/e2e-zellij.sh
```

Ten of `discover.rs`'s tests execute the real scan script through `sh` against a stubbed `ps` and a real staged spool directory, rather than asserting on the script's text. The awk program is the part that can silently return nothing - which is indistinguishable from "no agents running" - so it is worth running rather than pattern-matching.

Two tests run the whole loop rather than one layer: `the_real_hook_and_scan_produce_a_live_foreign_row` drives the real hook script, the real scan script, and the real merge in sequence, and `real_machine_capture_renders_live_cross_session_rows` replays bytes captured from two live Zellij sessions. Between them they cover the seams each single-layer test assumes.

Zellij host calls (`focus_terminal_pane`, `hide_self`, `run_command`, ...) are WASM imports with no native symbol, so they're behind the `host` shim that no-ops off-wasm. That keeps the whole state machine and all layout code unit-testable with a plain `cargo test`.

## Iterating against a live session

```sh
cargo build --release --target wasm32-wasip1
mkdir -p "$HOME/.config/zellij/plugins"
cp target/wasm32-wasip1/release/zj-agent-mob.wasm "$HOME/.config/zellij/plugins/zj-agent-mob.wasm"

# Zellij caches compiled plugins, so force a reload or the old build stays live.
zellij action launch-or-focus-plugin --skip-plugin-cache --floating \
  "file:$HOME/.config/zellij/plugins/zj-agent-mob.wasm"
```

That reloads the plugin but not the hook, which is the right loop for plugin-only
changes. A hook change requires copying the selected hook to its configured path
and restarting the agent, since hook settings are read at session start.

Feed the panel a status without running a real agent:

```sh
zellij pipe --name agent-status \
  --plugin "file:$HOME/.config/zellij/plugins/zj-agent-mob.wasm" \
  --args "pane_id=$ZELLIJ_PANE_ID,tool=claude,status=waiting,task=manual test"
```

## Cutting a release

Releases are published by [`.github/workflows/release.yml`](../.github/workflows/release.yml) when a `v*` tag is pushed. The workflow builds the wasm, asserts it exports the six symbols Zellij needs, checks the tag matches `Cargo.toml`, then creates the GitHub release with generated notes and the wasm attached.

The tag and `Cargo.toml` version must agree or the workflow fails on purpose, so bump the version first:

```sh
# 1. bump `version` in Cargo.toml, then refresh Cargo.lock
cargo build --release --target wasm32-wasip1
git commit -am "chore: release v0.2.0"

# 2. tag and push; the workflow does the rest
git tag -a v0.2.0 -m "v0.2.0"
git push origin main --follow-tags
```

Published releases are listed on the [releases page](https://github.com/mohseenrm/zj-agent-mob/releases).

## Why a bin target

The crate builds a **bin** target (`src/main.rs`), not just a cdylib. Zellij's loader needs the WASI `_start` export, which only a bin provides; a bare cdylib fails at load with `could not find exported function`. `register_plugin!` also generates its own `fn main()`, so it must be invoked in `main.rs`. The lib target (`src/lib.rs`) holds all the logic so `cargo test` can run it natively.

To check a build has the right exports:

```sh
wasm-objdump -x target/wasm32-wasip1/release/zj-agent-mob.wasm | grep -A8 'Export\['
```

You want `_start`, `load`, `update`, `render`, `pipe`, and `plugin_version`. CI asserts all six.

## Rendering note

The panel is built from Zellij's `Text` and ribbon UI components, so colours resolve from your Zellij theme instead of fixed 256-colour codes, and Zellij owns cursor positioning.

`Text::color_range()` indices are **character** offsets, not byte offsets - `Text::color_substring()` converts a byte position with `chars().count()` before delegating to `color_range()`. Byte offsets shift the highlight right by the extra UTF-8 bytes of any earlier multi-byte glyph (`▶`, `↵`, `·`, the braille spinner), which colours part of the following word rather than the intended one. Use `style::chars()` when computing a range, and assert the covered substring in a test - the drift is invisible to text-only assertions.
