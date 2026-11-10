# F09 / F11 / F08 独立验收

负责人：Sol（6.1 Sol high）。2026-11-10最终状态：**F09 / F11 / F08 本批本地实现、独立行为验收及受影响集成收尾PASS；Astra最终增量静态PASS。未发布或合入GitHub。** F08证据分组为核心26项、OpenAI真实SDK离线11项、LiteLLM拒绝/默认兼容2项、Agent共享6场景、CLI/Web及真实重启12场景、App交互8场景，不将异质或重叠复验计数相加。下文保留准备/失败历史，以最终模块节和最终结案节为准。基线remote main `5c6d4ed`，沿用工作区，不重置旧HEAD/已发布dirty；不把旧dirty当新变更。只编辑本批独立资产，不修改产品或实现者测试。

报告相应F09/F11/F08条目及Astra正式PLAN/F09/F11/F08/REVIEW设计已读，并已直接对齐边界。实现freeze之前不执行新功能。禁止真实付费模型、用户MCP/hooks/密钥。所有Git、辅助文件、配置和provider响应使用独立fixture；网络请求只允许本地HTTP。各模块冻结后记录源码hash与实际日志，发现直接Terra/Astra并抄root，修复后只必要定向复验；受影响回归最后一次，不重复旧158/590套件。

|编号|独立触发|可观察断言|
|---|---|---|
|S01|多层.gitignore、否定例外、默认排除目录、特殊文件名|grep/glob/map不泄漏忽略内容；例外保留；直接传文件也不绕过凭证/链接边界|
|S02|固定mtime、同路径排序、两页/末页、非法offset/limit|页间不重复/不漏固定fixture目标；有下一offset及截断原因；bool/负数/超上限不是无界请求|
|S03|Python跨文件import/class/def+其他语言文件|从实际工具入口可定位消费者、定义、引用所在路径与行号；地图不宣称解析了未支持语言符号|
|S04|查询后编辑/删除/新建文件、改变ignore|实时重扫反映现状；无不可见过期缓存；跨页变化局限有文档，不将变化前后页边界当稳定快照|
|S05|无rg、无rg/git、大文件/二进制/超宽行/扫描上限|降级可解释且仍不泄漏忽略或越界内容；输出、扫描及子进程有界，不能静默当完整结果|
|S06|CLI/Web/子Agent工具与chat|新工具注册模式正确；code/plan使用公共实现；chat在发现前零文件访问；不执行仓库脚本|
|S07|固定跨文件定位任务旧/新对照|记录实际用时、返回字符/近似token、发现的正确路径；不将单fixture推广为普遍提速|
|K01|项目skills列表、内置review/test/PR-summary|列表只元数据，未调用前不读正文或辅助文件；调用后来源/版本/范围可追溯|
|K02|技能正文与显式辅助文件预算恰好/超限|预算包含正文与辅助内容，超限整份拒绝或按设计明确跳过；不能静默截断指令|
|K03|重名项目技能/内置模板/已有slash冲突、坏frontmatter|冲突/解析错误可见，不能覆盖已有命令或悄悄选错来源；重复加载不无限累积|
|K04|显式合法辅助文件、../外部、绝对外部、symlink/junction、敏感文件、二进制|安全文件可读；其余明确拒绝且外部/敏感正文不被打开或注入上下文；不执行辅助脚本|
|K05|技能要求allow_all、shell写入或扩权；chat技能|原工具权限/模式保留，拒绝实际写入；chat项目技能发现前零IO；不假定文档中的allowed-tools能授权|
|B01|请求数/Token/Decimal费用分别恰好及超限|每次物理provider调用前预留，超限时provider recorder计数不增加；剩余/已花/在途分开|
|B02|parent→child及两子并发竞争最后一份额度|共享同一预算且原子预留；最多一方进入provider，不靠事后统计阻止第二调用|
|B03|真实LLM客户端离线transport注入retry/fallback/summary|每个物理尝试都有独立记账与预留；失败重试也计请求；摘要不绕过预算|
|B04|已知/未知价格与usage缺失|费用上限+未知价格默认零调用拒绝；显式allow才放行并显示不确定；缺usage保留未知预留、不虚构0花费|
|B05|provider在途阻塞、取消、预算耗尽/配置更改|预留仍可见，禁止启动新请求；取消不能声称撤销供应商已接受请求；实际完成后结算不双计|
|B06|会话恢复/导入、Web预算展示与CLI配置|预算/账本关系遵循设计，旧档不虚构剩余额；非法负数/NaN/布尔上限拒绝；显式说明最终账单无法绝对硬封顶|

## 准备资产与边界

`acceptance/retrieval_fixture.py`构建固定Git初态和跨文件包，独立记录期望路径/符号，提供ignore/二进制/超宽/多页哨兵，不调用产品。`acceptance/test_f09_independent.py`为首批真实工具断言，待实现冻结才执行。`acceptance/skill_budget_fixture.py`提供技能文件及物理provider记录器，接口明确后接到真实入口；尚未执行不计为通过。

F09当前盘实时扫描、无常驻索引是Astra确认范围；以修改后再次查询实际变化验证新鲜度，不要求不存在的索引服务。Python轻量符号图不等于完整语言服务器。F11内置模板和项目技能按需加载，不自动赋权。F08未知价格默认拒绝受费用限额请求，显式allow允许不确定费用；in-flight预留不是供应商最终账单保证。详细数值/事件契约由正式设计锁定后补充，不镜像实现私有字段。

设计已确认的具体验证边界：F11正文/显式辅助资料合计32KiB、辅助最多8份、一次一层，按正式实现公开值核对；wheel内置模板可用。F08是每顶层run一个预算控制器，默认上限均空；requests/total_tokens零禁止，cost为十进制定点字符串，reserve/settle按attempt ID幂等；SDK/适配层隐式retry在启用预算时不得绕过物理尝试门禁。需新增无底层计量适配客户端显式拒绝、供应商usage超过预留保存超额并阻断后续、重启未确认预留未知而不重放等断言。尚无执行结果。

