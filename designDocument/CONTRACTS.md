# 跨问题公共契约

以下定义是本次设计选择，均需在后续修复中实现。保留现有库调用的入口，避免 11 个方案各自引入一套状态、错误或计量协议。

## 1. 会话与工具归属（R01 / R02 / R03）

建议新增 `corecoder/state.py`：

```python
@dataclass
class SessionState:
    workspace_root: Path
    cwd: Path
    session_id: str = field(default_factory=new_session_id)
    # 每次构造均新建，禁止可变类属性或共享默认值
    todos: list[dict] = field(default_factory=list)
    changed_files: set[str] = field(default_factory=set)
    # 实现时注入以 workspace_id/session_id 隔离的 CheckpointStore

    def resolve_path(self, value: str) -> Path: ...
    def fork(self) -> "SessionState": ...

def create_tools(state: SessionState) -> list[Tool]: ...
```

`Agent(..., state: SessionState | None = None)` 为兼容性可选参数；省略时用构造时的进程 cwd 初始化根目录和 cwd。`fork()` 复制根目录与当时 cwd，新建 session_id、Todo 和工具实例。父子显式共享 LLM、permission、hooks；工具实例和 cwd 不共享。自定义工具需声明是无状态对象还是实现 `clone_for_state(state)`；不得静默重新绑定其他 Agent 已持有的 `AgentTool`。

建议持久目录从 `CORECODER_DATA_DIR` 读取，缺省为 `~/.corecoder`。验收 fixture 将其重定向到临时目录。按规范化工作区路径摘要和 session_id 建 checkpoint 命名空间；同一进程的锁和底层传输客户端可以共享，权限与会话状态不能借此跨顶层 Agent 共享。

## 2. 撤销记录和兼容入口（R03 / R04）

```text
CheckpointEntry v2:
  id, workspace_id, session_id, absolute_path, created_at
  before_exists: bool, before_bytes: base64 | null
  after_exists: bool, after_sha256: str | null
  status: prepared | committed | recovery_required
```

hash 基于实际落盘字节；“不存在”是独立状态，不能用空文件 hash 代替。`undo(force=False) -> str` 保留文本返回；冲突包含 `Conflict` 和绝对路径；可重试的系统失败包含 `Error`、阶段及路径，保留记录。后续库可增加结构化 `UndoResult`，首版不要求改变调用者。

`record(path)` 旧接口仅保留为兼容入口；新写入流程通过事务捕获 before、原子写入、落盘后 finalize after。旧记录缺 after 信息时必须提示并拒绝自动覆盖，只能经显式 force 使用。模块级 `undo/pending/clear` 委托兼容默认 store；Agent 工具和 CLI 委托自身 state 的 store。功能测试中直接调用模块入口，是对兼容默认 store 的行为验收。

## 3. 请求预算（R05）

```python
def estimate_request_tokens(messages, *, system: str, tools: list[dict]) -> int: ...

@dataclass
class CompressionResult:
    messages: list[dict]       # 候选消息；调用者仅在 budget_ok 时提交
    changed: bool
    budget_ok: bool
    input_tokens: int         # 含 system、schemas、协议开销及计量安全余量
    output_reserve: int
    limit: int
    artifacts: list[str]      # 已写入的输出归档绝对路径
    reason: str | None        # 失败为 context_budget_exceeded

class ContextBudgetExceeded(RuntimeError): ...

# ContextManager 新增；历史 maybe_compress 保留兼容但不再作为发送许可
def prepare_request(self, messages, *, system, tools, output_reserve,
                    artifact_dir, llm=None) -> CompressionResult: ...
```

`budget_ok` 只在 `input_tokens + output_reserve <= limit` 时成立。这保证客户端所采用计量器下的预算，不宣称启发式估算等于所有 provider 的真实 token 数。计量器优先用模型 tokenizer；未知 tokenizer 用保守估算和可配置余量，并展示 `estimated`。主请求、摘要请求和子 Agent 均走预算闸门；最近用户指令和 schemas 不可悄悄裁剪。

## 4. 工具调用（R07）

`ToolCall` 增加 `raw_arguments: str | None = None` 与 `arguments_error: str | None = None`，原 `id/name/arguments` 构造仍有效。工具执行流水线统一为：校验 → 显示 → pre hooks → 授权 → 执行 → post hooks → 一次性 reply。格式错误为 `Error: invalid_arguments ...`；未知工具为 `Error: unknown_tool ...`。

新 `ToolOutcome` 至少包含 `call_id/name/status/content/stage`，status 为 `succeeded/failed/refused/cancelled/unknown`；Agent 在本地保留该对象供 R06/R09 使用，对模型仍输出文本 tool reply。post hook 错误附加 warning，不能把实际成功写入伪装成未执行。中断或异常后补齐整个 batch；不确定外部副作用时输出 `unknown`，不得自动重试变更调用。

## 5. 费用（R08）

每次请求开始固定 model/provider/endpoint 标识和费率快照，完成后追加不可变 `UsageEntry`。`usage_ledger` 暴露只读序列；`cost_summary` 提供 `known_cost`、`unknown_prompt_tokens`、`unknown_completion_tokens`、`unknown_requests`、`missing_usage_requests`。已知部分使用 Decimal 累加，展示层再格式化。

`estimated_cost` 保留属性：账本全部可定价时返回总额，含未知价格或缺失 usage 时返回 None；CLI 同时展示已知小计与未知部分，不能显示为 0。`_drain` 独立调用时在入口捕获当前模型/费率；正式 `chat` 显式传入请求开始时的快照。LLM 与 LiteLLM 使用同一初始化和记账路径。

## 6. 执行结果（R09）

新增 `Agent.chat_result(...) -> RunResult`，与 `chat(...) -> str` 复用同一内部循环。保留 chat 的历史异常语义；新接口将普通异常转换为 failed，将中断转换为 cancelled，先确保调用链收尾。

```text
RunResult:
  status: completed | max_rounds | cancelled | failed
  text: str
  rounds: int                 # 已发起的主循环模型轮数；摘要请求另计 usage
  tool_errors: list[dict]      # call_id/name/status/stage/message
  usage: dict                 # 本次运行增量；含摘要和子任务，避免父子重复累计
  finish_reason: str
```

`_run_once(agent, prompt, json_output=False) -> int` 返回 `completed=0 / max_rounds=2 / cancelled=130 / failed=1`；main 用 `raise SystemExit(code)` 传播。`--json` 的 stdout 是一份 JSON，含 `schema_version=1/status/text/rounds/tool_errors/usage/finish_reason/exit_code`；工具显示、reasoning、警告、诊断进 stderr。completed 仅代表模型循环结束，工具错误仍保留，业务成功由使用方判断。

## 7. 配置和持久化（R10 / R11）

结构校验统一返回含 `file/field/reason` 的诊断。配置根错则忽略该文件；数组/对象内单项错则跳过该项，保留有效项。session 是消息链整体：结构错则整份拒绝恢复，避免删除单条消息后形成假历史。新存档 `schema_version=2`，无版本视为 legacy v1，未知版本拒绝并告警。加载接口继续返回 `(messages, model) | None`。

原子存档：同目录唯一临时文件 → 写入 → flush/fsync → `Path.replace` → 清理临时文件；不支持通过同时覆盖正式文件与临时文件模拟原子性。MCP 分页的候选工具表也遵循“全部验证成功再发布”，部分发现失败不暴露半份注册表。
