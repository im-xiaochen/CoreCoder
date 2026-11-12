# 上游增量三切片独立验收

负责人 Sol（6.1 Sol high）。基线 main `e36891ed67305a0dc28e089f3ea78f077ed503cd`，沿用既有工作区与旧 dirty。本地验收复用旧批证据。**三切片独立验收、前端定向复验、生产构建与 wheel 冒烟通过。** 远端矩阵由 root 负责。

## 独立可观察矩阵

|编号|触发|验收结果要求|
|---|---|---|
|P01|真实SDK SSE reasoning_content分片+工具调用，实际Agent工具第二轮|仅同源有工具assistant的协议字段完整回传；delta.reasoning只显示，不把所有推理回送|
|P02|会话save/load、换模型/endpoint、旧档无来源、普通最终回答|正确来源历史可恢复；未知/不同源推理不发provider，普通final不落推理档，内部来源标签不进provider wire|
|P03|长历史触发真实摘要，含工具推理和内部来源|摘要输入不包含私有推理及内部来源标记；归档保持合法工具链；不能靠摘要fallback绕预算|
|P04|quoted/unquoted temperature 400、近似token名字、max_tokens/max_completion_tokens拒绝|独立token拒绝适配、有界fallback；max参数保持精确区别，所有实际请求分别过F08/F01|
|T01|真实公开team入口，0/1/8/9任务及并发阻塞记录器|合法8任务可执行；最多4在途；非法输入在启动/读文件/送模前拒绝|
|T02|实际子任务尝试write/bash/agent/MCP，自定义工具替代同名|只读暴露与实际权限门禁均生效；保持clone_for_state及现有mode边界，不能借team扩权|
|T03|物理provider线程同时竞争最后额度、缺usage、子实际usage|跨线程共享同run预算、父账本范围逐attempt一致；最多一份额度被预留；请求失败/unknown不伪装零费用|
|T04|多线程Permission.ask/always/save阻塞记录器|交互串行化，持久授权状态原子，锁外模型调用不整体串行，读请求无多余授权|
|T05|结构化task结果：completed/failed/max_rounds/refused/cancelled/budget_exceeded|task_id/status/finish_reason/usage/artifact明确，不依错误字符串判断成功；父/CLI/Web wrapper一致|
|D01|链、菱形、重复名、未知依赖、自依赖、环|校验在任何任务启动前完成；严格按拓扑启动，结构化前置结果仅传声明依赖|
|D02|单任务失败及独立分支|相关下游skipped且原因/依赖可追溯；无关分支继续；依赖报告有来源/大小限制，不能授权工具|
|D03|全局预算耗尽与取消，有任务在途/排队|停止整体新调度，保留已完成任务；不得将预算当普通task失败后继续其他分支；在途限制明确且无重复执行|
|D04|真实Web HTTP事件、刷新/断连、进程退出重启、实际App|team/task状态持久并按seq续传；重启interrupted/不重放；UI切session/run迟到隔离，结构化状态真实可见|
|R01|最后一次受影响集成|包括旧CLI/Web入口、既有子Agent预算、干净wheel/源码外加载；无关旧MCP/hooks/undo整套不重复|

## 执行约束

等待每slice生产freeze和公共接口后立即执行，实际记录前后raw SHA、命令、日志和局限。使用真正LLM/Agent/team/Service入口，provider仅离线SDK transport或显式fake物理调用记录器；不使用虚拟预算替代公共入口，不触真实付费模型或用户MCP/hooks/credentials。并发使用Event/Barrier观察真实在途数量，不以运行耗时推断并发上限。UI必须实际import App，避免只测纯helper。

报告和Astra首轮边界已读：本批不做远程MCP或并发写隔离。只读team成功不宣称并发写可用；本地Windows结果不替代多平台CI。准备与实现者自测不是独立通过，失败直接发Terra/Astra并抄root；修后仅风险目标复验。root授权持续至三个切片验收完成，准备阶段不结束整个任务。

