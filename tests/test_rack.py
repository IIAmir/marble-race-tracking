"""The finish-rack camera: reading the slots and checking the board against them."""
import cv2
import numpy as np

from marbletrack.color import ColorModel, bgr_to_lab
from marbletrack_app.rack import compare, read_rack

COLOURS = {"red": (40, 40, 210), "blue": (200, 90, 30), "yellow": (30, 200, 230), "black": (25, 25, 25)}


def _scene(order, shadows=False):
    bg = np.full((120, 400, 3), (150, 150, 150), np.uint8)
    img = bg.copy()
    slots = [(40 + 50 * k, 60) for k in range(6)]
    for (x, y), mid in zip(slots, order):
        if shadows:
            cv2.circle(img, (x + 8, y + 8), 17, (95, 95, 95), -1)  # cast shadow: the background darkened
        cv2.circle(img, (x, y), 17, COLOURS[mid], -1)
        cv2.circle(img, (x - 5, y - 5), 4, (255, 255, 255), -1)  # highlight
    return img, bg, slots


def _palette():
    ids = list(COLOURS)
    models = []
    for mid in ids:
        px = np.array([COLOURS[mid]] * 50, np.uint8) + np.random.default_rng(0).integers(0, 6, (50, 3)).astype(np.uint8)
        models.append(ColorModel.fit(bgr_to_lab(px)))
    return models, ids


def test_rack_slots_are_read_in_order_and_empty_slots_end_the_order():
    models, ids = _palette()
    img, bg, slots = _scene(["yellow", "red", "black", "blue"], shadows=True)
    got = read_rack(img, bg, slots, [17] * 6, models, ids)
    assert got == ["yellow", "red", "black", "blue", None, None]


def test_board_is_checked_against_the_rack():
    agree = compare(["a", "b", "c", None], ["a", "b", "c"])
    assert agree["match"] and agree["status"] == "agrees"
    wrong = compare(["a", "c", "b"], ["a", "b", "c"])
    assert not wrong["match"] and wrong["status"] == "differs" and "slot 2" in wrong["detail"]
    filling = compare(["a", None, "c"], ["a", "b", "c"])
    assert not filling["match"] and filling["status"] == "filling"
    unsure = compare(["a", "?", "c"], ["a", "b", "c"])
    assert unsure["status"] == "unreadable"
