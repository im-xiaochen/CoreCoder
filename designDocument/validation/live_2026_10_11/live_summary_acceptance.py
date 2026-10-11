"""Single-request live verification that ContextManager used model output."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path

from corecoder.config import Config
from corecoder.context import ContextManager
from corecoder.llm import LLM

REPORT_DIR = Path(__file__).resolve().parent
SECRET_RE = re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*[^,}\s]+")


def scrub(value):
    return SECRET_RE.sub(lambda m: m.group(1) + "=<redacted>", str(value))


def check(label, condition, detail=""):
    if not condition:
        raise AssertionError(f"{label}: {detail}" if detail else label)


class CapturingLLM:
    """Adapter that preserves the real LLM call while exposing its response."""

    def __init__(self, base):
        self.base = base
        self.responses = []
        self.errors = []
        self.extra = base.extra

    def chat(self, messages, **kwargs):
        try:
            response = self.base.chat(messages, **kwargs)
        except Exception as exc:
            self.errors.append(scrub(exc))
            raise
        self.responses.append(response)
        return response


def main():
    config = Config.from_env()
    check("DeepSeek API key configured", bool(config.api_key))
    check("DeepSeek endpoint configured", "deepseek" in (config.base_url or "").lower())
    scratch = Path(tempfile.mkdtemp(prefix="corecoder-summary-", dir=str(REPORT_DIR)))
    report = {
        "model": config.model,
        "endpoint": (config.base_url or "").split("?", 1)[0],
        "summary_source": "fallback",
        "request_count": 0,
        "usage_known": [],
        "summary": "",
        "errors": [],
        "result": "FAIL",
    }
    try:
        base = LLM(config.model, config.api_key, base_url=config.base_url, temperature=0, max_tokens=1024)
        before = len(base.usage_ledger)
        capture = CapturingLLM(base)
        messages = [
            {"role": "user", "content": "Constraint: preserve LIVE_SUMMARY_CONSTRAINT. Remaining: verify the final output."},
            {"role": "assistant", "tool_calls": [{"id": "live-summary-ok", "function": {"name": "write_file", "arguments": {"file_path": "live-summary.txt", "content": "KEEP"}}}]},
            {"role": "tool", "tool_call_id": "live-summary-ok", "content": "Wrote live-summary.txt", "status": "succeeded"},
            {"role": "assistant", "tool_calls": [{"id": "live-summary-fail", "function": {"name": "edit_file", "arguments": {"file_path": "missing-live-summary.txt"}}}]},
            {"role": "tool", "tool_call_id": "live-summary-fail", "content": "Error: missing-live-summary.txt not found", "status": "failed"},
        ]
        summary = ContextManager(max_tokens=8192)._get_summary(messages, capture)
        report["summary"] = summary
        report["request_count"] = len(base.usage_ledger) - before
        report["usage_known"] = [entry.usage_known for entry in base.usage_ledger[before:]]
        report["errors"] = capture.errors
        response_text = capture.responses[0].content if capture.responses else ""
        # _get_summary returns raw model content only when the response is
        # non-empty; otherwise its deterministic extractor supplies fallback.
        model_source = bool(capture.responses and response_text and summary == response_text)
        report["summary_source"] = "model" if model_source else "fallback"
        check("summary made one real request", report["request_count"] >= 1)
        check("summary response was captured", bool(capture.responses))
        check("summary response was non-empty", bool(response_text.strip()))
        check("summary did not raise", not capture.errors, capture.errors)
        check("summary source is model", model_source, "ContextManager returned fallback text")
        check("summary usage was present", all(report["usage_known"]), report["usage_known"])
        for fact in ("LIVE_SUMMARY_CONSTRAINT", "live-summary.txt", "live-summary-fail", "missing-live-summary.txt"):
            check("model summary preserves fact", fact in summary, f"missing {fact}: {summary}")
        check("model summary preserves failed status", "failed" in summary.lower(), summary)
        remaining = summary.lower().split("remaining", 1)[-1]
        check("model summary preserves pending work", "verify the final output" in remaining)
        check("model summary does not invent completion", not re.search(r"\b(verified|completed|complete|done)\b", remaining), remaining)
        report["result"] = "PASS"
    except Exception as exc:  # report is still written with a scrubbed reason
        report["failure"] = scrub(exc)
        raise
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
        (REPORT_DIR / "live_summary_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
