"""MCP server fixture that violates the JSON-RPC envelope (P02): missing jsonrpc member."""

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
            # valid envelope: handshake must succeed
            send(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "protocolVersion": msg["params"]["protocolVersion"],
                        "capabilities": {},
                        "serverInfo": {"name": "fixture-malformed", "version": "1.0.0"},
                    },
                }
            )
        elif method == "ping":
            # missing the required jsonrpc member
            send({"id": rid, "result": {}})
            # follow up with the proper reply so correlation checks still pass
            send({"jsonrpc": "2.0", "id": rid, "result": {}})
        else:
            send({"jsonrpc": "2.0", "id": rid, "result": {}})


if __name__ == "__main__":
    main()
