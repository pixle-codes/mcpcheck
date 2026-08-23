"""Spec-derived checks run against a spawned MCP server."""

import dataclasses
from typing import Any, List, Optional

from .client import Client
from .http_transport import HttpError

KNOWN_VERSIONS = {
    "2024-11-05",
    "2025-03-26",
    "2025-06-18",
    "2025-11-25",
    "2026-07-28",
}

JSON_TYPES = ("array", "boolean", "integer", "null", "number", "object", "string")

MAX_PAGES = 20
MAX_SCHEMA_DEPTH = 6

ANNOTATION_HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")


@dataclasses.dataclass
class Finding:
    code: str
    severity: str  # "error" | "warning" | "info"
    message: str

    def as_dict(self):
        return dataclasses.asdict(self)


def _err(code, msg):
    return Finding(code, "error", msg)


def _warn(code, msg):
    return Finding(code, "warning", msg)


def _ok(code, msg):
    return Finding(code, "info", msg)


class Report:
    def __init__(self):
        self.findings: List[Finding] = []
        self.spawn_error: Optional[str] = None
        self.stderr_tail: str = ""

    def add(self, finding):
        self.findings.append(finding)

    @property
    def has_errors(self):
        return any(f.severity == "error" for f in self.findings) or self.spawn_error is not None

    def exit_code_for(self, fail_on="error"):
        if self.spawn_error is not None:
            return 2
        if any(f.severity == "error" for f in self.findings):
            return 1
        if fail_on == "warning" and any(f.severity == "warning" for f in self.findings):
            return 1
        return 0

    @property
    def exit_code(self):
        return self.exit_code_for("error")

    def summary(self):
        errors = sum(1 for f in self.findings if f.severity == "error")
        warnings = sum(1 for f in self.findings if f.severity == "warning")
        passed = len(self.findings) - errors - warnings
        return {"errors": errors, "warnings": warnings, "passed": passed}

    def as_dict(self):
        return {
            "spawn_error": self.spawn_error,
            "findings": [f.as_dict() for f in self.findings],
            "summary": self.summary(),
        }


def _initialize(client: Client, protocol_version: str):
    return client.request(
        "initialize",
        {
            "protocolVersion": protocol_version,
            "capabilities": {},
            "clientInfo": {"name": "mcpcheck", "version": "0.1.0"},
        },
    )


def check_handshake(client: Client, report: Report, protocol_version: str) -> Optional[dict]:
    try:
        resp = _initialize(client, protocol_version)
    except TimeoutError:
        report.add(_err("C01", "server did not answer 'initialize' within timeout"))
        note = client.failure_note()
        if note:
            report.add(_err("C01b", f"{note} during handshake"))
        return None
    except HttpError as exc:
        if getattr(client, "kind", "") == "http" and exc.status == 401:
            report.add(
                _ok("C01", "endpoint requires authentication (HTTP 401); running auth-discovery checks")
            )
            return None
        report.add(_err("C01", f"handshake failed: {exc}"))
        return None
    except ConnectionError as exc:
        report.spawn_error = str(exc)
        return None
    except Exception as exc:
        report.add(_err("C01", f"handshake failed: {exc}"))
        return None

    if "error" in resp and resp["error"] is not None:
        report.add(_err("C02", f"initialize returned JSON-RPC error: {resp['error']}"))
        return None
    result = resp.get("result")
    if not isinstance(result, dict):
        report.add(_err("C02", "initialize response missing valid 'result' object"))
        return None

    if resp.get("jsonrpc") != "2.0":
        report.add(_err("C02", f"response 'jsonrpc' must be \"2.0\", got {resp.get('jsonrpc')!r}"))
    else:
        report.add(_ok("C02", "jsonrpc version echoed correctly"))

    if resp.get("id") is None:
        report.add(_err("C02", "initialize response did not echo request id"))
    else:
        report.add(_ok("C02", "request id echoed correctly"))

    pv = result.get("protocolVersion")
    if not isinstance(pv, str) or not pv:
        report.add(_err("C03", "'protocolVersion' must be a non-empty string"))
    elif pv != protocol_version:
        report.add(
            _warn("C03", f"server negotiated protocolVersion {pv!r}, requested {protocol_version!r}")
        )
    else:
        report.add(_ok("C03", f"protocolVersion {pv!r} negotiated"))
    if isinstance(pv, str) and pv not in KNOWN_VERSIONS:
        report.add(_warn("C03", f"protocolVersion {pv!r} is not a known spec revision"))

    info = result.get("serverInfo")
    if not isinstance(info, dict) or not isinstance(info.get("name"), str) or not info.get("name"):
        report.add(_err("C04", "'serverInfo.name' (non-empty string) is required"))
    else:
        report.add(_ok("C04", f"serverInfo.name={info['name']!r}"))
        if not isinstance(info.get("version"), str):
            report.add(_warn("C04", "'serverInfo.version' should be a string"))

    caps = result.get("capabilities")
    if caps is None:
        report.add(_err("C04", "'capabilities' object is required in initialize result"))
        caps = {}
    elif not isinstance(caps, dict):
        report.add(_err("C04", "'capabilities' must be an object"))
        caps = {}
    else:
        report.add(_ok("C04", "capabilities present"))
    return {"result": result, "capabilities": caps}


