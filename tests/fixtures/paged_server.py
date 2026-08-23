"""Paginated MCP server fixture: tools/list spans two pages via nextCursor; all valid."""

import json
import sys

PAGE1 = [
    {
        "name": "echo",
        "description": "Echo text back.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "add",
        "description": "Add two numbers.",
        "annotations": {"readOnlyHint": True, "idempotentHint": True},
        "title": "Adder",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "number", "default": 0},
                "b": {"type": "number", "default": 0},
            },
            "required": ["a", "b"],
        },
    },
]
PAGE2 = [
    {
        "name": "now",
        "description": "Current time.",
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
        method = msg.get("method")
        rid = msg.get("id")
        if "id" not in msg:
            continue
        result = {}
        if method == "initialize":
            result = {
                "protocolVersion": msg["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fixture-paged", "version": "1.0.0"},
            }
        elif method == "tools/list":
            cursor = (msg.get("params") or {}).get("cursor")
            if not cursor:
                result = {"tools": PAGE1, "nextCursor": "page-2"}
            elif cursor == "page-2":
                result = {"tools": PAGE2}
            else:
                result = {}
        elif method == "ping":
            result = {}
        send({"jsonrpc": "2.0", "id": rid, "result": result})


if __name__ == "__main__":
    main()
