# Upstream v0.8.0 acceptance — 2026-10-25

Fork baseline: `4b8edf2993c8fe268a523abb4996bf981cb0fbce`.
Upstream: `bd64cfe7ad2789fa0ab1b3a689ad160567b5cdb0` (13 new commits).
Integrated product code: `ed83328f2b01c50c480360ebc86bcd8d753935eb`.
Branch: `sync/upstream-v0.8.0-20261025`; PR: https://github.com/im-xiaochen/CoreCoder/pull/2.

## Results

- Full offline suite: **477 passed, 2 skipped**, in 56.21 seconds. This includes
  the original tests, Web backend tests, R01–R11 acceptance and 15 new integration
  regressions. Skips: opt-in live smoke and a Windows symlink privilege check.
- Focused MCP/ledger compatibility: **31 passed**. Final documentation checks:
  **2 passed**. Ruff and compileall passed; compilation excludes temporary test
  directories. Runtime: Windows, Python 3.12.
- Real `deepseek-flash`: **PASS**. The upstream smoke actually read its generated
  file. The real model called a local MCP echo tool before a forced crash and
  after reconnect, using the same registered tool wrapper. Execution evidence
  contains succeeded outcomes; historical usage entries remain unchanged.
  A real CLI invocation returned exit code 0 and exactly one parseable JSON object.
- `live_report.json`: final successful run, **7 requests** (6 in-process plus 1
  CLI request), all with provider usage. Rates remain unknown rather than being
  inferred from another model or endpoint. `known_cost=0` is only the known
  subtotal, not the total bill.
- `live_trial_1_report.json`: **4 earlier requests**. File-read smoke passed;
  the first MCP round completed, then an incorrect harness assertion looked for
  status on API tool messages. Status is stored in `state.execution_records`.
  The harness was corrected to verify this authoritative evidence, the tool
  name/result and an empty tool-error list. No product change was needed.
- Total real-model usage for both runs: **11 requests**, 13,830 input tokens and
  432 output tokens. Secrets and raw SDK exception messages are not recorded.
- GitHub Actions returned zero runs/checks for the pushed product commit at
  verification time. Local results above do not imply remote CI passed.

## Compatibility decisions

The upstream additions preserve the fork's atomic session validation, immutable
request/attempt usage ledger and paginated MCP discovery with one startup deadline.
Price files are read when a new request context is created, never to reprice
historical usage. Model-only rates apply to the default endpoint; custom endpoint
rates require an exact `endpoints[endpoint][model]` entry. Explicit constructor
rates still support provider/endpoint/model keys. Malformed or nonfinite file
rates are ignored without discarding other valid entries.

Reconnect uses a new process, isolates old readers and pending calls, and does
not replay calls. Discovery failure leaves the published tool table unchanged.
Changed tool names/descriptions/schemas require restarting the session to avoid
executing against definitions different from those registered with the agent.

The GitHub automatic review's filesystem example finding was addressed: initial
startup failures are skipped, so users must warm the package cache separately and
restart. Manual reconnect applies only to clients previously registered in the
current session.

## Reproduction

```powershell
python -X utf8 -m pytest -c designDocument/pytest.ini tests web_backend/tests designDocument/tests -q --basetemp=designDocument/validation/tmp-upstream-reproduce
python -X utf8 -m ruff check corecoder tests web_backend examples designDocument/tests designDocument/validation/upstream_2026_10_25/live_acceptance.py
$env:PYTHONPATH = (Get-Location).Path
python -X utf8 designDocument/validation/upstream_2026_10_25/live_acceptance.py
```

Live reproduction uses the existing ignored `.env` from the sibling original
`CoreCoder` checkout, or credentials already set in the environment. It sends only
synthetic test content and incurs model API usage. API keys are never committed.
