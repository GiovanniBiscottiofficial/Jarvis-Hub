from contextlib import AbstractContextManager
from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app import internet_intelligence as broker
from app.routers import internet as internet_router


AUTH = {"Authorization": "Bearer test-token"}
REPO_ROOT = Path(__file__).resolve().parents[2]


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")
        return self

    def json(self):
        return self.payload

    @property
    def text(self):
        return self.payload if isinstance(self.payload, str) else ""


class FakeClient(AbstractContextManager):
    def __init__(self, handler):
        self.handler = handler

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get(self, url, **kwargs):
        return self.handler(url, kwargs)


def ready(source_id, data=None):
    return broker._source(source_id, source_id.title(), "ready", data=data or {})


def test_snapshot_isolates_sources_and_keeps_action_authority_locked(fresh_db, monkeypatch):
    monkeypatch.setattr(broker, "weather_source", lambda force=False: ready("weather"))
    monkeypatch.setattr(broker, "air_quality_source", lambda force=False: ready("air_quality"))
    monkeypatch.setattr(broker, "weather_alerts_source", lambda force=False: ready("weather_alerts"))
    monkeypatch.setattr(broker, "calendar_source", lambda force=False: ready("calendar"))
    monkeypatch.setattr(broker, "commute_source", lambda force=False: ready("commute"))

    result = broker.internet_snapshot()

    assert result["summary"] == {"live_sources": 5, "degraded_sources": 0, "total_sources": 5}
    assert result["policy"]["internet_results_authorize_actions"] is False
    assert result["policy"]["checkout_allowed"] is False
    assert result["policy"]["financial_transfers_allowed"] is False
    assert all(source["authority"] == "read_only_advisory" for source in result["sources"])


def test_weather_source_exposes_hourly_rain_uv_and_provenance(monkeypatch):
    broker.clear_cache()
    monkeypatch.setattr(broker, "LAT", "36.0")
    monkeypatch.setattr(broker, "LON", "-79.0")
    payload = {
        "current": {"temperature_2m": 74, "apparent_temperature": 76, "weather_code": 2, "wind_speed_10m": 5},
        "hourly": {
            "time": ["2026-08-27T09:00", "2026-08-27T10:00"],
            "temperature_2m": [74, 76], "precipitation_probability": [10, 45], "weather_code": [2, 61],
        },
        "daily": {
            "temperature_2m_max": [82], "temperature_2m_min": [65], "weather_code": [2],
            "uv_index_max": [7.2], "sunrise": ["2026-08-27T06:46"], "sunset": ["2026-08-27T19:53"],
        },
    }
    monkeypatch.setattr(broker, "_client", lambda timeout=7: FakeClient(lambda _url, _kwargs: FakeResponse(payload)))

    result = broker.weather_source(force=True)

    assert result["status"] == "ready"
    assert result["source_url"] == "https://open-meteo.com/"
    assert result["data"]["max_rain_chance_8h"] == 45
    assert result["data"]["uv_index_max"] == 7.2
    assert result["data"]["next_hours"][1]["conditions"] == "light rain"


def test_research_combines_reference_and_news_without_persistence(monkeypatch):
    def handler(url, _kwargs):
        if "crossref" in url:
            return FakeResponse({"message": {"items": [{
                "title": ["Thunderstorm safety"], "publisher": "Safety Journal",
                "type": "journal-article", "published": {"date-parts": [[2025, 2, 3]]},
                "URL": "https://doi.org/10.1/example",
            }]}})
        return FakeResponse("""<?xml version="1.0"?><rss><channel><item><title>Storm update</title><link>https://example.com/storm</link><pubDate>Wed, 27 Aug 2026 12:00:00 GMT</pubDate><Source>example.com</Source></item></channel></rss>""")

    monkeypatch.setattr(broker, "_client", lambda timeout=7: FakeClient(handler))
    result = broker.research("thunderstorm safety")

    assert result["status"] == "ready"
    assert "Safety Journal" in result["references"][0]["summary"]
    assert result["news"][0]["source"] == "example.com"
    assert result["policy"]["query_persisted"] is False
    assert result["policy"]["citations_required"] is True


