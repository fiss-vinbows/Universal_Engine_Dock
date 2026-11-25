"""シミュレーションモードの入力(src/simulator/controls.py)のテスト。"""

import tempfile
import unittest
from pathlib import Path

from PyQt6 import QtCore

from uedock.games.puyo.simulator import controls


class ControlsTest(unittest.TestCase):
    def test_key_name_with_modifier(self):
        self.assertEqual(controls.key_name(QtCore.Qt.Key.Key_R, QtCore.Qt.KeyboardModifier.ShiftModifier), "Shift+R")
        self.assertEqual(controls.key_name(QtCore.Qt.Key.Key_Left, QtCore.Qt.KeyboardModifier.NoModifier), "Left")
        self.assertIsNone(controls.key_name(QtCore.Qt.Key.Key_Shift, QtCore.Qt.KeyboardModifier.ShiftModifier))

    def test_bindings_roundtrip_and_fill_missing(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "keys.json"
            path.write_text('{"drop": "Space", "unknown": "Q"}', encoding="utf-8")
            b = controls.load_bindings(path)
            self.assertEqual(b["drop"], "Space")
            self.assertEqual(b["move_left"], "Left")
            self.assertNotIn("unknown", b)
            controls.save_bindings(b, path)
            self.assertEqual(controls.load_bindings(path), b)
        self.assertEqual(controls.action_for(controls.DEFAULT_BINDINGS, "Shift+R"), "reset_new")

    def test_pad_inputs(self):
        inputs = controls.inputs_from_raw(0b11, 9000, 0, 32767)  # ボタン1・2、十字キー右、スティック左
        self.assertEqual(inputs, {"Button1", "Button2", "PovRight", "X-"})
        self.assertEqual(
            controls.actions_for_inputs(controls.DEFAULT_PAD_BINDINGS, inputs),
            {"rotate_ccw", "rotate_cw", "move_right", "move_left"},
        )

    def test_repeat_only_for_moves(self):
        done = []
        r = controls.RepeatTracker()
        r.update({"move_left", "drop"}, 0, done.append)
        r.update({"move_left", "drop"}, controls.REPEAT_DELAY_MS, done.append)
        r.update({"move_left", "drop"}, controls.REPEAT_DELAY_MS + controls.REPEAT_INTERVAL_MS, done.append)
        self.assertEqual(done.count("move_left"), 3)
        self.assertEqual(done.count("drop"), 1)  # 置く操作は押しっぱなしでも1回


    def test_step_texts(self):
        from uedock.games.puyo.simulator.window import step_texts

        lines = step_texts(["rotate_cw", "move_right", "move_right"], controls.DEFAULT_BINDINGS)
        self.assertEqual(lines, ["1. 右回転 (X)", "2. 右へ移動 (Right) ×2", "3. 置く (Down)"])


class NextCountTest(unittest.TestCase):
    def test_toggle_hides_second_next_only(self) -> None:
        # ネクストの表示数 1 / 2 の切り替え。AI にも見えている組数だけ渡す
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6 import QtWidgets

        from uedock.games.puyo.simulator import window as sim

        _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        w = sim.SimulatorWindow()
        sent: list = []
        try:
            w.show()
            self.assertTrue(w.next_views[1].isVisible())
            w.next_count_toggle.setChecked(False)
            self.assertTrue(w.next_views[0].isVisible())
            self.assertFalse(w.next_views[1].isVisible())
            for advisor in w.advisors.values():
                advisor.request = sent.append
            w.next_count_toggle.setChecked(True)
            self.assertTrue(w.next_views[1].isVisible())
            self.assertEqual(len(sent[-1].pairs), 3)
            w.next_count_toggle.setChecked(False)
            self.assertEqual(len(sent[-1].pairs), 2)
            self.assertEqual(sent[-1].pairs, w.state.upcoming(2))
        finally:
            w.close()


class CandidateActionTest(unittest.TestCase):
    def test_analyze_and_next_candidate(self) -> None:
        # 検討と候補手の切り替えもキー・コントローラーに割り当てられる
        import os
        from types import SimpleNamespace
        from unittest import mock

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6 import QtWidgets

        from uedock.games.puyo.simulator import controls, window as sim

        for action in ("analyze", "next_candidate"):
            self.assertIn(action, controls.ACTIONS)
        keys = list(controls.DEFAULT_BINDINGS.values())
        self.assertEqual(len(keys), len(set(keys)))
        pads = [i for inputs in controls.DEFAULT_PAD_BINDINGS.values() for i in inputs]
        self.assertEqual(len(pads), len(set(pads)))

        _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        w = sim.SimulatorWindow()
        try:
            with mock.patch.object(w, "_refresh"), mock.patch.object(w, "_analyze") as analyze:
                w.analyze_button.setEnabled(False)
                w._perform("analyze")
                analyze.assert_not_called()
                w.analyze_button.setEnabled(True)
                w._perform("analyze")
                analyze.assert_called_once()

                w.candidates = None
                w._perform("next_candidate")  # 検討の前は何もしない
                self.assertIsNone(w.selected_candidate)
                w.show_toggle.setChecked(True)
                w.candidates = SimpleNamespace(candidates=[1, 2, 3])
                seen = []
                for _ in range(3):
                    w._perform("next_candidate")
                    seen.append(w.selected_candidate)
                self.assertEqual(seen, [1, 2, 0])
                w.candidates = None
        finally:
            w.close()


if __name__ == "__main__":
    unittest.main()
