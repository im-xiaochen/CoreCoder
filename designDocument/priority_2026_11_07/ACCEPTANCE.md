# F06 / F10 / F18 独立验收

负责人：Sol（6.1 Sol high）。最终状态（2026-11-08）：**F06 PASS、F10 PASS、F18 PASS；受影响Python集成回归158 passed**。下方保留准备和开发历史，均以各模块最终结论为准。基线来源见STATUS.md；不将旧dirty文件归为本批新增。

执行口径：不调用付费模型，不加载用户MCP/hooks，不操作用户仓库的index/提交。真实Git和命令探针均使用独立临时目录。按新模块及风险受影响回归执行一次；修复后只必要定向复验。原上批完整功能证据可复用，不重复旧590套件。开发者自测与代码review不代替独立行为证据。

## 独立验收矩阵

| 编号 | 输入/触发 | 独立可观察断言 |
| --- | --- | --- |
| G01 | 无repo、空repo、有提交repo | 状态可解释；空仓无HEAD可审阅；读取不隐式init/stage/commit |
| G02 | 同一文件index部分修改+worktree额外尾行 | staged/unstaged分开；审阅index内容不混入未暂存尾行 |
| G03 | binary/delete/rename/untracked混合 | 路径、原路径、状态完整；binary明确、删除内容可审；新文件可选择 |
| G04 | 开始前dirty，期间clean文件改变、原dirty文件再外改 | 预脏与期间变化可区分；只能称期间变化/归属不确定，不声称agent独有 |
| G05 | 显式stage与commit分离；只提交已审partial staged，另有未选user-staged | commit只取已审index，未暂存尾行保留；未选staged可明确拒绝；不夹带。stage partial先提示会纳入余下worktree改动 |
| G06 | 审阅后index/worktree/HEAD外部漂移 | 提交冲突拒绝，不覆盖漂移，不静默采用未审内容 |
| G07 | leading dash/方括号/空格中文路径、../、绝对外路径、pathspec魔法 | 合法名称按字面处理；越界/参数注入拒绝；无额外文件/index副作用 |
| G08 | task/CLI/Web/刷新重启 | 审阅基线与来源可追溯；Web操作必须明确确认；busy/权限边界保留 |
| C01 | pass退出0；fail打印“passed”却退出7 | 只按实际exit/status判定，保留stdout/stderr及原始日志路径 |
| C02 | 未触发、空配置、错误JSON/字段/路径/未知check | not_run/config_error与passed分开；不执行未知/坏配置命令 |
| C03 | 项目命令写入哨兵且未授权/拒绝/超时授权 | 未授权不执行；配置不能自行提升权限；拒绝原因结构化 |
| C04 | wait真实心跳命令timeout/cancel | 结构区分取消/超时/未知/失败，心跳停止，日志完整或有截断说明 |
| C05 | 第一版无自动repair，手工重试保留原失败 | 默认修复次数0且失败后不调用模型；若后续提供循环才验证预算/上限N，不将未实现可选循环作为本批失败 |
| C06 | 任务完成自动触发与手工重跑/会话刷新 | 固定触发规则；不因恢复重放命令；CLI/Web结果与日志一致 |
| E01 | 固定bug/跨文件/长日志/恢复/取消/授权/Git审阅fixture | fixture/input/environment/version/budget标识固定；输出可重复；测试正确性与产物分别判定 |
| E02 | 已知/未知/缺失usage成本混合 | 未知不当0或“免费”；已知成本独立汇总，合成usage明确来源；报告断言缺失/失败不能作为门禁通过 |
| E03 | 离线默认/付费opt-in | 默认零真实模型/MCP/hooks/网络；离线系统脚本结果不标为模型成功率或竞品提升 |
| E04 | 人工干预、权限拒绝、interrupted恢复、失败任务 | 计入对应指标且分母明确；失败不从报告消失；无自动重放副作用 |

## 准备资产

- `acceptance/git_fixture.py`：真实隔离repo，固定作者/时间与分支，禁用户Git配置/签名/hooks，支持保存HEAD/index语义、index字节、工作树文件hash证据；不引用实现接口。
- `acceptance/check_fixture.py`：退出0/7、长日志、可停心跳、以真实文件正确性决定repair退出码。只在独立目录执行，不触真实模型。
- 具体公共接口由设计明确后编写独立断言；矩阵可以因明确范围调整，但拒绝夹带index、未审漂移、真实退出码、费用未知与零付费等核心断言不能放宽。