## F09恢复准备（未执行）

实现负责人已切为Terra_phase3。已核对公共接口仍是grep(pattern,path,include,offset,limit)、glob(pattern,path,offset,limit)、repo_map(path,query,offset,limit)，返回文本含next_offset/incomplete/backend；原9项准备断言适用，不依据实现者5项测试判独立通过。

新增`acceptance/test_f09_boundaries.py`四项准备边界：无rg真实Git后备的嵌套ignore/直传文件；无rg/git时不能安全解释ignore而明确拒绝；真实外部junction（POSIX为symlink）、未归一化link/../src及绝对外部路径三工具零canary泄漏；超大/超宽/AST行限制与灾难Python regex实际超时。均尚未运行。

`acceptance/run_f09_comparison.py`将同一260-file固定Git仓库的两个跨文件查询各对旧/新执行一次，分别用隔离子进程并检查实际import路径。旧源来自root已核验与main一致的`.phase3-preparation/baseline/`；记录时间、输出字符、chars/4近似token及完整结果。不是通用性能结论，不调用模型。尚未运行。

Terra明确尚未冻结，待其链接测试及兼容断言完成再正式执行。Astra当前因线程限制不能继续静态审阅，**静态gate未通过**；后续行为实测与静态审阅分开记录，不用行为通过替代静态gate。F11/F08仍准备阶段。本轮没有测试执行或通过数字，没有改产品代码。

## F09独立行为验收 — PASS；静态gate仍pending

Root允许执行当前生产切片，Terra_phase3确认生产冻结、仅补兼容测试。真实离线独立结果如下，不复用实现者自测计数，不重跑旧全套。

|证据|实际结果|独立覆盖|
|---|---|---|
|test_f09_independent.py + test_f09_boundaries.py 首轮|13 passed，24.24秒|grep/glob四页稳定无重复，嵌套ignore与否定例外，敏感/二进制直传不泄漏，跨文件Python定义/导入与修改/删除后实时重扫，非法分页；无rg Git后备、无rg/git明确拒绝ignore；真实junction/raw link/../src/绝对越界三工具拒绝且外部canary不变；超大文件/宽行/AST行限制与灾难正则5秒worker截止明确incomplete|
|test_f09_entrypoints.py|4 passed，7.58秒|真实REPL plan调用repo_map、子Agent复用正常工具、chat即使模型要求map仍无项目Path.open且无tool schema、实际loopback HTTP plan run真实工具返回src/consumer.py:3|
|test_f09_boundaries.py -k actual_large_inventory|1 passed、4 deselected，34.21秒|真实新增5005文件，超扫描预算的负结果明确incomplete（可先触发worker deadline），glob offset4999仅返回1项并标scan incomplete|

命令均为`E:\github项目\.corecoder-review-py312\Scripts\python.exe -m pytest`显式选择上列文件/名称，`-q --basetemp <本批acceptance/validation/tmp-f09-*> --junitxml <同目录/f09-*.xml>`；日志及JUnit：`acceptance/validation/f09-first.log/xml`、`f09-entrypoints.log/xml`、`f09-large-final.log/xml`。large初轮严格要求先触发file scan limit，但本机先触发了明确timeout/incomplete；修正独立断言接受设计允许的两类预算耗尽，再只复验该项。原日志`f09-large.log/xml`保留，不是产品隐瞒截断或吞错。4 deselected是名称过滤，不是环境skip。总计18个不同场景的证据分组记录，未重跑前17项。

首轮6个检索相关文件跑前/后hash一致，最终也未改变；原始hash证据`validation/f09-before.json`、`f09-after.json`。当前8个相关生产hash见`validation/f09-final-hashes.json`，repository_search=`01646a28081a`、worker=`9fcffae161ec`、grep=`ec900fb1cdbf`。只读核验，未改生产或实现者测试。

### 固定旧/新定位对照（不宣称提速）

实际命令：`python acceptance/run_f09_comparison.py`（相对本设计目录）。同一260-file fixture、初始tree `3a99eb52fe07e88a3fcfadb7d2359cac29e622c3`；两个独立子进程分别导入`.phase3-preparation/baseline/`和当前产品，均核验实际module路径。每版本每查询仅一次，绝对观测如下：

|查询（path=src/include=*.py）|旧耗时|新耗时|旧/新输出字符|旧/新chars÷4近似token|
|---|---|---|---|---|
|calculate|0.012903秒|0.459451秒|460 / 485|115 / 122|
|Ledger\|invoice|0.002747秒|0.408426秒|446 / 471|112 / 118|

两版都找到定义与跨文件消费者，结果路径/行号一致。此小样本新版因隔离扫描/枚举明显慢于旧版，新增backend/live元信息增加25字符，**不支持提速或降低上下文的结论**。token仅字符代理而非模型计量；Windows单次测量不外推其他平台或大仓库普遍性能。完整输出与环境路径见`validation/f09-comparison/comparison.json`及old.log/new.log，driver日志`f09-comparison-driver.log`。5005文件场景总34.21秒含fixture构建与多个查询，也不是纯检索耗时指标。

当前行为门禁无未关闭功能失败；边界覆盖本机Windows/Python3.12、真实本地工具/REPL/HTTP和离线ScriptedLLM，不调用真实付费模型/用户扩展。没有独立证明所有文件替换TOCTOU或所有平台竞态，亦未实测枚举器自身5秒超时的每种后端故障。Astra因线程限制尚未完成当前版本静态审阅，**F09整体静态gate继续pending，不能把独立行为PASS写成静态PASS或发布gate已全通过**。F11/F08等待新实现冻结后另行验收。

