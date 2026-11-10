"""Explicit, bounded loading of project and bundled skill text."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

MAX_BYTES = 32 * 1024
MAX_AUXILIARY = 8
MAX_ENTRIES = 256
_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_BUILTIN = Path(__file__).parent / "builtin_skills"


@dataclass(frozen=True)
class SkillEntry:
    name: str
    source: str
    directory: Path
    valid: bool = True
    conflict: bool = False
    anchor: Path | None = None


@dataclass(frozen=True)
class LoadedSkill:
    name: str
    source: str
    version: str
    files: tuple[str, ...]
    text: str


def _link(path):
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()) or bool(
        getattr(path.lstat(), "st_file_attributes", 0) & 0x400
    )


def _safe(root, relative):
    path = Path(relative)
    if path.is_absolute() or not path.parts or any(part in {"..", "."} for part in path.parts):
        raise ValueError("Invalid skill path")
    current = root
    for part in path.parts:
        name = part.casefold()
        if (name in {".git", ".ssh", ".aws", ".env", "credentials", "credentials.json", "id_rsa", "id_ed25519"}
                or name.startswith(".env.") and name not in {".env.example", ".env.sample", ".env.template"}
                or name.endswith((".key", ".pem", ".p12", ".pfx"))):
            raise ValueError("Sensitive skill path")
        current = current / part
        if _link(current):
            raise ValueError("Skill links are not allowed")
    if not current.resolve().is_relative_to(root.resolve()) or not current.is_file():
        raise ValueError("Skill file not found or outside skill directory")
    return current


def list_skills(workspace_root):
    root = Path(workspace_root).absolute()
    current = Path(root.anchor)
    for part in root.parts[1:]:
        current = current / part
        if _link(current):
            raise ValueError("Workspace path is redirected")
    if root.resolve() != root:
        raise ValueError("Workspace path is not canonical")
    entries = []
    for directory, source in ((_BUILTIN, "builtin"), (root / ".corecoder" / "skills", ".corecoder/skills")):
        try:
            if source != "builtin":
                current = root
                for part in (".corecoder", "skills"):
                    current = current / part
                    if _link(current):
                        raise ValueError("Skill directory links are not allowed")
            children = []
            for child in directory.iterdir():
                children.append(child)
                if len(children) > MAX_ENTRIES:
                    raise ValueError("Too many skill entries")
            children.sort(key=lambda p: p.name)
        except FileNotFoundError:
            continue
        if len(children) > MAX_ENTRIES:
            raise ValueError("Too many skill entries")
        for child in children:
            if _link(child) or not child.is_dir():
                continue
            name = child.name
            entries.append(SkillEntry(name, source if source == "builtin" else f"{source}/{name}", child,
                                      bool(_NAME.fullmatch(name)), anchor=root if source != "builtin" else _BUILTIN))
    counts = {e.name: sum(x.name == e.name for x in entries) for e in entries}
    return tuple(SkillEntry(e.name, e.source, e.directory, e.valid, counts[e.name] > 1, e.anchor) for e in entries)


def resolve_skill(snapshot, name, source=None):
    if not _NAME.fullmatch(name):
        raise ValueError("Invalid skill name; use lowercase ASCII letters, digits and hyphens")
    candidates = [e for e in snapshot if e.name == name and e.valid and (source is None or e.source == source)]
    if len(candidates) != 1:
        raise ValueError("Skill not found or ambiguous; specify its source")
    entry = candidates[0]
    if entry.anchor is None:
        raise ValueError("Skill has no trusted anchor")
    try:
        current = entry.anchor
        if _link(current) or current.resolve() != current:
            raise ValueError("Skill anchor is redirected")
        for part in entry.directory.relative_to(entry.anchor).parts:
            current = current / part
            if _link(current):
                raise ValueError("Skill directory links are not allowed")
    except (OSError, ValueError) as exc:
        raise ValueError("Skill directory is unavailable or redirected") from exc
    files = []
    used = 0

    def read(relative):
        nonlocal used
        path = _safe(entry.directory, relative)
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES - used + 1)
        used += len(raw)
        if used > MAX_BYTES:
            raise ValueError("Skill exceeds 32 KiB budget")
        if b"\0" in raw:
            raise ValueError("Binary skill file")
        files.append(relative)
        return raw.decode("utf-8-sig")

    body = read("SKILL.md").replace("\r\n", "\n")
    version = None
    auxiliary = []
    if body.startswith("---\n"):
        head, sep, rest = body[4:].partition("\n---\n")
        if not sep:
            raise ValueError("Invalid skill metadata")
        seen = set()
        for line in head.splitlines():
            key, colon, value = line.partition(":")
            key = key.strip()
            if not colon or key not in {"version", "files"} or key in seen or not value.strip():
                raise ValueError("Invalid or duplicate skill metadata")
            seen.add(key)
            if colon and key.strip() == "version":
                version = value.strip()
            elif colon and key.strip() == "files":
                auxiliary = [v.strip() for v in value.split(",")]
                if any(not v for v in auxiliary):
                    raise ValueError("Invalid auxiliary file list")
        body = rest
    if len(auxiliary) > MAX_AUXILIARY or len(set(auxiliary)) != len(auxiliary):
        raise ValueError("Invalid auxiliary file list")
    texts = [body]
    for path in auxiliary:
        texts.append(f"\n--- {path} ---\n" + read(path))
    text = "\n".join(texts)
    version = version or "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return LoadedSkill(name, entry.source, version, tuple(files), text)
