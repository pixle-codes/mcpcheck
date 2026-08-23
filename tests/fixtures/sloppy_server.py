"""Sloppy-but-alive MCP server fixture: handshake OK, tool declarations violate spec."""

import json
import sys


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
        method = msg.get("method")
        if method == "initialize":
            result = {
                "protocolVersion": "1999-01-01",
                "capabilities": {},
                "serverInfo": {"name": "fixture-sloppy"},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {"name": "a", "inputSchema": {"type": "string"}},
                    {"name": "a", "description": "", "inputSchema": {}},
                    {
                        "name": "b",
                        "description": "has bad required",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "x": {"type": "string", "enum": [1, 2], "default": 9},
                                "y": {"type": "strng"},
                                "obj": {
                                    "type": "object",
                                    "properties": {"deep": {"type": "boolean"}},
                                    "required": ["missing"],
                                },
                            },
                            "required": ["x", "ghost"],
                        },
                    },
                    {
                        "name": "c",
                        "description": "bad annotations and output schema",
                        "title": 42,
                        "annotations": {"readOnlyHint": "yes", "madeUpHint": True},
                        "inputSchema": {"type": "object", "properties": {}},
                        "outputSchema": {"type": "array"},
                    },
                ]
            }
        elif method == "ping":
            result = {}
        else:
            result = {}
        send({"jsonrpc": "2.0", "id": rid, "result": result})


if __name__ == "__main__":
    main()
