from pathlib import Path

from fastapi.testclient import TestClient

from app import main


AUTH = {"Authorization": "Bearer test-token"}
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_body_facts_share_one_operating_picture_after_mutations(fresh_db):
    client = TestClient(main.app, headers=AUTH)
    profile = next(item for item in client.get("/api/profiles").json() if item["active"])

    assert client.put(
        f"/api/profiles/{profile['id']}/targets",
        json={"protein_target_g": 120, "step_target": 9000, "calorie_target": 2100},
    ).status_code == 200
    assert client.post("/api/body/meals/log", json={"name": "Consistency meal", "protein_g": 35}).status_code == 200
    assert client.post("/api/body/steps", json={"count": 4321}).status_code == 200
    assert client.post("/api/body/water", json={"glasses": 3}).status_code == 200
    assert client.post("/api/body/vitamins/take").status_code == 200

    today = client.get("/api/today").json()
    summary = client.get("/api/body/summary").json()
    loop = client.get("/api/body/daily-loop").json()
    command = client.get("/api/command-center?event_limit=1").json()["context"]["lifeos"]
    answers = client.get("/api/ask").json()

    assert today["profile"] == command["profile"] == profile["name"]
    assert today["protein"] == summary["protein"]
    assert today["protein"]["today_g"] == loop["today"]["protein_g"] == command["body"]["protein_g"] == 35
    assert today["protein"]["target_g"] == loop["targets"]["protein_g"] == command["body"]["protein_target_g"] == 120
    assert today["steps_today"] == summary["steps"]["today"] == loop["today"]["steps"] == command["body"]["steps"] == 4321
    assert today["step_target"] == summary["steps"]["target"] == loop["targets"]["steps"] == command["body"]["step_target"] == 9000
    assert today["water"]["today"] == summary["water"]["today"] == loop["today"]["water_glasses"] == command["body"]["water"] == 3
    assert today["water"]["target"] == summary["water"]["target"] == loop["targets"]["water_glasses"] == command["body"]["water_target"]
    assert today["vitamins_taken"] is summary["vitamins_taken"] is loop["completion"]["vitamins_taken"] is command["body"]["vitamins_taken"] is True
    assert "4,321 steps today against a 9,000 target" in answers["steps"]


def test_finance_facts_match_today_command_and_money_os(fresh_db):
    client = TestClient(main.app, headers=AUTH)
    accounts = client.get("/api/vault/accounts").json()
    onepay = next(account for account in accounts if account["name"] == "OnePay")
    assert client.put(
        f"/api/vault/accounts/{onepay['id']}/balance", json={"balance": 777.77}
    ).status_code == 200

    expected_total = round(sum(
        777.77 if account["id"] == onepay["id"] else account["balance"]
        for account in accounts
    ), 2)
    today = client.get("/api/today").json()
    command = client.get("/api/command-center?event_limit=1").json()["context"]["lifeos"]
    money = client.get("/api/money/command-center").json()
    money_total = round(sum(account["balance"] for account in money["accounts"]), 2)

    assert round(today["vault_total"], 2) == expected_total
    assert round(command["vault"]["accounts_total"], 2) == expected_total
    assert money_total == expected_total


def test_frontend_mutations_invalidate_all_affected_surfaces():
    static = REPO_ROOT / "lifeos/app/static"
    app_script = (static / "app.js").read_text(encoding="utf-8")
    body_script = (static / "bodyops-enhanced.js").read_text(encoding="utf-8")
    money_script = (static / "money-command.js").read_text(encoding="utf-8")

    assert 'new CustomEvent("jarvis:data-changed"' in app_script
    assert 'window.addEventListener("jarvis:data-changed"' in app_script
    assert 'window.addEventListener("jarvis:refresh-active"' in body_script
    assert 'window.addEventListener("jarvis:refresh-active"' in money_script
    assert 'if (panelName === "today") loadToday();' in app_script
    assert 'if (panelName === "todo") loadShopping();' in app_script
    assert 'window.setInterval(() =>' in app_script
