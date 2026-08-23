"""Warnings-only MCP server fixture: zero spec errors, several warnings (for --fail-on)."""

import json
import sys

TOOLS = [
    {
        "name": "quiet",
        "inputSchema": {"type": "object", "properties": {}},
    }
]


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        rid = msg.get("id")
        if "id" not in msg:
            continue
        result = {}
        if msg.get("method") == "initialize":
            result = {
                # unknown revision -> C03 warning
                "protocolVersion": "1999-01-01",
                "capabilities": {"tools": {}},
                # no version -> C04 warning
                "serverInfo": {"name": "fixture-warny"},
            }
        elif msg.get("method") == "tools/list":
            result = {"tools": TOOLS}
        elif msg.get("method") == "ping":
            result = {}
        send({"jsonrpc": "2.0", "id": rid, "result": result})


if __name__ == "__main__":
    main()