## F11核心首轮 — 18通过 / 1失败，待修复

上轮执行请求被自动审批服务usage limit拒绝，动作未执行，不把准备文件当结果。本轮实际确认skills.py仍为冻结hash`a11b0bd4ab0a5b919bf112d6d7102f3666772a02af575c536f716a22fe95a0c2`后重新提交授权范围内同一审批请求，正常执行成功。命令venv Python `-m pytest designDocument/priority_2026_11_08/acceptance/test_f11_core_independent.py -q --basetemp .../validation/tmp-f11-core --junitxml .../validation/f11-core.xml`；日志`validation/f11-core.log/xml`，**18 passed、1 failed，3.64秒**。未执行旧F09/其他整套。

通过：list阶段将Path.open替换为必失败探针仍成功，证明无正文/辅助读取；内置三个来源可见、项目同名冲突与显式source选择、非法名称；缺版本内容hash及重新resolve后反映文件变化；真实UTF8多字节+CRLF metadata正文与8aux合计恰好32768 bytes成功、多1字节拒绝；9aux/重复aux/重复metadata/未知字段拒绝；凭证、密钥、NUL二进制、坏UTF8、父目录路径拒绝，父目录外文件零打开；真实skill目录与skills目录snapshot后junction替换拒绝；恶意正文与附带脚本只作为文本返回，未执行哨兵。核心仅返回文本，此项不追认为实际Agent权限/schema已验（等CLI/Web入口）。

失败：`test_snapshot_rechecks_real_redirect_at_all_directory_levels[workspace]`。独立快照取自真实project目录，之后将该目录移至saved-original，原project路径建立junction到external，external含同结构技能与EXTERNAL CANARY。resolve_skill未抛错并载入该外部镜像技能。独立fixture finally只移除junction、不删除外部目标，产品未改。已直报Terra/Astra/root；核心entry.anchor自身未检查重定向，不能只检查anchor下级组件。待修复后仅该失败及修复影响边界复验。

F09复审新增两条由Astra提出，待实现修订冻结后增量核对：Windows venv正则worker使用base解释器/真实超时后结束；无rg但有git、工作区并非Git且无ignore应可搜索，遇不能解释ignore继续拒绝。原18个行为证据仍保留，不重跑无关项。

### F11信任锚修复复验 — 核心行为PASS，入口待验

Terra修复resolve对anchor自身链接及canonical路径重查，skills.py新hash为`0d88612826adbccacdea840fa4a2e2d68afeb742650ba27ebee24260d72d502b`。独立保留原工作区junction失败断言，并额外加入工作区上层祖先目录被替换为junction的同类边界；只执行`test_f11_core_independent.py -k 'snapshot and (workspace or ancestor)'`，**2 passed、18 deselected，2.46秒**。日志/JUnit=`validation/f11-anchor-fixed.log/xml`，basetemp=`validation/tmp-f11-anchor-fixed`。独立fixture仅重排自己新建目录以容纳祖先测试，没有改变外部读取必须拒绝的标准。测试只移除新建junction，外部目标保留。

原18个通过场景未重跑；结合原失败修复与新增祖先边界，20个不同核心场景现有实际通过证据。workspace/ancestor外部镜像读取问题已关闭，当前核心行为无未关闭缺陷。当前核心和3内置文本raw SHA256见`validation/f11-core-hashes.json`。这不代表CLI/Web集成通过，也不将“核心返回文本而不执行”算成真实Agent工具权限或chat零IO已测；后续按入口冻结补验。独立wheel安装/资源验证尚未执行，开发者证据不冒计为Sol覆盖。

### F11复审追加pre-list根重定向 — FAIL待修

Astra在上述20项结束后新增长期session场景：工作区保持原Path，但在下一次list_skills之前整个根已被junction替换。独立新增`test_long_lived_workspace_redirected_before_new_listing_is_rejected`单项实测**1 failed、20 deselected，3.55秒**，日志/JUnit=`validation/f11-prelist.log/xml`。list_skills开头resolve丢弃了原信任边界，发现外部镜像outside-only技能而未拒绝。旧snapshot后resolve锚问题仍已关闭；新增此失败使核心门禁重新pending。已报告Terra/Astra/root，只新增该项不重复20。CLI/Web切chat竞争和draft保存另待入口冻结。

### F09静态审阅修复独立增量 — 2 PASS

仅运行`test_f09_review_increment.py`，**2 passed in 8.01s**，不重复原18。命令venv pytest显式该文件，basetemp=`validation/tmp-f09-review`，日志/JUnit=`validation/f09-review.log/xml`。

1. 实际昂贵Python正则，真实worker在执行原可信脚本前额外写PID/解释器哨兵（只在独立Popen传输观察器替换bootstrap，产品未改，regex仍由原worker执行，timeout仍实际5秒）。PID等于受管Popen.pid，原启动命令确为Windows base解释器，返回timeout/incomplete后该PID poll已退出；finally仅防御清理本次捕获的PID。不是伪造TimeoutExpired或只测返回字符串。
2. 实际git安装、屏蔽rg发现，使用系统临时目录确保不位于产品Git祖先下；纯非Git无ignore命中真实canary并标backend walk，新增.gitignore后明确拒绝且无canary泄漏。

冻结源码核对：grep=`4eb4710c1d879f0e56dcd1474ec0cdf326f192c29ca37a4613f6ebca91a86c1d`；inventory=`7fa4345ed02b869775edcddc60ff38df4eabc6c01aea759d8465f5ddac2e7ee10`。行为增量无未关闭失败；静态最终gate由Astra确认，不代为标通过。此后性能变化未重跑原对照，前表时间只归原已注明hash版本。

### F11 pre-list修复 — 核心行为PASS恢复

