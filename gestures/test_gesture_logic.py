from gesture_logic import PoseLatch, classify_swipe, hand_pose, open_hand


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


def test_swipe_ignores_stationary_dwell_before_motion():
    samples = [
        (0.00, 0.40, 0.50),
        (0.15, 0.40, 0.50),
        (0.30, 0.40, 0.50),
        (0.40, 0.45, 0.50),
        (0.50, 0.56, 0.51),
        (0.60, 0.68, 0.51),
    ]
    assert classify_swipe(
        samples,
        x_travel=0.20,
        y_travel=0.18,
        minimum_speed=0.18,
    ) == "forward"


def test_open_hand_requires_three_extended_fingers():
    points = [(0.5, 0.9)] * 21
    for pip, tip in ((6, 8), (10, 12), (14, 16), (18, 20)):
        points[pip] = (0.5, 0.65)
        points[tip] = (0.5, 0.35)
    assert open_hand(points) is True
    points[12] = (0.5, 0.72)
    points[16] = (0.5, 0.72)
    assert open_hand(points) is False


def pose_points(*, extended=(), thumb=(0.42, 0.72)):
    points = [(0.5, 0.8)] * 21
    points[0] = (0.5, 0.8)
    points[9] = (0.5, 0.55)
    points[4] = thumb
    for finger, (pip, tip) in enumerate(((6, 8), (10, 12), (14, 16), (18, 20))):
        points[pip] = (0.5 + finger * 0.02, 0.62)
        points[tip] = (0.5 + finger * 0.02, 0.35 if finger in extended else 0.68)
    return points


def test_static_pose_vocabulary_is_conservative():
    assert hand_pose(pose_points(extended=(0, 1, 2, 3))) == "open"
    assert hand_pose(pose_points(extended=(0, 1))) == "two_finger"
    assert hand_pose(pose_points(thumb=(0.52, 0.34))) == "thumb_up"
    assert hand_pose(pose_points(thumb=(0.52, 0.92))) == "thumb_down"
    assert hand_pose(pose_points()) == "fist"
    pinched = pose_points(extended=(0, 1, 2, 3), thumb=(0.5, 0.36))
    assert hand_pose(pinched) == "pinch"


def test_pose_latch_requires_hold_release_and_rearm():
    latch = PoseLatch()
    assert latch.update("pinch", 1.0, 0.65) is None
    assert latch.update("pinch", 1.5, 0.65) is None
    assert latch.update("pinch", 1.7, 0.65) == "play_pause"
    assert latch.update("pinch", 2.7, 0.65) is None
    assert latch.update("open", 2.8, 0.65) is None
    assert latch.update("pinch", 3.0, 0.65) is None
    assert latch.update("pinch", 3.7, 0.65) == "play_pause"


def test_gesture_worker_declares_kiosk_only_safety_boundary():
    worker = (__import__("pathlib").Path(__file__).with_name("gesture_service.py")).read_text()
    assert '"house_action_execution": False' in worker
    assert '"kiosk_action_executed": True' in worker
    for protected in ("light.turn_", "lock.", "alarm_control_panel.", "switch.turn_"):
        assert protected not in worker