## 实际执行证据

已读Astra的F06/F10/F18设计并对齐：commit只取已审index；stage另行显式执行；无repo/空仓与冲突路径；chat不自动读Git/config；第一版F10无自动修复；F18只报告脚本化系统回归。当前仅准备；接口未就绪，尚未执行任何F06/F10/F18产品功能验收。后续按模块写实际命令、输出日志、缺陷与修复复验，并明确UI/平台局限。

准备自检：`E:\github项目\.corecoder-review-py312\Scripts\python.exe designDocument/priority_2026_11_07/acceptance/prepare_fixtures.py`，exit 0。两个新建Git仓库初始commit OID一致，混合状态与partial index版本符合独立预期；检查脚本真实退出0/7，失败输出故意包含“passed”。未导入或执行F06/F10/F18实现。日志式结构证据：`acceptance/validation/fixture-preparation.json`（含HEAD/index/文件hash/状态、stdout/stderr）。首次受限临时目录PermissionError属执行环境，使用授权离线权限后完成；未改用户仓库index或提交。

### F06核心API开发探针（2026-11-07）

状态：**开发中，非最终通过门禁**。直接测试`corecoder.git_workspace.GitWorkspace`公共status/baseline/diff/stage/commit；期间生产源码仍更新，未将工作区当前hash追认为完整已冻结测试版本。

命令：`E:\github项目\.corecoder-review-py312\Scripts\python.exe -m pytest designDocument/priority_2026_11_07/acceptance/test_f06_core_independent.py -q --basetemp designDocument/priority_2026_11_07/acceptance/validation/tmp-f06-development --junitxml designDocument/priority_2026_11_07/acceptance/validation/f06-development.xml`。

实际结果：**11 passed**，56.44秒，exit 0。独立断言覆盖：无repo不初始化；空仓首次提交保留提交后的未审worktree版本；partial staged两段diff且读后HEAD/index字节/文件不变；序列化任务基线后原脏文件同状态码下内容外改仍标期间变化、未知基线不冒认来源；未选user-staged不被夹带；已审index提交保留未暂存尾行与原index；worktree/index/HEAD三类审阅漂移拒绝且不覆盖外改；binary/删除/重命名/新文件可见与半边rename拒绝；前导横线/方括号/空格中文按字面stage，越界与pathspec注入拒绝。

证据：`acceptance/validation/f06-development.log`、`f06-development.xml`，独立用例`acceptance/test_f06_core_independent.py`。未检出本轮已证实行为缺陷，已直接反馈Terra。后续冻结时按实现改动复验必要范围；尚待Web接入、权限/模式、任务生命周期及其他设计边界，不将11个核心用例当成F06全模块完成。

### F06核心冻结增量 — PASS；Web整模块待验

独立增量 **6 passed**，9.49秒，exit 0；仅本轮filter/运行时上限修改范围，未重跑旧11或旧功能全套。`corecoder/git_workspace.py`跑前/后SHA-256均为`d461af9cc336314886e65d378c9ff81003ff67f70903b5b6af41dc8832144394`。

- 在真实Git仓库配置clean/process filter，外部脚本若执行会写哨兵。status/baseline/diff/stage均明确拒绝，哨兵不存在、用户index原始bytes不变；配置脚本由独立fixture提供，不加载用户配置。
- 真正的大文本Git diff超8MB被明确拒绝，没有把截断结果当成完整审阅。
- 对传输边界替换启动程序为真实离线持续stdout/stderr writer（写完后等待60秒），验证8MB stdout/8192-byte stderr上限在运行期间触发，3秒内返回、进程已终止、双输出pipe已关闭。该探针不伪造subprocess输出或退出结果。
- timeout使用同一真实子进程，将实际Popen.wait的30秒deadline缩为200ms以避免慢测；验证超时错误/终止/资源关闭，不宣称实测完整30秒墙钟期限。

命令：`E:\github项目\.corecoder-review-py312\Scripts\python.exe -m pytest designDocument/priority_2026_11_07/acceptance/test_f06_limits_independent.py -q --basetemp designDocument/priority_2026_11_07/acceptance/validation/tmp-f06-limits --junitxml designDocument/priority_2026_11_07/acceptance/validation/f06-limits.xml`。证据：`acceptance/validation/f06-limits.log/xml`，独立断言`acceptance/test_f06_limits_independent.py`。

