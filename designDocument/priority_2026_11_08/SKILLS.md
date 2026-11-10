# 项目技能示例

在工作区创建 `.corecoder/skills/my-review/SKILL.md`：

```markdown
---
version: 1
files: checklist.md
---
检查用户指定的变更，并参考附带清单。
```

`files` 列出技能目录内的相对文件，以逗号分隔，最多八个；正文和资料合计最多 32 KiB。LF 和 CRLF 换行均可。文件必须是 UTF-8 文本；链接、父目录、凭证和二进制文件会被拒绝。未设置版本时使用内容摘要。项目技能与内置技能同名时必须显式选择来源（`builtin` 或 `.corecoder/skills/<name>`）；不能自动覆盖。技能是供本次请求使用的文本，不授予额外权限。
