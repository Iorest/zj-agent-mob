#!/usr/bin/env python3
"""Fail-open hook adapter for coding-agent integrations."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote_to_bytes

MAX_VALUE_LENGTH = 60
MAX_PATH_LENGTH = 512
# `--args` is a `key=value,key=value` list, so a raw path containing a comma
# would split the record. Percent-encoding carries it losslessly instead; the
# plugin decodes this marker and treats an unknown one as a malformed record.
CWD_ENCODING = "uri-v1"
PIPE_TIMEOUT_SECONDS = 0.5
FANOUT_DEADLINE_SECONDS = 0.8
FANOUT_STATUSES = {"waiting", "idlewait", "failed", "done"}
SPOOL_FIELDS = (
    "ts", "pane_id", "session", "tool", "status", "session_id", "cwd",
    "task", "detail", "block", "perm_mode", "model", "agent_type", "agent_id",
    "repo", "wt", "branch", "tool_secs",
)
EVENT_STATUS = {
    "SessionStart": "idle",
    "UserPromptSubmit": "working",
    "PreToolUse": "working",
    "PostToolUse": "working",
    "PostToolUseFailure": "working",
    "PermissionRequest": "waiting",
    "Stop": "done",
    "StopFailure": "failed",
    "PreCompact": "compact",
    "PostCompact": "working",
    "SessionEnd": "ended",
}
# Notification kinds that carry no request for the user, even though they
# arrive on the same event. CodeBuddy sends `auth_success` on every sign-in and
# `elicitation_dialog` when an MCP server wants input it answers in its own UI.
# Treating either as a question wrote `status=waiting, block=question` over a
# row that was idle or working, which is a false "needs you" *and* put the
# footer's `y`/`n` reply keys on a pane that was not reading stdin.
IGNORED_NOTIFICATION_KINDS = {"auth_success", "elicitation_dialog"}
# CodeBuddy has no `async` config field: its hook executor detaches a hook only
# when the hook itself prints `{"async": true}` as the first JSON object on
# stdout (`tryAsyncProbe` in the bundled CLI). Claude and Codex get the same
# effect from `"async": true` in their settings, so without this every
# CodeBuddy tool call blocked the agent for the hook's whole runtime.
ASYNC_ACK_TIMEOUT_MS = 10_000
# Events whose stdout *is* the result the agent waits on: a permission verdict,
# injected context, or a queued follow-up. Detaching any of them drops exactly
# the output the event exists to deliver.
DECISION_EVENTS = frozenset({"PermissionRequest", "Stop", "UserPromptSubmit"})
COUNTER_EVENTS = {
    "SubagentStart": {"subagent_delta": "1"},
    "SubagentStop": {"subagent_delta": "-1"},
    "TaskCreated": {"task_delta": "1"},
    "TaskCompleted": {"task_done_delta": "1"},
}


def sanitize_value(value: object, limit: int = MAX_VALUE_LENGTH) -> str:
    text = str(value or "")
    text = "".join(c if c.isprintable() and c not in ",=\\" else " " for c in text)
    return " ".join(text.split())[:limit]


def sanitize_path(value: object, limit: int = MAX_PATH_LENGTH) -> str:
    """Sanitize a filesystem path or identifier without destroying it.

    `,,` separates --args pairs and `=` separates a key from its value, so both
    are still folded; but a path must not lose its tail. Truncating one turns
    the verdict file into a path the panel cannot write and the pane's cwd into
    a directory that does not exist, so the budget here is bytes, not
    legibility. `=` is kept: it is legal in paths and the plugin splits on the
    first one only.
    """
    text = str(value or "")
    text = "".join(c if c.isprintable() and c != "," else " " for c in text)
    return " ".join(text.split())[:limit]


def encode_cwd(value: object) -> str:
    """Percent-encode a working directory for the comma-delimited wire format."""
    return quote(str(value or ""), safe="")


def decode_cwd(value: str) -> str:
    """Invert `encode_cwd`, rejecting malformed escapes instead of guessing."""
    for index, char in enumerate(value):
        if char == "%" and (
            index + 2 >= len(value)
            or any(c not in "0123456789abcdefABCDEF" for c in value[index + 1 : index + 3])
        ):
            raise ValueError("invalid percent escape")
    return unquote_to_bytes(value).decode("utf-8", errors="strict")


def sanitize_session(value: str) -> str:
    raw = str(value or "").encode("utf-8", "surrogateescape")
    allowed = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
    folded = bytes(byte if byte in allowed else ord("_") for byte in raw)
    result = folded.decode("ascii")
    if folded != raw:
        result = f"{result}-{raw[:8].hex()}"
    return result


def first_value(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def agent_tool() -> str:
    return os.environ.get("ZJ_AGENT_TOOL", "").strip()


def extract_tool_call(data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    raw = data.get("toolCall") or data.get("tool_call")
    if isinstance(raw, dict):
        name = raw.get("name", "")
        arguments = raw.get("args") or raw.get("arguments") or {}
    else:
        name = data.get("tool_name", "")
        arguments = data.get("tool_input", {})
    return str(name or ""), arguments if isinstance(arguments, dict) else {}


def extract_cwd(data: dict[str, Any]) -> str:
    workspace = data.get("workspace")
    if isinstance(workspace, dict):
        return first_value(workspace, "current_dir", "cwd")
    return first_value(data, "cwd", "current_dir")


def extract_model(data: dict[str, Any]) -> str:
    model = data.get("model", "")
    if isinstance(model, dict):
        return first_value(model, "display_name", "name", "id")
    return str(model or "")


def extract_session_id(data: dict[str, Any]) -> str:
    return first_value(data, "conversationId", "conversation_id", "sessionId", "session_id")


def extract_usage(data: dict[str, Any]) -> dict[str, str]:
    sources = tuple(source for source in (data.get("metrics"), data.get("usage"), data) if isinstance(source, dict))

    def first(*keys: str) -> str:
        for source in sources:
            value = first_value(source, *keys)
            if value:
                return value
        return ""

    result: dict[str, str] = {}
    input_tokens = first("promptTokens", "prompt_tokens", "inputTokens", "input_tokens")
    output_tokens = first("completionTokens", "completion_tokens", "outputTokens", "output_tokens")
    total_tokens = first("totalTokens", "total_tokens")
    cost = first("cost", "total_cost")
    if input_tokens:
        result["tokens_in"] = sanitize_value(input_tokens)
    if output_tokens:
        result["tokens_out"] = sanitize_value(output_tokens)
    if total_tokens:
        result["tokens_total"] = sanitize_value(total_tokens)
    elif input_tokens and output_tokens:
        try:
            result["tokens_total"] = str(int(input_tokens) + int(output_tokens))
        except ValueError:
            pass
    if cost:
        result["cost"] = sanitize_value(cost)

    context = data.get("context_window") or data.get("contextWindow")
    if isinstance(context, dict):
        used = first_value(context, "used_percentage", "usedPercentage", "percentage")
        size = first_value(context, "size", "context_size", "used_tokens", "usedTokens")
        limit = first_value(context, "limit", "context_limit", "max_tokens", "maxTokens", "window_size")
        if used:
            result["ctx_pct"] = sanitize_value(used)
        if size:
            result["ctx_size"] = sanitize_value(size)
        if limit:
            result["ctx_limit"] = sanitize_value(limit)
    return result


def zellij_context() -> tuple[str, str] | None:
    # The pane id is what scopes this hook to an agent running inside Zellij:
    # no pane id means there is nothing to report. ZELLIJ itself is only a
    # marker and is "0" inside a session, so it must not gate anything.
    pane_id = os.environ.get("ZELLIJ_PANE_ID", "")
    if not pane_id.isdigit():
        return None
    return pane_id, os.environ.get("ZELLIJ_SESSION_NAME", "")


def spool_dir() -> Path:
    configured = os.environ.get("ZJ_AGENT_SPOOL_DIR")
    if configured:
        return Path(os.path.abspath(os.path.expanduser(configured)))
    return Path(os.environ.get("TMPDIR", "/tmp")) / f"zj-agent-mob-{os.getuid()}" / "status"


def record_path(directory: Path, session: str, pane_id: str) -> Path:
    return directory / f"{sanitize_session(session)}.{pane_id}"


def read_fields(path: Path) -> dict[str, str]:
    try:
        line = path.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError):
        return {}
    result: dict[str, str] = {}
    for item in line.split(","):
        key, separator, value = item.partition("=")
        if separator and key:
            result[key] = value
    if result.get("cwd_encoding") == CWD_ENCODING and "cwd" in result:
        try:
            result["cwd"] = decode_cwd(result["cwd"])
        except (ValueError, UnicodeDecodeError):
            # A record we cannot decode is not evidence about the agent.
            return {}
    return result


def write_atomic(path: Path, content: str) -> None:
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        temporary.write_text(content + "\n", encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        try:
            temporary.unlink()
        except (UnboundLocalError, OSError):
            pass


def serialize(fields: dict[str, str], include_empty: bool = False, paths: frozenset[str] = frozenset()) -> str:
    """Join fields into a `key=value,key=value` --args string.

    `cwd` is percent-encoded with a `cwd_encoding` marker so a path containing a
    comma survives the delimited format intact. Keys named in `paths` carry
    other filesystem paths (`verdict_file`) and are sanitized without the
    display-length clamp; everything else stays short enough for a row label.
    """
    output = []
    for key, value in fields.items():
        if key == "cwd":
            # Lossless on the wire; `paths` cannot express this, so cwd is
            # handled before the clamp rather than by it.
            encoded = encode_cwd(value)
            if include_empty or encoded:
                output.append(f"cwd_encoding={CWD_ENCODING}")
                output.append(f"cwd={encoded}")
            continue
        sanitized = sanitize_path(value) if key in paths else sanitize_value(value)
        if include_empty or sanitized:
            output.append(f"{key}={sanitized}")
    return ",".join(output)


def notification_kind(data: dict[str, Any]) -> str:
    # The exact key depends on the agent build; a wrong guess silently turns
    # every idle prompt into `waiting` and the panel never shows "idle-wait".
    return first_value(data, "notification_type", "notificationType", "type", "kind")


def event_status(event: str, data: dict[str, Any]) -> str | None:
    if event == "Notification":
        kind = notification_kind(data)
        # `None` means "not a transition": the row keeps whatever it already
        # reported instead of being overwritten by an informational ping.
        if kind in IGNORED_NOTIFICATION_KINDS:
            return None
        return "idlewait" if kind in {"idle_prompt", "agent_needs_input"} else "waiting"
    return EVENT_STATUS.get(event)


def async_ack(event: str) -> None:
    """Let CodeBuddy stop waiting on a hook whose result nobody needs.

    Printed before any work, because the probe only reads the first JSON object
    to reach stdout. A decision event must not be detached, so it prints
    nothing here and the agent keeps blocking on it as intended.
    """
    if event in DECISION_EVENTS or agent_tool() != "codebuddy":
        return
    sys.stdout.write(json.dumps({"async": True, "asyncTimeout": ASYNC_ACK_TIMEOUT_MS}, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def tool_argument(arguments: dict[str, Any]) -> str:
    return first_value(arguments, "file_path", "command", "pattern", "path", "url", "description", "notebook_path")


def tool_detail(event: str, data: dict[str, Any], tool_name: str, arguments: dict[str, Any]) -> str:
    if event == "Notification":
        return first_value(data, "message", "detail")
    if event == "StopFailure":
        return first_value(data, "error_message", "error_type", "detail")
    if event == "PreCompact":
        return f"compacting context ({first_value(data, 'trigger') or 'auto'})"
    if event == "PermissionRequest":
        return f"needs approval: {tool_name} {tool_argument(arguments)}".strip()
    if event in {"PreToolUse", "PostToolUse", "PostToolUseFailure"}:
        detail = f"{tool_name} {tool_argument(arguments)}".strip()
        if event == "PostToolUseFailure":
            detail += " (failed)"
        return detail
    return first_value(data, "detail")


def task_from_transcript(data: dict[str, Any], tool: str, session_id: str) -> str:
    if tool == "claude":
        transcript = first_value(data, "transcript_path", "transcriptPath")
        if transcript:
            try:
                lines = Path(transcript).read_text(encoding="utf-8", errors="replace").splitlines()[-300:]
                records = []
                for line in lines:
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(item, dict):
                        records.append(item)
                for kind, field in (("ai-title", "aiTitle"), ("last-prompt", "lastPrompt")):
                    values = [str(item.get(field, "")) for item in records if item.get("type") == kind]
                    if values and values[-1]:
                        return values[-1]
            except OSError:
                pass
    if tool == "codex" and session_id:
        root = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "sessions"
        try:
            rolls = sorted(root.rglob(f"*-{session_id}.jsonl"))
            if rolls:
                for line in rolls[0].read_text(encoding="utf-8", errors="replace").splitlines():
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(record, dict):
                        continue
                    payload = record.get("payload", {})
                    if not isinstance(payload, dict):
                        continue
                    if record.get("type") == "event_msg" and payload.get("type") == "user_message":
                        return str(payload.get("message", ""))
        except OSError:
            pass
    return ""


def status_fields(event: str, data: dict[str, Any], status: str, pane_id: str, session: str) -> dict[str, str]:
    tool_name, arguments = extract_tool_call(data)
    kind = notification_kind(data)
    block = ""
    if event == "PermissionRequest":
        block = "plan" if tool_name in {"ExitPlanMode", "exit_plan_mode"} else "tool"
    elif event == "Notification":
        block = "idle" if kind in {"idle_prompt", "agent_needs_input"} else "tool" if kind == "permission_prompt" else "question"
    task = first_value(data, "task", "prompt")
    if event == "Stop" and not task:
        task = first_value(data, "last_assistant_message")
    session_id = extract_session_id(data)
    if event in {"SessionStart", "UserPromptSubmit", "Stop"} and not task:
        task = task_from_transcript(data, agent_tool(), session_id)
    if event == "Stop" and task:
        task = task.splitlines()[0]
    fields = {
        "pane_id": pane_id,
        "session": sanitize_session(session),
        "tool": agent_tool(),
        "status": status,
        "session_id": session_id,
        "cwd": extract_cwd(data),
        "task": task,
        "detail": tool_detail(event, data, tool_name, arguments),
        "block": block,
        "perm_mode": first_value(data, "permission_mode", "perm_mode"),
        "model": extract_model(data),
        "agent_type": first_value(data, "agent_type"),
        "agent_id": first_value(data, "agent_id"),
    }
    if fields["perm_mode"] == "default":
        fields["perm_mode"] = ""
    fields.update(extract_usage(data))
    return fields


def update_duration(event: str, data: dict[str, Any], directory: Path, session: str, pane_id: str) -> str:
    tool_use_id = first_value(data, "tool_use_id", "toolUseId")
    if not tool_use_id:
        return ""
    path = directory / f"inflight.{sanitize_session(session)}.{pane_id}"
    if event == "PreToolUse":
        write_atomic(path, f"{int(time.time())} {sanitize_value(tool_use_id)}")
        return ""
    if event not in {"PostToolUse", "PostToolUseFailure"}:
        return ""
    try:
        started, saved_id = path.read_text(encoding="utf-8").split(maxsplit=1)
        if saved_id.strip() != sanitize_value(tool_use_id):
            return ""
        elapsed = max(0, int(time.time()) - int(started))
        path.unlink(missing_ok=True)
        return str(elapsed)
    except (OSError, ValueError):
        return ""


def git_identity(cwd: str, directory: Path, session: str, pane_id: str) -> tuple[str, str, str]:
    if not cwd:
        return "", "", ""
    cache = directory / f"git.{sanitize_session(session)}.{pane_id}"
    try:
        cached = cache.read_text(encoding="utf-8").splitlines()[0].split("|", 3)
        if len(cached) == 4 and cached[0] == cwd:
            return tuple(cached[1:])  # type: ignore[return-value]
    except (OSError, IndexError, ValueError):
        pass
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, check=False, timeout=0.3,
        )
        lines = result.stdout.splitlines()
        if result.returncode != 0 or len(lines) < 3:
            return "", "", ""
        top, common, branch = lines[:3]
        main = common[:-5] if common.endswith("/.git") else common
        if main != common and main != top:
            repo, worktree = os.path.basename(main), os.path.basename(top)
        else:
            repo, worktree = os.path.basename(top), ""
        values = tuple(sanitize_value(value if value != "HEAD" else "") for value in (repo, worktree, branch))
        write_atomic(cache, "|".join((cwd, *values)))
        return values  # type: ignore[return-value]
    except (OSError, subprocess.SubprocessError):
        return "", "", ""


def pipe_timeout() -> float:
    """Seconds to give one `zellij pipe` before abandoning it.

    A hook must never block an agent, so a pipe that outlives this budget is
    killed and its update dropped; the next event, or the panel's own poll,
    repairs the row. The default assumes `zellij` answers in milliseconds, which
    is what it does on an idle machine - a loaded one can take hundreds, so the
    budget is raisable rather than fixed.
    """
    try:
        return max(0.01, float(os.environ.get("ZJ_AGENT_PIPE_TIMEOUT", "")))
    except ValueError:
        return PIPE_TIMEOUT_SECONDS


def send_pipe(
    args: str,
    plugin: str,
    session: str = "",
    deadline: float | None = None,
    name: str = "agent-status",
    include_plugin: bool = False,
) -> None:
    """Send one named pipe, without launching the plugin by default.

    `zellij pipe --plugin <url>` means "send to this plugin, launching it if it
    is not running"; a hook that fires on every tool call would therefore pop
    the panel open unasked. Without `--plugin` zellij delivers to every loaded
    plugin listening on the pipe name, which is exactly the panel when the user
    opened one. `include_plugin` exists for the rare explicit case only.
    """
    command = ["zellij"]
    if session:
        command.extend(["--session", session])
    command.extend(["pipe", "--name", name])
    if include_plugin:
        command.extend(["--plugin", plugin])
    command.extend(["--args", args])
    budget = pipe_timeout()
    timeout = budget if deadline is None else min(budget, max(0.01, deadline - time.monotonic()))
    try:
        subprocess.run(command, check=False, capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        pass


def discover_peers(directory: Path, session: str) -> list[str]:
    peers: list[str] = []
    own = sanitize_session(session)
    try:
        entries = list(directory.glob("panel.*"))
    except OSError:
        return peers
    for entry in entries:
        if not entry.is_file() or entry.name == f"panel.{own}":
            continue
        try:
            target = entry.read_text(encoding="utf-8").splitlines()[0].strip()
        except (OSError, IndexError):
            target = ""
        target = target or entry.name.removeprefix("panel.")
        if target != session and target not in peers:
            peers.append(target)
        if len(peers) == 8:
            break
    return peers


def fanout(args: str, directory: Path, session: str, plugin: str) -> None:
    if os.environ.get("ZJ_AGENT_FANOUT") == "0":
        return
    deadline = time.monotonic() + FANOUT_DEADLINE_SECONDS
    for target in discover_peers(directory, session):
        if time.monotonic() >= deadline:
            break
        send_pipe(args, plugin, target, deadline)


def peer_context(directory: Path, session: str, cwd: str) -> str:
    if not cwd or os.environ.get("ZJ_AGENT_CONTEXT") == "0":
        return ""
    peers = []
    own = f"{sanitize_session(session)}."
    for path in directory.glob("*.*"):
        if path.name.startswith(("panel.", "inflight.", "git.")) or path.name.startswith(own):
            continue
        fields = read_fields(path)
        if fields.get("cwd") != cwd or fields.get("status") not in {"working", "waiting", "idlewait", "compact"}:
            continue
        peers.append(f"pane {fields.get('pane_id', '?')} ({fields.get('status')}): {fields.get('task') or 'no summary'}")
    if not peers:
        return ""
    shown = peers[:3]
    more = len(peers) - len(shown)
    suffix = f" (and {more} more)" if more else ""
    newline = chr(10)
    listed = newline.join(shown)
    return (
        f"zj-agent-mob: {len(peers)} other agent(s) are working in this same directory right now:"
        f"{newline}{listed}{newline}"
        f"Coordinate before wide-reaching changes (rebases, file moves, dependency bumps).{suffix}"
    )


def hook_output(event: str, body: dict[str, Any]) -> None:
    if event == "PermissionRequest":
        # Claude, Codex, and CodeBuddy all parse this event as
        # `hookSpecificOutput.decision.behavior`; `permissionDecision` is the
        # (unrelated) PreToolUse schema and is ignored here by every agent.
        decision = body["decision"]
        payload = {"hookEventName": event, "decision": {"behavior": decision}}
        print(json.dumps({"hookSpecificOutput": payload}, separators=(",", ":")))
    elif event == "UserPromptSubmit":
        print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": body["context"]}}, separators=(",", ":")))
    elif event == "Stop":
        if agent_tool() == "codebuddy":
            print(json.dumps({"continue": False, "reason": body["reason"]}, separators=(",", ":")))
        else:
            print(json.dumps({"decision": "block", "reason": body["reason"]}, separators=(",", ":")))


def permission_response(event: str, data: dict[str, Any], pane_id: str, session: str, plugin: str, directory: Path) -> None:
    if event != "PermissionRequest" or os.environ.get("ZJ_AGENT_APPROVE", "1") == "0":
        return
    tool_name, arguments = extract_tool_call(data)
    tool_arg = tool_argument(arguments)
    rules_file = Path(os.environ.get("ZJ_AGENT_APPROVE_RULES", Path.home() / ".config/zj-agent-mob/approve.rules"))
    if tool_name and rules_file.is_file():
        try:
            for line in rules_file.read_text(encoding="utf-8").splitlines():
                rule = line.strip()
                if not rule or rule.startswith("#"):
                    continue
                parts = rule.split(" ", 2)
                if len(parts) >= 2 and parts[0] == "allow" and parts[1] == tool_name and (len(parts) == 2 or tool_arg.startswith(parts[2])):
                    hook_output(event, {"decision": "allow"})
                    return
        except OSError:
            pass
    verdict_dir = Path(os.environ.get("TMPDIR", "/tmp")) / "zj-agent-mob"
    verdict = verdict_dir / f"verdict.{sanitize_session(session)}.{pane_id}"
    try:
        verdict_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        verdict.unlink(missing_ok=True)
    except OSError:
        return
    timeout_raw = os.environ.get("ZJ_AGENT_APPROVE_TIMEOUT", "30")
    try:
        timeout = max(0, int(timeout_raw))
    except ValueError:
        timeout = 30
    args = serialize(
        {"pane_id": pane_id, "session": sanitize_session(session), "verdict_file": str(verdict), "tool_name": tool_name, "tool_arg": tool_arg, "timeout": str(timeout)},
        True,
        # The panel writes this exact path: a clamped one is unwritable and the
        # approval silently times out.
        paths=frozenset({"verdict_file"}),
    )
    send_pipe(args, plugin, name="agent-ask")
    fanout(args, directory, session, plugin)
    for _ in range(timeout):
        try:
            if verdict.stat().st_size:
                answer = verdict.read_text(encoding="utf-8").strip()
                verdict.unlink(missing_ok=True)
                if answer in {"allow", "deny"}:
                    hook_output(event, {"decision": answer})
                return
        except OSError:
            pass
        time.sleep(1)
    verdict.unlink(missing_ok=True)


def process_event(event: str, data: dict[str, Any], pane_id: str, session: str) -> None:
    tool = agent_tool()
    if not tool:
        return
    directory = spool_dir()
    heartbeat_off = os.environ.get("ZJ_AGENT_HEARTBEAT", "1") == "0"
    status = event_status(event, data)
    counters = COUNTER_EVENTS.get(event, {})
    if heartbeat_off and event in {"PreToolUse", "PostToolUse", "PostToolUseFailure", *COUNTER_EVENTS}:
        return
    # Counter events (subagents, tasks) carry no status of their own; they are a
    # delta on a row that already exists. Dropping them here cost the panel its
    # subagent and task counters entirely.
    if status is None and not counters:
        return
    async_ack(event)
    plugin = os.environ.get("ZJ_AGENT_PLUGIN", f"file:{Path.home()}/.config/zellij/plugins/zj-agent-mob.wasm")
    fields = status_fields(event, data, status or "", pane_id, session)
    fields.update(counters)
    followup_text = ""
    if event == "Stop" and os.environ.get("ZJ_AGENT_FOLLOWUP", "1") != "0":
        followup = Path(os.environ.get("TMPDIR", "/tmp")) / "zj-agent-mob" / f"followup.{sanitize_session(session)}.{pane_id}"
        try:
            followup_text = followup.read_text(encoding="utf-8").splitlines()[0].strip()
        except (OSError, IndexError):
            pass
        if followup_text:
            followup.unlink(missing_ok=True)
            fields["status"] = status = "working"
            fields["detail"] = f"followup: {followup_text}"
    duration = update_duration(event, data, directory, session, pane_id) if not heartbeat_off else ""
    fields["tool_secs"] = duration
    fields.setdefault("subagent_delta", "0")
    fields.setdefault("task_delta", "0")
    fields.setdefault("task_done_delta", "0")
    if duration:
        slow = int(os.environ.get("ZJ_AGENT_SLOW_TOOL", "10"))
        if int(duration) >= slow and fields.get("detail"):
            fields["detail"] = f"{fields['detail']} ({duration}s)"
    repo, worktree, branch = git_identity(fields.get("cwd", ""), directory, session, pane_id)
    fields.update({"repo": repo, "wt": worktree, "branch": branch})
    payload = serialize(fields, include_empty=True, paths=frozenset({"repo", "wt", "tool_arg"}))
    if os.environ.get("ZJ_AGENT_DEBUG") == "1":
        try:
            debug = Path.home() / ".cache" / "zj-agent-mob" / "hook.log"
            debug.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with debug.open("a", encoding="utf-8") as stream:
                stream.write(f"{time.strftime('%H:%M:%S')} pane={pane_id} tool={tool} event={event} status={status} task=[{fields.get('task', '')}] detail=[{fields.get('detail', '')}]\n")
        except OSError:
            pass
    if payload:
        send_pipe(payload, plugin)
    if status in FANOUT_STATUSES:
        fanout(payload, directory, session, plugin)

    path = record_path(directory, session, pane_id)
    if status == "ended":
        key = f"{sanitize_session(session)}.{pane_id}"
        path.unlink(missing_ok=True)
        (directory / f"git.{key}").unlink(missing_ok=True)
        # An aborted or never-finished tool call leaves its start stamp behind;
        # nothing else prunes it once the pane is gone.
        (directory / f"inflight.{key}").unlink(missing_ok=True)
        return
    previous = read_fields(path)
    if not status and not previous.get("status"):
        return
    merged = dict(previous)
    for key, value in fields.items():
        if value or key not in merged or key == "status":
            merged[key] = value
    # A delta event must not blank the row: inherit the last known status so the
    # record still parses as an agent instead of an empty row.
    if not status and not merged.get("status"):
        return
    if not status:
        merged["status"] = status = previous["status"]
    merged["ts"] = str(int(time.time()))
    spool = {key: merged.get(key, "") for key in SPOOL_FIELDS}
    spool.update({key: value for key, value in merged.items() if key.startswith(("tokens_", "ctx_", "cost"))})
    if os.environ.get("ZJ_AGENT_SPOOL", "1") != "0" and merged.get("status"):
        write_atomic(path, serialize(spool, include_empty=True, paths=frozenset({"repo", "wt"})))

    if event == "UserPromptSubmit":
        context = peer_context(directory, session, fields.get("cwd", ""))
        if context:
            hook_output(event, {"context": context})
    if event == "PermissionRequest":
        permission_response(event, data, pane_id, session, plugin, directory)
    if followup_text:
        hook_output(event, {"reason": followup_text})


def main() -> int:
    try:
        context = zellij_context()
        if context is None:
            return 0
        data = json.load(sys.stdin)
        if not isinstance(data, dict):
            return 0
        event = first_value(data, "event", "event_name", "hook_event_name")
        if not event:
            return 0
        process_event(event, data, *context)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
