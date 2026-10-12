import json
import tempfile
import unittest
import urllib.request
from pathlib import Path

from PIL import Image

from core import Defaults, Task, TaskStore
from server import Bridge, BridgePortInUseError


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = TaskStore(self.root / "state.json")

    def tearDown(self): self.temp.cleanup()

    def image(self, name):
        path = self.root / name; Image.new("RGB", (4, 4), "red").save(path); return path

    def test_natural_order_and_deduplication(self):
        for name in ("10.png", "2.png", "1.png"): self.image(name)
        self.assertEqual(self.store.add_paths([str(self.root), str(self.root / "1.png")]), 3)
        self.assertEqual([t.name for t in self.store.tasks], ["1.png", "2.png", "10.png"])

    def test_parameter_rules(self):
        path = self.image("one.png"); self.store.add_paths([str(path)])
        task = self.store.tasks[0]
        cfg = task.effective(Defaults(usage="首帧", resolution="1080p", ratio="9:16"))
        self.assertEqual(cfg["ratio"], "由底图决定")
        cfg = task.effective(Defaults(usage="参考", resolution="1080p", ratio="9:16"))
        self.assertEqual(cfg["resolution"], "720p")
        cfg = task.effective(Defaults(usage="循环", resolution="1080p", ratio="1:1"))
        self.assertEqual(cfg["resolution"], "720p")

    def test_audio_setting_is_part_of_effective_task(self):
        path = self.image("audio.png"); self.store.add_paths([str(path)])
        task = self.store.tasks[0]
        self.assertEqual(task.effective(Defaults())["audio"], "开启")
        task.overrides = {"audio": "关闭"}
        self.assertEqual(task.effective(Defaults())["audio"], "关闭")

    def test_theme_preference_is_persisted_and_invalid_values_fall_back(self):
        self.store.theme_mode = "dark"
        self.store.save()
        self.assertEqual(TaskStore(self.store.state_file).theme_mode, "dark")
        self.store.state_file.write_text('{"theme_mode":"unknown"}', encoding="utf-8")
        self.assertEqual(TaskStore(self.store.state_file).theme_mode, "light")

    def test_helper_state_summarizes_queue_status(self):
        stats = self.store.stats()
        self.assertEqual(stats["helper_state"], "ready")
        self.image("state.png"); self.store.add_paths([str(self.root / "state.png")])
        self.assertEqual(self.store.stats()["helper_state"], "warning")
        self.store.running = True
        self.assertEqual(self.store.stats()["helper_state"], "running")
        self.store.tasks[0].status = "提交失败"
        self.assertEqual(self.store.stats()["helper_state"], "error")

    def test_only_one_inflight(self):
        for name in ("1.png", "2.png"): self.image(name)
        self.store.add_paths([str(self.root)]); self.store.running = True
        first = self.store.dispatch_next(); self.assertTrue(first["id"])
        self.assertIsNone(self.store.dispatch_next())
        self.store.event(first["id"], "已提交")
        self.assertTrue(self.store.dispatch_next()["id"])

    def test_waiting_task_can_be_prioritized(self):
        first = Task("first.png")
        second = Task("second.png")
        self.store.tasks = [first, second]
        self.assertTrue(self.store.prioritize(second.id))
        self.assertEqual([task.id for task in self.store.tasks], [second.id, first.id])
        second.status = "正在上传"
        self.assertFalse(self.store.prioritize(second.id))

    def test_submit_failure_pauses_batch(self):
        path = self.image("one.png"); self.store.add_paths([str(path)]); self.store.running = True
        task = self.store.dispatch_next()
        self.store.event(task["id"], "提交失败", error="button not found")
        self.assertFalse(self.store.running)

    def test_last_success_auto_pauses_by_default(self):
        path = self.image("one.png"); self.store.add_paths([str(path)]); self.store.running = True
        task = self.store.dispatch_next()
        self.store.event(task["id"], "已提交")
        self.assertFalse(self.store.running)
        self.assertEqual(self.store.tasks, [])

    def test_continuous_mode_stays_running(self):
        path = self.image("one.png"); self.store.add_paths([str(path)]); self.store.running = True
        self.store.defaults.auto_pause = False
        task = self.store.dispatch_next()
        self.store.event(task["id"], "已提交")
        self.assertTrue(self.store.running)

    def test_completed_tasks_remain_when_auto_clear_is_disabled(self):
        path = self.image("one.png"); self.store.add_paths([str(path)]); self.store.running = True
        self.store.defaults.clear_on_complete = False
        task = self.store.dispatch_next()
        self.store.event(task["id"], "已提交")
        self.assertEqual(len(self.store.tasks), 1)
        self.assertEqual(self.store.tasks[0].status, "已提交")

    def test_detected_ratio_is_saved_with_task_status(self):
        path = self.image("portrait.png"); self.store.add_paths([str(path)])
        task = self.store.tasks[0]
        self.store.event(task.id, "正在设置", detected_ratio="9:16")
        self.assertEqual(task.detected_ratio, "9:16")

    def test_clear_stops_and_removes_all_tasks(self):
        path = self.image("one.png"); self.store.add_paths([str(path)]); self.store.running = True
        self.store.clear()
        self.assertFalse(self.store.running)
        self.assertEqual(self.store.tasks, [])

    def test_http_bridge(self):
        path = self.image("one.png"); self.store.add_paths([str(path)]); self.store.running = True; self.store.helper_version = "0.7.0"
        bridge = Bridge(self.store, port=17844); bridge.start()
        try:
            req = urllib.request.Request("http://127.0.0.1:17844/api/heartbeat", data=json.dumps({"client":"test", "version":"0.7.0", "browser":"Edge"}).encode(), headers={"Content-Type":"application/json"})
            heartbeat = json.load(urllib.request.urlopen(req))
            self.assertTrue(heartbeat["active"])
            self.assertTrue(heartbeat["running"])
            self.assertEqual(heartbeat["required_version"], "0.7.0")
            self.assertEqual(self.store.connected_helper_version, "0.7.0")
            self.assertEqual(self.store.connected_browser, "Edge")
            helper = json.load(urllib.request.urlopen("http://127.0.0.1:17844/api/helper-version"))
            self.assertEqual(helper["version"], "0.7.0")
            self.assertTrue(helper["busy"])
            task = json.load(urllib.request.urlopen("http://127.0.0.1:17844/api/next"))
            self.assertEqual(task["name"], "one.png")
            self.assertGreater(len(urllib.request.urlopen(f"http://127.0.0.1:17844/api/image/{task['id']}").read()), 10)
        finally: bridge.stop()

    def test_http_bridge_port_is_exclusive(self):
        first = Bridge(self.store, port=0); first.start()
        port = first.httpd.server_address[1]
        second = Bridge(self.store, port=port)
        try:
            with self.assertRaises(BridgePortInUseError):
                second.start()
        finally:
            first.stop()


if __name__ == "__main__": unittest.main()
