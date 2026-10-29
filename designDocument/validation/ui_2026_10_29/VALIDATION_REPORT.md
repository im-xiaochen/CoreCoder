# 工作台验证报告与关键实现方案

日期：2026-10-29（Asia/Shanghai）。验证人：主代理。修复尚未开始。

## 范围与基线

- 仓库：im-xiaochen/CoreCoder；基线提交 `5cafa2ec1bf8d75eab0dec69959230c619457e4d`。
- 新分支：`fix/workbench-ux-20261029`。不直接推送 main，修复后由主代理独立验收，再决定是否具备合并条件。
- 保留用户原有未跟踪文件 `bubble-sort.html` 与 `designDocument/validation/test-store-cleanup.json`。
- 启动：`./start-web.ps1 -Build -NoBrowser`，浏览器访问 `http://127.0.0.1:8765`。
- 生产构建通过；后端基线 `python -m pytest web_backend/tests -q`：16 passed。
- 沙箱内 esbuild spawn EPERM、pytest 临时目录 PermissionError 经获准本机执行均消失，属于验证环境限制，未计作产品缺陷。
- 浏览器实际发送：『界面验证任务：请只读取 frontend/package.json，列出开发和构建命令，用三行中文说明。不要修改文件，不要运行 shell。』实际模型 deepseek-flash；工具 read_file 成功，返回 dev/build/preview 三个命令。未批准任何修改工具。
- 桌面验证 1280×720；手机宽度验证 390×844。截图见 before-task.jpg、before-mobile.jpg。

## 发现与优先级

| 编号 | 级别 | 证据与影响 | 关键实现 |
| --- | --- | --- | --- |
| UX-01 | P1 | 实测：输入草稿，切到另一会话再切回来，输入为空。App.tsx loadSession/newSession 无条件 setDraft('')。 | 草稿按 workspace + session id 隔离保存，切换/刷新恢复；发送被后端接受后才清除，保留用户运行中输入的下一条草稿。 |
| UX-02 | P1 | 代码确认：send 在服务接受前清空草稿且乐观插入消息，catch 仅 toast；403/409/断网失败会失去原输入。最终状态读取失败时 busy 也可能永久锁住。尚未故障注入。 | 同步 ref 防重复发送；区分未接受与已开始的流；未接受时回滚乐观消息、恢复草稿；断流后显式恢复/轮询，不能声称完成或盲目重复提交。 |
| UX-03 | P1 | 后端 service.py 发出 run_result（completed/failed/cancelled/max_rounds），前端没有消费；ToolEvent.status 仅定义工具状态。任务结束只显示“服务就绪”，失败 toast 会消失。 | 分离 RunStatus 与 ToolStatus，展示持久的本次运行结果/错误；从 timeline 恢复刷新后的结果，处理旧会话没有 run_result 的情况；区分停止请求与已停止。 |
| UX-04 | P2 | 实测窄屏：侧栏覆盖约 244/390 宽，无背景遮罩；选择会话后仍覆盖消息。styles.css 工作台 min-height:550px 对短屏也有风险。 | 手机侧栏与 inspector 抽屉加遮罩、关闭入口、Esc；选择会话后收起，断点切换同步；短屏可滚动，输入框保持可达。 |
| UX-05 | P2 | 实测手机输入框 computed font-size=11px；正文 11px，多处提示 8–10px；浅灰配色难读。桌面首页大留白、低对比卡片，行动入口弱。 | 统一设计变量、14–16px 正文/输入、12px 以上辅助字；提升对比度与点击区；清晰工作区/任务/输入层级，紧凑欢迎区；保持中文优先和本地开发工作台定位。 |
| UX-06 | P2 | App.tsx 会话请求无序号/取消保护，快速切换时慢响应可能覆盖最后选择；会话按钮仅检查 busy，未覆盖 pendingAction。 | 会话选择使用序号或 AbortController，异步结果只作用于当前会话；创建/切换/设置期间一致禁用，避免旧请求覆盖。 |
| UX-07 | P2 | liveTools 按 call id 查找首个结果，复杂 filter 有重复条件；仅 result 的工具可被显示为 running；unknown 缺省图标为成功。 | 提取按发生次序归并的工具视图；以明确状态显示成功/失败/拒绝/取消/未知，保留 warning；测试重复 id 和缺失 start。 |
| QA-01 | P2 | package.json 只有 dev/build/preview，无前端交互测试；GitHub CI 未构建前端。 | 增加有意义的前端交互回归测试与 frontend CI 作业（npm ci、test、build）。不要只用字符串检查代替行为测试。 |

## 修复实现边界

由一个修复子代理实现上述前端状态和交互修复、样式更新、测试及必要文档。优先保持既有 HTTP/SSE 协议、权限边界及会话兼容。不要扩展成框架重构，不改密钥，不引入图片生成或外部设计依赖，不动用户原有文件。CSS 应可读、分区维护，避免继续叠加同选择器覆盖。

建议拆出 draft storage、run result、tool event 归并等小模块；App 保留界面协调职责。对 HTTP 非 JSON、失败状态与 SSE 截断给出可理解错误，流读取在 finally 释放锁。只有为上述行为所需时才调整后端。

## 主代理验收门槛

1. 子代理给出改动清单与测试证据后，主代理检查完整 diff。
2. 前端交互测试、TypeScript + Vite 构建、现有后端测试通过；相关 Python 代码有改动则补相应回归。
3. 浏览器再次发送真实只读任务，确认回答、工具结果、明确终态。
4. 实测草稿切换/刷新保留、发送失败恢复、停止反馈、中文输入法兼容、对话请求乱序保护。
5. 桌面与 390px 手机、短屏检查；遮罩可关闭，无水平溢出，输入与发送按钮可达；保存修复后截图。
6. 仅提交本任务文件至新分支；发布 PR 后检查 CI。任何关键失败均不合并 main，不以“所有测试通过”代替未执行的验收。

## 初步结论

真实读文件任务能够完成，后端接口基线正常；主要不足集中在前端状态恢复、任务结果可见性、草稿保护和响应式可读性。此报告提供的是修复前证据，不代表修复成果已验收。