## 切片1首轮：12通过、1产品失败

实际SDK/Agent独立文件`acceptance/test_slice1_independent.py`，运输观察器`sdk_fixture.py`仅注入实际OpenAI构造器与MockTransport，保留真实stream、LLM.chat、Agent read_file工具、F08及F01；会话/历史/归档都位于新临时workspace。

Terra原freeze c09217后Astra追加SDK有效端点/role审阅，再正式冻结`6d3f6d487471aa9658ca7990bbf7b9dd1622816b468b7675ec2e1b98827b89c0`供实际执行；首个未创建日志目录的shell重定向请求没有启动pytest，不算结果。创建目录并严格检查上述freeze后执行venv Python `-m pytest designDocument/priority_2026_11_10/acceptance/test_slice1_independent.py -q -p no:cacheprovider --basetemp=.../validation/tmp-slice1 --junitxml=.../validation/slice1.xml`，**12 passed、1 failed in 4.32s**，exit1。日志/JUnit=`acceptance/validation/slice1.log/xml`，跑前hash=`slice1-before.sha256`。

12项通过证据含实际SSE reasoning_content片段与工具第二轮、generic reasoning只显示/普通final不落推理档、来源标记不进wire、save/load后同源保留及换模型/端点不传、未知legacy推理不进正常或真实summary请求、OPENAI_BASE_URL两有效端点隔离、role变user后即使同源工具元数据也不传、已知私有推理不泄summary、近似参数名保持temperature/max_tokens不误改，以及request cap阻止第二运输。参数化用例不当成13项端到端成功率。

首轮唯一失败为真实SDK收到400 message=`Unsupported parameter: temperature`，第二次物理请求仍带temperature .7。原matcher不接受parameter后的冒号，仅走stream_options通用fallback。原独立断言保留，已直报Terra/Astra/root。

## 切片1修复复验

`corecoder/llm.py` 跑前、跑后 SHA-256 均为 `a51b347d207d8e30ae92fb38a7e31e909d96fbd54abb3d20616e0aec22a0b8c1`。仅运行原失败 pytest nodeid `test_temperature_fallback_real_requests_each_have_distinct_budget_attempt[Unsupported parameter: temperature]`。用 `E:/github项目/.corecoder-review-py312/Scripts/python.exe -m pytest`、`-q -p no:cacheprovider --basetemp=E:/github项目/.phase4-sol-colon-retest` 和对应JUnit输出路径执行，**1 passed in 2.46s，exit 0**。实际SDK第二个请求移除temperature，父预算与账本均各记两次。首个名称过滤命令语法有误，随后受限模式无法读pytest新建目录，均未得到用例结果。用已授权的本地离线执行及唯一临时目录完成复验。证据为 `acceptance/validation/slice1-colon-retest.log/xml` 和同目录 `slice1-colon-before/after.sha256`。

直接matcher补充风险用例 `acceptance/test_slice1_colon_matcher.py` 在同一版本 **6 passed in 1.96s，exit 0**，覆盖大小写、冒号周边空白、句号结束、两种复合temperature名称、旧max_tokens翻译与max_completion_tokens区分。证据为 `acceptance/validation/slice1-colon-matcher.log/xml`。首轮已通过12项未重跑。

## 切片2初版只读团队

Terra确认CLI和Browser初版可测试后复制 `corecoder/`、`web_backend/`、`pyproject.toml` 至隔离源码目录 `E:/github项目/.phase4-sol-team-source-initial`，在该目录执行独立入口测试。用 `E:/github项目/.corecoder-review-py312/Scripts/python.exe -m pytest <绝对路径>/acceptance/test_slice2_independent.py -k 'not last_slot' -q -p no:cacheprovider --basetemp=E:/github项目/.phase4-sol-team-initial --junitxml=<绝对路径>/acceptance/validation/slice2-initial.xml`。结果 **15 passed, 1 deselected in 3.86s，exit 0**。执行前对照原工作区和隔离快照，执行后核对隔离快照。同目录 `slice2-initial-before/after.sha256` 记录 team、工具、Agent、usage、Permission、Web Service 六文件。前一次命令的 PowerShell 循环管道解析失败未启动pytest。

