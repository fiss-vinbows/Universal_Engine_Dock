"""プロセスごとの CPU 使用率(uedock/process_cpu.py)と、ぷよの上のバー(テトリスと同じ起動時間・CPU 使用率)のテスト。"""

from __future__ import annotations

import os
import sys
import unittest

from PyQt6 import QtWidgets

from uedock.process_cpu import CpuBreakdownStats, ProcessCpuMeter, ProcessGroup, process_cpu_time, with_descendants


class ProcessCpuMeterTest(unittest.TestCase):
    def test_usage_is_share_of_whole_pc(self) -> None:
        """1秒に 2コア分(2e7 × 100ns)使えば、4コアの PC の 50%。エンジンは子のプロセスも足す。"""
        clock = iter([0.0, 1.0])
        times = {10: iter([0, 10_000_000]), 20: iter([0, 5_000_000]), 21: iter([0, 15_000_000])}
        meter = ProcessCpuMeter(
            [ProcessGroup("アプリ", lambda: 10), ProcessGroup("AI", lambda: 20, descendants=True)],
            read_time=lambda pid: next(times[pid]),
            read_children=lambda: {20: [21]},
            clock=lambda: next(clock),
            cpus=4,
        )
        self.assertEqual(meter.sample(), {}, "初回は基準を取るだけ")
        usage = meter.sample()
        self.assertAlmostEqual(usage["アプリ"], 25.0)
        self.assertAlmostEqual(usage["AI"], 50.0)

    def test_missing_process_is_skipped(self) -> None:
        meter = ProcessCpuMeter([ProcessGroup("ゲーム", lambda: None)], read_time=lambda pid: 0,
                                read_children=dict, clock=iter([0.0, 1.0]).__next__, cpus=1)
        meter.sample()
        self.assertEqual(meter.sample(), {})

    def test_app_does_not_count_engine_children(self) -> None:
        """Universal Engine Dock は思考エンジンの親なので、子を足さない(足すと AI の分を二重に数える)。"""
        self.assertEqual(sorted(with_descendants(1, {1: [2, 3], 3: [4]})), [1, 2, 3, 4])
        clock = iter([0.0, 1.0])
        times = {1: iter([0, 1_000_000]), 2: iter([0, 9_000_000])}
        meter = ProcessCpuMeter([ProcessGroup("アプリ", lambda: 1)], read_time=lambda pid: next(times[pid]),
                                read_children=lambda: {1: [2]}, clock=lambda: next(clock), cpus=1)
        meter.sample()
        self.assertAlmostEqual(meter.sample()["アプリ"], 10.0)

    @unittest.skipUnless(sys.platform == "win32", "Windows だけ")
    def test_reads_own_process(self) -> None:
        self.assertIsInstance(process_cpu_time(os.getpid()), int)

    def test_summary(self) -> None:
        stats = CpuBreakdownStats(["アプリ", "AI"])
        for value in (10.0, 30.0, 20.0):
            stats.add({"アプリ": value})
        self.assertEqual(stats.count(), 3)
        self.assertEqual(stats.summary(), "アプリ 中央 20%・最大 30%")
        stats.reset()
        self.assertEqual(stats.summary(), "")


class PuyoBadgeTest(unittest.TestCase):
    """ぷよの上のバーも、テトリスと同じく左に起動時間・CPU 使用率、終了ボタンは「支援モードを終了」。"""

    def test_status_labels_like_tetris(self) -> None:
        from uedock.games.puyo.worker import ControlBadge
        from uedock.ui.status_labels import CpuUsageLabel, FpsLabel, UptimeLabel

        _ = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        badge = ControlBadge(on_stop=lambda: None)
        self.addCleanup(badge.close)
        self.assertEqual(badge.stop_button.text(), "支援モードを終了")
        width = badge.width()
        badge.add_status_labels(["1p", "2p"])
        layout = badge.layout()
        self.assertIsInstance(layout.itemAt(0).widget(), UptimeLabel)
        self.assertIsInstance(layout.itemAt(1).widget(), CpuUsageLabel)
        self.assertIsInstance(layout.itemAt(2).widget(), FpsLabel)
        self.assertIs(layout.itemAt(3).widget(), badge.stop_button)
        self.assertGreater(badge.width(), width, "起動時間・CPU の分だけ広がっていない")
        extra = QtWidgets.QPushButton("足した部品")
        badge.add_widget(extra)
        self.assertIs(layout.itemAt(4).widget(), extra, "プラグインが足す部品は終了ボタンの隣")
        badge.fps_label.set_fps("2p", 31.4)
        self.assertEqual(badge.fps_label.text(), "観測 1P --・2P 31fps")

    def test_overlay_shows_vision_fps_only(self) -> None:
        from uedock.games.puyo.engine.perf_meter import vision_text
        from uedock.games.puyo.overlay.panel_text_en import to_english

        self.assertEqual(vision_text(33.2), "観測 33fps")
        self.assertEqual(to_english(vision_text(33.2)), "Vision 33fps")


class FpsLabelTest(unittest.TestCase):
    """観測の FPS も上のバーに出す。テトリスも同じ。"""

    def test_format(self) -> None:
        from uedock.ui.status_labels import format_fps

        self.assertEqual(format_fps({"1p": 32.4}, ["1p"]), "観測 32fps")
        self.assertEqual(format_fps({"1p": None}, ["1p"]), "観測 --fps")
        self.assertEqual(format_fps({"1p": 32.0, "2p": 30.6}, ["1p", "2p"]), "観測 1P 32・2P 31fps")

    def test_tetris_bar_has_fps_next_to_cpu(self) -> None:
        from uedock.games.tetris.worker import ControlBadge, CpuUsageLabel, attach_fps, attach_uptime
        from uedock.ui.status_labels import FpsLabel

        _ = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        badge = ControlBadge(on_stop=lambda: None)
        self.addCleanup(badge.close)
        attach_uptime(badge)
        label = attach_fps(badge, ["1p"])
        self.assertIsInstance(badge.layout().itemAt(1).widget(), CpuUsageLabel)
        self.assertIs(badge.layout().itemAt(2).widget(), label)
        self.assertIsInstance(label, FpsLabel)
        label.set_fps("1p", 59.6)
        self.assertEqual(label.text(), "観測 60fps")
        label.set_fps("2p", 10.0)  # 受け持っていない側は無視する
        self.assertEqual(label.text(), "観測 60fps")


if __name__ == "__main__":
    unittest.main()
