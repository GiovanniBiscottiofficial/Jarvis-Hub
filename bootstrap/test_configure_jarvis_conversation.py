import copy
import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).with_name("configure_jarvis_conversation.py")
SPEC = importlib.util.spec_from_file_location("jarvis_conversation_config", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def fixture():
    return {
        "data": {
            "entries": [
                {"domain": "mobile_app", "data": {"secret": "preserve-me"}},
                {
                    "domain": "ollama",
                    "subentries": [
                        {
                            "subentry_id": "jarvis-subentry",
                            "title": "Jarvis Local Brain",
                            "data": {
                                "model": "llama3.2:1b",
                                "prompt": "old",
                                "max_history": 4.0,
                                "num_ctx": 2048.0,
                                "llm_hass_api": ["unsafe-tool"],
                            },
                        }
                    ],
                },
            ]
        }
    }


def test_updates_only_jarvis_conversation_options_and_preserves_secrets():
    payload = fixture()
    before_mobile = copy.deepcopy(payload["data"]["entries"][0])
    result = MODULE.update_jarvis_subentry(
        payload,
        prompt="new contextual prompt",
        model="llama3.2:1b",
        max_history=10,
        num_ctx=3072,
    )
    data = payload["data"]["entries"][1]["subentries"][0]["data"]
    assert result == "jarvis-subentry"
    assert data["prompt"] == "new contextual prompt"
    assert data["max_history"] == 10.0
    assert data["num_ctx"] == 3072.0
    assert data["think"] is False
    assert data["llm_hass_api"] == []
    assert payload["data"]["entries"][0] == before_mobile


def test_refuses_ambiguous_ollama_subentries():
    payload = fixture()
    duplicate = copy.deepcopy(payload["data"]["entries"][1]["subentries"][0])
    payload["data"]["entries"][1]["subentries"].append(duplicate)
    try:
        MODULE.update_jarvis_subentry(
            payload, prompt="x", model="llama3.2:1b", max_history=10, num_ctx=3072
        )
    except RuntimeError as error:
        assert "expected one" in str(error)
    else:
        raise AssertionError("ambiguous configuration must be rejected")