实际 SDK 入口记录了合法 8 项由 4 个在途物理请求组成两轮、10 条父预算和账本记录、各成员单独用量与原始证据文件、结果声明顺序、含路径片段的任务名不能覆盖用户文件。独立测试还核对输入无效时零子请求和零产物、实际模型收到的工具集合、同名工具拦截、写/bash/再委派不可执行、父子 cwd/todo、项目规则、父级 hook 正常且子级禁用、Permission 并发授权持久化、计划/聊天边界，以及旧单任务与一人团队各自真实失败、轮数耗尽和取消。证据 `acceptance/validation/slice2-initial.log/xml`。仅全局预算停止竞争用例 `last_slot` 移到切片3，Browser实际入口和持久事件亦在切片3独验。本轮是隔离初版证据，后续验证覆盖修改影响。

## 切片 3 依赖调度与产物

独立 Python 验收通过真实 Agent/SDK 入口，使用离线 SDK transport，在隔离源码目录执行 pytest。首轮 **8 passed、2 failed、5 deselected**，通过项包含 5 种无效依赖图、计划模式拒绝仿冒类、链接目录无外部副作用、成员构造异常隔离。两项依赖报告断言检查了外层 JSON 的 Windows 路径转义。改为解码请求后检查实际子任务提示，失败分支通过，菱形仍偶发缺少 evidence。独立重复探针随后复现成员 `isolated` 成功但 `artifact_error` 为 `artifact directory is not a managed path`。证据见 `acceptance/validation/slice3-dag-initial.log/xml`、`slice3-dag-retest.log/xml`、`slice3-dag-artifact-stress-1.log/xml`。

管理目录预准备与并发路径校验修复后，首个定向轮次 **3 passed**，覆盖菱形、链接祖先和 8 成员并发。再以五个独立临时目录复测菱形，均通过。证据见 `acceptance/validation/slice3-artifact-fixed.log`、`slice3-artifact-fixed-{1..6}.log/xml` 和 `slice3-artifact-fixed-before/after.sha256`。该轮 `team.py` 为 `f198c8ad…`，`tools/agent.py` 为 `9cc0a9e9…`。

## 切片 3 HTTP 持久状态与根取消

实际 HTTP/SSE 与 SDK 请求记录器的定向集合 **10 passed、22 deselected in 13.63s**。覆盖只读工具边界、断连后按序继续、日志压缩后的权威重置、真实子进程终止和重启后的 `interrupted`、零重放、全局预算最后一份跨线程竞争、缺失 usage、等待任务未启动、停止发生在可重试发送时、失败分支和链接祖先边界。证据见 `acceptance/validation/slice3-web.log/xml` 和 `slice3-web-before/after.sha256`。该轮 `team.py` 为 `f198c8ad…`，`tools/agent.py` 为 `122ba0c5…`。

`test_cli_root_interrupt_stops_running_members_before_sdk_retry` **1 passed in 2.26s**。控制线程等四个子请求实际发出后，向主线程注入 `KeyboardInterrupt`，随后释放会返回 503 的请求。Agent 根结果为 `cancelled`，四个已发子请求保留结算，队列成员没有发送且无 retry，父预算和账本均记录五次请求。该用例覆盖 CLI 使用的根 Agent 边界。证据见 `acceptance/validation/slice3-cli-interrupt.log/xml` 和 `slice3-cli-interrupt-before/after.sha256`。该轮 `team.py`、`tools/agent.py`、`llm.py` 的 SHA-256 与本地最终源码一致。

## 最终回归与构建