Root确认skills.py `260f80e816f42762a022495bffcb62b1b68237d32b46002c7a01eabdc1963312`冻结供复验。只执行`-k long_lived_workspace`，**1 passed、20 deselected，2.25秒**；原21项之外不新增业务范围，原20个通过用例未重跑。日志/JUnit=`validation/f11-prelist-fixed.log/xml`，basetemp=`validation/tmp-f11-prelist-fixed`。pre-list工作区根junction现在明确拒绝，新增缺陷关闭；结合前轮20项实际通过，21个不同核心场景都有证据。最终核心/3模板hash见`validation/f11-core-final-hashes.json`。CLI/Web仍在实现收尾，尚未正式冻结或独立验收，不将核心PASS写成F11整模块PASS。

## F11 CLI / HTTP入口独立验收 — PASS；前端待验

Root授权冻结后端先验，Terra确认skill_commands=`dd9bf3042a21`、CLI=`cf28cc3dcc16`、Service=`f6259a6878af`、server=`69e895752791`，与独立跑前hash一致。只新增`acceptance/test_f11_entrypoints.py`，不重跑核心21或F09。

- 实际REPL `/skills`不会resolve正文、不调用模型；实际显式`/independent first task`送入正常Agent消息，含source/version/files/原任务/辅助正文。plain followup保留历史但不额外重复注入，第二次显式技能调用才增加第二份；system prompt未持久改写，Permission无额外allow_all/always。
- 实际loopback HTTP无API Key且无llm factory时，code/plan的GET skills均200且只metadata；Path.open探针确保不读SKILL.md/aux。chat列表和显式技能在discovery前拒绝；坏aux加载不创建run、不发模型、busy/lock不遗留。
- 实际HTTP plan技能即使正文要求扩权，write_file仍拒绝；另独立code模式真实write_file出现pending_permission，文件尚不存在，HTTP明确deny后仍无写入/永久授权。
- 技能加载函数用Event暂停时，实际HTTP mode→chat与delete均409，被execution锁挡住，未送模前mode不变。另在start取得旧session、获取锁前注入实际delete方法赢得执行，start拒绝且不读取技能、不创建run、不复活会话。

实际结果分组：首轮**3 passed、5 failed**（10.60秒）；5失败均因独立HTTP夹具函数名遮蔽http模块，尚未真正发请求，非产品失败。只修自身导入为HTTPConnection并选择`-k test_http`，**5 passed、3 deselected，6.20秒**；原3已过不重跑。新增code权限单项`-k test_http_code_skill`，**1 passed、8 deselected，4.06秒**。合计9个不同入口场景有通过证据，不把三轮数字简单相加。

命令均venv `-m pytest`显式独立文件，basetemp置`validation/tmp-f11-entrypoints`/`tmp-f11-http-fixed`/`tmp-f11-code-permission`；日志/JUnit为`validation/f11-entrypoints.log/xml`（保留夹具错误）、`f11-http-fixed.log/xml`、`f11-code-permission.log/xml`。全程fake ScriptedLLM、隔离文件与扩展关闭，无真实付费模型/用户hooks/MCP。5个后端文件跑前后hash未变，证据`validation/f11-entry-before.json`及`f11-entry-final-hashes.json`。

前端Astra新发现旧/skills GET结束后finally可能解锁后续POST，正在交Terra收尾；尚未开始独立UI验收。builtin slash命令同名技能的列表冲突展示已提请Astra核对，暂不冒称此边界已验证。核心+后端PASS不能代替前端gate，F11整模块仍待完成。

### F11最终入口与App定向增量 — 独立行为PASS

上述前端与保留命令待验项现均关闭。新增`acceptance/f11-ui.test.tsx`通过实际App、React DOM与API替身独立交互：列表仅GET不POST；显式来源和冲突可见；编辑draft后旧成功/失败结果均不能覆盖新draft或显示过期提示；切会话保留目标draft；chat本地短路不GET/POST；同会话切plan后旧响应无效；尤其旧skills GET晚归时，新的技能POST仍pending，发送按钮保持disabled，程序化再次submit也不能发第二次POST。

UI首轮报告7 tests passed，但末项mode change的独立action替身遗漏sessions，产生未处理异常，进程exit 1；不将该首轮称完整通过。只修独立替身补齐sessions，再`-t 'mode change'`复验：**1 passed、6 skipped（名称过滤），0 errors，2.82秒总耗时/341ms测试耗时**。其余六项未重复，七个不同交互均有无错误通过证据。原日志`validation/f11-ui.log`和修正日志`f11-ui-mode-fixed.log`保留。

新增保留slash冲突单项实际CLI+HTTP：`/help`仍为内置命令且不送模；列表明确显示`/help@.corecoder/skills/help`及conflict；HTTP无来源隐式调用拒绝、不创建run；显式来源调用经正常Agent且只送模一次。`test_f11_entrypoints.py -k reserved_slash`：**1 passed、9 deselected，3.63秒**，日志/JUnit=`validation/f11-collision.log/xml`。配合原9个入口场景，现有10个不同CLI/HTTP场景PASS，不简单相加重叠复验。App列表采用最终command字段，原列表交互追加准确`@source`拼写断言后只选`-t 'lists metadata'`：**1 passed、6 skipped，0 errors，2.75秒总耗时/330ms测试耗时**，`validation/f11-ui-command.log`。

命令UI为`frontend/node_modules/.bin/vitest`，指定独立`acceptance/vitest.f11.config.mjs`与上述文件/名称过滤；只执行本批独立交互，不运行旧前端整套。最终9个相关文件raw SHA256见`validation/f11-final-hashes.json`：skills=`260f80e816f4`，adapter=`e2ed2ef012cb`，CLI=`cf28cc3dcc16`，Service=`db844e66449b`，server=`69e895752791`，App=`e271b6503c66`，三模板与核心已验版本一致。全部与Terra最终冻结契约相符，历史hash证据保留未覆盖。

