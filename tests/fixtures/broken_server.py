"""Broken MCP server fixture: pollutes stdout and never answers initialize."""

import sys

sys.stdout.write("hello there, this is not JSON-RPC\n")
sys.stdout.write("still not jsonrpc\n")
sys.stdout.flush()

try:
    for line in sys.stdin:
        pass
except KeyboardInterrupt:
    pass
