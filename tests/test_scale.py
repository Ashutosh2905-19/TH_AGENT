import argparse
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from story_agent import memory
from story_agent.config import Settings, price
from story_agent.store import Story

VOCAB = "signal tower archive courier ledger stairwell elevator coroner badge key fire flood debt mask server vault pulse ghost route rooftop basement".split()
CAST = ["Elias Thorne", "Sloane", "Director Vane", "Detective Miller", "Unit 734", "Mara Quill", "Ivo Petrov", "Nadia Sen"]
HEADER = f"{'episode':>7} {'prompt tokens':>13} {'characters':>10} {'facts':>6} {'open threads':>12} {'build ms':>8} {'cost $':>9}"


def row(n, tokens, bible, ms, cost):
    open_threads = sum(t["status"] == "open" for t in bible["threads"].values())
    return f"{n:>7} {tokens:>13} {len(bible['characters']):>10} {len(bible['facts']):>6} {open_threads:>12} {ms:>8.1f} {cost:>9.4f}"


def simulated(s, total, checkpoints):
    rng = random.Random(7)
    text = lambda k: " ".join(rng.choice(VOCAB) for _ in range(k))
    plan = {
        "title": "Simulated serial", "logline": "x", "tone": "noir", "setting": "tower",
        "acts": [{"name": str(i + 1), "start": i * total // 4 + 1, "end": (i + 1) * total // 4, "purpose": "p"} for i in range(4)],
        "turning_points": [{"episode": e, "event": "turn"} for e in range(20, total + 1, 20)],
        "characters": [{"name": c, "role": "r", "description": "d", "arc": "a"} for c in CAST],
        "threads": [{"question": f"mystery {i}", "opens": i * 10, "resolves": i * 10 + 60} for i in range(1, 13)], "ending": "e",
    }
    state = {"plan": plan, "beats": [{"beat": f"{rng.choice(CAST)} faces {text(12)}", "hook": text(6)} for _ in range(total)],
             "episodes": {}, "chapters": {}, "directives": [{"id": f"D{i}", "text": text(20), "from": i * 10, "active": True} for i in range(1, 6)]}
    stop = frozenset("a an the and of to in".split())
    out_tokens = s.est_writer_tokens[1]
    print(f"Simulated {total}-episode story, no AI calls. Cost uses WRITER_MODEL={s.writer_model} price and {out_tokens} output tokens per draft.\n")
    print(HEADER)
    for n in range(1, total + 1):
        if n in checkpoints:
            for c in range((max(1, n - s.recent_window) - 1) // s.chapter_size):
                state["chapters"].setdefault(str(c), text(s.chapter_words))
            start = time.perf_counter()
            bible = memory.fold(state, n, s)
            ctx = memory.context(state, n, s, bible, stop)
            ms = (time.perf_counter() - start) * 1000
            tokens = len(ctx) // 4
            print(row(n, tokens, bible, ms, price(s.writer_model, tokens, out_tokens)))
        cast = rng.sample(CAST, 3) + ([f"Visitor Number{n}"] if n % 4 == 0 else [])
        state["episodes"][str(n)] = {
            "n": n, "summary": text(40), "hook": text(10), "time": f"Day {n}",
            "characters": [{"name": c, "status": "alive", "note": text(15)} for c in cast],
            "facts": [{"text": text(12), "tags": [rng.choice(CAST).lower(), rng.choice(["world", "tower", "vault"])]} for _ in range(3)],
            "opened": {f"{n}.1": text(6)} if n % 3 == 0 else {}, "advanced": [], "resolved": [f"{n - 30}.1"] if n % 9 == 0 else [],
        }
    last = checkpoints[-1]
    print(f"\nWithout this memory design, episode {last} would need all {last - 1} earlier episodes in the prompt: about {(last - 1) * 550 * 4 // 3:,} tokens.")


def real(s, story_id):
    story = Story.find(s.stories_dir, story_id)
    st = story.state
    if not st["episodes"]:
        raise SystemExit("This story has no approved episodes yet.")
    draft_tokens, cost = {}, defaultdict(float)
    for e in story.tracer.read():
        if e["event"] == "llm":
            cost[e["ep"]] += e["cost"]
            if e["step"] == "draft" and e["ok"]:
                draft_tokens[e["ep"]] = e["prompt_tokens"]
    stop_path = story.root / "stopwords.txt"
    stop = frozenset(stop_path.read_text(encoding="utf-8").split()) if stop_path.exists() else frozenset()
    print(f"Real story {st['id']}: {len(st['episodes'])} approved episodes. Prompt tokens are the Writer's real input from trace.jsonl.\n")
    print(HEADER)
    for n in sorted(map(int, st["episodes"])):
        start = time.perf_counter()
        bible = memory.fold(st, n, s)
        memory.context(st, n, s, bible, stop, story.root)
        ms = (time.perf_counter() - start) * 1000
        print(row(n, draft_tokens.get(n, 0), bible, ms, cost[n]))
    print(f"\nPlanning cost: ${cost[0]:.4f}. Total: ${sum(cost.values()):.4f}")


def main():
    ap = argparse.ArgumentParser(description="Show how memory and prompt size grow with the story. Read-only.")
    ap.add_argument("--story", help="story id from stories/ to show real numbers per episode")
    ap.add_argument("--episodes", type=int, default=200, help="length of the simulated story")
    a = ap.parse_args()
    s = Settings()
    if a.story:
        real(s, a.story)
    else:
        marks = [n for n in (5, 15, 50, 100, 150, 200, 300, 500, 1000) if n <= a.episodes]
        marks = marks if a.episodes in marks else marks + [a.episodes]
        simulated(s, a.episodes, marks)


if __name__ == "__main__":
    main()
