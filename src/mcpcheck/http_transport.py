"""Streamable HTTP transport for MCP (POST JSON-RPC, Accept json+SSE, session ids)."""

import http.client
import json
import socket
import urllib.error
import urllib.request


class HttpTransport:
    """Same duck-typed surface as client.Client: request/notify/close, pollution,
    malformed, notifications buckets, failure_note()."""

    kind = "http"

    def __init__(self, url, timeout=10.0):
        self.url = url
        self.timeout = timeout
        self.pollution = []
        self.malformed = []
        self.notifications = []
        self.content_type_violations = []
        self.session_id = None
        self.server_assigned_session = False
        self.protocol_version_header = None
        self.last_http_status = None
        self.connection_error = None
        # auth state captured during connect/auth probes
        self.auth_challenge_status = None
        self.www_authenticate = None

    def _headers(self, extra=None):
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        if self.protocol_version_header:
            h["Mcp-Protocol-Version"] = self.protocol_version_header
        if extra:
            h.update(extra)
        return h

    def _post_raw(self, body_bytes, headers, timeout):
        req = urllib.request.Request(self.url, data=body_bytes, headers=headers, method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
            return resp.status, _ci_dict(resp.headers), resp
        except urllib.error.HTTPError as e:
            return e.code, _ci_dict(e.headers), e
        except (urllib.error.URLError, OSError, socket.timeout) as exc:
            raise ConnectionError(f"cannot reach {self.url}: {exc}") from exc

    def post_message(self, msg, timeout=None):
        """POST one JSON-RPC message; returns (status, headers, parsed_messages).

        parsed_messages is the list of JSON-RPC messages extracted from the body
        (single JSON object or SSE data frames). Raises ConnectionError on
        network-level failure. Non-2xx responses return status + parsed body.
        """
        t = timeout if timeout is not None else self.timeout
        body = json.dumps(msg, separators=(",", ":")).encode("utf-8")
        status, headers, stream = self._post_raw(body, self._headers(), t)
        self.last_http_status = status
        if status == 401:
            self.auth_challenge_status = 401
            self.www_authenticate = headers.get("www-authenticate")
        sid = headers.get("mcp-session-id")
        if sid and not self.server_assigned_session:
            self.server_assigned_session = True
            self.session_id = sid
        ctype = (headers.get("content-type") or "").split(";")[0].strip().lower()
        try:
            raw = stream.read()
        finally:
            try:
                stream.close()
            except OSError:
                pass
        messages = []
        if status in (202,) or not raw:
            return status, headers, messages
        if status < 200 or status > 299:
            # Error-status bodies are informational (JSON-RPC errors, HTML error
            # pages); they never count as protocol pollution.
            if ctype in ("application/json", "application/json-rpc"):
                try:
                    obj = json.loads(raw.decode("utf-8"))
                    if isinstance(obj, dict):
                        messages.append(obj)
                except ValueError:
                    pass
            return status, headers, messages
        if ctype == "text/event-stream":
            messages = self._parse_sse(raw.decode("utf-8", errors="replace"))
        elif ctype in ("application/json", "application/json-rpc"):
            messages = self._parse_json_body(raw, ctype_ok=True)
        else:
            note = f"status {status} Content-Type {ctype!r}"
            if len(self.content_type_violations) < 20:
                self.content_type_violations.append(note)
            messages = self._parse_json_body(raw, ctype_ok=False)
        for m in messages:
            self._classify(m, raw if ctype not in ("text/event-stream", "application/json") else None)
        return status, headers, messages

    def _parse_json_body(self, raw, ctype_ok):
        try:
            obj = json.loads(raw.decode("utf-8"))
        except ValueError:
            snippet = raw[:200].decode("utf-8", errors="replace").strip()
            if len(self.pollution) < 50:
                label = "unparseable response body" if ctype_ok else f"{self.last_http_status} body"
                self.pollution.append(f"{label}: {snippet}")
            return []
        return [obj] if isinstance(obj, dict) else []

    def _parse_sse(self, text):
        out = []
        data_lines = []
        for block in text.replace("\r\n", "\n").split("\n\n"):
            data_lines = []
            event_name = "message"
            for line in block.split("\n"):
                if not line or line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())
                elif line.startswith("data"):
                    data_lines.append("")
            if not data_lines:
                continue
            payload = "\n".join(data_lines)
            if event_name != "message":
                continue
            try:
                obj = json.loads(payload)
            except ValueError:
                if len(self.malformed) < 50:
                    self.malformed.append(f"SSE data not JSON: {payload[:200]}")
                continue
            if isinstance(obj, dict):
                out.append(obj)
        return out

    def _classify(self, msg, raw_pollution_context=None):
        envelope_ok = msg.get("jsonrpc") == "2.0"
        if "id" in msg and ("result" in msg or "error" in msg):
            if not envelope_ok and len(self.malformed) < 50:
                self.malformed.append(f'response missing jsonrpc:"2.0": {str(msg)[:200]}')
        elif "method" in msg:
            if len(self.notifications) < 200:
                self.notifications.append(msg)
            if not envelope_ok and len(self.malformed) < 50:
                self.malformed.append(f'message missing jsonrpc:"2.0": {str(msg)[:200]}')
        else:
            if len(self.malformed) < 50:
                self.malformed.append(f"not a JSON-RPC message: {str(msg)[:200]}")

    def request(self, method, params=None, timeout=None):
        rid = getattr(self, "_next_id", 0) + 1
        self._next_id = rid
        msg = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        status, headers, messages = self.post_message(msg, timeout=timeout)
        if status >= 400:
            detail = ""
            err_resp = next((m for m in messages if m.get("id") == rid), None)
            if err_resp and "error" in err_resp:
                detail = f": {err_resp['error']}"
            raise HttpError(status, f"HTTP {status} from server for {method}{detail}")
        for m in messages:
            if m.get("id") == rid and ("result" in m or "error" in m):
                return m
        raise TimeoutError(
            f"no response to '{method}' (id={rid}) in HTTP response (status {status})"
        )

    def notify(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        try:
            self.post_message(msg)
        except ConnectionError:
            raise

    def get_stream_probe(self, path_timeout=4.0):
        """Open GET on the MCP endpoint with Accept: text/event-stream.
        Returns (status_or_'timeout', content_type)."""
        req = urllib.request.Request(
            self.url,
            headers={"Accept": "text/event-stream", **{
                k: v for k, v in self._headers().items() if k != "Accept"
            }},
            method="GET",
        )
        try:
            resp = urllib.request.urlopen(req, timeout=path_timeout)
            return resp.status, (resp.headers.get("content-type") or "")
        except urllib.error.HTTPError as e:
            try:
                e.read()
            except OSError:
                pass
            return e.code, (e.headers.get("content-type") or "" if e.headers else "")
        except (urllib.error.URLError, OSError, socket.timeout, http.client.HTTPException) as exc:
            reason = getattr(exc, "reason", None) or exc
            if isinstance(reason, socket.timeout) or isinstance(exc, socket.timeout) or "timed out" in str(reason).lower():
                return "timeout", ""
            return "error", ""

    def close(self):
        pass

    @property
    def crashed(self):
        return self.connection_error is not None

    def failure_note(self):
        if self.connection_error:
            return f"connection failed ({self.connection_error})"
        return ""

    def stderr_tail(self, n=10):
        return ""


class HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _ci_dict(message):
    """Case-insensitive header view (uvicorn sends lowercase, others title-case)."""
    try:
        return {k.lower(): v for k, v in message.items()}
    except AttributeError:
        return {}
