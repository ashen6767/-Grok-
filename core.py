from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Callable


APP_DIR = Path(os.getenv("LOCALAPPDATA", Path.home())) / "GrokBatchAssistant"
STATE_FILE = APP_DIR / "tasks.json"

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".avif", ".heic", ".heif"}


@dataclass
class Defaults:
    prompt: str = ""
    usage: str = "首帧"
    resolution: str = "480p"
    duration: str = "10s"
    ratio: str = "1:1"
    audio: str = "开启"
    submit_interval: int = 3
    auto_pause: bool = True
    clear_on_complete: bool = True


@dataclass
class Task:
    path: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "等待提交"
    error: str = ""
    overrides: dict = field(default_factory=dict)
    detected_ratio: str = ""

    @property
    def name(self) -> str:
        return Path(self.path).name

    def effective(self, defaults: Defaults) -> dict:
        values = asdict(defaults)
        values.update(self.overrides)
        if values["usage"] == "首帧":
            values["ratio"] = "由底图决定"
        elif values["resolution"] == "1080p":
            values["resolution"] = "720p"
        return values


def natural_key(path: str) -> list:
    import re
    return [int(v) if v.isdigit() else v.casefold() for v in re.split(r"(\d+)", Path(path).name)]


class TaskStore:
    def __init__(self, state_file: Path = STATE_FILE):
        self.state_file = state_file
        self.defaults = Defaults()
        self.theme_mode = "light"
        self.tasks: list[Task] = []
        self.running = False
        self.lock = threading.RLock()
        self.on_change: Callable[[], None] | None = None
        self.browser_seen = 0.0
        self.browser_owner = ""
        self.helper_version = ""
        self.connected_helper_version = ""
        self.connected_browser = ""
        self.load()

    def notify(self) -> None:
        self.save()
        if self.on_change:
            self.on_change()

    def add_paths(self, paths: list[str]) -> int:
        found: list[str] = []
        for raw in paths:
            path = Path(raw)
            candidates = path.rglob("*") if path.is_dir() else [path]
            found.extend(str(p.resolve()) for p in candidates if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
        existing = {os.path.normcase(t.path) for t in self.tasks}
        unique = sorted({p for p in found if os.path.normcase(p) not in existing}, key=natural_key)
        self.tasks.extend(Task(p) for p in unique)
        if unique:
            self.notify()
        return len(unique)

    def remove(self, ids: set[str]) -> None:
        with self.lock:
            self.tasks = [t for t in self.tasks if t.id not in ids]
            self.notify()

    def clear(self) -> None:
        with self.lock:
            self.running = False
            self.tasks.clear()
            self.notify()

    def reset_failed(self) -> None:
        with self.lock:
            for task in self.tasks:
                if task.status in {"提交失败", "需要人工处理"}:
                    task.status, task.error = "等待提交", ""
            self.notify()

    def prioritize(self, task_id: str) -> bool:
        """Move one waiting task to the front so it is dispatched next."""
        with self.lock:
            index = next((i for i, task in enumerate(self.tasks) if task.id == task_id), None)
            if index is None or self.tasks[index].status != "等待提交":
                return False
            task = self.tasks.pop(index)
            self.tasks.insert(0, task)
            self.notify()
            return True

    def dispatch_next(self) -> dict | None:
        with self.lock:
            if not self.running or any(t.status in {"正在上传", "正在设置", "正在提交"} for t in self.tasks):
                return None
            task = next((t for t in self.tasks if t.status == "等待提交"), None)
            if not task:
                return None
            task.status = "正在上传"
            cfg = task.effective(self.defaults)
            self.notify()
            return {"id": task.id, "name": task.name, **cfg}

    def event(self, task_id: str, status: str, **data) -> bool:
        with self.lock:
            task = next((t for t in self.tasks if t.id == task_id), None)
            if not task:
                return False
            task.status = status
            task.error = data.get("error", "")
            if data.get("detected_ratio"):
                task.detected_ratio = str(data["detected_ratio"])
            if status in {"提交失败", "需要人工处理"}:
                self.running = False
            elif status == "已提交":
                no_pending = not any(
                    item.status in {"等待提交", "正在上传", "正在设置", "正在提交"}
                    for item in self.tasks
                )
                all_completed = bool(self.tasks) and all(item.status == "已提交" for item in self.tasks)
                if self.defaults.auto_pause and no_pending:
                    self.running = False
                if self.defaults.clear_on_complete and all_completed:
                    self.tasks.clear()
            self.notify()
            return True

    def stats(self) -> dict:
        with self.lock:
            waiting = sum(t.status == "等待提交" for t in self.tasks)
            active = sum(t.status in {"正在上传", "正在设置", "正在提交"} for t in self.tasks)
            failed = sum(t.status in {"提交失败", "需要人工处理"} for t in self.tasks)
            helper_state = "error" if failed else "running" if self.running else "warning" if waiting else "ready"
            return {
                "running": self.running,
                "waiting": waiting,
                "active": active,
                "failed": failed,
                "helper_state": helper_state,
                "browser_connected": time.time() - self.browser_seen < 15,
            }

    def save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "defaults": asdict(self.defaults),
            "theme_mode": self.theme_mode,
            "tasks": [asdict(t) for t in self.tasks],
        }
        temp = self.state_file.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, self.state_file)

    def load(self) -> None:
        if not self.state_file.exists():
            return
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            default_names = {item.name for item in fields(Defaults)}
            task_names = {item.name for item in fields(Task)}
            self.defaults = Defaults(**{key: value for key, value in data.get("defaults", {}).items() if key in default_names})
            theme_mode = str(data.get("theme_mode", "light"))
            self.theme_mode = theme_mode if theme_mode in {"light", "dark", "system"} else "light"
            self.tasks = [Task(**{key: value for key, value in item.items() if key in task_names}) for item in data.get("tasks", [])]
            known = {"等待提交", "正在上传", "正在设置", "正在提交", "已提交", "提交失败", "需要人工处理"}
            for task in self.tasks:
                if task.status in {"正在上传", "正在设置", "正在提交"}:
                    task.status = "需要人工处理"
                    task.error = "上次运行在提交确认前中断，请检查 Grok 页面后再决定是否重试。"
                elif task.status not in known:
                    task.status = "已提交"
        except (OSError, ValueError, TypeError):
            self.defaults, self.theme_mode, self.tasks = Defaults(), "light", []