F11独立行为结论：核心21个不同场景、CLI/HTTP10个、App7个分别PASS；不将这些异质计数当通用成功率。没有未关闭独立功能缺陷；静态门禁由Astra负责。覆盖Windows/Python3.12、本地HTTP和React DOM/API替身；未独立执行wheel安装或真实浏览器操作，wheel仅实现者有证据；没有调用真实模型或用户扩展，也未改产品/实现者测试。F08尚未验，不能把本节当整个第三批已完成。

## F08纯核心准备 — 尚未执行

新增`acceptance/test_f08_core_independent.py`，仅导入公共BudgetLimits/RunBudget与F01 UsageEntry：16线程同时争最后一份共享额度、三类0cap、Decimal每百万费率精确边界、真实账本费用优先于估算、实际超估保留与后续阻断、未知价默认拒绝/显式放行及未知剩余、已发缺usage保留/可证明未发释放、重复预留与结算幂等、快照深复制、JSON恢复时在途转未确认不重放，以及负数/布尔/非有限金额/坏state和错误usage拒绝。

本轮原核心freeze `5a485b6f48af`存在Astra指出的同总token但input/output构成变动未拒绝问题；独立测试已明确覆盖该冲突。Terra要求等待此修复后新freeze再执行，故本节没有测试通过数字，不以实现者7项PASS代替独立结果。LLM物理attempt、retry/fallback、子Agent/summary与Web/CLI预算入口尚未冻结，均未验。

### F08核心修订版独立执行 — 26 PASS

Terra修复相同attempt_id对input/output分别比较、恢复预留合计校验后冻结`corecoder/budget.py` raw SHA256=`ce5d8f69cc987df974367fa314df81e4d120821dfd4e0649a6795763357f3d20`。实际执行venv Python `-m pytest designDocument/priority_2026_11_08/acceptance/test_f08_core_independent.py -q -p no:cacheprovider --junitxml=designDocument/priority_2026_11_08/acceptance/validation/f08-core.xml`，**26 passed in 2.29s**，exit 0。日志/JUnit=`validation/f08-core.log/xml`，跑前SHA=`f08-core-before.sha256`，跑后相同hash=`f08-core-final-hashes.json`。

本次覆盖上节全部核心断言，16个真实线程用Barrier同时争最后一份额度，确实仅1预留、15拒绝；同总token但输入/输出互换明确拒绝。费用独立计算每百万定价边界，并以独立构造的F01 UsageEntry不同实际费用验证真值替换，未从实现公式生成期望值。负数/布尔/非有限金额及坏恢复记录按参数分别验证；因此26是含参数边界的用例计数，不等同26个独立端到端功能。原`5a485b6`没有执行结果，未把待修版本算通过；本次未重跑F09/F11或旧整套。

结论仅为F08纯核心行为PASS，没有未关闭独立核心缺陷。真实物理provider调用门禁、SDK重试关闭、retry/fallback/摘要/父子共享、取消/断流与CLI/Web预算入口尚未验，F08整模块继续pending。测试全离线且没有网络/模型/用户扩展读取，没有修改产品或实现者测试。

### F08 OpenAI真实SDK离线链路准备（2026-11-09，尚未执行）

新增`acceptance/test_f08_openai_independent.py`：使用实际OpenAI SDK与httpx.MockTransport，替换仅构造器以确保所有运输都离线，不使用虚拟budget/伪chat方法替代真实LLM入口。通过公共budget_scope→LLM.chat→SDK.create/stream观察每次物理请求，SDK初始配置max_retries=7以检出预算路径是否关闭隐式重试。测试三类0cap零运输、503逐次门禁、400参数fallback新attempt、F01实际费用一致、最终system+tools计入预留与显式输出cap、缺usage保留不确定预留；真实SyncByteStream先输出再抛httpx.ReadError以验证中途断流重试；真实on_token内嵌套chat验证内外物理ID各自结算；本地with_options异常验证可证明未发送不计F01或已发预算。

独立文件仅语法检查通过，尚未执行业务断言，等待Terra冻结新LLM版本；纯核心26项证据复用，不重跑F09/F11或旧套件。LiteLLM/Agent/父子与summary/Web/CLI预算集成仍在后续范围，不能据此准备文字计为通过。

### F08 OpenAI真实SDK物理attempt — 11 PASS（2026-11-09）

Terra冻结llm.py=`099038a02a6bb1c17fd6e10433da5b92fa63224616041ed223f8260bdab345ae`，budget.py=`ce5d8f69cc987df974367fa314df81e4d120821dfd4e0649a6795763357f3d20`。执行venv Python `-m pytest designDocument/priority_2026_11_08/acceptance/test_f08_openai_independent.py -q -p no:cacheprovider --junitxml=designDocument/priority_2026_11_08/acceptance/validation/f08-openai-sdk.xml`，**11 passed in 2.23s**，exit 0；日志/JUnit=`validation/f08-openai-sdk.log/xml`。9个测试函数、其中0cap三参数，实际共11项；先前沟通中口头13项为计数错误，以实际收集为准。

初次收集`f08-openai.log/xml`因环境没有httpx而失败，未执行产品断言。读取已装OpenAI 3.24.0源码确认依赖httpx2，独立fixture增加对应导入兼容，再完整执行上述11项；未安装包、改产品或跳过实际运输。真实SDK初配max_retries=7；503时requests=1实际仅1运输且下一次被门禁拒绝，足以检出SDK隐含重试绕过。另两次503再成功共3运输/3唯一attempt，400 max_tokens→max_completion_tokens共2运输。两者均逐次先存在in_flight预留；成功费用与同一F01 ledger entry完全相符，独立期望12输入×0.1及3输出×0.2每百万为0.0000018。

