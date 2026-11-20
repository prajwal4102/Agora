# The two voice agents in Agora Agent Studio

The voice agents can run from agents saved in Agora's console instead of being
built in code for each call. This folder holds what to paste into Studio.

When a pipeline ID is set in `server/.env.local`, calls for that role start
from the Studio agent. Agora uses the saved agent as the base, and the backend
sends only what changes per call: the opening line with the live shelf count.
Leave the IDs empty and the agents are built from `server/src/agent.py`.

## 1. Add the tools (once)

Console → **Integrations → MCPs → Create MCP Server**

| Field | Value |
|---|---|
| Name | `shop` |
| Endpoint | `https://<your-ngrok-domain>/mcp` |
| Protocol | HTTP |
| Timeout (ms) | 10000 |

## 2. Create the two agents

Console → **Agents → Create Agent**, twice.

| Setting | Procurement Agent | Floor Agent |
|---|---|---|
| Talks to | the store supervisor | the floor employee |
| Instructions | paste `procurement_agent_prompt.txt` | paste `floor_agent_prompt.txt` |
| Greeting | first one in `greetings.txt` | second one in `greetings.txt` |
| LLM | GPT-4o mini | GPT-4o mini |
| Speech-to-text | Deepgram nova-3, English | Deepgram nova-3, Multilingual |
| Voice | Murf Falcon, voice Anisha, `en-IN` | Murf Falcon, voice Anisha, `hi-IN` |
| Tools | attach the `shop` MCP server | attach the `shop` MCP server |

The greeting is only what the agent says when someone calls in. When the agent
places a call itself, the backend replaces it with the reason for the call.

## 3. Publish and connect

Publish each agent and copy its pipeline ID, then add to `server/.env.local`:

```
STUDIO_PROCUREMENT_AGENT_ID=<pipeline id of Procurement Agent>
STUDIO_FLOOR_AGENT_ID=<pipeline id of Floor Agent>
```

Restart the backend. While a call is live, the dashboard strip shows
"Agent Studio agent" when it was started this way.

To go back to code-built agents, remove those two lines and restart.
