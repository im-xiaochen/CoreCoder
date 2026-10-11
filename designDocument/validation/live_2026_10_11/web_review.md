# Web 与交付一致性审查

日期：2026-10-11（Asia/Shanghai）
范围：`frontend/`、`web_backend/`、`start-web.ps1` 与 R01–R11 模型/运行时接口变更。
约束：未读取或打印 `.env`，未调用真实模型/API，未创建 Git 分支、提交、推送或 PR；未修改生产源码。

## 验证证据

| 检查 | 命令 | 结果 |
|---|---|---|
| 前端 TypeScript + Vite 生产构建 | `cd frontend; npm run build` | 通过：`vite v6.4.4`，1582 modules，`dist/index.html`、CSS、JS 产物生成；第一次沙箱内运行因 esbuild `spawn EPERM` 中止，受控提权重跑通过 |
| Web 离线回归 | `E:\github项目\.corecoder-review-py312\Scripts\python.exe -m pytest web_backend/tests/test_web.py -q --basetemp E:\github项目\CoreCoder\.pytest-web-review3` | **12 passed in 6.27s** |
| 静态产物忽略规则 | `git check-ignore -v frontend/dist/index.html frontend/node_modules/react/index.js frontend/tsconfig.tsbuildinfo` | 三者均由 `frontend/.gitignore` 忽略 |

Web 回归覆盖 bootstrap/会话隔离、授权批准/拒绝、计划模式、停止与消息链修复、保存/恢复/reset、cwd 与凭证保护、子代理隔离、HTTP Host/Origin/token/path guard、SSE 完成和 JSON 形状校验。没有真实模型调用。

## 发现

### [P1] Web 层丢失 `RunResult` 的 max-rounds/工具错误状态

`web_backend/service.py:447` 仍调用兼容接口 `session.agent.chat(...)`，该接口只返回文本；`service.py:453` 只识别旧的精确字符串 `"(reached maximum tool-call rounds)"`。当前 `Agent.chat()` 在 `RunResult.status == "max_rounds"` 时返回 `result.text`（见 `corecoder/agent.py:311-317`），因此真实长任务达到轮次上限会进入 `complete` 事件，页面显示正常完成，无法看到 `status=max_rounds`、`rounds` 或 `tool_errors`。普通 failed/cancelled 也只转成截断字符串 error，结构化结果不会进入快照。

建议 Web runner 改用 `chat_result()`，将稳定状态、结束原因、轮数和工具错误映射到 SSE `complete/error/snapshot`；前端应显示 max-rounds/failed 的可行动提示。当前 Web 测试使用一次短 ScriptedLLM 响应，未覆盖此分支，因此 12/12 通过不能证明 R09 在 Web 中可见。

### [P2] R08 `cost_summary` 未穿透 Web API/UI

`web_backend/service.py:293-298` 的 `snapshot().usage` 只返回累计 prompt/completion、`llm.estimated_cost` 和上下文字段；`frontend/src/types.ts:27` 与 `frontend/src/App.tsx:334` 也只消费 `usage.cost`。当费率未知或 provider 没有 usage 时，`estimated_cost` 按 R08 合约为 `None`，但 `known_cost`、`unknown_prompt_tokens`、`unknown_completion_tokens`、`unknown_requests`、`missing_usage_requests` 都未返回，UI 只能显示“当前模型暂无估价”，已知费用小计和未知部分不可见。

这不会让金额被错误显示为 0，但不能满足 R08 要求的“已知小计与未知部分均可见”的消费层语义。建议在 `usage` 中增加 JSON-safe 的 `cost_summary`（Decimal 转字符串），并在费用面板显示已知小计及未知/missing 计数。

### [P2] R07 工具 outcome 状态被压成字符串，UI 会把拒绝/失败画成成功

`web_backend/service.py:128-130` 对每个工具只发送 `tool_start` 及 `str(result)` 的 `tool_result`；拒绝路径仅增加 `refused: true`（`service.py:136`）。R07 的 `ToolOutcome.status/stage/warning`、unknown/cancelled/failed 等结构没有进入事件。`frontend/src/App.tsx:309` 只要看到 `tool_result` 就停止 loading，`ToolCard`（`App.tsx:64`）固定显示勾号，因此参数验证失败、计划模式拒绝、执行未知结果在实时卡片上均可能呈现为“已完成”。历史消息中的工具回复文本仍保留错误，但状态不可筛选或区分。

建议保留原有 `result` 文本兼容字段，同时发送 `status`、`stage`、`warning`；前端按状态显示拒绝/失败/未知，而不是统一勾号。R07 离线测试验证了核心消息链，但当前 Web 测试没有断言这些事件字段。

### 交付边界记录：Web 用量账本恢复

`WebSession.persist()` 目前只持久化 `prompt_tokens`/`completion_tokens`（`service.py:337-338`），重载后通过 `import_legacy_usage()` 恢复累计值；这会把旧会话的费用视为未知价格，不能恢复完整 `UsageEntry` 账本。设计记录将“账本恢复”列为后续产品增强，因此本审查将其记为已知限制，不作为本轮 Web 源码阻断项；若要求重启后费用历史保持可定价，需要把账本条目及去重键纳入 Web session schema。

## 当前可提交的 Web 源码

应提交以下源码/依赖清单文件：

```text
frontend/.gitignore
frontend/README.md
frontend/index.html
frontend/package.json
frontend/package-lock.json
frontend/public/favicon.svg
frontend/src/App.tsx
frontend/src/api.ts
frontend/src/main.tsx
frontend/src/styles.css
frontend/src/types.ts
frontend/tsconfig.json
frontend/vite.config.ts
web_backend/__init__.py
web_backend/README.md
web_backend/server.py
web_backend/service.py
web_backend/tests/test_web.py
start-web.ps1
```

不要提交 `frontend/dist/`、`frontend/node_modules/` 或 `frontend/tsconfig.tsbuildinfo`；它们均已被忽略。此审查没有更改上述生产源码。

## 本轮 Web 修复与复验

按父代理验收要求，本轮已实现以下修复：

- `Service.start()` 使用 `Agent.chat_result()`，发送 `run_result`（status、finish_reason、rounds、tool_errors、usage），并仅在 `completed` 时发送成功 `complete`；`max_rounds` 发 error，不再伪装成功。
- 快照新增 JSON-safe `usage.cost_summary`，前端显示 known subtotal、unknown token/request 和 missing usage。
- `tool_result` 附带 `status/stage/warning/refused`；前端区分成功、失败、拒绝、取消和未知图标。`tool_start` 通过 Agent 的校验后回调触发，非法参数只产生 validate 失败结果，不触发 start、授权或副作用。
- 新增 5 个行为回归及拒绝/取消状态断言（测试总数从 12 增至 16），包括同一 tool call ID 跨轮 success → failed 的有序快照证据；前端按顺序元数据恢复历史卡片状态，刷新后仍不会把失败显示为成功。

复验结果：`web_backend/tests/test_web.py` **16 passed**；`npm run build` **通过**（TypeScript + Vite，1582 modules）；`ruff check web_backend` **通过**；`git diff --check` 无差异错误。未修改 `corecoder/` 或 `designDocument/tests/`，未提交 Git。
