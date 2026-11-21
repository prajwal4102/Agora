# Architecture: AgentsMart

AgentsMart watches a supermarket shelf, decides who must act when a product
runs low, and has a voice agent phone that person. This file describes the
running system. The product story and screenshots are in the
[README](./README.md).

## The five parts

| Part | Kind | Job | Code |
|---|---|---|---|
| Shelf Watcher | Vision, local | Counts each product in webcam frames | `server/src/vision.py` |
| Orchestrator | Fixed rules | Decides who must act and when; queues calls, refill tasks and callbacks | `server/src/dispatch.py` |
| Replenishment Agent | Voice agent (LLM) | Calls the floor employee in Hinglish to refill from the back room | `server/src/agent.py`, `server/src/prompts.py` |
| Procurement Agent | Voice agent (LLM) | Calls the supervisor in English with a forecast and supplier quotes; places the order | `server/src/agent.py`, `server/src/prompts.py` |
| Order Gate | Fixed rules | Refuses any purchase that is unapproved, unheard, stale, over budget, short at the supplier, re-priced or duplicated | `server/src/shop_db.py` |

The two voice agents run in Agora's cloud. Everything else runs in one backend
process.

## Request flow

```
Webcam (browser)
  │  POST /api/vision/frame           one frame about every second
  ▼
Next.js (web/, :3000)                 rewrites /api/* and /mcp to the backend
  ▼
Backend (server/, :8000)              FastAPI + FastMCP in one process
  │  Shelf Watcher   YOLO-World finds objects, CLIP names wrapped products;
  │                  a count is believed after three matching frames
  │  Orchestrator    low shelf? already handled? back room has stock?
  │                  → refill task + ring the employee, or ring the supervisor
  │  agent.py        starts an Agora voice agent briefed for that person
  ▼
Agora Conversational AI Engine (cloud)
  │  Deepgram nova-3 speech-to-text → GPT-4o mini → Murf Falcon text-to-speech
  │  the phone page (web /phone) rings over Agora RTC; the person answers
  │  the model emits tool calls
  │  POST <MCP_ENDPOINT>              streamable HTTP, called by Agora's cloud
  ▼
Shop Tools (FastMCP at /mcp, same backend)
  │  12 tools; the server checks the caller's role on every call
  │  purchases go through the Order Gate
  ▼
SQLite shop (server/data/)            stock, suppliers, budget, orders, events
  ▼
Dashboard (web /)                     polls /api/state and /api/events:
                                      shelf, live trace, calls, orders
```

After the call, the Orchestrator rings back when a promised callback is due,
and the Shelf Watcher closes the case when it sees the stock back on the shelf.

## Orchestrator rules

1. Shelf count above the product's low mark: nothing to do.
2. Product switched off, a call in progress, an open refill task, or a pending
   callback for it: stay quiet.
3. Back room has stock: create a refill task and ring the employee.
4. Back room empty and an order already placed: no call.
5. Back room empty and nothing on order: ring the supervisor.
6. `report_backroom_shortage` from the employee's call: ring the supervisor.

## Tool scope

One MCP server serves both agents. `shop_tool` in `server/src/mcp_server.py`
wraps every tool and returns `OUT_OF_SCOPE` when the current call's role may
not use it.

| Role | Tools |
|---|---|
| Shared | `get_shelf_status`, `schedule_callback` |
| Replenishment (employee call) | `get_refill_tasks`, `complete_refill_task`, `report_backroom_shortage` |
| Procurement (supervisor call) | `get_sales_rate`, `get_demand_forecast`, `get_supplier_quotes`, `get_budget`, `propose_order`, `confirm_order`, `get_order_status` |

The Procurement Agent's first turn calls `get_shelf_status`,
`get_demand_forecast`, `get_supplier_quotes` and `get_budget` together.

## Order Gate

`propose_order` stores a priced proposal. `confirm_order` places it. Refusals:

| Code | Raised when |
|---|---|
| `ALREADY_ON_ORDER` | An order for the product is already placed (at proposal) |
| `OVER_BUDGET` | The total exceeds the remaining budget (at proposal and again at confirmation) |
| `SUPPLIER_SHORT` | The supplier cannot fill the quantity (at proposal and again at confirmation) |
| `APPROVAL_NOT_CLEAR` | The supervisor's words are not a clear yes, or include a no or a wait |
| `NOT_READ_OUT` | The proposal is under four seconds old, so it cannot have been heard |
| `PROPOSAL_EXPIRED` | The proposal is over ten minutes old |
| `PRICE_CHANGED` | The supplier's price differs from the one read out |

## Single-process design

The FastMCP server is mounted inside the FastAPI app, so tokens, agent
lifecycle, the shop, the camera and the tools share one process and one port
(8000). The demo exposes the web app with one tunnel (`ngrok http 3000`); Next
rewrites pass `/api/*` and `/mcp` through to the backend, so phones, the
dashboard and Agora's cloud all use the same address.

`MCP_ENDPOINT` must be that **public** address (`https://<tunnel>/mcp`),
because Agora's cloud calls the tools, not the browser.

## API (backend, port 8000)

The browser calls these as `/api/*`.

| Endpoint | Method | Purpose |
|---|---|---|
| `/get_config` | GET | Agora token, channel and user IDs |
| `/startAgent`, `/stopAgent` | POST | Start or stop a voice agent |
| `/state`, `/events` | GET | Shop snapshot and the live event trail |
| `/vision/frame` | POST | A webcam frame for the Shelf Watcher |
| `/shelf`, `/products/enabled`, `/calendar` | POST | Manual count, per-product on/off, upcoming occasion |
| `/products/details` | GET | Everything known about one product |
| `/calls/pending`, `/calls/{id}/{action}` | GET, POST | Ringing calls; answer, decline, end |
| `/phone/*` | GET, POST | Optional real phone calls through a SIP trunk |
| `/admin/reset` | POST | Reset the demo shop |
| `/mcp` | POST | The MCP tools, called by Agora's cloud |

## Auth

- Backend → Agora: Token007 from `AGORA_APP_ID` and `AGORA_APP_CERTIFICATE`,
  which never leave `server/`.
- Speech-to-text and the LLM use Agora-managed credentials; Murf Falcon uses
  `MURF_API_KEY`.
- Browser → backend and Agora → `/mcp` are unauthenticated in this demo. A
  deployment should put both behind authentication or a gateway.
