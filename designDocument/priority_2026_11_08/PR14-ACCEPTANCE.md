# PR14 独立增量验收

负责人：Sol（6.1 Sol high）。2026-11-10最终：**PR14本地独立增量验收PASS，无未关闭本地缺陷。远端跨平台矩阵及合入由root负责，未在此声明通过。** 复用本批既有通过证据，不重跑旧全套，不修改旧ACCEPTANCE/STATUS或产品、实现者测试。

本轮用户授权处理PR14 Codex意见及12个跨平台CI矩阵jobs。root负责远端写入、矩阵观察及合入；本记录只声明实际本地验证范围，不能以本地Windows通过代替GitHub三平台/四Python矩阵通过。

## 独立可观察门禁

- 最小干净源码副本仅包含corecoder、pyproject、README和LICENSE，不含.git、.phase3-preparation或任何既有wheel；pip --no-index/--no-cache-dir实际构建，再从新wheel离线安装到临时target，-I子进程在源码外验证实际import来自installed及三个内置模板可解析。既有本地构建产物不参与。
- 实际Service/BrowserAgentTool/真实LLM SDK离线运输：父同批先agent、后write_file；1次额度时子发送前拒绝，2次时子实际写文件后耗尽；两种均必须顶层failed/budget_exceeded、父后续write不执行、已启动child不能冒称before start，F01与budget attempt集合相符。没有真实API、用户MCP/hooks。
- CI相关夹具修复只验证对应目标：检索含ignore仓库应真实Git初始化；缺后端不能处理ignore仍拒绝；外部路径安全测试显式工作区且不能放宽越界；schema required仍为合法数组、工具数量9；旧CLI适配器不应因未启用预算而收到新kwarg。

独立源码：`acceptance/test_pr14_independent.py`。当前仅准备，待Terra修复全部CI目标并冻结hash后执行；最终命令、日志、结果和局限追加于本文件，不把实现者自测或准备资产算通过。

## 最终实际结果

Terra冻结后实际执行独立文件，**3 passed in 21.69s**。两例是真实Service/BrowserAgentTool/实际LLM SDK配MockTransport：请求上限1时child发送前拒绝，上限2时child真实write_file落盘后拒绝后续模型请求；两者父同batch剩余write_file都未执行。顶层failed/budget_exceeded、child记录不冒称before start，父pending记录cancelled before start；F01物理attempt与budget逐笔一致。第三例是真正构建干净源码副本，无.git/.phase3-preparation/任何预存whl，`pip wheel --no-index --no-build-isolation --no-cache-dir`，再`pip install --no-index --no-deps --target`安装新wheel；源码外`python -I`核对实际导入来自installed且三个builtin模板正文均存在。不是复制旧wheel或只检查当前源码模板。

CI复现只选原13失败及额外非Gitignore拒绝1例，在PATH中保留Git与System32、明确确认rg不可用：首轮**11 passed、3 failed in 29.20s**。三失败是本次执行使用了仓库内basetemp，Git祖先识别导致两安全读取目标和额外非Git目标不再是真正非Git场景；CI原始/tmp在checkout外。没有修改产品或放宽任何安全断言，仅这3目标改系统Temp后实际**3 passed in 1.32s**。因此14个不同相关目标均有实际PASS，不声称14整套再次重跑。安全矩阵写入拒绝、读取成功、Ctrl+C已执行记录与unknown补齐断言保留；无后端无法安全解释ignore时仍拒绝。

实际pytest命令都venv `-m pytest`显式选择文件/节点，`-q -p no:cacheprovider`、隔离basetemp与JUnit；完整CI选择名单=`acceptance/validation/pr14-ci-selection.txt`。独立命令文件=`test_pr14_independent.py`。日志/JUnit：`pr14-independent.log/xml`、`pr14-ci.log/xml`（保留初轮临时位置不当的失败）、`pr14-ci-outside.log/xml`；真实干净wheel构建/安装/子进程导入日志保留于`validation/tmp-pr14-independent/`。系统临时目录具体位置见`pr14-outside-basetemp.txt`。未在系统临时目录做递归删除。

PR14生产改动影响源码计数，额外仅两计数目标实际**2 passed in 0.48s**，证据`pr14-count.log/xml`。CI完整规定范围Ruff实际通过，`pr14-ruff.log`；未运行旧590/全部本地业务。所有日志位于`designDocument/priority_2026_11_08/acceptance/validation/`。

## 最终源码哈希与局限

最终raw SHA记录=`acceptance/validation/pr14-final-hashes.json`，跑前=`pr14-before.json`。产品与冻结一致：CLI `b2cd013ba332d5c2b6501dcfadf55dd5998e83d6546de6b20d6977aeb0c80507`，repo_map `32469dab69959c336c9118e7f3ebd756779dadb38f863841f1761dcb7e0ed5e9`，Service `f84d2b898af64790acab0d5480c496669cc592cca45af933ec9aa62ed16b825c`。安全测试最终移除无关第三处state插入后的raw为`234cd54102b3688fa5559fb271ad8074c4b8e5a07a6759894becce94c13336d9`，最终两安全目标已在此版本通过；相关测试和README hash也在最终JSON中，不覆盖历史旧证据。

结论只针对本机Windows/Python3.12及上述增量。真实wheel构建安装和模型SDK均离线（MockTransport不访问网络模型），用户MCP/hooks未加载；开发CI wheel fixture会自行新建临时wheel，不再引用未提交artifact。本地未复刻三平台/四Python全部矩阵，必须由root确认GitHub CI全绿后才按用户授权合入。本轮未调用远端写入、提交或合并工具。旧验收/状态文档保持不动；若发布此新验收文件及独立资产，需刷新相应manifest哈希。