def test_media_and_nutrition_never_claim_availability_or_auto_log(monkeypatch):
    def handler(url, kwargs):
        if "itunes" in url:
            return FakeResponse({"results": [{
                "trackName": "Example", "artistName": "Creator", "kind": "feature-movie",
                "trackViewUrl": "https://example.com/media", "primaryGenreName": "Drama",
            }]})
        return FakeResponse({"foods": [{
            "fdcId": 12, "description": "PROTEIN BAR", "brandOwner": "Example",
            "servingSize": 1, "servingSizeUnit": "bar",
            "foodNutrients": [
                {"nutrientName": "Protein", "unitName": "g", "value": 20},
                {"nutrientName": "Energy", "unitName": "kJ", "value": 800},
                {"nutrientName": "Energy", "unitName": "kcal", "value": 200},
            ],
        }]})

    monkeypatch.setattr(broker, "_client", lambda timeout=7: FakeClient(handler))
    media = broker.media_search("Example")
    nutrition = broker.nutrition_search("protein bar")

    assert media["results"]
    assert media["policy"]["availability_claimed"] is False
    assert media["policy"]["playback_authorized"] is False
    assert nutrition["results"][0]["protein_g"] == 20
    assert nutrition["results"][0]["calories"] == 200
    assert nutrition["results"][0]["nutrient_basis"] == "per 100 g reference basis"
    assert nutrition["policy"]["requires_user_confirmation_before_logging"] is True


def test_internet_api_requires_auth_and_returns_broker_payload(fresh_db, monkeypatch):
    expected = {"status": "ready", "summary": {"live_sources": 5}}
    monkeypatch.setattr(internet_router, "internet_snapshot", lambda force=False: expected)
    client = TestClient(main.app)

    assert client.get("/api/internet").status_code == 401
    assert client.get("/api/internet", headers=AUTH).json() == expected
    assert client.post("/api/internet/refresh", headers=AUTH).json() == expected


def test_connected_feed_is_profile_scoped_redacted_and_action_locked(fresh_db):
    result = broker.ingest_connected_feed(
        "bank_sync",
        source="read-only test connector",
        ttl_minutes=30,
        payload={
            "balance": 120.50,
            "account_name": "Checking",
            "account_number": "123456789",
            "access_token": "secret-token",
            "transfer": {"amount": 50},
        },
    )

    assert result["payload"] == {"balance": 120.50, "account_name": "Checking"}
    assert result["authority"] == "read_only_advisory"
    feeds = broker.connected_feeds()
    assert len(feeds) == 1
    assert feeds[0]["capability"] == "bank_sync"
    assert feeds[0]["status"] == "ready"
    assert "account_number" not in feeds[0]["payload"]


def test_connected_feed_rejects_unsupported_capability(fresh_db):
    try:
        broker.ingest_connected_feed("door_unlock", source="bad", payload={})
    except ValueError as exc:
        assert "unsupported" in str(exc)
    else:
        raise AssertionError("unsupported capability was accepted")


def test_internet_ui_is_touch_safe_and_uses_safe_dom():
    index = (REPO_ROOT / "lifeos/app/static/index.html").read_text(encoding="utf-8")
    script = (REPO_ROOT / "lifeos/app/static/app.js").read_text(encoding="utf-8")
    styles = (REPO_ROOT / "lifeos/app/static/style.css").read_text(encoding="utf-8")
    for element_id in (
        "internet-status", "internet-refresh", "internet-environment", "internet-agenda",
        "internet-sources", "internet-capabilities", "internet-research-form",
        "internet-media-form", "internet-nutrition-form",
    ):
        assert f'id="{element_id}"' in index
        assert f'$("{element_id}")' in script
    internet_script = script.split("// ---------- Internet Intelligence ----------", 1)[1].split(
        "// ---------- Learning Ledger ----------", 1
    )[0]
    assert "innerHTML" not in internet_script
    assert 'parsed.protocol !== "https:"' in internet_script
    assert ".internet-link { min-height: 44px;" in styles
    assert "Internet boundary" in index
