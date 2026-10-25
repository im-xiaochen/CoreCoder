# R10：配置结构校验与原子会话存档

优先级 P2。关联 [spec](specs/R10.spec.md)、[功能测试](tests/test_r10_config_session_validation.py)、[公共契约 §7](CONTRACTS.md)。

## 问题与目标

hooks/MCP/session 只捕 JSON 语法错误，合法 JSON 错类型仍抛 AttributeError/TypeError；save 直接覆盖正式文件。目标是坏项可诊断、有效项可继续、存档不因半写入损坏。

## 配置校验规则

| 文件 | 根与字段 | 失败处理 |
|---|---|---|
| hooks | 根 object；PreToolUse/PostToolUse 为 array；项 object；command 非空 string；matcher 可省略或 string | 根/阶段类型错忽略该文件/阶段；单项错跳过，保留其他有效项 |
| MCP | 根 object；mcpServers object；服务器 spec object；command 非空 string；args string[]；env string→string | 无效项不启动进程，其余服务器继续 |
| session | 根 object；messages array；model 非空 string；版本受支持；消息与工具链完整 | 整份拒绝，load 返回 None，list 跳过并告警 |

诊断统一为文件绝对路径 + 字段路径 + 期望/实际类型，例如 `hooks.json: PreToolUse[0]: expected object, got integer`。缺文件是正常可选配置，无 warning；坏文件产生有限条 warning。诊断不能把含凭证的 env value 原样打印。

## 存档结构与消息验证

新写入 `schema_version=2/id/model/saved_at/messages`；state/usage 等为后续可选字段。无版本按 legacy v1 验证后读取；未知版本拒绝，不按猜测字段静默降级。

roles 限 user/assistant/system/tool。user/system content 为 string；assistant 可 content=None 但必须有有效 tool_calls；function name、ID、arguments 为合适类型；tool 必须有 string content 和对应未回答 ID。重复/孤儿/缺失 reply 拒绝恢复；不通过丢弃坏消息拼接剩余历史。session 的 tool arguments 是协议字符串，不与 R07 的已解析 dict 混淆。

load_session 保持 `(messages, model) | None`，list_sessions 复用相同 validator，防止列表可见但恢复崩溃。原 ID 规范化与路径边界继续保留；读取非 UTF-8、截断、OSError 均产生诊断并返回无效。

## 原子保存流程

保存前校验全部结构 → 在目标同目录创建唯一临时文件 → UTF-8 JSON 写入 → flush/fsync → `Path.replace(target)` → 尽力 fsync 目录（平台支持时）。临时文件必须唯一，不能固定 `.tmp` 让并发保存互撞。replace 前失败保持原档完整，并向调用者传播 OSError；REPL 由 R04 的命令异常边界维持存活。成功之后才显示“Session saved”。

finally 仅清理自己创建的临时文件。明确同一 session_id 并发保存的策略（会话锁串行，后者覆盖前者），不要依赖 replace 保证逻辑顺序。此处原子性针对单份 session；跨 checkpoint 的一致性仍由 R04 journal 解决。

## 文件改动落点与实施

建议新增 `validation.py` 复用字段诊断、基础类型判断；hooks/mcp 的 loader 先 validate 再构建；session load/list/save 共用 validator；cli 不吞保存失败并伪装成功。先补形状验证，再落版本与原子写入。新依赖若选择 JSON Schema validator 可与 R07 共用，但配置项跳过策略仍需显式实现。

## 验收

根与嵌套错误、混合有效/无效 hooks 和 MCP、会话非法 role/content/孤儿 tool/未知版本、截断和 legacy、replace 失败保留旧字节、新存档版本及无凭证字段都有测试。无效消息链、编码错误、并发保存与真实 REPL /save 失败需继续补齐。