def check_ping_and_notify(client: Client, report: Report):
    client.notify("notifications/initialized")
    try:
        resp = client.request("ping", {}, timeout=client.timeout)
    except TimeoutError:
        report.add(_err("C06", "server did not answer 'ping'"))
        return
    except Exception as exc:
        report.add(_err("C06", f"ping failed: {exc}"))
        return
    if "error" in resp and resp["error"] is not None:
        report.add(_err("C06", f"'ping' returned error: {resp['error']}"))
    elif not isinstance(resp.get("result"), dict):
        report.add(_err("C06", "'ping' result must be an empty object"))
    else:
        report.add(_ok("C06", "ping answered"))


def _json_value_matches(value, types):
    """Loose JSON-Schema primitive-type compatibility check (bool ≠ int)."""
    for t in types:
        if t == "string" and isinstance(value, str):
            return True
        if t == "boolean" and isinstance(value, bool):
            return True
        if t == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if t == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        if t == "null" and value is None:
            return True
        if t == "array" and isinstance(value, list):
            return True
        if t == "object" and isinstance(value, dict):
            return True
    return False


def _declared_types(schema):
    t = schema.get("type")
    if isinstance(t, str):
        return [t]
    if isinstance(t, list) and t and all(isinstance(x, str) for x in t):
        return t
    return None


def _check_annotations(report: Report, name: str, tool: dict):
    """Tool-level title plus the annotations object (hints are booleans, title a string)."""
    t_title = tool.get("title")
    if t_title is not None and not isinstance(t_title, str):
        report.add(_err("C12", f"tool {name!r}: title must be a string"))
    ann = tool.get("annotations")
    if ann is None:
        return
    if not isinstance(ann, dict):
        report.add(_err("C12", f"tool {name!r}: annotations must be an object"))
        return
    title = ann.get("title")
    if title is not None and not isinstance(title, str):
        report.add(_err("C12", f"tool {name!r}: annotations.title must be a string"))
    for hint in ANNOTATION_HINTS:
        if hint in ann and not isinstance(ann[hint], bool):
            report.add(
                _err("C12", f"tool {name!r}: annotations.{hint} must be a boolean (got {ann[hint]!r})")
            )
    unknown = sorted(set(ann) - {"title", *ANNOTATION_HINTS})
    if unknown:
        report.add(_warn("C12", f"tool {name!r}: unknown annotation key(s): {unknown}"))


def _lint_schema(report: Report, name: str, schema: Any, path: str, depth: int):
    if depth > MAX_SCHEMA_DEPTH:
        return
    if not isinstance(schema, dict):
        report.add(_err("C08", f"tool {name!r}: {path} must be an object"))
        return
    types = _declared_types(schema)
    bad = [t for t in types or [] if t not in JSON_TYPES]
    if bad:
        report.add(
            _err("C08", f"tool {name!r}: {path}.type is not a valid JSON Schema type: {bad}")
        )
    vtypes = [t for t in types or [] if t in JSON_TYPES]

    props = schema.get("properties")
    if props is not None:
        if not isinstance(props, dict):
            report.add(_err("C08", f"tool {name!r}: {path}.properties must be an object"))
        else:
            for pname, pschema in props.items():
                _lint_schema(report, name, pschema, f"{path}.properties.{pname}", depth + 1)

    required = schema.get("required")
    if required is not None:
        if not isinstance(required, list) or not all(isinstance(r, str) for r in required):
            report.add(_err("C08", f"tool {name!r}: {path}.required must be an array of strings"))
        elif isinstance(props, dict):
            unknown = [r for r in required if r not in props]
            if unknown:
                report.add(
                    _err("C08", f"tool {name!r}: {path}.required names not in properties: {unknown}")
                )

    items = schema.get("items")
    if items is not None:
        if not isinstance(items, dict):
            report.add(
                _err("C08", f"tool {name!r}: {path}.items must be an object under JSON Schema 2020-12")
            )
        else:
            _lint_schema(report, name, items, f"{path}.items", depth + 1)

    enum = schema.get("enum")
    if enum is not None:
        if not isinstance(enum, list) or not enum:
            report.add(_err("C08", f"tool {name!r}: {path}.enum must be a non-empty array"))
        elif vtypes:
            off = [v for v in enum if not _json_value_matches(v, types)]
            if off:
                report.add(
                    _warn("C08", f"tool {name!r}: {path}.enum values don't match declared type: {off}")
                )

    if "default" in schema and vtypes and not _json_value_matches(schema["default"], vtypes):
        report.add(
            _warn(
                "C08",
                f"tool {name!r}: {path}.default ({schema['default']!r}) doesn't match "
                f"declared type {types}",
            )
        )

    examples = schema.get("examples")
    if examples is not None:
        if not isinstance(examples, list):
            report.add(_err("C08", f"tool {name!r}: {path}.examples must be an array"))
        elif vtypes:
            off = [v for v in examples if not _json_value_matches(v, types)]
            if off:
                report.add(
                    _warn("C08", f"tool {name!r}: {path}.examples values don't match declared type: {off}")
                )


