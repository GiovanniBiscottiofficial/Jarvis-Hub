from datetime import datetime, timezone

from app import commute


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return self

    def json(self):
        return self.payload


def test_commissioned_fresh_ha_travel_sensor_wins(monkeypatch):
    monkeypatch.setattr(commute, "HA_URL", "http://ha")
    monkeypatch.setattr(commute, "HA_TOKEN", "secret")
    monkeypatch.setattr(commute, "COMMUTE_ENTITY", "sensor.home_to_work")
    monkeypatch.setattr(commute, "_cache", None)
    monkeypatch.setattr(
        commute.httpx,
        "get",
        lambda *args, **kwargs: Response([{
            "entity_id": "sensor.home_to_work",
            "state": "24",
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "attributes": {
                "friendly_name": "Home to Work travel time",
                "unit_of_measurement": "min",
                "distance": 11.2,
            },
        }]),
    )
    result = commute.commute_snapshot(force=True)
    assert result["traffic_live"] is True
    assert result["minutes"] == 24
    assert result["miles"] == 11.2
    assert result["planned_departure"] == "07:35"


def test_baseline_is_explicitly_not_live_traffic(monkeypatch):
    monkeypatch.setattr(commute, "_cache", None)
    monkeypatch.setattr(commute, "_home_assistant_commute", lambda: None)
    monkeypatch.setattr(commute, "_baseline_route", lambda: {
        "status": "degraded", "minutes": 17, "miles": 8.5,
        "source": "OpenStreetMap / OSRM baseline", "traffic_live": False,
        "fresh": True, "entity_id": None,
    })
    result = commute.commute_snapshot(force=True)
    assert result["minutes"] == 17
    assert result["traffic_live"] is False
    assert "Baseline route only" in result["guidance"]
