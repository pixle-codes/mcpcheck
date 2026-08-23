"""JSON-RPC 2.0 client speaking line-delimited MCP over a spawned subprocess (stdio)."""

import json
import os
import queue
import signal
import subprocess
import threading


class SpawnError(Exception):
    pass


class Client:
    def __init__(self, cmd, timeout=10.0):
        self.cmd = cmd
        self.timeout = timeout
        self.pollution = []
        self.malformed = []
        self.stderr_lines = []
        self.notifications = []
        try:
            self.proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                start_new_session=True,
            )
        except OSError as exc:
            raise SpawnError(f"cannot spawn {cmd!r}: {exc}") from exc
        self._inbox = queue.Queue()
        self._next_id = 1
        self._alive = True
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

    def _read_stdout(self):
        try:
            for raw in self.proc.stdout:
                line = raw.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    if len(self.pollution) < 50:
                        self.pollution.append(line[:300])
                    continue
                if not isinstance(msg, dict):
                    self._record_malformed(line)
                    continue
                envelope_ok = msg.get("jsonrpc") == "2.0"
                if "id" in msg and ("result" in msg or "error" in msg):
                    if not envelope_ok and len(self.malformed) < 50:
                        self.malformed.append(
                            f"response missing jsonrpc:\"2.0\": {line[:200]}"
                        )
                    self._inbox.put(msg)
                elif "method" in msg:
                    if len(self.notifications) < 200:
                        self.notifications.append(msg)
                    if not envelope_ok and len(self.malformed) < 50:
                        self.malformed.append(
                            f"message missing jsonrpc:\"2.0\": {line[:200]}"
                        )
                else:
                    self._record_malformed(line)
        except (ValueError, OSError):
            pass

    def _record_malformed(self, line):
        if len(self.malformed) < 50:
            self.malformed.append(f"not a JSON-RPC message: {line[:200]}")

    def _drain_stderr(self):
        try:
            for raw in self.proc.stderr:
                if len(self.stderr_lines) < 200:
                    self.stderr_lines.append(raw.rstrip("\n")[:300])
        except (ValueError, OSError):
            pass

    def _send(self, obj):
        if self.proc.stdin is None or self.proc.stdin.closed:
            raise SpawnError("server stdin is closed")
        try:
            data = json.dumps(obj, separators=(",", ":")) + "\n"
            self.proc.stdin.write(data)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise SpawnError(f"server closed stdin: {exc}") from exc

    def request(self, method, params=None, timeout=None):
        rid = self._next_id
        self._next_id += 1
        msg = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)
        deadline = timeout if timeout is not None else self.timeout
        return self._wait_response(rid, deadline)

    def _wait_response(self, rid, timeout):
        end = _monotonic() + timeout
        pending = []
        while True:
            remaining = end - _monotonic()
            if remaining <= 0:
                for m in pending:
                    self._inbox.put(m)
                raise TimeoutError(f"no response to request id={rid} within {timeout:.1f}s")
            try:
                msg = self._inbox.get(timeout=min(remaining, 0.25))
            except queue.Empty:
                continue
            if msg.get("id") == rid:
                return msg
            pending.append(msg)

    def notify(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)

    def close(self, grace=2.0):
        if not self._alive:
            return self.proc.returncode
        self._alive = False
        try:
            if self.proc.stdin and not self.proc.stdin.closed:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(timeout=grace)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except OSError:
                    pass
        for stream in (self.proc.stdout, self.proc.stderr):
            try:
                if stream and not stream.closed:
                    stream.close()
            except OSError:
                pass
        return self.proc.returncode

    @property
    def crashed(self):
        return self.proc.poll() is not None

    def stderr_tail(self, n=10):
        return "\n".join(self.stderr_lines[-n:])


def _monotonic():
    import time

    return time.monotonic()
