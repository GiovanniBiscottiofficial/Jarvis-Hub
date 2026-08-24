from gesture_logic import classify_swipe, open_hand


def trail(dx: float, dy: float, duration: float = 0.5):
    return [
        (duration * step / 5, dx * step / 5, dy * step / 5)
        for step in range(6)
    ]


def test_swipes_require_direction_distance_and_speed():
    options = {"x_travel": 0.20, "y_travel": 0.18, "minimum_speed": 0.24}
    assert classify_swipe(trail(0.30, 0.02), **options) == "forward"
    assert classify_swipe(trail(-0.30, 0.02), **options) == "back"
    assert classify_swipe(trail(0.01, -0.25), **options) == "up"
    assert classify_swipe(trail(0.01, 0.25), **options) == "down"
    assert classify_swipe(trail(0.08, 0.02), **options) is None
    assert classify_swipe(trail(0.30, 0.02, duration=2.0), **options) is None
    assert classify_swipe(trail(0.25, 0.22), **options) is None


def test_open_hand_requires_three_extended_fingers():
    points = [(0.5, 0.9)] * 21
    for pip, tip in ((6, 8), (10, 12), (14, 16), (18, 20)):
        points[pip] = (0.5, 0.65)
        points[tip] = (0.5, 0.35)
    assert open_hand(points) is True
    points[12] = (0.5, 0.72)
    points[16] = (0.5, 0.72)
    assert open_hand(points) is False
