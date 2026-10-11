# R04：撤销事务、失败补偿与 REPL 存活

优先级 P2。关联 [spec](specs/R04.spec.md)、[功能测试](tests/test_r04_undo_transaction.py)、[公共契约 §2](CONTRACTS.md)。与 R03 共享 store 和冲突校验，不能分别重写 undo。

## 问题与目标

当前 undo 先 pop，再写文件；恢复失败丢内存条目，REPL 的命令分支又在异常捕获之外。单纯交换两行只解决普通写入失败，还会留下“文件恢复成功但快照持久化失败”的不一致。

目标：失败时不丢撤销机会，明确文件是否已改变；命令错误留在 REPL；重启时能辨认未完成事务。

## 事务步骤

1. 获取与 write/edit 相同的 FILE_MUTATION_LOCK，peek 条目；按 R03 校验当前版本。锁只由一个层次持有，store 提供内部 unlocked 方法，避免当前非重入 Lock 递归死锁。
2. 生成 undo 事务 ID，保存独立、原子写入的 journal：checkpoint ID、目标、undo 前当前字节/不存在标志、期望恢复后 hash、phase=prepared。journal 持久化失败时不触碰目标。
3. 存在的 before 写入同目录唯一临时文件，flush/fsync 后 `Path.replace(target)`；新建文件撤销则 unlink。任一步失败返回 Error，清理临时文件，保留 stack 与原目标。
4. 目标恢复成功后，将剩余栈原子写入 checkpoint 文件。只有持久化成功才更新内存 stack、把 journal 标为 committed 并清理。禁止继续使用吞掉 OSError 的 `_save()` 作为成功依据。
5. 若步骤 4 失败，使用 journal 的 undo 前字节补偿目标回到 agent after 版本，条目保留，返回 `Error: checkpoint_commit_failed ...; retryable`。补偿也失败时保留 journal，置 recovery_required，明确展示当前文件状态，不允许下一条变更或再次 undo 掩盖不确定结果。
6. 启动恢复读取 journal：current 等于 agent after 表明未恢复/已补偿；等于 before 且 checkpoint 已删除表明已提交；等于 before 但 checkpoint 尚在则允许完成提交；其他版本视为外部冲突，保留并要求恢复选择。不得只靠“文件存在”决定删除记录。

## REPL 与改动落点

`checkpoints.py` 拆出原子写入 helper、事务 journal、补偿与恢复；`cli.py` 将所有 slash-command 分发和普通 chat 纳入统一异常边界。OSError 显示 Error 后 continue；KeyboardInterrupt 取消当前命令回到提示；EOF/quit 仍退出。save、sessions 等命令同时获得此异常边界。

`undo() -> str` 首版保留，普通恢复错误自行变成 Error；CLI 仍捕获意外异常，防止第三方 store 故障打断交互。日志必须写明阶段，不能对“文件变了但记录未提交”显示“撤销成功”。

## 验收与故障注入

自动测试覆盖 Path.write_bytes 失败、unlink 权限失败、checkpoint `Path.replace` 提交失败后的补偿和重载、/undo 抛错后仍处理 /help。正常场景重试成功才减少 pending。

故障注入点是实际存储边界，不是把整个 undo mock 成目标行为。后续需添加 journal 写入失败、补偿失败、各阶段硬退出后的重启恢复和并发 edit/undo 测试。原子 replace 保护单个文件；journal 才负责文件与 checkpoint 两份资源的恢复，不能把两个 rename 当作一个 OS 原子事务。
