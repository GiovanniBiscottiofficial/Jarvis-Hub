from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


def _dashboard(name: str) -> dict:
    return yaml.safe_load(
        (REPO_ROOT / "ha-config" / "dashboards" / name).read_text(encoding="utf-8")
    )


def _iframe_cards(view: dict) -> list[tuple[dict, dict]]:
    found = []
    for section in view.get("sections", []):
        for card in section.get("cards", []):
            if card.get("type") == "iframe":
                found.append((section, card))
    for card in view.get("cards", []):
        if card.get("type") == "iframe":
            found.append(({}, card))
    return found


def test_jarvis_iframes_are_cache_versioned_and_have_explicit_x1_sizing():
    dashboard = _dashboard("jarvis.yaml")
    views = {view["path"]: view for view in dashboard["views"]}
    iframe_cards = [item for view in dashboard["views"] for item in _iframe_cards(view)]

    assert len(iframe_cards) == 6
    assert all("?v=" in card["url"] for _, card in iframe_cards)

    wall = {card["url"].split("?")[0]: card for _, card in _iframe_cards(views["wall"])}
    assert wall["/local/jarvis-avatar.html"]["grid_options"] == {
        "columns": "full",
        "rows": 4,
    }
    assert wall["/local/jarvis-lights.html"]["grid_options"] == {
        "columns": "full",
        "rows": 8,
    }

    wall_plus_section, wall_plus_map = next(
        item
        for item in _iframe_cards(views["wall-plus"])
        if item[1]["url"].startswith("/local/jarvis-rooms.html")
    )
    assert wall_plus_section["column_span"] == 2
    assert wall_plus_map["grid_options"] == {"columns": "full", "rows": 12}

    floor_section, floor_map = _iframe_cards(views["twin"])[0]
    assert floor_section["column_span"] == 3
    assert floor_map["grid_options"] == {"columns": "full", "rows": 12}

    media = _iframe_cards(views["media-command"])[0][1]
    assert "min-height: calc(100vh - 8px)" in media["card_mod"]["style"]


def test_legacy_lifeos_dashboard_iframe_is_cache_versioned():
    lifeos = _dashboard("lifeos.yaml")
    card = lifeos["views"][0]["cards"][0]
    assert card["type"] == "iframe"
    assert card["url"].startswith("/local/lifeos.html?v=")
