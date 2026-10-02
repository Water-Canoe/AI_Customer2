from copy import deepcopy


class MemoryState:
    """向当前视频任务同步发送进度，不重复缓存数据库已有的任务结果。"""

    def __init__(self):
        self._listener = None

    def set_listener(self, listener):
        self._listener = listener

    def update_task(self, task_id: str, state: int | None = None, progress: int | None = None, **kwargs):
        # 只发送本次明确提供的字段，局部进度更新不重置状态或结果。
        snapshot = {"task_id": task_id, **kwargs}
        if state is not None:
            snapshot["state"] = state
        if progress is not None:
            snapshot["progress"] = min(100, int(progress))
        listener = self._listener
        if listener is not None:
            listener(deepcopy(snapshot))


state = MemoryState()


def set_progress_listener(listener) -> None:
    state.set_listener(listener)
