"""MCP server fixture with a broken pagination loop: nextCursor never advances."""

import json
import sys

TOOLS = [
    {
        "name": "only",
        "description": "Only tool.",
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
                "protocolVersion": msg["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fixture-loop", "version": "1.0.0"},
            }
        elif msg.get("method") == "tools/list":
            result = {"tools": TOOLS, "nextCursor": "same"}
        elif msg.get("method") == "ping":
            result = {}
        send({"jsonrpc": "2.0", "id": rid, "result": result})


if __name__ == "__main__":
    main()
