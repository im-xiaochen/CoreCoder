# 增量审阅

|切片|静态结论|独立验证|
|---|---|---|
|1 工具历史与参数适配|PASS|Sol 首轮 12 通过；失败目标修后 1 PASS，另 6 项 matcher 边界通过|
|2 只读团队|PASS（默认 CLI SIGINT 路径）|见 ACCEPTANCE 最终独验|
|3 DAG 与 Web|PASS（最终静态复核）|见 ACCEPTANCE 最终独验|

切片 1 使用实际 OpenAI 客户端端点建立协议来源，与 F01 定价键独立。同源 assistant 工具消息才回传 reasoning_content，内部键在发请求前剥除，通用 reasoning 与最终回合不会进入历史。摘要输入提取正文、工具及结果。无引号 temperature 绑定独立参数及局部拒绝短语，旧引号格式和 max_tokens 适配保留。参数回退继续经过 F08 物理请求门禁。

Sol 实际 wire 捕获发现 Unsupported parameter: temperature 未适配，第二次发送仍含 temperature。该缺陷已修复并由 Sol 实际 SDK 目标复验通过。

恢复增量审阅：当前 llm.py SHA256 a51b347d207d8e30ae92fb38a7e31e909d96fbd54abb3d20616e0aec22a0b8c1 的参数正则已接受 parameter 后可选冒号，temperature 后保留词边界，未改 max_tokens 精确匹配与预算发送入口。此为静态结论，独验结果以 ACCEPTANCE 为准。

设计复核：plan 放行必须限定已验证的 tasks 只读形态，不可把 agent 整体加入 READ_ONLY；旧 task 保持既有授权。Web 的 BrowserAgentTool 必须复用同一调度器并保留受限工具包装。其余阶段按 PLAN 执行，待实现冻结后审阅。



阶段2施工中增量审阅（非冻结通过）：Permission 的同实例 RLock 将 check/always/_save 串行化，临时文件加 os.replace 保持完整 JSON；新增8线程同名授权测试覆盖单次提示与落盘。该测试不等价于阻塞 ask 时不同工具提示不重叠或只读继续的完整独验。Web 初始化 restore 在运行前，revoke 受 Service.execution 非阻塞锁保护；当前 snapshot 直接读 _always，不等待提示锁，因此本批只读团队无需为了假设性 GET 阻塞强制拆锁。如改为加锁 snapshot，必须使用独立短状态锁，不能复用跨 ask 的锁。不同 Permission 实例共享同一路径的合并不属于此同父权限对象承诺。

team.py 初版仍为全量 submit 并按声明顺序等待，尚未具有共享停止与取消队列；必须在最终实现中解决预算失败观察延迟与根取消。RunUsageScope.as_dict 的 summary 之后未锁读取 request_ids/_entries 已通知 Terra 改为一致快照，避免并发成员导致用量字段不一致。继续等待 CLI/Web 接入及冻结。

CLI/Browser 初版复核：只读工具采用内建精确类型；Browser 保留 ScopedTool 且校验底层精确类型，子状态 clone 与禁用 hooks 已接入。单 task 保持原工具克隆和父 hooks，并依据 chat_result 状态返回及预算再抛。usage.as_dict 已将全部聚合纳入共享 RLock，先前快照问题已修。

待 Terra 修复：plan gate 目前使用 isinstance + __module__ 字符串，仍可被声明可信模块名的自定义子类仿冒，须采用实际精确类身份。artifact 在 resolve 校验前 mkdir，祖先 .corecoder-artifacts 被链接时会先在外部创建目录，应在创建前校验现存祖先及 Windows junction。成员构造/clone/chat_result 的普通异常应返回该成员 failed，不能使整个 run_team 丢失其他成员结果。预算/根取消、依赖和持久事件继续作为后续完整冻结项，不据初版测试宣称最终通过。

DAG 施工增量：plan gate 已改用实际 AgentTool/BrowserAgentTool 类身份，artifact 已在 mkdir 前检查全部祖先 symlink/junction 及 resolve，普通 Exception 已在成员边界隔离，三处初版问题主体修复。依赖验证覆盖重复/未知/自依赖/环，且先于启动；只注入直接上游摘要和引用，失败依赖转 skipped。未作为最终冻结通过。

剩余边界已告 Terra：异常成员结果及 skipped 缺稳定 id 等统一字段，应调度前分配并贯穿终态。根取消在 Agent.chat_result 中转换 cancelled 返回，不能仅依赖 future 的 KeyboardInterrupt；须区分根取消共享停止与局部取消分支跳过。预算异常当前向上抛会丢弃已完成团队结果，需先持久终态并保留快照。LLM 自有 retry 已新增 stop 检查，但无预算时 SDK 默认隐式 retry 仍可能绕过，应在团队作用域同样关闭 SDK 隐式重试。已发送流式请求仍按 finally 结算，不应描述为远端已取消。Web 持久事件与 UI 尚未冻结。

