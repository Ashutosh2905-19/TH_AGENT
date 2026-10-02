import json
import os
import re
import threading
import time
from pathlib import Path


class Tracer:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()

    def log(self, event, **data):
        line = json.dumps({"ts": round(time.time(), 3), "event": event, **data}, ensure_ascii=False)
        with self.lock, self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def read(self):
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line]


class Story:
    def __init__(self, root: Path):
        self.root = root
        self.state = json.loads((root / "state.json").read_text(encoding="utf-8"))
        self.state["id"] = root.name
        self.tracer = Tracer(root / "trace.jsonl")
        (root / "episodes").mkdir(exist_ok=True)

    @classmethod
    def create(cls, base: Path, premise: str, total: int):
        slug = re.sub(r"[^a-z0-9]+", "-", premise.lower()).strip("-")[:40]
        root = base / f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}"
        root.mkdir(parents=True)
        state = {
            "id": root.name,
            "premise": premise,
            "total": total,
            "plan": None,
            "beats": [],
            "plan_approved": False,
            "episodes": {},
            "chapters": {},
            "directives": [],
            "feedback": [],
            "pending": None,
        }
        (root / "state.json").write_text(json.dumps(state, indent=1), encoding="utf-8")
        return cls(root)

    @staticmethod
    def find(base: Path, story_id=None):
        if story_id:
            return Story(base / story_id)
        runs = sorted(p for p in base.glob("*/state.json"))
        if not runs:
            raise SystemExit("No stories yet. Start one with: python main.py new \"<premise>\"")
        return Story(runs[-1].parent)

    def save(self):
        tmp = self.root / "state.json.tmp"
        tmp.write_text(json.dumps(self.state, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.root / "state.json")

    def next_episode(self):
        return max(map(int, self.state["episodes"]), default=0) + 1

    def episode_path(self, n):
        return self.root / "episodes" / f"ep_{n:03d}.md"

    def keep(self, n, label, text):
        folder = self.root / "history" / f"ep_{n:03d}"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{sum(1 for _ in folder.iterdir()) + 1:02d}_{label}"
        path.write_text(text, encoding="utf-8")
        return str(path.relative_to(self.root))

    def versions(self, n):
        folder = self.root / "history" / f"ep_{n:03d}"
        return sorted(p.name for p in folder.iterdir()) if folder.exists() else []

    def write_file(self, name, text):
        (self.root / name).write_text(text, encoding="utf-8")
