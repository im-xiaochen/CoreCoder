# R11：MCP 工具分页发现与完整注册

优先级 P2。关联 [spec](specs/R11.spec.md)、[功能测试](tests/test_r11_mcp_pagination.py)、[公共契约 §7](CONTRACTS.md)。

## 问题与协议依据

当前 MCPClient 只拉一次 tools/list。审查采用的固定协议版本 2025-06-18 支持请求 `cursor` 和响应 `nextCursor`，应将其作为不透明字符串逐页传递。依据为 [MCP Tools 2025-06-18](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)。初始化的版本协商要求见 [MCP Lifecycle 2025-06-18](https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle)。这两份固定版本规范已核对；不假设当前最新版与仓库相同。

## 具体设计

1. initialize 验证 result 根、protocolVersion、capabilities、serverInfo。保存 `client.protocol_version` 并显示协商版本；首版支持集合为 `{"2025-06-18"}`，协商其他版本明确 MCPError 并关闭进程。tools 能力缺失时不发送 tools/list，工具表为空；字段错则按协议错误处理。
2. 全发现过程共享 monotonic deadline=`start + INIT_TIMEOUT`；每次 `_request` 使用剩余时间。初始化和每页不能各自重获完整超时预算。
3. 首次 tools/list params={}；随后传 `{"cursor": nextCursor}`。cursor 原样传输，不转整数、不自行计算。缺 nextCursor 表示结束；若存在必须是非空 string。空 tools 页仍按 cursor 继续。
4. 维护 seen_cursors，重复值立即错误；新增 `MAX_DISCOVERY_PAGES=100` 和工具总数上限，防止不同 cursor 的无限序列。达到限制且仍有下一页则明确失败。
5. 每页验证 tools 为数组，各工具 name 非空且唯一、inputSchema 为 object、description 合法；跨页重复名字即协议冲突，不能后者静默覆盖。汇总到局部 candidate，全部页成功后一次性设置 self.tools。
6. 分页中服务退出/超时/错结构，不发布半份表，抛 MCPError 并 close。loader 按服务器隔离失败，其他服务器仍可用。最终注册名 `mcp__server__tool` 还需检查跨服务器拼接碰撞与内置/自定义同名，发现冲突拒绝注册并告警。
7. close 幂等，terminate 后 wait，超时 kill 后也 wait；关闭管道并唤醒未完成请求。分页失败不能留下子进程或等待线程。

## 伪代码

```python
deadline = monotonic() + INIT_TIMEOUT
initialize_and_validate(remaining(deadline))
candidate, seen_cursors, seen_names = [], set(), set()
cursor = None
for page in range(MAX_DISCOVERY_PAGES):
    result = request("tools/list", {} if cursor is None else {"cursor": cursor}, remaining(deadline))
    candidate.extend(validate_page(result, seen_names))
    if "nextCursor" not in result:
        self.tools = candidate
        break
    cursor = validate_new_cursor(result["nextCursor"], seen_cursors)
else:
    raise MCPError("pagination page limit exceeded")
```

## 改动落点与兼容

`mcp.py` 增发现函数、响应校验、协商版本、页数/总量限制和生命周期完善；`agent.py` 注册表不允许 dict 推导静默覆盖重复名；cli 启动诊断展示服务名/版本/工具数。单页服务与已有 tools/call 接口保持兼容，notifications/list_changed 留作后续增量刷新能力。

## 验收

真实本地 stdio 假服务端覆盖三页与 opaque cursor、单页/空页、重复 cursor、重复名字、中途退出、非法 tools、版本不支持、无穷不同 cursor 和共享 deadline。客户端失败时进程应被回收。真实第三方 MCP、跨服务器名称碰撞、tools capability 缺失和通知刷新仍需后续补测。