实际SDK读取SyncByteStream先产生partial内容再抛ReadError，触发第二个物理请求，预算/F01分别保留首个未知与次个actual。on_token内嵌套真实LLM.chat共2运输、2actual且没有遗留in_flight；本地with_options异常则0运输、0ledger、请求额度全部保留。其余覆盖三类0cap零请求、长system+tools输入超限前拒绝、实际输出参数32与预留、缺usage留unconfirmed且不显示actual零花费。全部MockTransport，主机offline.invalid不进入真实网络，不调用付费模型。

跑前SHA=`validation/f08-openai-before.sha256`。测试结束通知Terra可开始下一LiteLLM切片后，取证时llm.py已变为`2e1100b7c34e0369a38ceba6f681df3c851b7d146506184ce5ca73359b10358c`，预算文件未变；不能声称整个文件跑前后相同，正在核对OpenAI切片是否无差异。OpenAI本切片11项实际断言通过，无未关闭功能失败；最终范围hash待核。核心26、F09/F11及旧套件未重复。此结论仍不覆盖LiteLLM、父子Agent/摘要、run恢复与CLI/Web预算展示，F08整体待后续集成。

### OpenAI范围hash确认与LiteLLM边界 — PASS

Terra确认仅在11项完成通知后修改LiteLLM fail-closed guard。最终llm.py raw hash为`7ae2f63e7854ad6112f6254f385d0a9cbca396ccd36e51fb686a2a5b4af038b5`；独立按原始bytes只移除该新增guard三行，立即重现已测整文件raw hash`099038a02a6bb1c17fd6e10433da5b92fa63224616041ed223f8260bdab345ae`。因此OpenAI切片未变，不需重跑11项。证据`validation/f08-openai-final-hashes.json`保存实际前后hash与重建核验方法；不称整个llm.py无变化。

新增独立`acceptance/test_f08_litellm_boundary.py`，sys.modules只注入离线provider completion记录器，不安装或加载真实LiteLLM/provider链。实际公共LiteLLM.chat在启用预算时抛可识别BudgetExceeded，completion零调用、F01零entry、budget快照完全不变；未启预算实际正常stream消费返回文本/4输入2输出并形成1个known ledger entry。首轮**1 passed、1 failed in 2.37s**，失败因独立测试仍期望首版ValueError，产品已改设计要求的BudgetExceeded；仅更新自己异常契约断言，选择`-k enabled_budget`复验**1 passed、1 deselected in 1.87s**。两个不同边界均有通过证据，未重复默认兼容或OpenAI测试。日志/JUnit=`validation/f08-litellm.log/xml`、`f08-litellm-final.log/xml`，最终hash=`f08-litellm-final-hashes.json`。

LiteLLM在本批仅验证预算开启时明确拒绝与默认兼容，**不支持受预算约束的LiteLLM执行**；不能将此2项PASS写成LiteLLM预算可用。F08整模块仍待共享父子/summary、预算中止传播和CLI/Web/run持久化与展示，所有旧已过范围未重跑。

### F08真实Agent共享预算 — 6个不同场景PASS

独立新增`acceptance/test_f08_agent_independent.py`，复用上轮离线SDK运输观察器（仅增加可读取Agent实际创建的当前budget的观察模式，不替换RunBudget、LLM.chat或Agent循环）。所有模型响应经真实SDK stream，实际AgentTool创建子Agent；明确传入空Hooks、无持久授权的隔离Permission以及临时workspace，不接触用户扩展。

- 父模型发起真实agent工具，子模型正常完成共2次运输，父继续请求因共享requests=2门禁拒绝；RunResult failed/budget_exceeded/exit1，子正常结果已留history，budget唯一attempt集合及已知费用与F01账本完全一致。
- 仅1次额度时父调用已发，子在发送前耗尽，BudgetExceeded穿过子/工具/父循环，不包装Sub-agent completed；下一顶层chat_result配置1次新额度能完成，两个run attempt集合不相交，F01累计账本保留两次。
- 真正由ContextManager.prepare_request触发摘要，实际SDK summary请求和最终请求共同消耗run预算；lossless history JSON实际存在，F01与budget逐笔费用一致。requests=1时摘要后额度耗尽，BudgetExceeded不被确定性摘要fallback吞掉，不发最终请求。
- 不支持的客户端零chat调用、零attempt且额度完整；最终RunResult与agent.last_result是同一对象。
- 真实子Agent执行WriteFileTool写入临时文件后预算耗尽：文件保留、父history中的已启动agent调用标明budget失败及可能副作用，不声称before start；同批未启动write_file明确cancelled before start且文件不存在。

首轮沙箱临时目录WinError5，尚未执行业务；同5项require_escalated离线执行**4 passed、1 failed in 2.57s**。失败为独立摘要成功fixture令最近保留4轮本身过长，正确触发context上限而非summary成功；仅修fixture让大正文位于可归档旧轮，保留最近短轮。期间Astra追加已写副作用/last_result边界，Terra修复后只选择`-k 'real_context_summary or unsupported or child_write'`，**3 passed、3 deselected in 2.14s**。故有6个不同场景实际PASS，不把4+3相加冒称7项；摘要失败保护等原已过不重复。

命令venv pytest显式上述文件、`-q -p no:cacheprovider`、各自隔离basetemp与JUnit；实际日志/JUnit=`validation/f08-agent-authorized.log/xml`、`f08-agent-target.log/xml`，沙箱失败日志=`f08-agent.log`保留。相关hash=`f08-agent-before.json`和`f08-agent-final-hashes.json`；Agent在准备至定向复验间因Astra新边界从0986版修订为eed02版，不声称整个阶段hash完全不变。最终agent=`eed02aedbce813149c5d0f15662cf57ee9f3bb8357e3acedbeb3dff972e52a3d`，context=`f61dc155c275`、results=`c5bfaf5f8581`、tools/agent=`224fc5a339fe`；最终定向3项使用此冻结修订版。