后端停止/持久冻结增量：稳定任务 ID 已在提交前分配，异常/skipped/cancelled 结果具统一基础字段；预算结果先进入成员产物与终态，再以携带 team_results 的 BudgetExceeded 上抛；Browser 根取消通过 session.check_cancelled 驱动共享停止。团队 SDK 隐式 retry 已关闭，实际已发流仍走原 usage finally。RunStore 拒绝父 run 终态后的 team_task，重启将 pending/running 标 interrupted，Service 的恢复 status 保存且不执行成员。

仍需修复后复核：CLI 真实 Ctrl+C 可在调度 wait/submit 外层触发，当前未统一设置 stop 并收集终态；落入 check_team_stop 的捕获时也未确保 stop.reason=cancelled，存在等待工作继续或错误返回风险。持久 publish 当前遗漏 evidence/usage/artifact_error，预算错误携带的完整结果未写入权威团队快照，需补受限完整字段。Sol 已复现正常并发目录 artifact_error=artifact directory is not a managed path；建议验证输入后单线程准备管理目录，避免成员并发创建时 Windows resolve 检查间歇失败，保持创建前祖先链接校验。上述均已直报 Terra，UI仍待审。

最终增量分项结论：artifact 目录预准备及共享 RLock 已加入，legacy 单 task completed 前缀已恢复；团队终态持久事件已含 evidence/usage/artifact_error。App 按稳定 task_id 与递增 seq 合并 HTTP 快照和 SSE，reset 替换权威快照，异步回调检查 session/run 身份且卸载中止订阅；依赖与跳过原因可见。以上静态通过，独验以 ACCEPTANCE 为准。

尚未关闭的精确取消窗口：submit 已入队/返回但 pending 登记之前，或 pending.pop 后 result 收集之前发生 Ctrl+C，外层虽设共享停止，却会把该索引补回 remaining 并标 cancelled；实际成员仍可结算/完成并写产物，而结果无法被收集。已请 Terra 用独立 worker 完成记录和 executor drain 后统一收集真实终态修复；该项关闭前不标阶段3整体静态 PASS。

最终 SIGINT 静态复核（2026-11-12）：corecoder/team.py SHA256 66a738eca065a10640c03138046b85c2b9bfba7adbeffee6bf26942a94953c2c。主线程且原处理器为 signal.default_int_handler 时，临时 SIGINT 处理器只写 signal_cancelled，不抛异步 KeyboardInterrupt；调度器和成员在协作检查点转换为共享停止。故上一段所述真实 CLI Ctrl+C 打断 submit→pending 登记或 pending.pop→result 收集的窗口，在该支持路径已关闭，无需将独立 worker 完成账本作为唯一修复方案。finally 恢复原处理器与 ContextVar。已提交 future 仍收集到终态，executor 退出等待已发工作结算；不宣称远端请求已被取消。该结论不扩展为任意自定义 SIGINT 处理器或任意异步注入 KeyboardInterrupt 的保证。阶段 2/3 已审后端及 UI 设计静态项通过，运行验收仍以最终冻结复验为准。

前端验收保留项：final-frontend.log 为 39 通过、2 失败；App.modes.test.tsx 的切换往返技能请求用例未建立 resolve，App.test.tsx 的慢轮询用例收到 2 次请求而预期 1 次。这两项位于本地累计新增功能/测试，不能凭该日志认定为既有基线失败。前者存在仅等待输入框出现、未等待会话初始化的测试时序可能；后者需核查预算轮询与团队快照是否重复请求同一 run。检查期间 App.tsx 正由实现者继续修订，因此此处不对未冻结前端给运行 PASS，待 Terra 定因修复及 Sol 完整复验后关闭。

最终前端静态复核：App.tsx SHA256 7d2bd7e4727796e55bf5584492e6389c4b127c6074022e61270b8f9e3a42e5f1。团队 HTTP 快照合并进既有预算轮询，移除独立重复 GET；仅 session/run 身份变化时清空团队状态，重新选择当前对话不再清空且无法重载。GET 与 team_task SSE 逐 task_id 按 seq 合并，身份检查隔离迟到响应。旧 reset 可能短暂覆盖较新 GET，但真实 RunStore 保留任务 ID，后续 SSE 从 reset 序号继续回放；终态后拒绝新的 team_task。因此未发现造成持久任务丢失或迟到 GET 复活已删除任务的实际路径，不将假设性的快照删除作为阻断项。静态通过与独立测试结论分开记录，此轮未重复运行测试。

基线更正：应以 .phase4-preparation/baseline 的精确远端 e36891e 副本为基线，而非当前 Git HEAD。已确认上述两个失败用例均存在于该基线，前段“本地累计新增”不代表本批新增；用例存在本身也不能证明失败已存在。慢轮询失败对应本批新增重复 GET，已合并修复；技能用例已补初始化及导航等待，是否原有时序不稳定以独验为准。最新运行结果由 Sol 写入 ACCEPTANCE，本文件仅给冻结代码静态结论。
