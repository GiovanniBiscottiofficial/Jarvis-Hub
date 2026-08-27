from copy import deepcopy

from app import conversation_bridge as bridge


def reset_state():
    with bridge._lock:
        bridge._state.clear()
        bridge._state.update(
            {
                "connected": False,
                "phase": "unavailable",
                "last_user": "",
                "last_assistant": "",
                "user_at": None,
                "assistant_at": None,
                "updated_at": None,
                "error": "Waiting for the local voice runtime.",
            }
        )


def test_snapshot_exposes_only_latest_ephemeral_exchange():
    reset_state()
    bridge.apply_voice_event(
        {
            "event": "snapshot",
            "data": {
                "ha_connected": True,
                "last_stt_text": "  What is next?  ",
                "last_tts_text": "Your next appointment is at three.",
            },
        }
    )

    status = bridge.conversation_status()
    assert status["connected"] is True
    assert status["phase"] == "idle"
    assert status["last_user"] == "What is next?"
    assert status["last_assistant"] == "Your next appointment is at three."
    assert status["privacy"] == {
        "raw_audio_stored": False,
        "transcript_persisted": False,
        "retention": "latest exchange in memory until LifeOS restarts",
    }


def test_live_events_track_phase_and_replace_previous_turn():
    reset_state()
    bridge.apply_voice_event({"event": "zeroconf", "data": {"status": "connected"}})
    bridge.apply_voice_event({"event": "stt_text", "data": {"text": "Brief me now"}})
    assert bridge.conversation_status()["phase"] == "thinking"

    bridge.apply_voice_event(
        {"event": "tts_text", "data": {"text": "Good evening, Giovanni."}}
    )
    status = bridge.conversation_status()
    assert status["phase"] == "speaking"
    assert status["last_user"] == "Brief me now"
    assert status["last_assistant"] == "Good evening, Giovanni."

    bridge.apply_voice_event({"event": "tts_finished", "data": {}})
    assert bridge.conversation_status()["phase"] == "idle"


def test_pipeline_failure_is_truthful_and_does_not_erase_last_exchange():
    reset_state()
    bridge.apply_voice_event({"event": "stt_text", "data": {"text": "Turn it down"}})
    before = deepcopy(bridge.conversation_status())
    bridge.apply_voice_event(
        {"event": "pipeline_error", "data": {"reason": "Speech service unavailable"}}
    )

    status = bridge.conversation_status()
    assert status["phase"] == "error"
    assert status["error"] == "Speech service unavailable"
    assert status["last_user"] == before["last_user"]
    assert "audio" not in status


def test_transcript_is_normalized_and_bounded():
    reset_state()
    bridge.apply_voice_event(
        {"event": "stt_text", "data": {"text": "  one\n two  " + ("x" * 900)}}
    )
    text = bridge.conversation_status()["last_user"]
    assert text.startswith("one two")
    assert len(text) == bridge.MAX_TRANSCRIPT_CHARS