本切片独立行为PASS，无未关闭失败。没有重跑原core26/OpenAI11/Lite2/F09/F11或旧套件；CLI/Web配置、run预算持久化/展示与刷新重启仍未验，F08整模块仍pending。

最终hash补充：取证时budget.py已因后续Web工作变为`2f91e60b7e19288ee0fe5b7d5ab31b9394760c43760fe9b91249b1ca859e65a4`。Terra确认新增可选on_change检查点与RLock串行化reserve/settle及持久快照，持久失败释放未发预留；默认无callback语义保留。该Web切片尚未冻结，因此此处不将原核心26项证据推广为新callback持久分支已通过；后续必须专项验证回调失败零运输/额度释放与崩溃恢复，而非重跑无关旧项。

### F08 CLI / Web预算持久化与真实重启 — 12个不同场景PASS（2026-11-10）

Terra/Astra确认冻结后执行`acceptance/test_f08_web_cli_independent.py`，实际**11 passed in 6.56s**，exit0；独立真实服务进程强杀/重启`test_f08_restart_independent.py`修正隔离worker环境代理后**1 passed in 6.00s**，exit0。均venv pytest显式独立文件，`-q -p no:cacheprovider`、本批validation隔离basetemp、JUnit；require_escalated用于pytest临时目录和本测试创建进程，只有loopback HTTP/离线transport。

11项覆盖：真实SDK provider用Event阻塞时HTTP GET /api/run和磁盘run JSON同时可见1个in_flight，完成后actual；initial snapshot故障保留旧session.run_id、无僵尸running、执行锁可重新获取、0运输；reserve checkpoint异常先失败后释放可证明未发送预留，0运输/0F01；settle单次磁盘故障最终再次保存actual，重建Service后无旧in_flight且F01已知用量仍在；Thread.start失败保留旧run引用并释放锁、无running/运输；HTTP空字典与三个额度null两参数均不启controller（使用不支持预算的ScriptedLLM仍能默认完成）；非法bool/costNaN/unknown_policy在创建run前拒绝；requests0创建可查询run但模型0发送；实际REPL /compact在0cap不发摘要且显示预算/耗尽，正常cap实际摘要并压缩；实际CLI参数解析与main --json路径传递requests/tokens/cost/unknown_policy，0请求结构化budget_exceeded退出1。

真实重启另起直接base Python解释器，运行独立`f08_service_worker.py`中的正常Service/HTTP，extensions=False；父进程只提供loopback离线provider。首次POST实际已到provider并阻塞，磁盘存在in_flight；只kill本探针直接创建的服务PID，等待退出，再同workspace/store启动新进程。新进程3次GET均interrupted、in_flight0/unconfirmed1、剩余额度1；磁盘相同，provider累计仍只有1个请求，无重放。两个服务PID和provider均在finally清理。该用例覆盖真实进程退出，不是只重新实例化Service。

首轮真实重启`f08-restart.log/xml`为1失败8.44秒：独立worker继承环境代理，localhost provider未收到且已有网络失败预留，尚未到预期kill边界；非产品恢复失败。仅修worker使用trust_env=False明确直连本地，不改产品，再单项成功。源码hash前后JSON=`validation/f08-web-cli-before.json`/`f08-web-cli-after.json`，五个raw SHA全等：budget=`2f91e60b7e19`、CLI=`9a6fc3e2f34e`、service=`62c7fffb0314`、run_store=`956ae2b743fe`、server=`64fed893ba1a`。

证据：`validation/f08-web-cli.log/xml`、`f08-restart.log/xml`（首轮夹具环境失败）、`f08-restart-direct.log/xml`（成功）；worker实际日志、info、会话和run元数据位于`validation/tmp-f08-restart-direct/`。没有未关闭独立功能失败；该切片不包括持续全部存储不可写的每一种故障或供应商最终账单保证。前端配置/报告和一次最终受影响集成仍待执行，F08整模块暂不写全PASS；既有core/SDK/Agent/F09/F11证据未重跑。

### 最后一次受影响集成建议（尚未运行）

待前后端最终冻结与文档计数更新后仅一次：本批实现者test_budget.py/test_budget_openai.py/test_budget_agent.py/test_budget_web_http.py，现有test_litellm/test_usage_persistence/test_repl/test_chat_mode/test_plan_mode；设计回归R05 context、R06 summary、R08 cost ledger、R09 CLI result；test_core按context/llm/agent及文档行数筛选；Web run持久化回归及test_web启动/状态/模式相关子集。前端App/API/modes及本批独立预算UI交互、生产build和CI范围ruff。确切命令以最终变更范围确定，不运行旧MCP/hooks/undo等无关全套，不整批重跑已过独立26/11/2/6/12，修复后只必要target。

### F08独立真实App预算交互 — 8 PASS

新增`acceptance/f08-ui.test.tsx`和独立`vitest.f08.config.mjs`，实际App组件/jsdom/API替身，不复用实现者测试。冻结App=`f3eff73c3ac8097177ab6e7abae974d2e9843c5298ba1fc0027248b3eba8643e`、api=`65e1e0bca668438c02988a4e2b1701c232f1f84cc33f136531a6e7834958666e`、types=`a0eaa172922e682c98897b67c0ce0f7e140259782f968e42d30993f59e920c6d`。

实际命令`node frontend/node_modules/vitest/vitest.mjs run --config designDocument/priority_2026_11_08/acceptance/vitest.f08.config.mjs`，**8 passed，3.40秒总/1.25秒测试，exit0**。日志=`validation/f08-ui.log`，三个源码hash前后相同=`f08-ui-before.json`/`f08-ui-after.json`。仅require_escalated离线jsdom/esbuild子进程，不访问真实服务或模型。