def _lint_input_schema(report: Report, name: str, schema: Any):
    if not isinstance(schema, dict):
        report.add(_err("C08", f"tool {name!r}: inputSchema must be an object"))
        return
    stype = schema.get("type")
    if stype != "object":
        report.add(_err("C08", f"tool {name!r}: inputSchema.type must be \"object\" (got {stype!r})"))
    _lint_schema(report, name, schema, "inputSchema", depth=1)


def _validate_tool(report: Report, tool: Any, seen: dict, page_label: str = "page 1"):
    if not isinstance(tool, dict):
        report.add(_err("C07", f"each entry in tools[] must be an object ({page_label})"))
        return
    name = tool.get("name")
    if not isinstance(name, str) or not name:
        report.add(_err("C07", "every tool must have a non-empty string 'name'"))
        name = "<unnamed>"
    if name in seen:
        report.add(_err("C09", f"duplicate tool name {name!r} (also on {seen[name]})"))
    seen[name] = page_label
    if not isinstance(tool.get("description"), str) or not tool["description"]:
        report.add(_warn("C07", f"tool {name!r}: missing description (models rely on it)"))
    if "inputSchema" not in tool:
        report.add(_err("C07", f"tool {name!r}: inputSchema is required"))
    else:
        _lint_input_schema(report, name, tool["inputSchema"])
    _check_annotations(report, name, tool)
    out = tool.get("outputSchema")
    if out is not None:
        if not isinstance(out, dict):
            report.add(_err("C08", f"tool {name!r}: outputSchema must be an object"))
        elif out.get("type") != "object":
            report.add(
                _err(
                    "C08",
                    f"tool {name!r}: outputSchema.type must be \"object\" (got {out.get('type')!r})",
                )
            )


def _collect_tools_page(report: Report, result: dict, seen: dict, page_label: str) -> int:
    tools = result.get("tools")
    if not isinstance(tools, list):
        report.add(_err("C07", f"'tools/list'.tools must be an array ({page_label})"))
        return 0
    if not tools and page_label != "page 1":
        report.add(
            _warn("C11", f"{page_label} returned an empty tools[] despite advertising more via nextCursor")
        )
    for tool in tools:
        _validate_tool(report, tool, seen, page_label)
    return len(tools)


