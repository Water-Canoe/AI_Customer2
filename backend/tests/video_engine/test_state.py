import unittest

from app.video_engine.models import const
from app.video_engine.services.state import MemoryState


class TestMemoryState(unittest.TestCase):
    def test_listener_receives_isolated_partial_updates(self):
        state = MemoryState()
        updates = []
        videos = ["first.mp4"]
        state.set_listener(updates.append)
        state.update_task("task-1", state=const.TASK_STATE_PROCESSING, videos=videos)
        state.update_task("task-1", progress=125)
        updates[0]["videos"].append("mutated.mp4")

        self.assertEqual(videos, ["first.mp4"])
        self.assertNotIn("progress", updates[0])
        self.assertEqual(updates[1], {"task_id": "task-1", "progress": 100})
        state.set_listener(None)
        state.update_task("task-2", progress=10)
        self.assertEqual(len(updates), 2)
        self.assertFalse(hasattr(state, "_tasks"))

    def test_listener_error_is_propagated(self):
        # 取消和数据库错误必须传回任务层，不能吞掉后继续生成。
        state = MemoryState()
        def cancel(_snapshot):
            raise RuntimeError("cancelled")
        state.set_listener(cancel)
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            state.update_task("task-1", progress=10)
