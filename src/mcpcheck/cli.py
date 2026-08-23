import argparse
import sys

from .checks import run_checks
from .report import emit


def build_parser():
    p = argparse.ArgumentParser(
        prog="mcpcheck",
        description="Headless conformance and smoke-test runner for MCP servers.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="spawn a server and run all conformance checks")
    run.add_argument("cmd", nargs=argparse.REMAINDER, help="server command to spawn, after --")
    run.add_argument("--timeout", type=float, default=10.0, help="per-request timeout in seconds")
    run.add_argument("--protocol-version", default="2025-06-18", help="protocolVersion to offer")
    run.add_argument("--json", action="store_true", dest="as_json", help="machine-readable output")

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    cmd = args.cmd
    while cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        parser.error("run requires a server command, e.g. mcpcheck run -- python server.py")
    report = run_checks(
        cmd,
        timeout=args.timeout,
        protocol_version=args.protocol_version,
    )
    code = emit(report, as_json=args.as_json)
    sys.exit(code)


if __name__ == "__main__":
    main()
