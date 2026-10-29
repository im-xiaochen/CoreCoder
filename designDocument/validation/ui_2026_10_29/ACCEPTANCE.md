# 主代理独立验收记录

## 修复前补充复现

- 完整 Python 基线：477 passed, 2 skipped，65.27 秒。
- 远端检查：`git fetch origin` 后 `main...origin/main` 为 0 / 0。
- 离线浏览器服务：`python -m designDocument.validation.ui_2026_10_29.serve_acceptance`（8766）。使用真实 Service、Agent 和 HTTP/SSE，只有模型为确定性替身；会话存入 `.tmp/ui-acceptance-20261029`。
- 输入 `reject`，服务在接受任务之前返回 HTTP 409。页面仅显示 toast，textarea.value 变成空字符串。UX-02 已真实复现。
- 输入 `fail`，模型替身抛出错误。初次有 toast，刷新后页面只剩用户消息，无失败结果或说明。UX-03 已真实复现。截图：before-failure-reload.jpg。

## 修复后验收

5.6 sol 子代理第一版提交：`48dfabc`。主代理独立浏览器检查：

| 场景 | 实际结果 |
| --- | --- |
| HTTP 409 未接受发送 | `reject` 保留在输入框，没有插入虚假的已发送消息 |
| 模型失败刷新 | `fail` 会话刷新后显示“任务运行失败”及具体错误 |
| 会话草稿 | A/B 各有独立草稿，来回切换及刷新均恢复正确 |
| 停止任务 | `slow` 运行中点击停止，最终显示“任务已停止”；运行中输入的下一条草稿保留 |
| 真实模型复测 | deepseek-flash 实际只读 frontend/package.json，read_file 成功并给出 test/build 命令；显示“任务已完成”，2轮，4514 tokens |
| 390×844 | 输入16px（原11px），页面scrollWidth=390；选择会话后收起，Esc关闭侧栏，工作区面板可开关 |
| 390×480 | 输入框 top317/bottom396，仍在可视区域内，页面scrollWidth=390 |
| 800×700 | 常驻侧栏没有错误触发抽屉遮罩 |
| 1280×720长对话 | **发现阻断缺陷**：main高度1203，输入框top997，被挤出视口；已退回子代理修复 |

第一轮代码审查补回：建议卡片与草稿ref同步、HTTP接受前发送锁、断流后轮询、HTTP200非JSON报错、SSE reader释放、IME与重复提交测试。另实测发现新建会话响应覆盖刚输入文字，子代理已在48dfabc中禁用pendingAction期间输入并补测试。

第一版子代理测试：15个前端测试通过、生产构建通过、16个后端接口测试通过。主代理还未最终签收。

依赖审计发现4项（3 critical/1 moderate），包括原有DOMPurify和新增Vitest工具链，已退回核对升级。DOMPurify公告涉及本项目未启用的SAFE_FOR_TEMPLATES及DOM返回模式；不能把审计告警直接等同本项目可被利用，但优先使用修复版本。依据：[DOMPurify GHSA](https://github.com/advisories/GHSA-cg2g-xh64-v4q7)、[Tinypool GHSA](https://github.com/advisories/GHSA-85c8-ppgw-ccpr)。

## 最终验收

本地验收通过（2026-10-29）。5.6 sol 完成主要修复提交及桌面布局/依赖升级补丁后触及使用额度限制，主代理接手最终复查与少量修正。

| 最终检查 | 证据 |
| --- | --- |
| 桌面 1280×720 长对话 | main 高度720，输入框 top513.81 / bottom593；历史长对话滚动内容1769px，滚动容器439px。输入框未被内容撑出页面，scrollWidth=1280。 |
| 短屏 390×480 | 最终构建输入字体16px，输入框 top324.81 / bottom396，scrollWidth=390。发现旧媒体查询覆盖成11px后已在原选择器修正。 |
| 手机 390×844 | 保存最终截图 after-mobile.jpg；抽屉关闭后输入及任务终态可见。 |
| 前端回归 | 最终16个测试通过，3个测试文件；包含草稿隔离、拒绝恢复、并发提交、IME事件、新建会话等待、持久失败、停止反馈、工具归并和API错误。 |
| 生产构建 | TypeScript + Vite通过，最终资源在frontend/dist。 |
| 依赖审计 | DOMPurify 3.4.17、Vitest 4.1.12；npm audit返回0项漏洞。 |
| 旧会话状态 | 没有明确结果状态的工具显示“结果未知”，不再默认打成功勾；补充回归测试。 |
| 浏览器控制台 | 本次最终页面检查无error/warn。 |
| Python | 本轮未改生产Python代码；基线全套477 passed / 2 skipped，修复提交后后端接口16 passed。远端CI继续执行完整Python矩阵。 |

桌面滚动修复使用 `grid-template-rows: minmax(0, 1fr)`，并让主面板 `min-height: 0; overflow: hidden`，消息区承担滚动。删除了子代理最后新增的JSDOM样式断言：JSDOM不执行真实布局，该断言只重复CSS声明；这里采用浏览器实际尺寸与截图作为验收依据。

关键实现分布：`frontend/src/workbench-state.ts` 处理草稿键、运行终态与工具归并；`App.tsx` 负责提交接受边界、同步互斥锁、会话请求序号、断流后的串行轮询和抽屉；`api.ts` 负责HTTP错误与SSE reader释放；`styles.css` 处理可读字号和有界滚动。PR新增前端CI执行npm ci、test、build。

范围说明：中文输入法使用composition事件回归测试，未逐一测试Windows各输入法；断流恢复及过期响应保护经过代码审查，未执行真实网络拔线试验。手机检查为浏览器390px视口，不代表所有真机。截图中的模型回复记录了执行当时的依赖版本；最终依赖版本以package-lock.json为准。

修复前截图与首次退回截图保留用于追溯；after-task.jpg和after-mobile.jpg为最终页面。所有提交均位于 `fix/workbench-ux-20261029`。本记录提交时远端PR/CI尚待运行；只有对应提交CI通过后才允许通过PR合并，实际结果以PR检查和合并记录为准。

## 远端CI补充

[PR #3](https://github.com/im-xiaochen/CoreCoder/pull/3) 的首轮CI发现既有接口测试 `test_web_snapshot_keeps_repeated_call_id_outcomes_in_order` 在英文Windows runner使用cp1252写入中文“保留”时抛出UnicodeEncodeError。失败发生在测试数据准备阶段；其他477项通过，1项跳过。已为该测试文件写入显式指定UTF-8，保持生产代码和断言不变，再由PR完整矩阵复验。前端CI、lint和打包首轮均通过。

编码修正后，Windows/Linux全部版本与前端、lint、打包通过；macOS ARM64的Python 3.13也通过。但另两个macOS任务在长期排队后被GitHub取消，注释为“未能分配hosted runner”和“macOS arm64容量受限”，未进入测试步骤。根据[GitHub官方runner表](https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job)，将macOS矩阵固定为标准 `macos-15-intel`，保留Python 3.10–3.13四个版本、完整测试命令及失败门槛，重新执行整个PR CI。该调整验证macOS Intel，不等同于四版本ARM64覆盖；本次前端和纯Python改动没有增加架构相关代码。
