"""Dispatch local diagnostics, the web workbench, or the interactive CLI."""
import sys


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "doctor":
        from .doctor import main as doctor_main
        raise SystemExit(doctor_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "web":
        from web_backend.server import workbench_main
        return workbench_main(sys.argv[2:])
    from .cli import main as cli_main
    return cli_main()