- Python 受影响集合 **57 passed in 4.43s**。选择清单为 `acceptance/final-python-selection.txt`，包含 CLI JSON 与 REPL、Web HTTP/SSE 和恢复、chat/plan、父子共享预算及持久用量。证据见 `acceptance/validation/final-python.log/xml` 和 `final-python-before/after.sha256`。该轮 `team.py` 为 `a750555f…`，早于最终取消修复。最终 `team.py` 为 `66a738ec…`，另有上述定向根取消证据。
- wheel `corecoder-0.8.0-py3-none-any.whl` 在独立源码目录构建，安装到隔离目标，从源码树外运行 `acceptance/wheel_smoke.py`。验证实际加载位置、`corecoder.cli:main` 入口、团队 schema 与任务验证、CLI 零额度 `budget_exceeded`、exit 1 和零物理发送。产物 SHA-256 `751b270c262c452d139780e5924adb427861564b51c675f8fd12dbefc024c5dc` 已重新核对。源码记录见 `acceptance/validation/final-wheel-before/after.sha256`，五个记录文件与当前源码一致，日志见 `final-wheel.log`。验收环境为本地 Windows，模型运输离线。
- App 独立验收首轮 **2 failed**，缺少团队行。复验中权威终态抵御旧事件的用例通过，切换会话用例未观察到新 run 订阅。再对切换会话定向复验 **1 passed、1 skipped**。证据见 `acceptance/validation/slice3-app.log`、`slice3-app-retest.log`、`slice3-app-switch.log` 及对应哈希清单。
- 前端受影响旧回归首轮 **39 passed、2 failed**。技能列表用例在初始化结束前提交，测试未发出 `/skills` 请求。慢轮询用例发现产品发出两次 run GET，分别来自团队状态加载和既有预算轮询。前端现通过一次请求读取预算和团队状态。首轮证据见 `acceptance/validation/final-frontend.log` 和 `final-frontend-before/after.sha256`。
- 最终 App SHA-256 `7d2bd7e4727796e55bf5584492e6389c4b127c6074022e61270b8f9e3a42e5f1`。用 Vitest `--maxWorkers 1 -t 'ignores a response after switching away and back|waits for a slow poll and stops after terminal response'` 运行两个旧失败用例，**2 passed、29 skipped**。从仓库根用 Vitest `--config designDocument/priority_2026_11_10/acceptance/vitest.config.mjs` 运行两个团队用例，**2 passed**。团队测试文件在这一轮前后有追加断言，追加部分另行定向验收。`npm.cmd run build` 通过 `tsc -b` 与 Vite 生产构建，生成 `dist/index.html`、CSS 和 JS。沙箱初次启动 esbuild 时返回 `EPERM`，授权离线执行后成功。日志见 `acceptance/validation/final-frontend-retest-existing.log`、`final-frontend-retest-team.log`、`final-frontend-build.log`，源码哈希见 `final-frontend-retest-before/after.sha256`。
- 团队终态用例增加了同一空闲会话的重新选择。等待真实会话 GET 完成后仍显示 `completed` 行，整个用例的 run GET 计数为一次。沿用团队 Vitest 配置并以 `-t 'keeps an authoritative terminal row'` 执行，**1 passed、1 skipped in 3.03s**。日志及匹配的跑前跑后 SHA-256 见 `acceptance/validation/final-frontend-idle-reselect.log` 和 `final-frontend-idle-reselect-before/after.sha256`。
- `/skills` 延迟响应用例再补强操作时序。提交前等待发送按钮启用，逐次观察乙、甲在侧栏成为当前会话，再释放甲的旧响应。以 Vitest `run src/App.modes.test.tsx --maxWorkers 1 -t 'ignores a response after switching away and back'` 复验，**1 passed、11 skipped in 3.09s**。日志及匹配的跑前跑后 SHA-256 见 `acceptance/validation/final-frontend-skills.log` 和 `final-frontend-skills-before/after.sha256`。
