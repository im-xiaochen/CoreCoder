"""Isolated, bounded Python-regex scanner; invoked only as a subprocess."""
import json
import re
import sys
from pathlib import Path

MAX_FILE_BYTES = 1_000_000
MAX_TOTAL_BYTES = 20_000_000

MAX_RESULT_BYTES = 100_000


def scan(request):
    try:
        regex = re.compile(request["pattern"])
    except re.error as exc:
        return f"Invalid regex: {exc}"
    matches = []
    found = 0
    total = 0
    result_bytes = 0
    reasons = set()
    more = False
    for filename in request["files"]:
        fp = Path(filename)
        if request["include"] and not fp.match(request["include"]):
            continue
        try:
            with fp.open("rb") as stream:
                raw = stream.read(min(MAX_FILE_BYTES + 1, MAX_TOTAL_BYTES - total + 1))
            total += len(raw)
            if total > MAX_TOTAL_BYTES:
                reasons.add("byte scan limit")
                break
            if len(raw) > MAX_FILE_BYTES:
                reasons.add("oversized files skipped")
                continue
            if b"\0" in raw:
                continue
            for lineno, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
                if len(line) > 10000:
                    reasons.add("long lines skipped")
                    continue
                if not regex.search(line):
                    continue
                found += 1
                if found <= request["offset"]:
                    continue
                if len(matches) >= request["limit"]:
                    more = True
                    break
                row = f"{fp}:{lineno}: {line.rstrip()}"
                size = len(row.encode("utf-8")) + 1
                if result_bytes + size > MAX_RESULT_BYTES:
                    more = True
                    break
                result_bytes += size
                matches.append(row)
            if more:
                break
        except OSError:
            reasons.add("unreadable files skipped")
    result = "\n".join(matches) or "No matches found."
    if more:
        result += f"\n... next_offset={request['offset'] + len(matches)}"
    if request["truncated"]:
        reasons.add("file scan limit")
    if reasons:
        result += "\n... incomplete: " + ", ".join(sorted(reasons))
    return result + f"\n[backend: {request['backend']}; live scan]"


if __name__ == "__main__":
    print(json.dumps(scan(json.load(sys.stdin)), ensure_ascii=True))
