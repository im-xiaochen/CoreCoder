"""Context budgeting, lossless tool-output archiving, and evidence summaries."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import math
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_CJK_RE = re.compile(r"[一-鿿㐀-䶿豈-﫿　-〿＀-･]")
_SYMBOL_RE = re.compile(r"[^\w\s]")

SUMMARY_SYSTEM_PROMPT = (
    "Summarize the supplied conversation as a factual, evidence-bound task state. "
    "Preserve user constraints and explicitly list remaining or unverified work. "
    "Tool outcomes and their evidence IDs are authoritative: report only the result "
    "actually shown by a succeeded tool call. Failed, refused, cancelled, or unknown "
    "outcomes remain unresolved. Do not infer verification or completion from a user "
    "request, an intention, or the absence of an error, and never turn a remaining task "
    "into a completed one. Keep paths, decisions, failures, and relevant tool output. "
    "Use concise sections: CONSTRAINTS, CHANGES, FAILURES, REMAINING/UNVERIFIED."
)


def _approx_tokens(text: str) -> int:
    cjk = len(_CJK_RE.findall(text))
    rest = len(text) - cjk
    dense = rest > 0 and len(_SYMBOL_RE.findall(text)) / max(len(text), 1) > 0.25
    return math.ceil(cjk / 1.5) + math.ceil(rest / (2.8 if dense else 3.4))


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def estimate_request_tokens(
    messages: list[dict],
    *,
    system: str = "",
    tools: list[dict] | None = None,
    tokenizer=None,
    safety_margin: int = 64,
    on_tokenizer_error=None,
) -> int:
    def count(value):
        if tokenizer is not None:
            try:
                result = tokenizer(value)
                if isinstance(result, int):
                    return result
                if hasattr(result, "input_ids"):
                    return len(result.input_ids)
                return len(result)
            except (TypeError, ValueError, AttributeError):
                if on_tokenizer_error is not None:
                    on_tokenizer_error()
        return _approx_tokens(value)

    total = 4 + count(system or "") + count(_json(tools or []))
    return total + sum(4 + count(_json(m)) for m in messages) + max(0, int(safety_margin))


def estimate_tokens(messages: list[dict]) -> int:
    return estimate_request_tokens(messages)


class ContextBudgetExceeded(RuntimeError):
    pass


def output_reserve_for(llm: Any, default: int = 0) -> int:
    """Return the completion reservation configured on an LLM.

    Providers use either ``max_tokens`` or ``max_completion_tokens`` and a
    few test/dummy clients expose the value as an attribute.  Keeping this in
    the budgeting module makes the request and summary gates use the same
    value instead of silently reserving an unrelated constant.
    """
    if llm is None:
        return max(0, int(default))
    extra = getattr(llm, "extra", {}) or {}
    value = extra.get("max_completion_tokens", extra.get("max_tokens"))
    if value is None:
        value = getattr(llm, "max_completion_tokens", None)
    if value is None:
        value = getattr(llm, "max_tokens", None)
    try:
        return max(0, int(value if value is not None else default))
    except (TypeError, ValueError):
        return max(0, int(default))


@dataclass
class CompressionResult:
    messages: list[dict]
    changed: bool
    budget_ok: bool
    input_tokens: int
    output_reserve: int
    limit: int
    artifacts: list[str] = field(default_factory=list)
    reason: str | None = None
    estimated: bool = True


@dataclass
class ExecutionRecord:
    call_id: str
    tool_name: str
    target: str = ""
    arguments: str = ""
    status: str = "unknown"
    result: str = ""
    artifact_path: str | None = None
    evidence_id: str = ""


@dataclass
class TaskMemory:
    constraints: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    remaining: list[str] = field(default_factory=list)
    records: list[ExecutionRecord] = field(default_factory=list)


_SENSITIVE_KEY = re.compile(r"(?i)(?:api[_. -]?key|access[_. -]?token|token|password|secret|credential)")


def _redact(text: Any) -> str:
    """Redact sensitive keys recursively, including Python repr mappings."""
    value = text
    try:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError, json.JSONDecodeError):
                try:
                    value = ast.literal_eval(value)
                except (SyntaxError, ValueError):
                    # Also cover log-style JSON fragments with quoted keys.
                    return re.sub(
                        r"(?i)([\"']?)(api[_. -]?key|access[_. -]?token|token|password|secret|credential)\1\s*[:=]\s*([\"']?)([^\s,}\"']+)\3",
                        r"\1\2\1: [REDACTED]",
                        value,
                    )

        def scrub(item):
            if isinstance(item, dict):
                return {str(k): "[REDACTED]" if _SENSITIVE_KEY.search(str(k)) else scrub(v) for k, v in item.items()}
            if isinstance(item, (list, tuple)):
                return [scrub(v) for v in item]
            return item

        return json.dumps(scrub(value), ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(value)


def _preview(text: str, chars: int) -> str:
    if len(text) <= chars:
        return text
    head = max(1, chars // 2)
    tail = max(1, chars - head)
    return text[:head] + "\n...[preview; full output archived]...\n" + text[-tail:]


def outcome_status(text: str = "", explicit: str | None = None) -> str:
    allowed = {"succeeded", "failed", "refused", "cancelled", "unknown"}
    if explicit in allowed:
        return explicit
    value = str(text).strip()
    lowered = value.lower()
    if lowered.startswith(
        (
            "permission denied",
            "blocked by hook",
            "plan mode refusal",
            "plan mode is on, so this call was refused",
            "chat mode is on, so this call was refused",
            "⚠blocked",
            "⚠ blocked",
        )
    ):
        return "refused"
    if lowered.startswith(("[interrupted]", "cancelled", "canceled")):
        return "cancelled"
    if re.match(r"^(?:error|sub-agent error)\b", lowered) or re.search(r"\[exit code:\s*(?:-[1-9]\d*|[1-9]\d*)\]\s*$", lowered):
        return "failed"
    return "unknown" if not value else "succeeded"


class ContextManager:
    def __init__(self, max_tokens: int = 128_000, *, tokenizer=None, safety_margin: int = 64):
        self.max_tokens = max_tokens
        self.tokenizer = tokenizer
        self.safety_margin = max(0, int(safety_margin))
        self.estimated = tokenizer is None
        self._snip_at = int(max_tokens * 0.50)
        self._summarize_at = int(max_tokens * 0.70)
        self._collapse_at = int(max_tokens * 0.90)

    def _estimate(self, messages, *, system="", tools=None):
        tokenizer = self.tokenizer
        failed = False

        def mark_failed():
            nonlocal failed
            failed = True

        result = estimate_request_tokens(
            messages,
            system=system,
            tools=tools,
            tokenizer=tokenizer,
            safety_margin=self.safety_margin,
            on_tokenizer_error=mark_failed,
        )
        if failed:
            self.estimated = True
            return estimate_request_tokens(messages, system=system, tools=tools, tokenizer=None, safety_margin=self.safety_margin)
        if tokenizer is not None:
            self.estimated = False
        return result

    @staticmethod
    def _archive_messages(messages, target_dir):
        body = _json(messages).encode("utf-8")
        digest = hashlib.sha256(body).hexdigest()
        path = target_dir / f"history-{digest[:16]}.json"
        if not path.exists():
            path.write_bytes(body)
        return str(path.absolute()).replace("\\", "/"), digest

    def prepare_request(self, messages, *, system="", tools=None, output_reserve=0, artifact_dir=None, llm=None):
        original = copy.deepcopy(messages)
        candidate = copy.deepcopy(messages)
        limit = self.max_tokens
        artifacts = []
        changed = False
        current = self._estimate(candidate, system=system, tools=tools or [])
        fixed = self._estimate([], system=system, tools=tools or [])
        latest = [m for m in candidate if m.get("role") == "user"]
        if fixed + output_reserve > limit or (
            latest and self._estimate([latest[-1]], system=system, tools=tools or []) + output_reserve > limit
        ):
            return CompressionResult(original, False, False, current, output_reserve, limit, reason="context_budget_exceeded")
        if current + output_reserve <= limit:
            return CompressionResult(candidate, False, True, current, output_reserve, limit, estimated=self.estimated)
        target_dir = Path(artifact_dir or (Path.cwd() / ".corecoder-artifacts"))
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return CompressionResult(original, False, False, current, output_reserve, limit, reason="context_budget_exceeded")
        tools_to_trim = [m for m in candidate if m.get("role") == "tool" and len(m.get("content", "") or "") > 1500]
        for m in tools_to_trim:
            body = m.get("content", "") or ""
            digest = hashlib.sha256(body.encode()).hexdigest()
            safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(m.get("tool_call_id", "unknown")))[:80] or "unknown"
            path = target_dir / f"tool-{safe_id}-{digest[:12]}.txt"
            try:
                path.write_bytes(body.encode("utf-8"))
            except OSError:
                return CompressionResult(
                    original, False, False, current, output_reserve, limit, artifacts, "context_budget_exceeded"
                )
            wire_path = str(path.absolute()).replace("\\", "/")
            m["artifact_path"] = wire_path
            m["sha256"] = digest
            artifacts.append(wire_path)
            changed = True
        if tools_to_trim:
            per = max(80, (limit - output_reserve - fixed) * 3 // len(tools_to_trim))
            for m in tools_to_trim:
                body = m["content"]
                m["content"] = (
                    _preview(body, per)
                    + f"\n[full output: {m['artifact_path']}; sha256={m['sha256']}; char_offset=0; char_limit={len(body)}]"
                )
        # If output previews are insufficient, replace complete old turns with a
        # derived evidence summary. The newest user turn remains canonical.
        if self._estimate(candidate, system=system, tools=tools or []) + output_reserve > limit and len(candidate) > 4:
            split = self._safe_split(candidate, 4)
            if split > 0:
                try:
                    history_path, history_hash = self._archive_messages(original[:split], target_dir)
                except OSError:
                    return CompressionResult(
                        original, changed, False, current, output_reserve, limit, artifacts, "context_budget_exceeded"
                    )
                artifacts.append(history_path)
                summary = f"[canonical history archive: {history_path}; sha256={history_hash}]\n" + self._get_summary(
                    candidate[:split], llm
                )
                summarized = [
                    {"role": "user", "content": "[Context evidence summary]\n" + summary},
                    {"role": "assistant", "content": "Earlier evidence retained."},
                    *candidate[split:],
                ]
                if self._estimate(summarized, system=system, tools=tools or []) < self._estimate(
                    candidate, system=system, tools=tools or []
                ):
                    candidate = summarized
                    changed = True
        while self._estimate(candidate, system=system, tools=tools or []) + output_reserve > limit:
            eligible = [m for m in candidate if m.get("artifact_path") and len(m.get("content", "")) > 160]
            if not eligible:
                break
            changed_round = False
            for m in eligible:
                marker = m["content"].find("\n[full output:")
                shown = m["content"][:marker] if marker >= 0 else m["content"]
                new_content = _preview(shown, max(60, len(shown) // 2)) + (m["content"][marker:] if marker >= 0 else "")
                if new_content != m["content"]:
                    m["content"] = new_content
                    changed_round = True
            if not changed_round:
                break
        for m in candidate:
            m.pop("artifact_path", None)
            m.pop("sha256", None)
        input_tokens = self._estimate(candidate, system=system, tools=tools or [])
        if input_tokens + output_reserve > limit:
            return CompressionResult(
                original, changed, False, current, output_reserve, limit, artifacts, "context_budget_exceeded"
            )
        return CompressionResult(
            candidate, changed, True, input_tokens, output_reserve, limit, artifacts, estimated=self.estimated
        )

    def maybe_compress(self, messages, llm=None):
        # Compatibility entry point: use the lossless request pipeline so old
        # callers retain a bool result without silently dropping raw history.
        if estimate_tokens(messages) <= self._snip_at:
            return False
        result = self.prepare_request(
            messages,
            output_reserve=output_reserve_for(llm, 0),
            artifact_dir=Path.cwd() / ".corecoder-artifacts",
            llm=llm,
        )
        if result.budget_ok and result.changed:
            messages[:] = result.messages
            return True
        return False

    @staticmethod
    def _snip_tool_outputs(messages):
        changed = False
        for m in messages:
            if m.get("role") == "tool" and len(m.get("content", "") or "") > 1500:
                m["content"] = _preview(m["content"], 1500)
                changed = True
        return changed

    @staticmethod
    def _safe_split(messages, keep_recent):
        split = max(0, len(messages) - keep_recent)
        while split > 0 and (messages[split].get("role") == "tool" or messages[split].get("tool_calls")):
            split -= 1
        return split

    def _summarize_old(self, messages, llm, keep_recent=8):
        if len(messages) <= keep_recent:
            return False
        split = self._safe_split(messages, keep_recent)
        summary = self._get_summary(messages[:split], llm)
        messages[:] = [
            {"role": "user", "content": "[Context compressed]\n" + summary},
            {"role": "assistant", "content": "Context restored."},
            *messages[split:],
        ]
        return True

    def _hard_collapse(self, messages, llm):
        split = self._safe_split(messages, 4)
        summary = self._get_summary(messages[:split], llm)
        messages[:] = [
            {"role": "user", "content": "[Hard context reset]\n" + summary},
            {"role": "assistant", "content": "Context restored."},
            *messages[split:],
        ]

    def _get_summary(self, messages, llm):
        if llm:
            outputs = []
            reserve = output_reserve_for(llm, 256)
            chunk_chars = max(1000, min(12000, self.max_tokens * 2))
            # _flatten_entries keeps a tool call and its reply together.  A
            # model failure on one chunk must not discard facts from later
            # chunks; deterministic extraction covers that chunk instead.
            for chunk in self._summary_chunks(messages, chunk_chars):
                request = [
                    {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": chunk},
                ]
                if self._estimate(request[1:], system=request[0]["content"]) + reserve > self.max_tokens:
                    outputs.append(self._extract_key_info(messages, only_text=chunk))
                    continue
                try:
                    resp = llm.chat(messages=request)
                    outputs.append(resp.content or self._extract_key_info(messages, only_text=chunk))
                except Exception:  # noqa: BLE001  # preserve canonical history on provider errors
                    outputs.append(self._extract_key_info(messages, only_text=chunk))
            if outputs:
                return "\n".join(outputs)
        return self._extract_key_info(messages)

    @staticmethod
    def _summary_chunks(messages, chunk_chars):
        entries = ContextManager._flatten_entries(messages)
        chunks, current, size = [], [], 0
        for entry in entries:
            # Do not split a complete tool group.  If one group itself is
            # larger than the request window, _preview preserves its head and
            # tail while retaining the call id and outcome.
            if len(entry) > chunk_chars:
                entry = _preview(entry, chunk_chars)
            if current and size + len(entry) + 1 > chunk_chars:
                chunks.append("\n".join(current))
                current, size = [], 0
            current.append(entry)
            size += len(entry) + 1
        if current or not chunks:
            chunks.append("\n".join(current))
        return chunks

    @staticmethod
    def _flatten(messages):
        return "\n".join(ContextManager._flatten_entries(messages))

    @staticmethod
    def _flatten_entries(messages):
        parts = []
        calls, replies, orphan_replies = ContextManager._call_reply_pairs(messages)
        call_positions = {(message_index, call_index): (call, replies.get((message_index, call_index)))
                          for message_index, call_index, call in calls}
        for message_index, m in enumerate(messages):
            if m.get("content") and m.get("role") != "tool":
                parts.append(f"[{m.get('role', '?')}] {_redact(m['content'])}")
            for call_index, call in enumerate(m.get("tool_calls", []) or []):
                fn = call.get("function", {}) if isinstance(call, dict) else {}
                call_id = call.get("id", "")
                _, reply = call_positions.get((message_index, call_index), (call, None))
                outcome = "" if reply is None else f" reply={_redact(reply.get('content', ''))}"
                status = outcome_status(reply.get("content", "") if reply else "", reply.get("status") if reply else None)
                parts.append(
                    f"[tool_call id={call_id} name={fn.get('name', '')} status={status}] {_preview(_redact(str(fn.get('arguments', ''))), 3000)}{outcome}"
                )
        for reply in orphan_replies:
            parts.append(f"[tool_reply id={reply.get('tool_call_id')} status=unknown] {_redact(reply.get('content', ''))}")
        return parts

    @staticmethod
    def _call_reply_pairs(messages):
        """Pair replies with calls by occurrence, allowing IDs to be reused.

        Providers normally emit a unique ID for each call, but completed
        rounds may legally reuse an ID.  A mapping keyed only by ID would bind
        every historical call to the last reply.  Queues preserve the wire
        order while still handling multiple calls with one ID in a batch.
        """
        pending = defaultdict(deque)
        calls = []
        replies = {}
        orphan_replies = []
        for message_index, message in enumerate(messages):
            for call_index, call in enumerate(message.get("tool_calls", []) or []):
                call_id = call.get("id", "") if isinstance(call, dict) else ""
                position = (message_index, call_index)
                calls.append((message_index, call_index, call))
                pending[call_id].append(position)
            if message.get("role") == "tool":
                call_id = message.get("tool_call_id")
                queue = pending.get(call_id)
                if queue:
                    replies[queue.popleft()] = message
                else:
                    orphan_replies.append(message)
        return calls, replies, orphan_replies

    def _extract_key_info(self, messages, only_text=None):
        parts = []
        records = ContextManager.extract_records(messages)
        memory = ContextManager.build_memory(messages)
        calls, _, _ = ContextManager._call_reply_pairs(messages)
        records_by_position = {
            (message_index, call_index): record
            for (message_index, call_index, _), record in zip(calls, records)
        }
        for message_index, m in enumerate(messages):
            text = m.get("content", "") or ""
            if only_text is not None and text not in only_text:
                text = ""
            if text:
                line = f"{m.get('role', '?')}: {_redact(text)}"
                parts.append(line)
            for call_index, call in enumerate(m.get("tool_calls", []) or []):
                fn = call.get("function", {})
                line = f"tool {call.get('id', '')} {fn.get('name', '')}: {_redact(_preview(str(fn.get('arguments', '')), 3000))}"
                record = records_by_position.get((message_index, call_index))
                line = line + (f" status={record.status}" if record else " status=unknown")
                parts.append(line)
        sections = []
        if memory.constraints:
            sections.append("CONSTRAINTS:\n" + "\n".join(memory.constraints))
        if memory.changes:
            sections.append("CHANGES:\n" + "\n".join(memory.changes))
        if memory.failures:
            sections.append("FAILURES:\n" + "\n".join(memory.failures))
        if memory.remaining:
            sections.append("REMAINING:\n" + "\n".join(memory.remaining))
        result = "\n".join(sections or parts) or "(no extractable context)"
        if _approx_tokens(result) > self.max_tokens // 2:
            compact = []
            for heading, values in (
                ("CONSTRAINTS", memory.constraints),
                ("CHANGES", memory.changes),
                ("FAILURES", memory.failures),
                ("REMAINING", memory.remaining),
            ):
                if values:
                    compact.append(heading + ":\n" + "\n".join(_preview(v, 320) for v in values))
            result = "\n".join(compact) or "\n".join([_preview(value, 320) for value in (parts[:3] + parts[-3:])])
        return result

    @staticmethod
    def extract_records(messages):
        calls, replies, _ = ContextManager._call_reply_pairs(messages)
        records = []
        for message_index, call_index, call in calls:
            fn = call.get("function", {})
            cid = call.get("id", "")
            reply = replies.get((message_index, call_index))
            raw_result = str(reply.get("content", "")) if reply else ""
            status = outcome_status(raw_result, reply.get("status") if reply else None)
            result = _redact(raw_result)
            args = _redact(str(fn.get("arguments", "")))
            target = ""
            try:
                parsed = fn.get("arguments", {})
                if isinstance(parsed, str):
                    parsed = json.loads(parsed)
                if isinstance(parsed, dict):
                    target = str(parsed.get("file_path", ""))
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
            records.append(
                ExecutionRecord(
                    cid, fn.get("name", ""), target, args, status, result, reply.get("artifact_path") if reply else None, cid
                )
            )
        return records

    @staticmethod
    def build_memory(messages):
        records = ContextManager.extract_records(messages)
        memory = TaskMemory(records=records)
        for r in records:
            line = f"{r.tool_name} {r.target} args={r.arguments} result={_redact(r.result)} status={r.status} evidence={r.evidence_id}"
            if r.status in {"failed", "refused", "cancelled"}:
                memory.failures.append(line)
            elif r.status == "unknown":
                memory.remaining.append(line)
            elif r.tool_name not in {"read_file", "glob", "grep"}:
                memory.changes.append(line)
        for m in messages:
            text = m.get("content", "") or ""
            if any(k in text.lower() for k in ("constraint", "约束", "要求", "必须", "preserve")):
                memory.constraints.append(_redact(text))
            if "remaining" in text.lower() or "todo" in text.lower() or "待完成" in text:
                memory.remaining.append(_redact(text))
        return memory