def check_tools(client: Client, report: Report, capabilities: dict):
    tools_cap = capabilities.get("tools")
    try:
        resp = client.request("tools/list", {})
    except TimeoutError:
        report.add(_err("C07", "server did not answer 'tools/list'"))
        return
    except Exception as exc:
        if tools_cap is None:
            report.add(_ok("C07", "no tools capability declared; skipping tools checks"))
            return
        report.add(_err("C07", f"tools/list failed despite declared tools capability: {exc}"))
        return
    if "error" in resp and resp["error"] is not None:
        if tools_cap is None:
            report.add(_ok("C07", "no tools capability declared; skipping tools checks"))
            return
        report.add(_err("C07", f"tools/list returned error: {resp['error']}"))
        return
    result = resp.get("result")
    if not isinstance(result, dict):
        report.add(_err("C07", "'tools/list'.result must be an object"))
        return

    tools_cap_undeclared = tools_cap is None
    seen: dict = {}
    count = _collect_tools_page(report, result, seen, "page 1")
    if tools_cap_undeclared and count:
        report.add(
            _warn(
                "C07",
                "server returns tools but did not declare the 'tools' capability at initialize",
            )
        )

    cursor = result.get("nextCursor")
    pages = 1
    seen_cursors = set()
    while cursor is not None:
        pages += 1
        if pages > MAX_PAGES:
            report.add(
                _warn("C11", f"pagination did not terminate within {MAX_PAGES} pages; stopping")
            )
            break
        if not isinstance(cursor, str) or not cursor:
            report.add(
                _err("C11", f"nextCursor must be a non-empty string (got {cursor!r}); cannot fetch next page")
            )
            break
        if cursor in seen_cursors:
            report.add(_err("C11", f"pagination loop: cursor {cursor!r} repeated"))
            break
        seen_cursors.add(cursor)
        try:
            resp = client.request("tools/list", {"cursor": cursor})
        except TimeoutError:
            report.add(_err("C11", f"server did not answer 'tools/list' for cursor {cursor!r}"))
            break
        except Exception as exc:
            report.add(_err("C11", f"tools/list with cursor {cursor!r} failed: {exc}"))
            break
        if "error" in resp and resp["error"] is not None:
            report.add(_err("C11", f"tools/list returned error for cursor {cursor!r}: {resp['error']}"))
            break
        nxt = resp.get("result")
        if not isinstance(nxt, dict):
            report.add(_err("C11", f"'tools/list'.result must be an object (cursor {cursor!r})"))
            break
        count += _collect_tools_page(report, nxt, seen, f"page {pages}")
        cursor = nxt.get("nextCursor")

    loop_broken = any(f.code == "C11" and f.severity == "error" for f in report.findings)
    if pages > 1 and not loop_broken and pages <= MAX_PAGES:
        report.add(_ok("C11", f"pagination round-trip OK ({pages} page(s), {count} tool(s))"))
    report.add(_ok("C07", f"tools/list OK ({count} tool(s))"))


def check_optional_lists(client: Client, report: Report, capabilities: dict):
    for cap_name, method, key in (
        ("resources", "resources/list", "resources"),
        ("prompts", "prompts/list", "prompts"),
    ):
        if capabilities.get(cap_name) is None:
            continue
        try:
            resp = client.request(method, {})
        except Exception as exc:
            report.add(_err("C10", f"{method} failed despite declared {cap_name} capability: {exc}"))
            continue
        result = resp.get("result") or {}
        items = result.get(key)
        if not isinstance(items, list):
            report.add(_err("C10", f"{method}.{key} must be an array"))
        else:
            report.add(_ok("C10", f"{method} OK ({len(items)} item(s))"))


def check_transport_hygiene(client: Client, report: Report):
    if getattr(client, "kind", "stdio") == "http":
        if client.pollution:
            sample = "; ".join(client.pollution[:3])
            report.add(
                _err(
                    "P01",
                    f"response pollution: {len(client.pollution)} response body(s) were neither "
                    f"application/json nor parseable SSE. Sample: {sample}",
                )
            )
        else:
            report.add(_ok("P01", "all responses carried JSON or SSE payloads"))
        if client.content_type_violations:
            sample = "; ".join(client.content_type_violations[:3])
            report.add(
                _err(
                    "H03",
                    f"invalid response Content-Type: {len(client.content_type_violations)} "
                    f"response(s) used something other than application/json or "
                    f"text/event-stream. Sample: {sample}",
                )
            )
        else:
            report.add(_ok("H03", "every response Content-Type was application/json or text/event-stream"))
    else:
        if client.pollution:
            sample = "; ".join(client.pollution[:3])
            report.add(
                _err(
                    "P01",
                    f"stdout pollution: {len(client.pollution)} non-JSON-RPC line(s) on stdout "
                    f"(breaks stdio transport). Sample: {sample}",
                )
            )
        else:
            report.add(_ok("P01", "stdout carried only JSON-RPC messages"))
    if client.malformed:
        sample = "; ".join(client.malformed[:3])
        report.add(
            _err(
                "P02",
                f"malformed JSON-RPC envelope(s): {len(client.malformed)} message(s) violate the "
                f"JSON-RPC 2.0 envelope (missing jsonrpc member / not a message shape). "
                f"Sample: {sample}",
            )
        )
    else:
        report.add(_ok("P02", "all stdout JSON lines were valid JSON-RPC envelopes"))


def run_checks(cmd=None, timeout=10.0, protocol_version="2025-06-18", url=None):
    report = Report()
    if url is not None:
        from .checks_http import run_http_checks

        return run_http_checks(url, timeout=timeout, protocol_version=protocol_version)
    try:
        client = Client(cmd, timeout=timeout)
    except Exception as exc:
        report.spawn_error = str(exc)
        return report
    try:
        ctx = check_handshake(client, report, protocol_version)
        if ctx is not None:
            check_ping_and_notify(client, report)
            check_tools(client, report, ctx["capabilities"])
            check_optional_lists(client, report, ctx["capabilities"])
        check_transport_hygiene(client, report)
    finally:
        client.close()
    report.stderr_tail = client.stderr_tail()
    return report
