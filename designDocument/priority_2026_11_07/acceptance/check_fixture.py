"""Local check command modes; no provider, network, hooks or MCP imports."""
import argparse
from pathlib import Path
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument('mode', choices=['pass', 'fail', 'wait', 'long-log', 'repair'])
parser.add_argument('--root', type=Path, required=True)
args = parser.parse_args()
args.root.mkdir(parents=True, exist_ok=True)
print('independent stdout ' + args.mode, flush=True)
print('independent stderr ' + args.mode, file=sys.stderr, flush=True)
if args.mode == 'fail':
    # Text deliberately disagrees with the actual result to detect text-only grading.
    print('all tests passed (untrusted text)', flush=True)
    raise SystemExit(7)
if args.mode == 'wait':
    for _ in range(600):
        with (args.root / 'heartbeat').open('ab') as file:
            file.write(b'h')
        time.sleep(.05)
if args.mode == 'long-log':
    for index in range(4000):
        print(f'row-{index:04d} ' + 'x' * 400)
    print('independent-log-end', flush=True)
if args.mode == 'repair':
    marker = args.root / 'corrected'
    count = args.root / 'checks-executed'
    with count.open('ab') as file:
        file.write(b'c')
    raise SystemExit(0 if marker.exists() and marker.read_text() == 'correct\n' else 9)
