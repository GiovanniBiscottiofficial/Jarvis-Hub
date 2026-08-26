from app.routers.insights import compose_briefing


def briefing_facts(**overrides):
    facts = {
        "name": "Giovanni",
        "affirmation": "You are capable and prepared for today.",
        "weather": {
            "conditions": "partly cloudy",
            "high_f": 79,
            "low_f": 61,
        },
        "protein": 0,
        "protein_target": 170,
        "vitamins_taken": False,
        "vitamin_streak": 3,
        "meal_name": "garlic chicken and rice",
        "bills": [{"name": "Spectrum", "amount": 93.95}],
        "bills_total": 93.95,
        "leftover": 100,
        "spent_week": 0,
        "safe_to_spend": 225,
        "audit_health": "scheduled",
        "workouts": [],
        "next_pay": {
            "label": "Paycheck 1",
            "date": "2026-08-28",
            "days_away": 2,
            "amount": 2064.24,
        },
    }
    facts.update(overrides)
    return facts


def test_morning_briefing_combines_real_priorities_naturally():
    result = compose_briefing(briefing_facts(), hour=7, day_ordinal=10)

    speech = result["speech"]
    assert result["period"] == "morning"
    assert "Giovanni" in speech
    assert "take your vitamins and pull out" in speech
    assert "garlic chicken and rice tonight" in speech
    assert "Your protein target today is 170 grams" in speech
    assert "Protein target is" not in speech
    assert "Breakfast pick:" not in speech
    assert "Safe to spend this paycheck:" not in speech
    assert "sir" not in speech.lower()


def test_evening_briefing_does_not_use_morning_departure_language():
    result = compose_briefing(briefing_facts(), hour=21, day_ordinal=10)

    assert result["period"] == "evening"
    assert "Good morning" not in result["speech"]
    assert "before you leave" not in result["speech"]
    assert "before you head out" not in result["speech"]
    assert "Vitamins are still open for today" in result["speech"]


def test_scheduled_budget_is_not_described_as_overdue_or_safe_to_spend():
    result = compose_briefing(briefing_facts(), hour=7, day_ordinal=11)

    assert "first budget cycle is staged" in result["speech"]
    assert "overdue" not in result["speech"].lower()
    assert "safe to spend" not in result["speech"].lower()


def test_action_needed_budget_withholds_safe_to_spend_claim():
    result = compose_briefing(
        briefing_facts(audit_health="action needed"),
        hour=14,
        day_ordinal=12,
    )

    assert "budget needs a quick review" in result["speech"]
    assert "$225.00 is safe" not in result["speech"]


def test_delivery_is_stable_for_the_same_day_and_varies_across_days():
    first = compose_briefing(briefing_facts(), hour=7, day_ordinal=10)
    repeated = compose_briefing(briefing_facts(), hour=7, day_ordinal=10)
    next_day = compose_briefing(briefing_facts(), hour=7, day_ordinal=11)

    assert first == repeated
    assert first["speech"] != next_day["speech"]
    assert all(set(section) == {"key", "text"} for section in first["sections"])
