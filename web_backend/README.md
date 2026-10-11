# 网页接口适配层

此目录独立于 `corecoder/` 核心代码，复用现有 Agent、LLM、工具、Permission、Context、会话、MCP 和 Hooks。只依赖 Python 标准库及项目已有依赖，不新增 Python Web 框架。

从项目根目录运行：

```powershell
python -m web_backend.server --port 8765 --open
```

`--workspace 路径` 可选用其他已存在目录作为 Agent 工作区；模型配置由启动进程的环境和 `.env` 加载。前端构建与完整启动步骤见 [`frontend/README.md`](../frontend/README.md)。此适配层供源码检出使用，不包含在原有 `corecoder` PyPI wheel 中。

## 接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/bootstrap` | 工作区、模型配置摘要、随机请求令牌、工具和会话列表 |
| GET | `/api/sessions` | 网页会话摘要 |
| POST | `/api/sessions` | 新建网页会话 |
| GET | `/api/session?id=…` | 消息、任务、权限、用量和变更摘要 |
| GET | `/api/changes?id=…` | 文件变更与 unified diff |
| GET | `/api/saved-sessions` | CLI 存档列表 |
| POST | `/api/chat` | `{id, prompt}`，返回 SSE 流 |
| POST | `/api/approve` | `{id, permission_id, answer}`，answer 为 once / always / deny |
| POST | `/api/stop` | `{id}`，取消后续步骤 |
| POST | `/api/action` | `{id, action, …}`：plan / model / save / import / compact / undo / reset / revoke |

POST 需要 `Content-Type: application/json` 和 `X-CoreCoder-Token`（来自 bootstrap）。流事件包含 `started`、`token`、`reasoning`、`tool_start`、`tool_result`、`permission`、`notice`、`run_result`、`error`、`complete`，最后以 `snapshot` 和 `done` 结束。

服务只监听 `127.0.0.1`，校验 Host、请求来源和随机令牌。接口不返回配置中的 API Key。普通文件工具限定当前工作区；Shell、MCP、Hooks 仍具有原引擎的能力，需要操作时通过页面授权。这里不是操作系统沙箱，不应用于公网部署或多用户共享。

SSE 还会发送 `run_result`，其中包含 `status`（completed / max_rounds / cancelled / failed）、`finish_reason`、`rounds`、`tool_errors` 和本次 `usage`；旧的 `complete` / `error` / `notice` 事件继续保留。`tool_result` 保留 `result` 和 `refused` 兼容字段，并附带 R07 的 `status`、`stage`、`warning`，因此前端可以区分成功、拒绝、失败、取消和结果未知。

网页会话使用独立工具实例、任务清单、持续授权和文件撤销存档。由于原工具包含进程级状态，网页任务及会话设置操作按顺序执行。撤销前校验当前文件摘要，避免覆盖会话之后产生的编辑。

## 验证

离线测试注入 `ScriptedLLM`，不使用真实 Key，不调用付费接口，也不写用户存档目录：

```powershell
python -m pytest web_backend/tests -q
python -m ruff check web_backend
```

覆盖权限批准与拒绝、计划模式、取消、任务并发、会话持久化、CLI 导入导出、真实 HTTP/SSE、路径边界、JSON 校验及撤销冲突。
