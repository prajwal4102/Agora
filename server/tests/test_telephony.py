"""Real phone calls stay off unless switched on and fully configured, and the
agent built for a phone call carries the right role, voice and tools."""
import sys


FULL = {
    "CALL_MODE": "phone",
    "TWILIO_FROM_NUMBER": "+14155550123",
    "TWILIO_SIP_ADDRESS": "shelf.pstn.twilio.com",
    "EMPLOYEE_PHONE": "+919000000001",
    "SUPERVISOR_PHONE": "+919000000002",
}


def test_phone_calls_are_off_by_default(monkeypatch):
    import telephony

    for key in FULL:
        monkeypatch.delenv(key, raising=False)
    assert telephony.number_for("supervisor") is None
    assert "TWILIO_FROM_NUMBER" in telephony.missing_settings()


def test_phone_mode_needs_every_setting(monkeypatch):
    import telephony

    for key, value in FULL.items():
        monkeypatch.setenv(key, value)
    assert telephony.number_for("employee") == "+919000000001"
    monkeypatch.setenv("TWILIO_SIP_ADDRESS", "")
    assert telephony.number_for("employee") is None
    monkeypatch.setenv("TWILIO_SIP_ADDRESS", "shelf.pstn.twilio.com")
    monkeypatch.setenv("CALL_MODE", "app")
    assert telephony.number_for("employee") is None


def test_sip_address_carries_trunk_credentials(monkeypatch):
    import telephony

    for key, value in FULL.items():
        monkeypatch.setenv(key, value)
    assert telephony._sip_address(telephony.settings()) == "shelf.pstn.twilio.com"
    monkeypatch.setenv("TWILIO_SIP_USERNAME", "asha")
    monkeypatch.setenv("TWILIO_SIP_PASSWORD", "secret")
    assert telephony._sip_address(telephony.settings()) == "asha:secret@shelf.pstn.twilio.com"


def test_phone_agent_is_briefed_for_its_role(fake_env):
    sys.modules.pop("agent", None)
    import agent

    built = agent.Agent().build_pipeline_agent("employee", "namaste", for_phone=True)
    assert built.greeting == "namaste"
    assert built.tts["params"]["locale"] == "hi-IN"
    assert built.llm["mcp_servers"][0]["name"] == "shop"
    assert "floor employee" in built.llm["system_messages"][0]["content"]