覆盖：空caps即使选择allow_unknown也不向POST带budget；显式Token零值不丢，POST发出后编辑控件不会改变已发送请求中的冻结配置；plan下非法cost的/approve既不切code也不POST，draft保留；负整数拒发；中文报告分别展示actual/inflight/unconfirmed，费用上限下unknown剩余额与未设Token额度清楚区分，切旧run缺预算时显示不可用；首GET耗时3.2秒仍只有一个请求，响应可见后才安排下一轮，第二GET再慢3秒也无并发，终态后推进6秒不再GET；切session或同session启动新run时，旧响应迟到不得覆盖新预算报告。没有首轮失败、没有修改产品或实现者测试。

F08各切片独立行为现均PASS，没有未关闭独立缺陷。最终一次受影响回归、CI范围lint/build及源码/文档count一致性尚待root确认最终freeze后执行，不将本节当整个最终集成已过。保留边界：LiteLLM有预算明确不支持、token输入为估计、未知/在途预留不保证供应商最终账单上限；UI为React DOM而非真实浏览器。

### 最终受影响集成首轮 — 待两项目标与lint收尾

Root确认46项LF清单/文档数字冻结后执行一次选择回归，未重跑既有独立套。Python选择清单逐项见`validation/final-python-selection.txt`，**145 passed、1 failed in 8.44s**，日志/JUnit=`final-python.log/xml`。失败仅旧`test_repl::test_unknown_slash_command_is_not_sent_to_the_model`期待Unknown command文案，而新F11显示Skill not found/ambiguous；需要实现者明确兼容契约并保留零送模断言。

前端实际命令`node node_modules/vitest/vitest.mjs run src/App.test.tsx src/api.test.ts src/App.modes.test.tsx src/ModeMenu.test.tsx src/workbench-state.test.ts`（cwd frontend），**43 passed、1 failed，12.25秒总**；日志=`final-frontend.log`。唯一失败为App.modes旧技能list隔离夹具的resolve尚未初始化即调用，已交Terra核对发送前/加载同步，不把夹具失败冒称产品response隔离失败。

CI范围Ruff实际失败3处：test_budget_agent.py参数ClassVar、test_repository_search.py本地import分组、test_skills_web_listing.py合并with，日志=`final-ruff.log`；均实现者测试文件，已直报Terra/Astra/root。最终集成暂不标PASS，待修后仅失败target及CI lint复验，已过其余业务不重复。66个相关生产/前端文件跑前raw SHA证据=`final-integration-before.json`；源码计数2项在本轮Python中通过，但若修复改源码行数需只复验两计数目标。

### 最终结案 — F09 / F11 / F08 本地PASS

上述首轮失败均已关闭。Terra最终补丁使真正不存在的技能抛LookupError，CLI恢复Unknown command兼容文案；已存在但损坏、冲突或无权限技能仍报Skill error。独立最终5目标为：旧unknown REPL零送模、两源码/文档计数、保留slash显式来源与冲突、**新增实际HTTP未知技能400/零run/零model/锁释放**。`validation/final-patch.log/xml`实际**5 passed in 1.70s**。新增HTTP断言在`test_f11_entrypoints.py::test_final_unknown_skill_http_is_rejected_without_creating_run`，未修改原独立测试通过标准，未改产品或实现者测试。

前端原唯一失败项补GET实际启动等待后目标1通过（`final-frontend-target.log`，1 passed/11名称过滤）；Astra随后指出两个mock缺真实command可能使toast负断言空通过，Terra补齐`/review@builtin`并更新负断言后，Sol只复验这两个相关目标，`validation/final-frontend-fixtures.log`实际**2 passed、10名称过滤，总3.80秒/测试1.34秒**。最终App.modes raw SHA=`80cfc13049e0dbeb4be4c669183f6b09b3787919ca1f001179308fc9ae17a23b`。原独立UI7及本批预算UI8不重复。REPL第一次单目标曾被pytest默认临时目录sandbox拒绝，授权隔离目录目标1通过（`final-python-target-authorized.log/xml`）；最终产品补丁仍以5目标日志为准。

统计准确表述：**首次Python相关集成145/146通过、首次前端相关集成43/44通过；修复后只复验失败目标和补丁直接风险目标通过。没有全量重跑，也不声称新146/44全套重跑通过。** CI原3项lint仅测试风格修复后完整CI范围Ruff通过，最终证据`validation/final-ruff-closed.log`。前端build复用Terra最终freeze构建通过证据，产品App/api/types最终hash未变，没有为计数重复构建。

最终66文件raw hash及相对首轮变化见`validation/final-integration-after.json`，不覆盖历史前置hash。变化仅CLI=`c78d7ff58168ac40009bc3dde8129ab503bb01aa0b40604168b2218b35dcfba0`、adapter=`be0363e98c86ca3811689202ea508ae455f1c1c44d86d30c7acb5d3b2bff0fa4`、Service=`9f6d56a039b576caa4d52e22c618f8c89940bfab106b905d09d76ca0f95937f0`、App.modes测试与README数字；对应产品补丁已5目标验证，两文档计数通过，前端真实产品源码未变。Astra已确认最终增量静态PASS并写REVIEW。

没有未关闭独立功能缺陷或本地发布阻碍。远端发布、跨平台GitHub CI和合入不在本轮执行范围；最终manifest需在ACCEPTANCE/STATUS/REVIEW及本批新增验收资产落完后刷新，raw与LF hash分开记录。覆盖局限保持：Windows/Python3.12；无真实付费模型；SDK运输离线；UI React DOM/API替身而非真实浏览器；LiteLLM预算执行不支持且前置拒绝；输入token为估计，未知价/未确认预留不保证供应商最终账单硬上限；F09固定样本不证明普遍提速或省token。无用户MCP/hooks、无提交推送、无旧dirty重置。
