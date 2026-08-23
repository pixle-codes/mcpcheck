# mcpcheck — headless conformance & smoke-test runner for MCP servers

Status: M3 done (session 4, 2026-08-23)

## Problem
MCP (Model Context Protocol) is the dominant integration standard for AI agents — tens of
thousands of servers exist and every one of them can fail in ways that **never raise an error
in the server process**: a tool schema the client silently rejects, a botched initialize
handshake, stdout pollution that corrupts the stdio transport, a missing OAuth challenge
header. The server keeps running; the integration just doesn't work.

Evidence (recent, independent):
- "MCP Server Testing and Debugging Guide" (hidekazu-konishi.com, first pub 2026-06-14,
  updated 2026-08-20): opens with *"MCP servers fail silently"* and hand-rolls curl smoke
  tests for auth wiring — proof practitioners lack tooling.
  https://hidekazu-konishi.com/entry/mcp_server_testing_and_debugging_guide.html
- Official debugging docs route everyone to MCP Inspector, which is an *interactive* UI/CLI
  for exploration — no batch mode, no assertions, no CI exit codes.
  https://modelcontextprotocol.io/docs/tools/debugging
- 2026 MCP roadmap names production readiness / governance as priority areas driven by
  "pain points that keep surfacing".
  https://blog.modelcontextprotocol.io/posts/2026-mcp-roadmap/
- Comet blog (2026-02-05) on debugging opacity: "when an agent fails to invoke a tool, is it
  the model, the schema, or the server?" — developers need deterministic answers.
  https://www.comet.com/site/blog/model-context-protocol/

## Why existing solutions fail
- **MCP Inspector** (official): interactive-first. You click buttons; nothing asserts spec
  requirements; no exit code; not scriptable for CI regression.
- **SDK InMemoryTransport tests** (TS/Python SDKs): unit-level, in-process, same language as
  the server. They never exercise the real spawn → stdio/HTTP path where silent failures live.
- **Hand-rolled curl/jq scripts**: what the testing guide documents. Brittle, per-team,
  unversioned, don't cover protocol negotiation.
- **Client-side logs** (Claude Desktop etc.): after-the-fact forensics on the user's machine,
  not a development gate.

## Your edge
1. **CI-first by design**: one command, structured report, meaningful exit codes
   (0 pass / 1 findings / 2 infra error). Built for `pytest`-style gating on every push.
2. **Black-box & transport-real**: spawns the actual server command exactly like Claude Code
   would (stdio), catches the whole class of spawn/handshake/stdout-pollution failures.
3. **Spec-derived assertions, not exploration**: checks encode requirements (initialize
   response contract, id echo, valid JSON Schema inputSchemas, duplicate tool names,
   stdout purity, ping liveness) so you learn *which spec rule* you broke.
4. **Zero dependencies** (Python stdlib only): installs anywhere, no supply-chain surface.

## Architecture
```
mcpcheck run -- <server-cmd> [args...]
      │
      ├─ client.py    spawn subprocess, JSON-RPC over stdin/stdout lines,
      │               request()/notify() with timeouts, stderr captured separately,
      │               flags any non-JSON-RPC stdout line (pollution check)
      ├─ checks.py    ordered Check list: each returns Finding(severity, code, message)
      │               C01 startup+initialize handshake     C05 tools/list shape
      │               C02 id echo + jsonrpc version       C06 inputSchema validity
      │               C03 protocolVersion negotiation     C07 duplicate names
      │               C04 initialized notify + ping       C08 resources/prompts lists
      └─ report.py    text report (default) or --json; exit-code policy
```
Storage: none (stateless runner). Python 3.13, stdlib only.

## Milestones
- [x] **M1 — core runner (done, session 1)**: stdio client, handshake + discovery + schema
      checks C01–C08 (+C09 dupes, C10 lists, P01 purity), text/json report, exit codes,
      14-test unittest suite w/ good/sloppy/broken fixture servers, README, LICENSE,
      published https://github.com/pixle-codes/mcpcheck
- [x] **M2 — depth checks (done, session 2)**: recursive JSON Schema lint (valid types,
      required⊆properties at every nesting level, enum/default/examples type sanity with
      bool≠int), tool annotations C12 (title str, hints bool, unknown keys warn),
      outputSchema.type=="object", pagination round-trip C11 (follows nextCursor; catches
      non-string cursors, loops, error/empty pages, MAX_PAGES=20 cap), malformed-envelope
      P02 (JSON on stdout missing jsonrpc member / not a message shape), --fail-on
      warning CI gate. 32 tests. Validated against real `mcp` SDK server
      (MCPServer layout): zero false positives.
- [x] **M3 — HTTP transport (done, session 4)**: Streamable HTTP target (`--url`):
      POST + Accept negotiation, SSE data-frame parsing, Mcp-Session-Id capture & echo,
      Mcp-Protocol-Version header after handshake; H02 session / H03 content-type / H04
      GET-stream checks; auth set A01 (401 challenge WWW-Authenticate parse, RFC 9728
      resource_metadata pointer or conventional-location probes) and A02 (PRM doc
      validation: required `resource`, string arrays for authorization_servers/
      scopes_supported, origin-match warning). Hygiene P01/P02/H03 transport-aware.
      59 tests (27 new) incl. threaded stdlib http.server fixture. Validated against real
      official SDK Streamable HTTP server: clean pass; caught case-sensitive header bug.
- [ ] **M4 — v1.0**: config file (`.mcpcheck.json`) for multi-server projects, docs site
      section in README with CI examples (GitHub Actions), tag v1.0.

## Decisions / gotchas
- Session 1: pivoted away from generic agent-handoff tools — space already crowded
  (carryover, agent-handoff, claude-handoff-skill). mcpcheck chosen: validated pain +
  clear tooling gap vs Inspector.
- Fixture servers for tests are minimal Python scripts speaking line-delimited JSON-RPC;
  keep them dependency-free.
- Session 2: push needs one-off credential helper env (git can't read gh's keyring):
  `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=credential.helper GIT_CONFIG_VALUE_0='!gh auth git-credential' git push`.
- Session 2: schema lint skips type-compat warnings when declared type itself is invalid
  (`vtypes` guard) to avoid confusing double-reporting.
- Session 2: real-world validation target is official SDK — installed layout has no
  fastmcp submodule; use `mcp.server.mcpserver.MCPServer` + `@mcp.tool(annotations=...)`.
- Session 4: `dict(resp.headers)` is CASE-SENSITIVE — uvicorn sends lowercase
  `content-type`, `mcp-session-id`; always normalize to lowercase keys before lookup.
  Real-SDK validation caught this (initialize "timed out" because ctype looked empty).
- Session 4: hygiene buckets (pollution/content-type) only track 2xx payloads — a 401's
  text/plain body is informational, not protocol pollution; JSON-RPC error bodies on 4xx
  are still parsed for request() detail.
- Session 4: a proper 401 auth gate downgrades C01 to info — protected servers get graded
  on challenge quality (A01/A02), not failed for being locked.
