"""Small, dependency-free validators shared by config and persistence loaders."""

import logging
from typing import Any

log = logging.getLogger(__name__)


def _typename(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def diagnostic(path, field, expected, value):
    return f"{path}: {field}: expected {expected}, got {_typename(value)}"


def validate_hooks(data, path):
    if not isinstance(data, dict):
        log.warning(diagnostic(path, "$", "object", data))
        return [], []
    out = []
    for phase in ("PreToolUse", "PostToolUse"):
        raw = data.get(phase, [])
        if not isinstance(raw, list):
            log.warning(diagnostic(path, phase, "array", raw))
            out.append([])
            continue
        good = []
        for i, item in enumerate(raw):
            field = f"{phase}[{i}]"
            if not isinstance(item, dict):
                log.warning(diagnostic(path, field, "object", item))
                continue
            command = item.get("command")
            if not isinstance(command, str) or not command.strip():
                log.warning(diagnostic(path, field + ".command", "non-empty string", command))
                continue
            matcher = item.get("matcher", "")
            if not isinstance(matcher, str):
                log.warning(diagnostic(path, field + ".matcher", "string", matcher))
                continue
            good.append({"command": command, "matcher": matcher})
        out.append(good)
    return tuple(out)


def validate_mcp_config(data, path):
    if not isinstance(data, dict):
        log.warning(diagnostic(path, "$", "object", data))
        return {}
    servers = data.get("mcpServers", {})
    if not isinstance(servers, dict):
        log.warning(diagnostic(path, "mcpServers", "object", servers))
        return {}
    good = {}
    for name, spec in servers.items():
        field = f"mcpServers.{name}"
        if not isinstance(spec, dict):
            log.warning(diagnostic(path, field, "object", spec))
            continue
        command = spec.get("command")
        if not isinstance(command, str) or not command.strip():
            log.warning(diagnostic(path, field + ".command", "non-empty string", command))
            continue
        args = spec.get("args", [])
        if not isinstance(args, list) or any(not isinstance(x, str) for x in args):
            log.warning(diagnostic(path, field + ".args", "string array", args))
            continue
        env = spec.get("env", {})
        if not isinstance(env, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in env.items()):
            log.warning(diagnostic(path, field + ".env", "string map", env))
            continue
        good[name] = (command, args, env)
    return good


def validate_session(data, path=None):
    def bad(field, expected, value):
        if path:
            log.warning(diagnostic(path, field, expected, value))
        return False

    if not isinstance(data, dict):
        if path:
            log.warning(diagnostic(path, "$", "object", data))
        return False
    version = data.get("schema_version", 1)
    if type(version) is not int or version not in (1, 2):
        return bad("schema_version", "integer 1 or 2", version)
    if version == 2 and (not isinstance(data.get("id"), str) or not data["id"].strip()):
        return bad("id", "non-empty string", data.get("id"))
    model, messages = data.get("model"), data.get("messages")
    if not isinstance(model, str) or not model.strip() or not isinstance(messages, list):
        return bad("model/messages", "non-empty string and array", model if not isinstance(model, str) else messages)
    pending = set()
    for i, m in enumerate(messages):
        if not isinstance(m, dict):
            return bad(f"messages[{i}]", "object", m)
        role = m.get("role")
        if not isinstance(role, str) or role not in {"user", "assistant", "system", "tool"}:
            return bad(f"messages[{i}].role", "user/assistant/system/tool string", role)
        if pending and role != "tool":
            return bad(f"messages[{i}].role", "tool completing pending calls", role)
        if role in {"user", "system", "tool"} and not isinstance(m.get("content"), str):
            return bad(f"messages[{i}].content", "string", m.get("content"))
        if role == "tool":
            tid = m.get("tool_call_id")
            if not isinstance(tid, str) or not tid or tid not in pending:
                return bad(f"messages[{i}].tool_call_id", "matching pending string", tid)
            pending.remove(tid)
        elif role == "assistant":
            calls = m.get("tool_calls")
            if calls is None:
                if not isinstance(m.get("content"), str):
                    return bad(f"messages[{i}].content", "string", m.get("content"))
            else:
                if m.get("content") is not None and not isinstance(m.get("content"), str):
                    return bad(f"messages[{i}].content", "string or null", m.get("content"))
                if not isinstance(calls, list) or not calls:
                    return bad(f"messages[{i}].tool_calls", "non-empty array", calls)
                batch = set()
                for j, call in enumerate(calls):
                    if (
                        not isinstance(call, dict)
                        or ("type" in call and call.get("type") != "function")
                        or not isinstance(call.get("id"), str)
                        or not call["id"].strip()
                        or call["id"] in batch
                    ):
                        return bad(f"messages[{i}].tool_calls[{j}]", "function call with unique non-empty id", call)
                    fn = call.get("function", {})
                    if (
                        not isinstance(fn, dict)
                        or not isinstance(fn.get("name"), str)
                        or not fn["name"].strip()
                        or not isinstance(fn.get("arguments"), str)
                    ):
                        return bad(f"messages[{i}].tool_calls[{j}].function", "name string and arguments string", fn)
                    batch.add(call["id"])
                if pending:
                    return bad(f"messages[{i}].tool_calls", "no pending calls", calls)
                pending = batch
        # tool response must close before another assistant turn; user may continue after closure
    if pending:
        return bad("messages", "tool replies for all pending calls", "pending")
    return True