核心门禁结合前轮11项、此次6项增量与Astra当前核心review，记为PASS；这是已覆盖核心API范围。F06全模块仍待HTTP、run基线持久化、模式与明确用户授权、UI及整合风险检查。未知归属、链接/子模块/并发边界未运行的部分不追认为已覆盖。

### F06 HTTP与任务基线 — PASS

独立实际loopback HTTP **8 passed**，29.38秒，exit 0。用例`acceptance/test_f06_http_independent.py`，证据`acceptance/validation/f06-http.log/xml`。首次运行完成后未重复跑整8；后续核对service/server文件hash仍完全一致：service=`b622dfc74165b859ccb502aa5b4a806f220998eb9019d3e1dec12950632960c9`；server=`866e25f1ff6fe3dd1b441999097c6db08d69d7609fb108fa2f57338c573b0b78`，故复用本次已执行结果。

覆盖严格`confirm:true`（null/false/string/1均403）、陈旧token409、HTTP stage与只含选中路径的真实Git commit/未选staged拒绝；chat/plan写拒绝与busy锁；chat run不实例化GitWorkspace且不捕获基线；基线在Agent副作用前捕获、重建Service后保留预脏/期间变化、绑定不同run时显示未知；执行锁获取后变更mode/会话identity/归档的3个竞争注入均拒绝并释放锁，HEAD/index/工作区不变。

全部在新建临时Git项目中执行；Agent结果使用明确离线替身，不调用模型或用户扩展。core与HTTP已覆盖范围通过，UI独立交互继续中。

### F06 UI — PASS（9个独立交互场景）

实际GitPanel/实际App与API替身，使用真实React DOM：首轮6个面板用例通过；实际App用例修正“先打开工作区面板/聊天模式不显示代码建议”前提后单项通过；新补refresh/stage generation两项通过。共9个不同场景，未重跑已过用例。初轮失败属于独立测试流程遗漏，未修改生产代码。名称过滤日志中的skip为未选择的无关用例，不是环境跳过。

覆盖：没有显式刷新不调用Git API；暂存需选择与确认（取消不发送），请求携带精确字面路径/token/confirm；partial再次stage警告；未选用户staged使commit禁用，选重命名自动包含源/目标；只读与不完整审阅禁用写入；新diff后的迟到旧resolve/reject不覆盖/报旧错；refresh或stage触发新审阅后待返回diff失效、选择清空；实际chat App启动与打开Git标签均无自动Git读取，手工刷新后仍禁stage。

独立文件`acceptance/f06-ui.test.tsx`与配置`acceptance/vitest.config.mjs`。证据：`validation/f06-ui.log`（首轮6过及测试前提失败）、`f06-ui-app.log`（单项通过）、`f06-ui-generation.log`（2项通过）。命令为本地Vitest带该独立config，后两轮分别仅`-t 'actual chat App'`和`-t 'invalidates pending diff'`。

GitPanel跑前/后hash均`19b4a9438898448baddacbcdbfe2fe8a5c2d6790d76743aafc2025af7f05ddd9`；App与Panel当次hash见`validation/f06-ui-hashes.json`。本轮无浏览器视觉验收；UI覆盖基于实际React/DOM。按已实现最小交付，F06核心+HTTP+UI独立门禁PASS，未检出未关闭功能缺陷；后续F10/F18整合变化只定向复验受影响接口，最终CI按影响范围执行。

### F10独立验收 — PASS（已确认范围与冻结hash）

仅本批新功能定向：核心11、CLI2、HTTP4、UI5，各组按自己的证据记录，不把这些与旧整套相加。未重新运行旧590或F06已过套件。

