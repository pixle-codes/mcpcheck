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

# useful flags
--timeout 10                 # per-request timeout, seconds
--protocol-version V         # protocolVersion to offer during initialize
--json                       # machine-readable report (includes exit_code)
```

Exit codes: `0` all checks passed · `1` findings/errors · `2` could not run server.
Warnings do not fail the run.

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
| C08  | `inputSchema` validity: type object, properties object, `required` ⊆ `properties` |
| C09  | Duplicate tool names |
| C10  | Declared `resources`/`prompts` capabilities actually serve their list methods |
| P01  | Stdout purity — any non-JSON-RPC line fails (the classic silent killer) |

## Development

```bash
python3 -m unittest discover -s tests   # fixture servers included, no network needed
```

See [PLAN.md](PLAN.md) for architecture and roadmap (HTTP transport + OAuth assertion set,
JSON Schema deep linting, config file for multi-server repos).

## License

MIT
