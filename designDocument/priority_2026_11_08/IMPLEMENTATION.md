# 当前实现状态（2026-11-10）

F09 检索、F11 按需技能和 F08 每次运行预算已完成实现；具体审查和独立验收结论以 [REVIEW.md](REVIEW.md) 与 [ACCEPTANCE.md](ACCEPTANCE.md) 的最终记录为准。预算支持 OpenAI 兼容适配器，LiteLLM 启用预算时发送前明确拒绝。

最终集成发现的 CI lint、CLI 未知 slash 命令兼容和前端迟到请求测试同步问题已修复；全 CI 范围 Ruff、对应两个目标测试及两项文档计数检查已通过，独立复验仍以验收记录为准。交付文件及哈希见 `.phase3-preparation/changed-paths.json`。

以下为历史交接，保留当时状态和哈希，不能作为当前验收结论。

# 历史记录：F09 初版交接（当时未验收）

- inventory 拒绝含 `..` 的输入路径，以防链接组件被归一化掩盖；逐组件检查链接和 junction。直接文件通过相同枚举与过滤。rg 报错、超时或输出上限时保留完整 NUL 项并标记 incomplete；Git 后备检查 ignored tracked 文件，无后端遍历保守拒绝 ignore 文件。
- grep 使用独立进程执行 Python 正则，超时终止，并限制文件读取、总字节、行宽、结果字节；grep/glob/repo_map 调用传入未解析的路径。
- `py_compile` 与 Ruff 对上述文件通过。真实仓库 grep 冒烟返回匹配并标记 incomplete；rg 返回 2 的原因是工作区多处目录拒绝访问。`..` 冒烟返回明确错误。尚未运行完整行为测试或独立验收。
- SHA256：repository_search.py `C367A68DA870B85A6587D53C1D12FC1255B1959D9D7D5B26C3F6F27530778906`；search_worker.py `64E8B6C8868FDA1D12FFA61DE69E3E44EF86797209476C410C50339779E2AFD7`；grep.py `F9094B86B39DE5B88E8056597EAC0CB75A7F9AC9BD3180E9D3F221B57C8475F7`；glob_tool.py `E508C39B606E5CE1226268C46D0CC1EAEDF1A0F55AA35FA3794E64AFB5092964`；repo_map.py `DED79DDD7090DAD36DD27E7242AA82A423DC50E99E3A78FA54950A4BB91C7070`。
- 待 Astra 审查：链接及竞态、rg 局部输出语义和子目录遗漏、Git/无后端 ignore 语义与预算、Windows 子进程清理、正则超时及分页、glob 锚定与单文件路径、repo_map AST/输出预算、敏感文件规则和所有工具入口。需实现者定向测试后交 Sol 独立验收。F11/F08 尚未实现。

## 2026-11-10 交付状态更新

上述初版记录保留供追溯，不代表当前状态。F09 检索、F11 按需技能和 F08 运行预算已实现；预算适配范围为 OpenAI 兼容客户端，LiteLLM 启用预算时发送前拒绝。预算按运行共享，未知费用和未确认预留单独报告；检索有预算和不完整提示，翻页反映实时文件状态。

独立验收已记录 F09 18 个行为场景及两项审查增量、F11 核心与入口行为、F08 核心/OpenAI/Agent/Web 后端场景；具体测试数、哈希及状态以同目录 ACCEPTANCE.md 和 REVIEW.md 为准。F08 前端报告已完成独立行为验收，最终审查与整体验证状态以 ACCEPTANCE.md 和 REVIEW.md 为准。本轮 README 中英文说明及源码计数两项定向测试通过；完整最终集成由独立验收执行。交付清单以 `.phase3-preparation/changed-paths.json` 为准，基线为远端 main `5c6d4ed34a4201b194183af5720e436779dd9cfa`。