- 核心`test_f10_core_independent.py`：**11 passed**，11.89秒。真实命令退出0/7；即使退出7的stdout声称passed也保持failed；stdout/stderr及run/session/exit/status持久；默认repair_attempts=0；严格授权/配置token漂移/空选择/无匹配不执行；预取消、执行中cancel、1秒timeout均区分并验证心跳停；长日志保存、非法内部ID/超16MiB记录拒绝；坏JSON/cwd越界/timeout布尔/重复ID拒绝。对实际执行过一次的检查人工构造crash遗留running记录，在两个新Python进程recover后标interrupted、输出保留、原计数器不增加。这是新进程恢复验证，不声称进行了真实OS强杀。
- CLI`test_f10_cli_independent.py`：首轮真实REPL暴露局部state未赋值使/check崩溃，已直报Terra修复为agent.state；只复验原2项，**2 passed**，3.83秒。默认空回答不执行（即使工具allow_all），明确y执行真实exit7并持久日志，显示失败退出码。
- HTTP`test_f10_http_independent.py`：**4 passed**，10.60秒。真实loopback批量0/7与结构化run失败、日志内ID、重建Service后history/result重复读取不执行；严格confirm/空ids/空files/无匹配/旧token/chat配置读取/plan执行均拒绝且不生成completed空run；实际stop停心跳且取消日志可查；启动persist错误还原run_id/busy、释放锁且不启动命令。
- UI`f10-ui.test.tsx`：**5 passed**，2.44秒。实际ChecksPanel按需读取、准确命令/文件/token及显式confirm、等待权威attach期间不能重复POST、只读禁用、网络不确定时锁住重发并协调、明确4xx可重试；持久history与事件id去重、原样文本日志不执行HTML、迟到旧记录不覆盖最新结果。

实际命令分别为Python venv `-m pytest`显式指定上述独立文件，`--basetemp`及`--junitxml`置本目录validation；UI命令`node frontend/node_modules/vitest/vitest.mjs run --config designDocument/priority_2026_11_07/acceptance/vitest.f10.config.mjs`。证据：`validation/f10-core.log/xml`、`f10-cli.log/xml`（首轮失败保留）、`f10-cli-fixed.log/xml`、`f10-http.log/xml`、`f10-ui.log`；源码hash见`validation/f10-hashes.json`。

确认范围：root与Astra明确接受CLI轻量交付（配置/授权执行/实际日志）；CLI不扫描其他CLI进程可能仍活跃的记录，也没有历史/重启恢复交互入口。**历史/恢复PASS仅归Web和核心存储，不称CLI历史已交付。** 默认不自动修复，首版不要求新增模型修复循环。无浏览器视觉验收，UI为真实React/DOM。最终受影响回归/构建/lint将在F18整合后按影响执行一次，当前不重复无关套件。

Terra确认F10无在途或计划语义修改，按`f10-hashes.json`冻结；Astra静态review通过。当前已确认范围下F10整体PASS，CLI首轮崩溃已关闭；F18仍待独立验收。

### F18独立执行准备（2026-11-08）

状态：准备，**尚未执行评测CLI或判定F18通过**。实现仍在补隔离/留存证据，等待Terra ready/freeze。

- 独立driver：`acceptance/run_f18_cli.py`。每轮新输出目录，调用者HOME/配置/credentials均为验收新建假值，不读取用户MCP/hooks/真实密钥；调用cwd带无效MAX_TOKENS的假.env，扩展配置若被执行会留下独立哨兵。记录CLI退出码、命令、源码前后hash、假扩展执行与报告路径。
- ready后执行两个完整CLI轮次（normal及-O）、单任务筛选、实际错误产物/断言失败的非零退出。检查worker真实优化模式，不以仅外层-O替代优化worker实测。
- 外部oracle计划：从保留仓库读取Git真实提交blob/index与用户worktree、初态tree/commit；独立导入calc并运行多组addition（不依赖评测内自写verify.py）；检查检查结果真实exit7原档、Service退出后恢复的写入计数、usage实际session档的known/unknown/missing三类。证据路径须运行结束后仍存在。
- 两轮比较固定fixture初态和结果，不要求时间/PID/运行UUID相等。报告必须表明脚本化系统行为而非模型成功率/真实资金花费，未知费率不得按零价混合计算。
- 最后受影响回归与CI检查将和Terra分工，避免重复已冻结F06/F10或旧590套件。

### F18独立验收 — PASS（2026-11-08）

本轮实际执行公开CLI两轮：`E:\github项目\.corecoder-review-py312\Scripts\python.exe acceptance/run_f18_cli.py f18-normal`及同入口`f18-optimized --optimize`（脚本路径均相对本设计目录）。driver实际调用`python [-O] -m corecoder.evaluation --output <新目录>`。normal六任务通过、exit 0、16.09秒；优化轮六任务通过、exit 0、19.17秒；**worker自身**分别记录optimize 0和1。运行前后evaluation.py SHA-256均为`bc331f462e6fbd14eabf9c46e6c58e8771168e037f98518db164f4f7a6aa8c01`。两轮均带调用cwd假.env中的非法MAX_TOKENS、继承的非法MAX_TOKENS、假密钥、假Git配置及若执行就写哨兵的假MCP/hooks；正常完成且假扩展哨兵不存在，没有使用真实用户扩展或凭据。

