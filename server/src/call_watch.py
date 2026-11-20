"""Watch live calls: caption them, and rescue an agent that has stalled.

A language model sometimes answers "Sure, I'll check that, one moment" and
then makes no tool call. On a phone that is a promise followed by silence, and
it stays silent until the person speaks again (78 seconds, on a real call
during testing). Telling the model not to do this helps but does not stop it.

So the backend reads each live call's conversation from Agora every couple of
seconds. That gives two things:

- every line spoken is logged, so the dashboard can caption the call;
- if the agent's last line was a promise to check something, no tool call
  followed, and nobody has spoken since, the agent is told to do it now.
"""
import re
import time
from typing import Any, Dict, List, Optional

import shop_db

# How long the agent may sit on an unkept promise before it is pushed.
STALL_SECONDS = 2.5
# What the agent is told when it stalls. Sent as an instruction into the call.
NUDGE = (
    "You said you would check, but you have not called a tool. Call the right tool now "
    "and tell me the result. Do not say you will check again."
)

_PROMISE = re.compile(
    r"\b(i'?ll|i will|let me|checking|check|verify|verifying|look|looking|pull|one moment|"
    r"a moment|one second|a second|just a sec|hold on|give me)\b|एक second|एक moment|देख रही|check कर",
    re.IGNORECASE,
)


def looks_like_unkept_promise(text: str) -> bool:
    """Is this line a promise to go and do something, rather than an answer or
    a question? "I'll check the stock again. One moment." is; "We have 2 on the
    shelf. Shall I reorder?" is not."""
    text = (text or "").strip()
    if not text or text.endswith("?"):
        return False  # a question hands the turn back properly
    if re.search(r"\d", text) or "rupees" in text.lower():
        return False  # it contains figures: this is an answer
    return bool(_PROMISE.search(text))


def _field(entry: Any, name: str) -> Any:
    value = getattr(entry, name, None)
    if value is None and hasattr(entry, "model_dump"):
        value = entry.model_dump().get(name)
    return value


class CallWatcher:
    """Tracks one live call."""

    def __init__(self, role: str, voice_agent: str):
        self.role = role
        self.voice_agent = voice_agent
        self.seen = 0
        self.nudged: set = set()

    def captions(self, contents: List[Any]) -> List[Dict[str, Any]]:
        """Lines not yet logged, as events for the dashboard."""
        fresh: List[Dict[str, Any]] = []
        for entry in contents[self.seen:]:
            role = _field(entry, "role")
            text = (_field(entry, "content") or "").strip()
            if role not in ("user", "assistant") or not text or text == NUDGE:
                continue
            said_by_agent = role == "assistant"
            fresh.append({
                "text": text,
                "agent": self.voice_agent if said_by_agent else self.role,
                "to": self.role if said_by_agent else self.voice_agent,
            })
        self.seen = len(contents)
        return fresh

    def stalled(self, contents: List[Any], now: Optional[float] = None) -> bool:
        """Has the agent promised to check, then done nothing?"""
        now = time.time() if now is None else now
        spoken = [e for e in contents if _field(e, "role") in ("user", "assistant") and (_field(e, "content") or "").strip()]
        if not spoken:
            return False
        last = spoken[-1]
        if _field(last, "role") != "assistant":
            return False  # the person spoke last: the agent is about to answer
        ended_ms = _field(last, "speech_end_ms") or _field(last, "speech_start_ms")
        started_ms = _field(last, "speech_start_ms") or ended_ms
        if not ended_ms:
            return False
        key = int(started_ms)
        if key in self.nudged or now - ended_ms / 1000 < STALL_SECONDS:
            return False
        if not looks_like_unkept_promise(_field(last, "content")):
            return False
        if shop_db.tool_called_since(started_ms / 1000 - 1):
            return False  # it did call a tool: the answer is on its way
        self.nudged.add(key)
        return True
