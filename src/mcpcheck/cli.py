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

    run = sub.add_parser(
        "run",
        help="run all conformance checks against a server (spawn via stdio, or --url for HTTP)",
    )
    run.add_argument("cmd", nargs=argparse.REMAINDER, help="server command to spawn, after --")
    run.add_argument("--url", default=None, help="MCP endpoint URL for the Streamable HTTP transport")
    run.add_argument("--timeout", type=float, default=10.0, help="per-request timeout in seconds")
    run.add_argument("--protocol-version", default="2025-06-18", help="protocolVersion to offer")
    run.add_argument(
        "--fail-on",
        choices=("error", "warning"),
        default="error",
        help="lowest severity that fails the run (exit code 1)",
    )
    run.add_argument("--json", action="store_true", dest="as_json", help="machine-readable output")

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    cmd = args.cmd
    while cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd and not args.url:
        parser.error(
            "run requires a server command after -- (stdio) or a --url (Streamable HTTP), "
            "e.g. mcpcheck run --url http://localhost:8000/mcp"
        )
    if cmd and args.url:
        parser.error("pass either --url or a command to spawn, not both")
    report = run_checks(
        cmd or None,
        timeout=args.timeout,
        protocol_version=args.protocol_version,
        url=args.url,
    )
    code = emit(report, as_json=args.as_json, fail_on=args.fail_on)
    sys.exit(code)


if __name__ == "__main__":
    main()