独立外部oracle：`acceptance/verify_f18_independent.py`，实际读取运行结束后仍在的12个Git仓库与原始产物。各任务/两轮初始commit一致`174d68e26cc88745fde697d7721f68b452a6b4bf`，tree一致`294b36cfc5756ccb65f6cfc566371c0afc9f8458`；作者、固定日期及fixture分支均一致，不受假调用者Git配置影响。

- Git提交对象/index实际是staged版本，用户worktree仍later、untracked仍preserve；检查原JSON实际command=`exit 7`、failed/exited/exit_code=7。
- 不依赖任务自写verify.py：新Python进程独立导入实际calc.py，以零/负数/小数/大数五组输入核验正确加法，且第二个文件确实存在。
- 正常任务使用真实受管命令，取消判定须实际观察输出后才通过，结果cancelled、exit非零、truncated=true。该任务覆盖输出后取消，不声称独立证明所有OS进程树情形（上批F03另有证据）。
- 恢复任务真实Service/Agent写入后进程以23退出；原run元数据与journal保留，重启后interrupted。外部oracle确认原档run_id相符且effect仍为after-crash，能区别重复执行原写入。
- saved-session.json保留schema3三条完整usage：已知费率/成本字符串1与0.001，未知费率tokens=5而价格/成本为null，缺失usage的usage_known=false。报告分别计unknown和missing各1，整体真实花费unknown/null，合成0.001明确不是真实支付。

失败oracle不是信任报告自述：在新的隔离worker中仅替换工具写calc.py的内容为固定return 5，产品源码未改。实际自写verify.py仍exit0输出5；产品独立算术oracle必须拒绝该错误实现。normal与真正-O均**exit 1**、task failed，error为`independent arithmetic oracle failed`，错误文件与报告保留。证明优化模式不会移除门禁，也不会把“自写测试通过”当作任务正确。

另执行`run_f18_cli.py f18-filter --task usage`：exit0、1.29秒，报告/任务目录都只有usage，没有执行其他五项。报告标识offline-scripted、fixture/schema版本、预算与产物路径，明确脚本化行为回归不衡量模型质量/真实花费。未声明竞品排名或模型成功率。

证据均位于`acceptance/validation/`：`f18-normal/{driver.json,cli.log,output/report.json,output/tasks/}`、`f18-optimized/`、`f18-filter/`；`f18-independent-oracle.json/log`；`f18-negative-{normal,optimized}/{cli.log,output/report.json,output/tasks/}`；`f18-final-hashes.json`。外部oracle初次因独立脚本默认GBK解码UTF-8 journal失败，已改自身解码后通过，不是产品缺陷；汇总hash初次用了不存在的components目录，也仅修正独立汇总路径。未修改生产或开发者测试。

### 最终受影响Python集成回归 — PASS

只执行一次`E:\github项目\.corecoder-review-py312\Scripts\python.exe designDocument/priority_2026_11_07/acceptance/run_phase2_regression.py`，子命令与所有文件hash见`acceptance/validation/phase2-final-regression/driver.json`。离线fake HOME、空.env、私有存储运行；网络guard只允许loopback用于真实HTTP用例。

实际结果：**158 passed in 39.13s**，exit0；源码与README/pyproject跑前/后hash相同。显式选择新Git/checks测试、受影响REPL/slash/session/usage/command/modes/Web/run、workspace/cost/CLI-result/session-validation契约及两项文档行数检查。没有重复旧590整套，也没有重跑F06/F10已过独立套件。日志与JUnit为`acceptance/validation/phase2-final-regression/pytest.log`及`result.xml`。执行session 58059已完成，没有后台验收任务。

Terra另行执行CI范围ruff、compileall、前端81项测试与build均通过，其日志由实现负责人记录，本独立报告不将其计入Sol实测数量，也不再重复。F06/F10原门禁沿用上述证据；F10 CLI仍限配置/授权执行/日志，历史恢复仅Web/核心。当前F06/F10/F18没有未关闭独立功能失败。平台实测限本机Windows/Python3.12，UI为React DOM交互而非真实浏览器视觉；没有付费模型效果评测。
