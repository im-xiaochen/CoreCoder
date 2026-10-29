# CoreCoder 第二版界面方案

用户反馈第一版配色与视觉仍有模板感。本版先研究真实界面，再使用 frontend-design 技能重构。

## 参考与取舍

- [shadcn/ui Dashboard](https://ui.shadcn.com/examples/dashboard)：已查看官方截图，借鉴浅色导航、文字对齐、中性图标与深色主按钮。没有照搬其业务卡片和图表。
- [Linear 重设计复盘](https://linear.app/now/how-we-redesigned-the-linear-ui)：借鉴导航层级、对齐、密度与减少色彩倾向的原则。文中使用 Inter Display/Inter；本项目以中文为主，选用 Segoe UI Variable / Microsoft YaHei UI，不依赖外部字体加载。
- [Raycast](https://www.raycast.com/)：参考其任务启动与键盘操作理念，不照搬官网的营销视觉。

## 编码前方案与复核

颜色：白 #FFFFFF、导航浅灰 #F7F7F8、墨灰 #242428、辅助灰 #6B6B73、边界灰 #E8E8EC、焦点蓝 #4169C1。状态颜色仅表达成功、失败、授权等语义。

字体：界面使用系统无衬线字体，正文 15px、导航 12–13px、辅助 11–12px、标题 26px/500。中文不做负字距；代码使用 Cascadia Code/Consolas。

布局候选：原版标题、卡片、底部输入区重心分散。选择将标题、工作区、快捷任务与输入组织成一个连续操作区域；进入对话后保留消息滚动和底部输入。

```text
[项目与对话] | [路径                        操作]
             |
             |      开始一个任务
             |      工作区信息
             |      轻量任务列表
             |      [输入任务                 ]
             |      [模式       模型      发送]
```

复核：删除深色侧栏、青绿主色、彩色图标底板、问候式大标题和三张独立卡片。导航使用轻微明度差，突出任务输入；避免通过添加装饰制造个性。继续保留授权、草稿与窄屏抽屉行为。

## 技能来源

[Anthropic frontend-design](https://github.com/anthropics/skills/tree/main/skills/frontend-design)，本机 C:/Users/lenovo/.codex/skills/frontend-design。2026-10-29 查询仓库约 184.7k stars，为本轮直接适用候选中最高，星数属于仓库而非单个技能。
