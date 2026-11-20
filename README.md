# Asha: the shelf agent that calls you

A voice agent for a supermarket. A camera watches the shelf. When a product
runs low, the agent works out what to do and **phones the right person**:

- Stock is in the back room → it calls the **floor employee**, in Hinglish, and
  asks them to refill the shelf.
- The back room is empty → it compares suppliers, calls the **supervisor** with
  a recommendation, negotiates quantity and supplier by voice, and places the
  order only after a spoken yes.

Either person can also call the agent to ask about stock or orders.

Built solo for **Build with Agora – BLR** (Track 3: AI Agents That Get Things
Done) on Agora Conversational AI and Murf Falcon.

## Why an agent and not an alert

A threshold alert can tell you a shelf is low. It cannot decide who should act,
check whether stock is sitting in the back room, find that the cheapest supplier
is out, fit an order to the budget, or change the order when the supervisor says
"too much, make it half". Asha does each of those with tools, out loud, and
shows every step on screen.

## The demo in two minutes

1. Take chocolate bars off the shelf in front of the camera.
2. The dashboard shows the count drop and the agent's decision: *low, back room
   empty, calling the supervisor*.
3. The supervisor's phone rings. The agent explains, checks sales and three
   suppliers live, and recommends one. The cheapest cannot fill the order.
4. Push back: "order half", "who is fastest?", "keep it under 3,000".
   It re-prices, reads the total back, and waits.
5. Say yes. The order appears on the dashboard with its number and arrival time.
6. Or say "not now, call me back in a minute". The call ends with a summary on
   the dashboard, and a minute later the phone rings again.
7. Take pens off the shelf. This time the employee's phone rings, in Hinglish,
   because pens are in the back room. Put pens back and the camera confirms the
   refill by itself.

## How it works

```
 Shelf camera ──frames──► Detector (local) ──steady counts──► Triage
                                                                │
                              back room has stock ◄─────────────┼────────────► back room empty
                                      │                                               │
                              ring the employee                               ring the supervisor
                                      │                                               │
                                      └──────────► Agora Conversational AI ◄──────────┘
                                                   speech-to-text → LLM → Murf Falcon
                                                              │ tool calls (MCP)
                                                              ▼
                                         shop tools: stock, sales, quotes, budget,
                                         propose order, confirm order, refill, callback
```

| Piece | What it does | Where |
|---|---|---|
| Voice agent | One agent per call, briefed for the person it is calling | Agora Conversational AI, started from `server/src/agent.py` |
| Voice | English to the supervisor, Hinglish to the employee, same voice | Murf Falcon (`hi-IN` and `en-IN`) |
| Tools | Eleven shop tools the model calls during the call | MCP server, `server/src/mcp_server.py` |
| Business rules | Stock, suppliers, budget, the order gate | `server/src/shop_db.py` |
| Triage and calls | Who to call and why, the call queue, refill tasks | `server/src/dispatch.py` |
| Shelf camera | Finds and counts products in webcam frames, locally | `server/src/vision.py` |
| Dashboard | Shelf, live trail of the agent's steps, calls, orders | `web/src/components/Dashboard.tsx` |
| Phone page | Rings, answers, and lets either person call in | `web/src/components/PhoneApp.tsx` |

### The whole journey

| Stage | What happens |
|---|---|
| Before | The camera count, back-room stock and open orders are known before anyone speaks; the agent opens the call with them |
| Understand and plan | The agent says its plan in a sentence, then works through it |
| Act | Tool calls over MCP: stock, sales, three suppliers, budget, the order |
| Confirm | The total is read back and approved aloud; the order number and arrival time are confirmed |
| Follow up | The call ends with a summary of what was done. A promised callback rings when due. The camera confirms the delivery or refill when the stock is back |

### The order gate

The model cannot place an order by deciding to. `propose_order` prices an order
and stores it as a proposal; `confirm_order` only places an order that matches
a live proposal and carries the supervisor's words of approval. A changed
quantity replaces the old proposal, so a stale one cannot be confirmed. The
supplier's stock and the budget are checked again at confirmation.

### The shelf camera

Recognition runs on the laptop, with no cloud vision service.

- An open-vocabulary detector (YOLO-World) is given the catalogue's product
  names as text, so adding a product is adding a description, with no training.
- Flat wrapped products cannot be named by shape: to the detector a chocolate
  bar in its wrapper is a "banner". Those go to a second model (CLIP), which
  reads each object and names the product or rejects it.
- A count is only believed after it holds steady for three frames, and a
  product is only tracked once it has been seen steadily, so a passing hand or a
  blurred frame does not ring anyone.
- When the camera sees stock return after a refill request or an order, it marks
  that as done. The outcome is verified by sight, not by anyone's word.

### Real phone calls

The demo rings a web page on each phone over Agora RTC. Dialling real mobiles
through a Twilio SIP trunk is implemented in `server/src/telephony.py` and is
switched on with `CALL_MODE=phone`. It has not been run against a live trunk,
because that needs a paid Twilio account, so it is off by default. If a phone
call cannot be placed, the call still rings on the web page.

## Run it

You need Python 3.10+, [Bun](https://bun.sh/), the
[Agora CLI](https://github.com/AgoraIO/cli), [ngrok](https://ngrok.com/) with a
free static domain, and a [Murf API key](https://murf.ai/api/dashboard).

```bash
bun run setup                               # Python venv + web dependencies
agora login
agora project env write server/.env.local   # Agora App ID and Certificate
```

Add to `server/.env.local`:

```
MURF_API_KEY=your_murf_api_key
MCP_ENDPOINT=https://<your-ngrok-domain>/mcp
PORT=8000
```

Then, in two terminals:

```bash
ngrok http 3000 --url=<your-ngrok-domain>   # one address for web, backend and tools
bun run dev                                 # backend on :8000, web on :3000
```

- Dashboard: `http://localhost:3000`
- Phones: `https://<your-ngrok-domain>/phone` — pick *Floor employee* on one and
  *Store supervisor* on the other.

The speech-to-text and LLM run on Agora-managed credentials, so no other keys
are needed. The first run downloads the detector's models (about 480 MB); they
are kept out of Git.

Tests: `cd server && venv/bin/python -m pytest tests -q`
(on Windows, `venv\Scripts\python.exe`).

## Limits

- **Product recognition depends on layout.** It is reliable for bottles, pens
  and upright chocolate bars with a small gap between them, with the camera
  close and fixed. Bars that overlap, or that are far from the camera, are
  undercounted, and frames blurred by a moving camera are wrong until it is
  still again.
- **One kind of wrapped product on camera.** The reader does not reliably tell a
  wafer chocolate bar from a biscuit packet, so biscuits are counted by hand on
  the dashboard.
- **Only products the camera has seen are tracked,** so a product that was never
  put in view is never reported empty.
- **Suppliers, stock and the budget are a mock shop** in a local SQLite file.
- **Hinglish recognition** is Deepgram's multilingual mode and has not been
  tested in a noisy hall.
- **A count takes a few seconds to settle,** since each frame takes about a
  second on a laptop processor and three must agree.

## Credits

Started from Agora's
[`recipe-agent-mcp`](https://github.com/AgoraIO-Conversational-AI/recipe-agent-mcp)
starter, which provided the token service, the agent start/stop calls and the
in-call screen. Everything about the shop, the calls, the triage, the camera and
the dashboard was built for this hackathon.

MIT licensed. See [LICENSE](./LICENSE).
