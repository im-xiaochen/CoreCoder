# CoreCoder 网页工作台

独立的 React + TypeScript + Vite 前端。布局参考 Codex：左侧对话列表、中间任务与消息、右侧工作区面板。前端通过 HTTP / SSE 调用 `web_backend`，不导入 Python 代码，也不持有模型 API Key。

## 启动

在项目根目录执行（Windows PowerShell）：

```powershell
.\start-web.ps1
```

第一次会安装前端依赖并构建，随后打开 <http://127.0.0.1:8765>。已有构建时直接启动；修改前端后使用 `.\start-web.ps1 -Build` 重新构建。可以用 `-Port 8766` 更换端口，或 `-NoBrowser` 只启动服务。终端中按 `Ctrl+C` 退出。

需要已安装项目 Python 依赖以及 Node.js 22.12+。脚本优先使用项目 `.venv`；在本机也会识别此前建立的 `.corecoder-review-py312` 环境。其他环境可手动启动：

```powershell
cd frontend
npm.cmd ci
npm.cmd run build
cd ..
python -m web_backend.server --open
```

模型配置继续读取项目根目录 `.env`，沿用 CLI 的 `CORECODER_PROVIDER`、`CORECODER_MODEL`、`CORECODER_API_KEY` 和 `OPENAI_BASE_URL`。修改密钥、Provider 或接口地址后重启 Python 服务。页面内可调整当前对话的模型名称。

## 开发

在项目根目录的终端中运行：

```powershell
python -m web_backend.server
```

另开终端运行：

```powershell
cd frontend
npm.cmd ci
npm.cmd run dev
```

访问 <http://127.0.0.1:5173>。Vite 将 `/api` 代理到 `127.0.0.1:8765`，开发期间前端自动热更新。后端默认提供 `frontend/dist`，不依赖开发服务器。

## 已接入的功能

- 流式对话、Markdown、思考过程和工具调用详情。
- 聊天 / 计划 / 编程模式；点击当前模式展开菜单，选择后才切换。聊天只基于对话回答、不调用工具；计划只读探索项目；编程可修改文件或执行命令，并按需请求许可。
- 新建、搜索、切换和自动恢复网页对话；保存成 CLI 会话与恢复 CLI 存档。
- 当前模型切换、Token 用量、已知费用小计及未知/缺失用量、上下文压缩、任务清单、MCP / Hooks 状态。
- 本对话文件工具产生的 diff 和最近一次修改撤销，文件被外部修改时拒绝覆盖。
- 停止后续步骤、清空上下文、清除持续工具授权。

输入 `/help` 查看命令；`Enter` 发送，`Shift+Enter` 换行，`Ctrl+K` 搜索，`Ctrl+J` 聚焦输入。中文输入法选字时不会误发送。

网页对话按工作区保存到 `~/.corecoder/web/<工作区标识>/`。停止不会杀死已启动的 Shell 进程或立即打断等待中的模型网络请求；当前操作返回后结束。Shell、MCP 和 Hooks 的副作用不在文件 diff / 撤销范围内。当前一次只允许一个网页任务执行；使用 CLI 同时编辑同一文件时也需留意冲突。

## 目录

```text
src/App.tsx    页面与交互
src/styles.css 视觉样式与响应式布局
src/api.ts    JSON 请求与 SSE 解码
src/types.ts  前后端数据类型
vite.config.ts 开发代理
```

验证：`npm.cmd test` 运行草稿、提交、停止、IME、工具事件与 SSE 等行为回归测试；`npm.cmd run build` 执行 TypeScript 检查和生产构建。两项均已纳入 PR 的前端 CI。接口测试说明见 [`web_backend/README.md`](../web_backend/README.md)。

输入草稿按工作区和对话保存在当前浏览器中。服务拒绝发送时保留原文；任务结束状态在刷新后仍可查看。窄屏侧栏和工作区面板可用遮罩或 Esc 关闭。

## 外观设置

打开侧栏的「设置」，在「外观」中选择经典、科技风、中国风或小清新主题，也可调整字体、全局字号（85%–125%）、强调色、页面背景和正文颜色。修改即时生效，仅保存在当前浏览器；刷新或切换对话仍会保留。代码块继续使用等宽字体，字体无需联网下载。

切换主题会采用该主题的字体和配色，保留当前字号。「恢复主题配色」保留字体和字号，「恢复全部默认」回到经典主题、系统字体与 100% 字号。自定义颜色支持颜色选择器和六位十六进制输入；对比不足时会提示。浏览器禁止本地存储时，设置仍对当前页面有效，页面会说明无法持久保存。

原有工作区、模型和工具授权设置位于「模型与工具」。设计方案见 [APPEARANCE_DESIGN.md](APPEARANCE_DESIGN.md)。
