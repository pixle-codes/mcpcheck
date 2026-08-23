# mcpcheck

**Headless conformance and smoke-test runner for [Model Context Protocol](https://modelcontextprotocol.io) (MCP) servers — built for CI.**

MCP servers fail silently: a tool schema the client quietly rejects, a botched `initialize`
handshake, stray prints on stdout that corrupt the stdio transport. Nothing raises in your
server process; the integration just doesn't work — usually first noticed inside an agent
on your user's machine.

`mcpcheck` spawns your server exactly like an MCP client would and asserts the spec
requirements that matter, then exits non-zero on failure so CI can gate on it:

```
$ mcpcheck run -- python my_server.py
  [ok  ] C02 jsonrpc version echoed correctly
  [ok  ] C02 request id echoed correctly
  [ok  ] C03 protocolVersion '2025-06-18' negotiated
  [ok  ] C04 serverInfo.name='my_server'
  ...
  [FAIL] C08 tool 'search': inputSchema.type must be "object" (got 'string')
  [FAIL] P01 stdout pollution: 2 non-JSON-RPC line(s) on stdout (breaks stdio transport). Sample: Server starting...

mcpcheck: FAILED — 2 error(s), 0 warning(s), 11 passed
$ echo $?
1
```

## Why

- **MCP Inspector** is interactive-first — great for exploring, but no assertions, no batch
  mode, no exit codes.
- **SDK InMemoryTransport tests** never exercise the real spawn → stdio path where silent
  failures live.
- **Hand-rolled curl/jq scripts** are per-team, brittle, and skip protocol negotiation.

`mcpcheck` is deterministic, dependency-free, and CI-first.

## Install

Requires Python ≥ 3.10. No dependencies.

```bash
pip install git+https://github.com/pixle-codes/mcpcheck.git
# or from a clone:
pip install .
```

## Usage

```bash
# smoke-test a stdio server (everything after -- is the spawn command)
mcpcheck run -- node dist/server.js

# audit a Streamable HTTP server
mcpcheck run --url https://mcp.example.com/mcp

# useful flags
--timeout 10                 # per-request timeout, seconds
--protocol-version V         # protocolVersion to offer during initialize
--fail-on warning            # make warnings fail the run too (default: error)
--json                       # machine-readable report (includes exit_code)
```

Exit codes: `0` all checks passed · `1` findings/errors · `2` could not run server.
Warnings do not fail the run unless you pass `--fail-on warning` — handy as a stricter
CI gate once your server is clean.

### Streamable HTTP & OAuth discovery

For HTTP targets mcpcheck speaks the Streamable HTTP transport: POST with
`Accept: application/json, text/event-stream`, SSE response parsing, and
`Mcp-Session-Id` echo after initialize. If the endpoint answers **401**, it grades the
auth-discovery story instead of failing blindly:

- **A01** — is there a parseable `WWW-Authenticate` challenge? Does it carry the
  RFC 9728 `resource_metadata` pointer?
- **A02** — fetch the Protected Resource Metadata document and validate it:
  required `resource`, string arrays for `authorization_servers` / `scopes_supported`,
  and whether `resource` actually matches the audited origin (a mismatch means tokens
  minted for it will be rejected).

A server that gates correctly and advertises valid metadata passes clean; one that 401s
without a challenge or points at broken metadata fails with actionable findings.

### GitHub Actions

```yaml
- name: MCP conformance
  run: |
    pip install mcpcheck
    mcpcheck run -- python -m my_mcp_server
```

## Checks

| Code | Check |
| ---- | ----- |
| C01  | Server starts and answers `initialize` within timeout |
| C02  | JSON-RPC envelope: `"jsonrpc":"2.0"`, request id echoed |
| C03  | `protocolVersion` negotiation (warns on unknown or differing revisions) |
| C04  | `serverInfo.name`/`version` present; `capabilities` is an object |
| C06  | Accepts `notifications/initialized`, answers `ping` with `{}` |
| C07  | `tools/list`: array shape, required `name`/`inputSchema`, descriptions present; capability declared when tools exist |
| C08  | Deep `inputSchema` lint: root type object, valid JSON Schema types, `required` ⊆ `properties` (recursively into nested objects/arrays), `enum`/`default`/`examples` type sanity, `outputSchema.type == "object"` |
| C09  | Duplicate tool names (within and across pages) |
| C10  | Declared `resources`/`prompts` capabilities actually serve their list methods |
| C11  | Pagination round-trip: follows `nextCursor`, catches non-string cursors, loops, erroring/empty pages |
| C12  | Tool annotations: `title` string; `readOnlyHint`/`destructiveHint`/`idempotentHint`/`openWorldHint` booleans |
| H02  | Streamable HTTP: `Mcp-Session-Id` issued at initialize and echoed on every later request |
| H03  | HTTP responses use `application/json` or `text/event-stream` |
| H04  | GET stream behaves per spec: SSE, `405`, auth-gated, or held open — not a random status |
| A01  | 401 responses carry a parseable `WWW-Authenticate` challenge (with RFC 9728 pointer where applicable) |
| A02  | Protected Resource Metadata document validates: required `resource`, string arrays for servers/scopes, origin match |
| P01  | Stdout purity (stdio) / response-body purity (HTTP) — non-protocol payloads fail |
| P02  | JSON-RPC envelope validity — stdout lines that parse as JSON but violate the envelope (missing `"jsonrpc":"2.0"`, neither response nor notification) fail |

## Development

```bash
python3 -m unittest discover -s tests   # fixture servers (stdio + in-process HTTP), no network needed
```

See [PLAN.md](PLAN.md) for architecture and roadmap (config file for multi-server repos,
deeper OAuth authorization-server metadata checks).

## License

MIT
