# R01–R11 delivery review — 2026-10-11

## Findings and fixes

- `ContextManager.extract_records()` and summary flattening previously keyed tool replies only by `tool_call_id`. A later round reusing an ID therefore replaced the earlier reply. The implementation now pairs calls and replies by occurrence in wire order, and `SessionState.record_outcome()` keeps both derived records distinct through the same path. Regression: `test_reused_id_keeps_prior_success_separate_from_later_failure`.
- Python `execute()` signature binding happened inside `_exec_tool()`, after display, pre-hooks, and authorization. Binding is now checked immediately after JSON Schema validation; the execution-side bind remains as a defense. Regression: `test_python_signature_error_is_before_callbacks`.
- Summary extraction could raise `AttributeError` when raw JSON arguments decoded to an array or scalar. Object-only target extraction now leaves `target=""` and preserves the failed outcome. Regression: `test_non_object_arguments_remain_failed_without_summary_crash`.
- Summary requests now explicitly require evidence-bound state, preserve constraints and remaining/unverified work, and prohibit inferring verification or completion from intent or absence of errors. Regression: `test_summary_request_requires_evidence_bound_unverified_state`. The canonical history archive remains authoritative.

## Validation evidence

- R01–R11 design tests: **219 passed, 1 skipped**. The skip is the existing Windows symlink privilege limitation. JUnit: `design-final.xml` in this directory.
- Existing CoreCoder tests: **214 passed** after the README line-count update. The final JUnit is `core-final.xml` in this directory. The focused README test passed after updating engine `2190`, physical `4713`, and net `4000` values in both README files.
- R06 focused tests: **7 passed**. R07 focused tests: **14 passed**. Duplicate-ID and signature-order acceptance cases are included in the design suite.
- Final frozen-tree run: **449 passed, 1 skipped** across `tests`, `web_backend/tests`, and `designDocument/tests`; JUnit and complete log are `final-all.xml` and `final-all.log` in this directory. The skip is the same Windows symlink privilege limitation.
- Frozen Web validation: **16 passed** and frontend production `npm run build` passed. The live provider runner completed 9 requests with 33 tool calls, no tool errors, complete chains, and CLI exit checks; evidence is `../final_runner.log` and `../acceptance_report.json`.
- Ruff passes for `corecoder`, `tests`, `web_backend`, `examples`, `designDocument/tests`, `designDocument/verify_delivery.py`, and the two live validation workers. CI lints the source and design tests; validation workers remain explicit review evidence rather than a broad recursive lint target that could include scratch code.
- `verify_delivery.py` reports 11 designs/specs/test modules mapped, links and syntax valid; `git diff --check` passes.

## Recommended submission scope

Include the CoreCoder runtime changes, `designDocument` specifications/tests/verification assets needed for a clean checkout, `.github/workflows/ci.yml`, README updates, and the reviewed `web_backend` plus frontend source. Exclude `frontend/dist`, `frontend/node_modules`, `frontend/tsconfig.tsbuildinfo`, pytest scratch directories, Python/ruff caches, `.corecoder-artifacts`, `.env`, and any local credentials. The frontend and backend should remain in the same delivery only if the web review's final API fixes and tests land; otherwise split them into a separately reviewed change.

## Known validation limits

Offline tests do not establish provider billing/tokenizer accuracy, third-party MCP compatibility, or cross-platform behavior. One symlink test remains skipped because this Windows host lacks the privilege to create symlinks. The real DeepSeek summary check is recorded in `../live_summary_report.json`; no credentials are stored in this directory.
