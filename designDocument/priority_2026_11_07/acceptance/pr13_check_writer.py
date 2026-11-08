"""Real quiet-after-first-output process for PR13 incremental log acceptance."""
import argparse
from pathlib import Path
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument("root", type=Path)
parser.add_argument("--large", action="store_true")
args = parser.parse_args()
args.root.mkdir(parents=True, exist_ok=True)
with (args.root / "execution-count").open("a", encoding="utf-8") as file:
    file.write("started\n")
print("PR13 stdout before release", flush=True)
print("PR13 stderr before release", file=sys.stderr, flush=True)
if args.large:
    for _ in range(384):
        print("B" * 8192, flush=True)
(args.root / "started").write_text(str(__import__('os').getpid()), encoding="utf-8")
deadline = time.monotonic() + 25
while not (args.root / "release").exists() and time.monotonic() < deadline:
    time.sleep(0.02)
print("PR13 final stdout", flush=True)
raise SystemExit(7)
