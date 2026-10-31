# 三模式离线浏览器验收

从项目根目录启动，先构建前端：

```powershell
npm --prefix frontend run build
python -m designDocument.validation.modes_2026_10_31.serve_acceptance
```

需要已有项目运行依赖。本机现有验收环境为 `E:\github项目\.corecoder-review-py312\Scripts\python.exe`，可替换命令中的 `python`。默认端口为 8767，可通过 `--port` 改为其他空闲端口；服务仅监听 `127.0.0.1`。

- 工作台：<http://127.0.0.1:8767/>
- 390px 视口：<http://127.0.0.1:8767/acceptance-frame?width=390>
- 1280px 视口：<http://127.0.0.1:8767/acceptance-frame?width=1280>
- 只读验收记录：<http://127.0.0.1:8767/acceptance-state>

使用真实 `Service`、`Agent`、权限流程和 HTTP/SSE；模型替换为确定性本地响应。配置在内存中使用固定测试值，不读取 `.env`，不访问模型提供商，不加载 MCP 或 Hooks。工作区、网页会话、CLI 存档与检查点均隔离在 `.tmp/modes-acceptance-20261031`；重启保留模式与测试对话，审计计数从零开始。测试专用服务器将 `frame-ancestors 'none'` 改为 `'self'` 以支持同源窄屏 iframe，产品服务器保持原策略。

## 验收步骤

1. 记录 `/acceptance-state` 的 `mode_requests` 长度，点击当前模式按钮，确认菜单展开但计数不变；选择当前项也不增加计数。
2. 依次选择聊天、计划、编程，核对按钮、选中项、说明与服务端 `sessions[].mode`；每次选择不同项只增加一条成功记录。
3. 在聊天模式发送普通问题，确认收到逐段文本，模式保持 `chat`，最新 `calls[].tools` 为空。
4. 在聊天模式发送 `probe`，模型会故意返回读取、写入、命令、子代理及未知 MCP 调用。必须全部拒绝，且没有权限弹窗。记录中的工具 schemas 仍为空，工作区不得出现 `probe.txt`。
5. 在计划模式发送 `read`，应读取测试工作区的 `welcome.txt`，然后输出编号计划；发送 `write` 应拒绝写入且不弹权限框。
6. 在编程模式发送 `write`，应出现写文件授权；允许一次后，变更面板与审计记录应出现 `acceptance-note.txt`。此文件位于隔离测试工作区。
7. 发送 `slow` 可验收任务进行中模式按钮禁用，以及停止后恢复操作。该任务最多运行约 12 秒。
8. 使用键盘验证 Enter/Space 打开、上下箭头/Home/End 导航、Escape 关闭与焦点恢复；点击外部关闭菜单。切换会话后菜单收起，并恢复各会话自己的模式。
9. 在 390px 视口重复打开菜单，检查没有水平溢出、文案可读、选择项可点击；刷新核对模式持久化。

`/acceptance-state` 仅返回模式请求计数及结果、模型请求工具名称、测试会话模式与变更摘要和测试工作区文件名，不返回对话全文、API 令牌或凭据。
