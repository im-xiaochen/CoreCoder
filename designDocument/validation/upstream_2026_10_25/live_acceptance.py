"""Live v0.8.0 acceptance using synthetic fixtures and existing model credentials.

Run from the repository root. Credentials stay in memory/environment and are
never written to the report. MCP uses a local test process, not external services.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

from corecoder import LLM, Agent, Config
from corecoder.mcp import MCPClient, MCPError
from corecoder.permissions import Permission
from tests.test_live_smoke import test_agent_reads_a_file_and_answers
from tests.test_mcp import FAKE_SERVER

ROOT = Path(__file__).resolve().parents[3]
REPORT = Path(__file__).resolve().parent


def main():
    # The original checkout owns the user's ignored configuration file.
    load_dotenv(ROOT.parent / "CoreCoder" / ".env", override=False)
    config = Config.from_env()
    assert config.api_key and config.base_url, "A configured real endpoint is required"
    assert Path(sys.modules["corecoder"].__file__).resolve().parent == ROOT / "corecoder"
    os.environ.update(OPENAI_API_KEY=config.api_key, CORECODER_API_KEY=config.api_key,
                      OPENAI_BASE_URL=config.base_url, CORECODER_MODEL=config.model,
                      CORECODER_SMOKE_MODEL=config.model)
    report = {"result": "FAIL", "model": config.model, "checks": {}, "requests": []}
    observed = []
    record = LLM._record_usage

    def capture(self, *args, **kwargs):
        record(self, *args, **kwargs)
        entry = self.usage_ledger[-1]
        observed.append({"request_id": entry.request_id, "attempt_id": entry.attempt_id,
                         "usage_known": entry.usage_known, "prompt_tokens": entry.prompt_tokens,
                         "completion_tokens": entry.completion_tokens,
                         "rate_known": entry.input_price is not None and entry.output_price is not None})

    LLM._record_usage = capture
    client = None
    try:
        with tempfile.TemporaryDirectory(prefix="tmp-live-upstream-", dir=REPORT.parent) as directory:
            scratch = Path(directory)
            os.environ["CORECODER_DATA_DIR"] = str(scratch / "stores")
            test_agent_reads_a_file_and_answers(scratch)
            report["checks"]["upstream_live_file_read_smoke"] = "PASS"

            script = scratch / "server.py"
            script.write_text(FAKE_SERVER, encoding="utf-8")
            client = MCPClient("live", sys.executable, [str(script)])
            echo = next(t for t in client.tools if t.name == "mcp__live__echo")
            llm = LLM(config.model, config.api_key, base_url=config.base_url,
                      temperature=0, timeout=90)
            agent = Agent(llm, tools=[echo], max_rounds=4, permission=Permission(allow_all=True))

            def turn(marker):
                start = len(agent.state.execution_records)
                result = agent.chat_result(
                    f"Call mcp__live__echo with text {marker}, then return its exact text result. "
                    "You must actually use the tool.")
                assert result.status == "completed" and marker in result.text
                records = agent.state.execution_records[start:]
                assert not result.tool_errors
                assert any(record.get("result") == f"echo: {marker}" and record.get("status") == "succeeded"
                           and record.get("tool_name") == "mcp__live__echo" for record in records)

            turn("BEFORE_RECONNECT_20261025")
            previous = llm.usage_ledger
            try:
                client.call_tool("crash", {})
            except MCPError:
                pass
            else:
                raise AssertionError("The synthetic MCP server did not crash")
            assert client.error is not None
            client.reconnect()
            assert client.error is None
            turn("AFTER_RECONNECT_20261025")
            assert llm.usage_ledger[:len(previous)] == previous
            report["checks"]["real_model_mcp_before_and_after_reconnect"] = "PASS"
            report["checks"]["historical_usage_entries_unchanged"] = "PASS"
            client.close()
            client = None

            completed = subprocess.run(
                [sys.executable, "-X", "utf8", "-m", "corecoder", "--json", "--max-rounds", "2",
                 "-p", "Reply with UPSTREAM_JSON_OK. Do not call any tools."],
                cwd=ROOT, text=True, encoding="utf-8", capture_output=True, timeout=180, check=False)
            assert completed.returncode == 0, "Live JSON command returned a failure code"
            payload = json.loads(completed.stdout)
            assert payload["status"] == "completed" and "UPSTREAM_JSON_OK" in payload["text"]
            report["checks"]["real_cli_stdout_single_json_object"] = "PASS"
            report["cli_usage"] = payload["usage"]
            assert observed and all(item["usage_known"] for item in observed)
            report["result"] = "PASS"
    except Exception as exc:  # noqa: BLE001
        # Exception strings from SDKs may contain credentials or response bodies.
        report["failure_type"] = type(exc).__name__
    finally:
        if client is not None:
            client.close()
        LLM._record_usage = record
        report["requests"] = observed
        (REPORT / "live_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
