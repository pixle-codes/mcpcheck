"""Spec-derived checks run against a spawned MCP server."""

import dataclasses
from typing import Any, List, Optional

from .client import Client

KNOWN_VERSIONS = {
    "2024-11-05",
    "2025-03-26",
    "2025-06-18",
    "2025-11-25",
    "2026-07-28",
}


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

    @property
    def exit_code(self):
        if self.spawn_error is not None:
            return 2
        return 1 if self.has_errors else 0

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
        if client.crashed:
            report.add(
                _err("C01b", f"server process exited during handshake (code {client.proc.returncode})")
            )
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


def _validate_input_schema(client: Client, report: Report, name: str, schema: Any):
    if not isinstance(schema, dict):
        report.add(_err("C08", f"tool {name!r}: inputSchema must be an object"))
        return
    stype = schema.get("type")
    if stype != "object":
        report.add(_err("C08", f"tool {name!r}: inputSchema.type must be \"object\" (got {stype!r})"))
    props = schema.get("properties")
    if props is not None and not isinstance(props, dict):
        report.add(_err("C08", f"tool {name!r}: inputSchema.properties must be an object"))
        props = None
    required = schema.get("required")
    if required is not None:
        if not isinstance(required, list) or not all(isinstance(r, str) for r in required):
            report.add(_err("C08", f"tool {name!r}: inputSchema.required must be an array of strings"))
        elif props is not None:
            unknown = [r for r in required if r not in props]
            if unknown:
                report.add(
                    _err("C08", f"tool {name!r}: required names not in properties: {unknown}")
                )


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
    result = resp.get("result") or {}
    tools = result.get("tools")
    if not isinstance(tools, list):
        report.add(_err("C07", "'tools/list'.tools must be an array"))
        return
    if tools_cap is None and tools:
        report.add(
            _warn(
                "C07",
                "server returns tools but did not declare the 'tools' capability at initialize",
            )
        )
    seen = {}
    for tool in tools:
        if not isinstance(tool, dict):
            report.add(_err("C07", "each entry in tools[] must be an object"))
            continue
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            report.add(_err("C07", "every tool must have a non-empty string 'name'"))
            name = "<unnamed>"
        if name in seen:
            report.add(_err("C09", f"duplicate tool name {name!r}"))
        seen[name] = True
        if not isinstance(tool.get("description"), str) or not tool["description"]:
            report.add(_warn("C07", f"tool {name!r}: missing description (models rely on it)"))
        if "inputSchema" not in tool:
            report.add(_err("C07", f"tool {name!r}: inputSchema is required"))
        else:
            _validate_input_schema(client, report, name, tool["inputSchema"])
    report.add(_ok("C07", f"tools/list OK ({len(tools)} tool(s))"))


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


def run_checks(cmd, timeout=10.0, protocol_version="2025-06-18"):
    report = Report()
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
