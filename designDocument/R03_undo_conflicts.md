# R03：撤销前验证文件版本，保护外部修改

优先级 P1。关联 [spec](specs/R03.spec.md)、[功能测试](tests/test_r03_undo_conflicts.py)、[公共契约 §2](CONTRACTS.md)。R04 负责同一流程的失败原子性。

## 问题与目标

当前快照只存 before，undo 无条件覆盖或删除目标。agent 写入后的人工作业、外部删除、其他会话覆盖都无法识别。目标是让默认撤销仅作用于 agent 自己留下的版本，并让冲突可见、可重试。

## 数据与算法

1. checkpoint v2 记录 before_exists/before_bytes、after_exists/after_sha256、绝对路径、工作区和会话 ID。记录的是实际字节，包括换行符；空文件和文件不存在必须可区分。
2. 写入工具在同一 FILE_MUTATION_LOCK 内捕获 before，完成原子文件写入后读取/确定实际 after 字节并 finalize hash。写入失败不产生可 undo 的 committed 条目；prepared 的恢复由 R04 处理。
3. undo 先 peek 当前作用域栈顶，显示目标绝对路径，比较当前存在状态与 after hash。匹配则恢复 before 或删除新文件；不匹配则返回 `Conflict: <path> ...`，文件和栈都不变。
4. 冲突输出附当前 hash、预期 hash 和有限长度 unified diff；完整 before/当前副本可保存为预览材料。二进制只显示元信息。不能为显示 diff 把整个超大文件放入终端或模型上下文。
5. 新增 `/undo --force` 对应 `undo(force=True)`。交互界面先展示目标和冲突，再要求清楚选择；只有该次 force 可绕过 hash。默认 `/undo`、脚本调用和 `--yes` 不自动继承强制覆盖意图。
6. checkpoint store 按工作区/会话命名空间；父撤销界面明确列出子任务关联组，不自动从全局栈取“最近别的项目的修改”。

## 文件改动落点

`checkpoints.py` 新增 v2、hash、preview、作用域 store；write/edit 改 begin/finalize；`cli.py` 添加预览与 force 命令解析；`state.py` 绑定 store。legacy 模块入口依照公共契约委托默认 store，保持库使用方式可迁移。

## 兼容、迁移与边界

旧 checkpoint 没有 after hash，不能根据当前文件补一个 hash 后假装无冲突。标记为 legacy，默认拒绝自动撤销，提示可经显式 force 使用。保留原始旧记录备份，版本迁移原子提交。

旧测试“父目录被外部删除后自动重建”与此契约冲突：缺失文件视为外部修改，默认拒绝；经 force 才允许重建父目录。正常无冲突路径的 `Restored ...` / `Removed ...` 文本可继续兼容。

进程内同锁降低内部竞争，但 hash 校验不是对任意外部进程的文件锁。恢复前应再次读取版本并将时间窗缩小；不能声称能阻止所有跨进程 TOCTOU 写入。需要严格多进程协作时再引入文件锁/版本协议；符号链接或文件类型替换应视为冲突，force 也需展示规范化目标。

## 验收

正常回退保留原始 CRLF 字节；既有文件及新文件被人工改写时保持不变；外部删除不重建；force 可执行；重载后冲突保护继续有效。完成时再补工作区隔离、symlink 替换、legacy 记录和真实 CLI 预览验收。
