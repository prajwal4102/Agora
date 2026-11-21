# Agent Development Guide

For coding agents working in **AgentsMart**: voice agents for a supermarket,
built on Agora Conversational AI and Murf Falcon. A camera counts the shelf;
when a product runs low an orchestrator decides who must act, and a voice agent
phones the floor employee (refill) or the supervisor (reorder). The model's
tool calls go from Agora's cloud to the FastMCP server mounted at `/mcp` in the
same backend process (at `MCP_ENDPOINT`, which must be public).

Read [README.md](./README.md) for the product and
[ARCHITECTURE.md](./ARCHITECTURE.md) for the running system.

## System shape

- **`server/`**: Python FastAPI backend (:8000). Owns Agora token generation,
  agent session lifecycle, the shop database, the shelf camera, the
  orchestrator, **and** the FastMCP server mounted at `/mcp` (same process,
  same port). SDK: `agora-agents>=2.10.0` (`import agora_agent`).
- **`web/`**: Next.js frontend (:3000): dashboard, agent playground, phone page.
- **`studio/`**: prompts and greetings for the two agents saved in Agora Agent
  Studio.
- Auth: Token007 from `AGORA_APP_ID` + `AGORA_APP_CERTIFICATE`. Speech-to-text
  and the LLM are Agora-managed by default; the voice needs `MURF_API_KEY`.

## Routing / ownership

| Concern | File |
|---|---|
| Counting products in camera frames | `server/src/vision.py` |
| Who to call and when; call queue, refill tasks, callbacks | `server/src/dispatch.py` |
| Starting and stopping Agora voice agents | `server/src/agent.py` |
| What each agent is told; greetings; filler phrases | `server/src/prompts.py` |
| MCP tools and per-role tool scope | `server/src/mcp_server.py` |
| Stock, suppliers, budget, forecast, the Order Gate | `server/src/shop_db.py`, `server/src/seasons.py` |
| HTTP routes | `server/src/server.py` |
| UI and RTC/RTM lifecycle | `web/` |

Browser-facing `/api/*` paths are Next rewrites (`web/next.config.ts`) to the
backend; do not add `web/app/api/**/route.ts` for agent/token logic.

## Naming

The UI says **Orchestrator** and **Replenishment Agent**. In code the same
things are still `dispatcher` / `dispatch.py` and the `employee` role
(`floor_agent` in the playground). The Procurement Agent is the `supervisor`
role. Do not rename the code keys without updating both sides.

## Supported modes

- **Local:** `bun run dev` starts `server` (:8000, including `/mcp`) and `web`
  (:3000). Expose the web app with `ngrok http 3000`; Next rewrites `/api/*`
  and `/mcp` to the backend, so one public address serves the phones, the
  dashboard and Agora's cloud.
- **Deploy:** deploy `web` (Next) + `server` (a single publicly reachable
  FastAPI process that also serves `/mcp`). Set `AGENT_BACKEND_URL` in the web
  deployment.

## Patterns

- Keep the web client calling `/api/*`; hide backend placement behind Next rewrites.
- Keep token generation and the App Certificate in `server/`.
- Keep `server/src/mcp_server.py` free of `agora-agents`.
- Every tool is wrapped by `shop_tool`, which enforces the caller's role. Add a
  new tool to `TOOL_SCOPE` or it is open to both roles.
- Purchases go through `propose_order` then `confirm_order`. Never add a path
  that places an order without the Order Gate.
- `MCP_ENDPOINT` is required and must be public; there is no localhost default.
- After editing `server/src/prompts.py`, regenerate the matching file in
  `studio/` from the prompt constant.
- The backend does not hot-reload; restart it after backend changes.

## Anti-patterns

- Do not reintroduce Next Route Handlers for agent/token logic.
- Do not add `agora-agents` to `mcp_server.py`.
- Do not default `MCP_ENDPOINT` to localhost.
- Do not put `PORT` in `server/.env.example` (it would clobber the random port
  that `verify:local:fastapi` injects via `load_dotenv(override=True)`).
- Do not reintroduce a separate `mcp/` service or `docker-entrypoint.sh`.
- Do not commit keys, `server/.env.local`, model weights or recordings.

## Commands

```bash
bun run setup
bun run dev
bun run doctor
bun run doctor:local
bun run verify         # web-only, no creds
bun run verify:local   # full local gate
cd server && venv/bin/python -m pytest tests -q   # Windows: venv\Scripts\python.exe
```

Narrower checks: `bun run verify:backend`, `bun run verify:web:proxy`.

## Done criteria

1. Run the narrowest relevant verification command.
2. Web-affecting changes: the type-check and `bun run verify:web` pass.
3. Backend-affecting changes: the backend tests pass.
4. If you change required env vars or setup steps, update the root README, the
   relevant module README, and `server/.env.example` together.

## Git conventions

- Conventional Commits: `type: description` or `type(scope): description`
  (`feat`, `fix`, `chore`, `test`, `docs`). Lowercase after the prefix, present
  tense.
- No AI tool names in commit messages or PR descriptions. No `Co-Authored-By`
  trailers. No `--no-verify`. No git config changes.
- Branch names: `type/short-description` (e.g. `feat/add-weather-tool`).
