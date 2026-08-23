"""Streamable-HTTP-specific checks: sessions, GET streams, OAuth challenges, PRM docs."""

import json
import urllib.error
import urllib.parse
import urllib.request

from .checks import (
    Report,
    _err,
    _ok,
    _warn,
    check_handshake,
    check_optional_lists,
    check_ping_and_notify,
    check_tools,
    check_transport_hygiene,
)
from .http_transport import HttpTransport

WELL_KNOWN = "/.well-known/oauth-protected-resource"


def parse_www_authenticate(value):
    """Parse 'Bearer realm="x", resource_metadata="https://…"' → (scheme, params)."""
    value = (value or "").strip()
    if not value:
        return "", {}
    scheme, _, rest = value.partition(" ")
    scheme = scheme.strip()
    params = {}
    i = 0
    while i < len(rest):
        while i < len(rest) and rest[i] in ", \t":
            i += 1
        eq = rest.find("=", i)
        if eq < 0:
            break
        key = rest[i:eq].strip()
        i = eq + 1
        while i < len(rest) and rest[i] in " \t":
            i += 1
        if i < len(rest) and rest[i] == '"':
            j = i + 1
            buf = []
            while j < len(rest):
                if rest[j] == "\\" and j + 1 < len(rest):
                    buf.append(rest[j + 1])
                    j += 2
                    continue
                if rest[j] == '"':
                    break
                buf.append(rest[j])
                j += 1
            params[key] = "".join(buf)
            i = j + 1
        else:
            j = rest.find(",", i)
            if j < 0:
                j = len(rest)
            params[key] = rest[i:j].strip()
            i = j
    return scheme, params


def fetch_json(url, timeout, accept="application/json"):
    """GET a URL expecting JSON. Returns (doc, None) or (None, error-string)."""
    req = urllib.request.Request(url, headers={"Accept": accept})
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except Exception as exc:
        return None, f"{exc.__class__.__name__}: {exc}"
    raw = b""
    try:
        raw = resp.read()
    except OSError as exc:
        return None, f"read failed: {exc}"
    finally:
        try:
            resp.close()
        except OSError:
            pass
    ctype = (resp.headers.get("Content-Type") if resp.headers else "") or ""
    try:
        doc = json.loads(raw.decode("utf-8"))
    except ValueError:
        return None, f"not JSON (Content-Type {ctype.split(';')[0]!r})"
    return doc, None


def validate_prm(report, doc, advertised_url, target_url):
    """RFC 9728 §2 protected-resource-metadata assertions."""
    if not isinstance(doc, dict):
        report.add(_err("A02", f"PRM document at {advertised_url} is not a JSON object"))
        return
    resource = doc.get("resource")
    if not isinstance(resource, str) or not resource:
        report.add(_err("A02", "PRM 'resource' (string) is required by RFC 9728"))
    else:
        target_origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(target_url))
        res_origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(resource))
        if res_origin != target_origin and resource.rstrip("/") != target_url.rstrip("/"):
            report.add(
                _warn(
                    "A02",
                    f"PRM 'resource' ({resource!r}) does not match the audited endpoint "
                    f"origin ({target_origin!r}); clients will reject tokens minted for it",
                )
            )
        else:
            report.add(_ok("A02", f"PRM resource={resource!r}"))
    for field, kind in (("authorization_servers", "strings"), ("scopes_supported", "strings")):
        val = doc.get(field)
        if val is None:
            continue
        if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
            report.add(_err("A02", f"PRM '{field}' must be an array of strings"))
        elif field == "authorization_servers" and not val:
            report.add(_warn("A02", "PRM 'authorization_servers' is empty"))
        else:
            report.add(_ok("A02", f"PRM {field} OK ({len(val)} entry(ies))"))
    rs = doc.get("resource_documentation")
    if rs is not None and not isinstance(rs, str):
        report.add(_err("A02", "PRM 'resource_documentation' must be a string URL"))


