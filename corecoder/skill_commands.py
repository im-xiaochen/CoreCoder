"""Explicit slash-command adapter shared by CLI and Web."""
from .skills import list_skills, resolve_skill

RESERVED = frozenset({"help", "quit", "exit", "checks", "check", "rules", "reset", "plan", "approve",
                      "tokens", "model", "compact", "save", "diff", "undo", "sessions", "mcp", "skills"})


def skill_command(root, command, chat=False):
    if chat:
        raise ValueError("Skills are unavailable in chat mode")
    snapshot = list_skills(root)
    if command.strip() == "/skills":
        lines = [f"/{e.name}@{e.source} | {e.source}" + (" | conflict" if e.conflict or e.name in RESERVED else "")
                 + (" | invalid name" if not e.valid else "") for e in snapshot]
        return "list", "\n".join(lines) or "No skills found."
    head, _, task = command.partition(" ")
    name, separator, source = head[1:].partition("@")
    if not any(entry.name == name for entry in snapshot):
        raise LookupError(name)
    if name in RESERVED and not separator:
        raise ValueError("Reserved command; specify skill source explicitly")
    skill = resolve_skill(snapshot, name, source if separator else None)
    text = (f"User-requested skill: {skill.name}\nSource: {skill.source}\nVersion: {skill.version}\n"
            f"Files: {', '.join(skill.files)}\nTask: {task}\n"
            f"--- Begin skill text ---\n{skill.text}\n--- End skill text ---")
    return "run", text
