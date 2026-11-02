import unittest
from gamepad.pointer import DesktopPointer


class FakeX11:
    def __init__(self): self.events, self.closed = [], False
    def move(self, dx, dy): self.events.append(("move", dx, dy))
    def button(self, number, down): self.events.append(("button", number, down))
    def flush(self): pass
    def close(self): self.closed = True


def state(buttons=(), pressed=(), released=(), axes=None, connected=True):
    return dict(buttons=frozenset(buttons), pressed=frozenset(pressed), released=frozenset(released),
                axes=axes or {}, connected=connected)


class PointerTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeX11()
        self.pointer = DesktopPointer(backend=self.backend)

    def tearDown(self): self.pointer.close()

    def test_right_stick_moves_even_when_dialog_scrolling_disabled(self):
        result = self.pointer.update(state(axes={"rx": 1, "ry": -1}), .02, False)
        self.assertTrue(result["moved"])
        self.assertEqual(self.backend.events, [("move", 20, -20)])

    def test_dt_clamped_after_stall(self):
        self.pointer.update(state(axes={"rx": 1}), 2, False)
        self.assertEqual(self.backend.events[0], ("move", 50, 0))

    def test_fractional_motion_accumulates(self):
        for _ in range(10): self.pointer.update(state(axes={"rx": .1}), .02)
        self.assertGreater(sum(e[1] for e in self.backend.events if e[0] == "move"), 0)

    def test_nonfinite_axes_and_time_do_not_move(self):
        self.pointer.update(state(axes={"rx": float("nan"), "ry": float("inf")}), float("nan"))
        self.assertEqual(self.backend.events, [])

    def test_r3_drag_release(self):
        self.pointer.update(state(["R3"], ["R3"]), .02)
        self.pointer.update(state(["R3"]), .02)
        self.pointer.update(state(released=["R3"]), .02)
        self.assertEqual(self.backend.events, [("button", 1, True), ("button", 1, False)])

    def test_fast_r3_tap_not_lost(self):
        result = self.pointer.update(state(pressed=["R3"], released=["R3"]), .02)
        self.assertTrue(result["clicked"])
        self.assertEqual(self.backend.events, [("button", 1, True), ("button", 1, False)])

    def test_back_r3_right_click_consumes_back_release(self):
        self.pointer.update(state(["BACK"], ["BACK"]), .02)
        result = self.pointer.update(state(["BACK", "R3"], ["R3"]), .02)
        self.assertTrue(result["back_consumed"])
        self.assertEqual(self.backend.events, [("button", 3, True)])
        self.pointer.update(state(["BACK"], released=["R3"]), .02)
        result = self.pointer.update(state(released=["BACK"]), .02)
        self.assertTrue(result["back_release_consumed"])
        self.assertEqual(self.backend.events[-1], ("button", 3, False))
        self.assertFalse(self.pointer.update(state(), .02)["back_consumed"])

    def test_complete_combo_between_ticks_consumed(self):
        result = self.pointer.update(state(pressed=["BACK", "R3"], released=["BACK", "R3"]), .02)
        self.assertTrue(result["back_release_consumed"])
        self.assertEqual(self.backend.events, [("button", 3, True), ("button", 3, False)])

    def test_adding_back_releases_left_before_right(self):
        self.pointer.update(state(["R3"], ["R3"]), .02)
        self.pointer.update(state(["R3", "BACK"], ["BACK"]), .02)
        self.assertEqual(self.backend.events, [("button", 1, True), ("button", 1, False), ("button", 3, True)])

    def test_normal_back_not_consumed(self):
        self.pointer.update(state(["BACK"], ["BACK"]), .02)
        self.assertFalse(self.pointer.update(state(released=["BACK"]), .02)["back_release_consumed"])

    def test_lb_rb_dont_scroll_game(self):
        self.pointer.update(state(["LB"], ["LB"]), .02, False)
        self.assertEqual(self.backend.events, [])

    def test_lb_rb_scroll_only_dialog_and_repeat_bounded(self):
        for _ in range(10): self.pointer.update(state(["LB"]), .02, True)
        self.assertEqual(self.backend.events.count(("button", 4, True)), 2)
        self.pointer.update(state(["RB"]), .02, True)
        self.assertEqual(self.backend.events[-2:], [("button", 5, True), ("button", 5, False)])

    def test_disconnect_releases_all_mouse_buttons(self):
        self.pointer.update(state(["R3"]), .02)
        self.pointer.update(state(connected=False), .02)
        self.assertEqual(self.backend.events[-1], ("button", 1, False))

    def test_close_releases_and_is_idempotent(self):
        self.pointer.update(state(["R3"]), .02)
        self.pointer.close()
        self.pointer.close()
        self.assertEqual(self.backend.events[-1], ("button", 1, False))
        self.assertTrue(self.backend.closed)
        self.assertFalse(self.pointer.available)


if __name__ == "__main__": unittest.main()
