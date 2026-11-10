# PR 14 增量审阅

两条 Codex 建议及本次 CI 暴露的兼容问题静态复审通过。

- BrowserAgentTool 在子任务预算耗尽时向父工具循环重新抛出 BudgetExceeded，父任务可据此结束当前批次并跳过余下写入。
- 两项 wheel 测试通过模块级 fixture 在临时目录从当前源码构建 wheel，分别检查归档和隔离加载。PEP 517 隔离构建需要可取得 pyproject 声明的构建依赖。
- 检索测试用临时 Git 仓库验证忽略规则，安全矩阵为实际检索工具指定测试工作区。产品的 workspace 和 ignore 检查继续生效。
- repo_map 明示空 required，工具集合检查包括九项及 repo_map 的只读属性。默认 REPL 调用保留原有两个参数的形式，配置了预算才传入预算参数。

已核对冻结文件 SHA-256 前十二位。cli.py 为 b2cd013ba332，repo_map.py 为 32469dab6995，service.py 为 f84d2b898af6。Terra 报告 14 个 CI 目标及一项 Web 后续写入阻断测试通过，CI 范围 Ruff 通过。GitHub 各平台最终 CI 状态由发布流程记录。
