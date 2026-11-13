"""Lightweight entry point for local diagnostics and the interactive CLI."""
import sys


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "doctor":
        from .doctor import main as doctor_main
        raise SystemExit(doctor_main(sys.argv[2:]))
    from .cli import main as cli_main
    return cli_main()
