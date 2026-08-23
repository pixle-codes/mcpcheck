"""Threaded stdlib http.server speaking Streamable-HTTP MCP for tests.

Usage:
    srv = McpHttpServer(mode="json")   # or "sse", "no_auth_header", "html", ...
    srv.start(); srv.url; ...; srv.stop()

Modes control response flavor and auth behavior.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PRM_DOC = {
    "resource": None,  # filled at request time to match the served origin
    "authorization_servers": ["https://auth.example.com"],
    "scopes_supported": ["mcp:tools"],
}

TOOLS = [
    {
        "name": "echo",
        "description": "Echo the input.",
        "inputSchema": {"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]},
    }
]


def _rpc_result(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _rpc_error(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def handle_rpc(msg, state):
    method = msg.get("method")
    rid = msg.get("id")
    if method == "initialize":
        if rid is None:
            return None
        if state["mode"] == "session":
            state["session"] = "sess-" + str(state["counter"])
            state["counter"] += 1
        resp = _rpc_result(
            rid,
            {
                "protocolVersion": msg["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fixture-http", "version": "1.0"},
            },
        )
        return resp, {"Mcp-Session-Id": state["session"]} if state.get("session") else {}
    if rid is None:
        return None
    if method == "ping":
        return _rpc_result(rid, {})
    if method == "tools/list":
        return _rpc_result(rid, {"tools": TOOLS})
    return _rpc_error(rid, -32601, f"unknown method {method}")


class McpHttpServer:
    def __init__(self, mode="json"):
        assert mode in (
            "json", "sse", "session", "no_auth_header", "with_auth_header",
            "prm_bad", "html", "malformed", "get_405", "get_stream", "get_weird",
        )
        self.mode = mode
        self.url = None
        self.seen_headers = []
        self._srv = None
        self._thread = None

    def start(self):
        outer = self
        state = {"mode": outer.mode, "session": None, "counter": 0}

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, ctype, body_bytes, extra=None):
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body_bytes)))
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body_bytes)

            def do_POST(self):
                outer.seen_headers.append(dict(self.headers))
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                if outer.mode in ("no_auth_header", "with_auth_header", "prm_bad"):
                    if "authorization" not in {k.lower() for k in self.headers}:
                        if outer.mode == "no_auth_header":
                            self._send(401, "text/plain", b"unauthorized")
                        else:
                            prm = f"{self._origin()}/.well-known/oauth-protected-resource"
                            self._send(
                                401,
                                "text/plain",
                                b"unauthorized",
                                {"WWW-Authenticate": f'Bearer realm="mcp", resource_metadata="{prm}"'},
                            )
                        return
                try:
                    msg = json.loads(raw.decode("utf-8"))
                except ValueError:
                    self._send(400, "application/json", b'{"error":"bad json"}')
                    return
                if state.get("session") and self.headers.get("Mcp-Session-Id") != state["session"]:
                    self._send(404, "text/plain", b"unknown session")
                    return
                out = handle_rpc(msg, state)
                if out is None:
                    self._send(202, "text/plain", b"accepted")
                    return
                resp, extra = out if isinstance(out, tuple) else (out, {})
                if outer.mode == "malformed":
                    resp.pop("jsonrpc", None)
                body = json.dumps(resp).encode("utf-8")
                if outer.mode == "html":
                    self._send(200, "text/html; charset=utf-8", b"<html>hello</html>")
                    return
                if outer.mode == "sse":
                    sse = ("event: message\r\ndata: " + json.dumps(resp) + "\r\n\r\n: keepalive\r\n\r\n").encode("utf-8")
                    self._send(200, "text/event-stream", sse, extra)
                    return
                self._send(200, "application/json", body, extra)

            def _origin(self):
                host = self.headers.get("Host") or "localhost"
                return f"http://{host}"

            def do_GET(self):
                if self.path == "/.well-known/oauth-protected-resource":
                    doc = dict(PRM_DOC)
                    doc["resource"] = self._origin() + "/"
                    if outer.mode == "prm_bad":
                        doc["resource"] = None
                        doc["scopes_supported"] = [42]
                    self._send(200, "application/json", json.dumps(doc).encode("utf-8"))
                    return
                if outer.mode == "get_405":
                    self._send(405, "text/plain", b"method not allowed")
                    return
                if outer.mode == "get_weird":
                    self._send(418, "text/plain", b"teapot")
                    return
                if outer.mode == "get_stream":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    return
                self._send(404, "text/plain", b"not found")

        self._state = state
        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()
        host, port = self._srv.server_address[:2]
        self.url = f"http://{host}:{port}/mcp"
        return self

    def stop(self):
        if self._srv:
            self._srv.shutdown()
            self._srv.server_close()
            self._thread.join(timeout=3)
