# R01：默认工具与父子 Agent 状态隔离

优先级 P1。关联 [spec](specs/R01.spec.md)、[功能测试](tests/test_r01_agent_state_isolation.py)、[公共契约 §1](CONTRACTS.md)。

## 问题与目标

`corecoder/tools/__init__.py` 的 `ALL_TOOLS` 是对象列表，`Agent.__init__` 直接引用并覆盖共享 `AgentTool._parent_agent`。后创建的 B 能替换 A 子任务的模型和授权，Todo 也跨实例共享。仅浅拷贝列表无法解决；深拷贝整组工具又可能复制锁、MCP transport 或 provider client。

目标：工具实例由所属 Agent 创建；顶层 Agent 的授权、Todo、目录和历史独立；子 Agent 只继承其直接父 Agent 显式允许共享的能力。

## 具体设计

1. 新增 `create_tools(state)`，每次实例化 8 个内置工具。`Agent(tools=None)` 调用工厂；CLI 拼装 `create_tools(agent_state) + load_mcp_tools()`，不再从 `ALL_TOOLS` 取对象。
2. 引入可注入 `SessionState`。Todo 和 changed_files 从模块可变全局迁入 state；系统提示每轮从该 state 渲染。`reset()` 只清除本 Agent 的历史和 Todo。
3. `AgentTool` 构造或首次绑定时确定父对象；已绑定其他父对象时抛出清晰的 ValueError，禁止后创建者覆盖。自定义 tools 列表仍可传入；有状态自定义工具需实现 `clone_for_state`，不能隐式假设任意对象可深拷贝。
4. 子任务构造使用 `parent.state.fork()` 和新工具工厂，去掉派生工具。使用 `parent.llm/permission/hooks` 的对象引用；不读取全局“当前 agent”。子 Todo 初始为空，返回总结供父更新自己的清单。
5. 子 Agent 的 checkpoint 挂在独立 child session 下，并记 parent_session_id；是否将 child checkpoint 合并为父撤销组由 R03 store 显式管理。不得凭共享文件工具来获得“隐式合并”。

## 文件改动落点

| 文件 | 修改 |
|---|---|
| `tools/__init__.py` | 新工厂；ALL_TOOLS 仅作为过渡期兼容导出，不用于生产 Agent 默认值 |
| `state.py`（新增） | 会话标识、Todo、changed_files、fork |
| `agent.py` | state 注入、所有权检查、提示与 reset 的状态读取 |
| `tools/agent.py` | 显式父继承、新建子工具、结果返回 |
| `tools/todo.py`、`tools/edit.py`、`tools/write.py` | 状态迁移 |
| `cli.py`、`__init__.py`、demo/旧测试 | 工厂组装和兼容导出更新 |

## 实施与兼容

先落工厂与父指针修复，再迁状态。保持 `Agent(llm, tools=...)`、工具 names/schemas 和 `chat -> str`。兼容 `ALL_TOOLS` 的存在不代表它可用于多 Agent 复用；README 和原有测试 helper 应改用工厂。与 R02 同时评审 state，避免第二次迁移路径 API。

脚本模型在父子之间共享是有意设计，子调用会消费同一父模型的下一次响应；两个顶层 Agent 的不同模型不会互相消费。共享 LLM 客户端的线程安全、用量并发累计交由 R08 的锁处理。

## 验收与风险

测试分别验证对象身份、Todo/reset 隔离、A 拒绝写入而 B 全自动时子任务仍被 A 拒绝、子清单不替换父清单。完成条件是所有 R01 测试通过，且原子 Agent 授权、计划模式测试保持相同语义。还需补一次反向授权组合与自定义有状态工具重绑定用例；不能只用“对象不同”替代授权行为验收。