def check_http_auth(transport: HttpTransport, report: Report):
    wa = transport.www_authenticate
    if not wa:
        report.add(
            _err(
                "A01",
                "endpoint answered 401 Unauthorized without a WWW-Authenticate challenge header "
                "(clients cannot discover how to authenticate)",
            )
        )
        return
    scheme, params = parse_www_authenticate(wa)
    if not scheme:
        report.add(_err("A01", f"unparseable WWW-Authenticate header: {wa[:120]!r}"))
        return
    report.add(_ok("A01", f"401 challenge present (scheme {scheme!r})"))
    advertised = params.get("resource_metadata")
    if advertised:
        if not advertised.startswith(("http://", "https://")):
            report.add(_err("A02", f"resource_metadata must be an absolute URL, got {advertised!r}"))
            return
        doc, err = fetch_json(advertised, transport.timeout)
        if err:
            report.add(_err("A02", f"challenge advertised resource_metadata at {advertised} but fetching failed ({err})"))
            return
        validate_prm(report, doc, advertised, transport.url)
        return
    # No pointer in the challenge: probe RFC 9728 default locations.
    parts = urllib.parse.urlsplit(transport.url)
    path = parts.path or "/"
    candidates = []
    stripped = path.rstrip("/")
    if stripped:
        candidates.append(urllib.parse.urlunsplit((parts.scheme, parts.netloc, WELL_KNOWN + stripped, "", "")))
    candidates.append(urllib.parse.urlunsplit((parts.scheme, parts.netloc, WELL_KNOWN + path, "", "")))
    for cand in candidates:
        doc, err = fetch_json(cand, min(transport.timeout, 5.0))
        if doc is not None:
            report.add(_ok("A01", f"found PRM document at conventional location {cand}"))
            validate_prm(report, doc, cand, transport.url)
            return
    report.add(
        _warn(
            "A02",
            "401 challenge carries no resource_metadata parameter and no PRM document found at "
            f"conventional locations ({', '.join(dict.fromkeys(candidates))}) — OAuth clients "
            "cannot auto-discover scopes/servers",
        )
    )


def check_session(transport: HttpTransport, report: Report):
    sid = transport.session_id
    if transport.server_assigned_session:
        if not sid:
            report.add(_err("H02", "server sent an empty Mcp-Session-Id header"))
        else:
            report.add(
                _ok("H02", f"server assigned Mcp-Session-Id; mcpcheck echoed it on every later request")
            )
    else:
        report.add(_ok("H02", "stateless endpoint (no Mcp-Session-Id issued); nothing to echo"))


def check_get_stream(transport: HttpTransport, report: Report):
    status, ctype = transport.get_stream_probe(path_timeout=min(transport.timeout, 4.0))
    if status == 200:
        if "event-stream" in ctype.lower():
            report.add(_ok("H04", "GET stream available (text/event-stream)"))
        else:
            report.add(_warn("H04", f"GET stream returned 200 but Content-Type {ctype!r}, expected text/event-stream"))
    elif status == 405:
        report.add(_ok("H04", "GET stream declined with 405 (allowed for servers without server-initiated messages)"))
    elif status == "timeout":
        report.add(_ok("H04", "GET stream held open without immediate data"))
    elif status in (401, 403):
        report.add(_ok("H04", f"GET stream requires auth (HTTP {status})"))
    elif status == "error":
        report.add(_warn("H04", "GET stream connection dropped unexpectedly"))
    else:
        report.add(_warn("H04", f"GET stream returned unexpected HTTP {status}"))


def run_http_checks(url, timeout=10.0, protocol_version="2025-06-18"):
    report = Report()
    transport = HttpTransport(url, timeout=timeout)
    try:
        ctx = check_handshake(transport, report, protocol_version)
        if transport.auth_challenge_status == 401 and ctx is None:
            check_http_auth(transport, report)
            check_transport_hygiene(transport, report)
            return report
        if ctx is not None:
            check_session(transport, report)
            check_ping_and_notify(transport, report)
            check_tools(transport, report, ctx["capabilities"])
            check_optional_lists(transport, report, ctx["capabilities"])
            check_get_stream(transport, report)
        check_transport_hygiene(transport, report)
    except ConnectionError as exc:
        report.spawn_error = str(exc)
    finally:
        transport.close()
    return report
