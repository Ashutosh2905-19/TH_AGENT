import json
import os
import random
import re
import sys

os.environ.update(
    PLANNER_MODEL="anthropic/claude-opus-5-5", WRITER_MODEL="anthropic/claude-opus-5-5", EDITOR_MODEL="anthropic/claude-haiku-4-5",
    MODEL_PRICES='{"claude-opus-5-5": [4, 20], "claude-haiku-4-5": [1, 5]}', LLM_BASE_URL="", LLM_API_KEY="", SAVE_CALLS="true",
)
import tempfile
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crewai.llms.base_llm import BaseLLM

import story_agent.agents as agents
from story_agent.agents import Runner
from story_agent.config import Settings
from story_agent.engine import CapReached, Engine
from story_agent.models import Check
from story_agent.report import evaluate
from story_agent.store import Story, Tracer

VOCAB = "lantern ledger stairwell parcel ember gauze violet rust tunnel oath locket radio harbor ash cipher".split()
FLAWS = {2: "fixable", 6: "stubborn", 8: "stubborn"}
FAIL = {"Compress these summaries": [429], "Connection check": [401]}


class APIError(Exception):
    def __init__(self, status_code):
        super().__init__(f"fake HTTP {status_code}")
        self.status_code = status_code


def words(seed, k):
    rng = random.Random(seed)
    return " ".join(rng.choice(VOCAB) + str(rng.randint(0, 999)) for _ in range(k))


def episode(n, flawed):
    body = " ".join(words(n * 31 + i, 1) for i in range(520))
    return f"# Episode {n}: Floor {n}\n\n{body}{f' FLAWED-{n}' if flawed else ''}\n\nThe lift opened on a floor that did not exist."


class FakeLLM(BaseLLM):
    def call(self, messages, tools=None, callbacks=None, available_functions=None, from_task=None, from_agent=None, response_model=None):
        text = messages if isinstance(messages, str) else "\n".join(str(m.get("content", "")) for m in messages)
        self._track_token_usage_internal({"prompt_tokens": len(text) // 4, "completion_tokens": 300})
        for marker, codes in FAIL.items():
            if marker in text and codes:
                raise APIError(codes.pop(0))
        num = lambda pat: int(re.search(pat, text).group(1))
        if "Connection check" in text:
            out = {"ok": True, "word": "ready"}
        elif "List 100 to 150 filler words" in text:
            out = {"language": "English", "words": "a an the and or but of to in on at by for with from is are was were be it this that he she they his her their him you we not no so then there who what when where how all".split()}
        elif "Design the full arc" in text:
            out = {
                "title": "Last Drop", "logline": "A rider delivers to the dead.", "tone": "eerie", "setting": "Mumbai tower",
                "acts": [{"name": "I", "start": 1, "end": 10, "purpose": "route"}, {"name": "II", "start": 11, "end": 30, "purpose": "truth"}],
                "turning_points": [{"episode": 8, "event": "the list is his own"}],
                "characters": [{"name": "Ravi Kumar", "role": "rider", "description": "tired", "arc": "fear to courage"},
                               {"name": "Meera", "role": "caretaker", "description": "sharp", "arc": "secret to truth"}],
                "threads": [{"question": "Who sends the parcels?", "opens": 1, "resolves": 25}],
                "ending": "He signs for his own parcel.",
            }
        elif "Write the beat for every" in text:
            a, b = num(r"from (\d+) to"), num(r"to (\d+) inclusive")
            out = {"beats": [{"episode": e, "beat": f"Ravi meets Meera {words(e, 6)}", "hook": f"hook {words(e + 99, 3)}"} for e in range(a, b + 1)]}
        elif "A human gave this note" in text:
            n = num(r"is at episode (\d+)")
            out = {"directive": "Keep Meera distant; no romance until act II.", "permanent": True, "supersedes": [],
                   "beats": [{"episode": n + 1, "beat": "Meera refuses Ravi's help and leaves.", "hook": "Her door is sealed."}]}
        elif "A human rewrote episode" in text:
            out = {"change": "Meera now knows the sender.", "conflicts": [{"episode": 5, "reason": "Meera claims ignorance of the sender"}]}
        elif "continuity editor" in text:
            n = num(r"continuity editor for episode (\d+)")
            out = {"issues": [f"Contradiction marked FLAWED-{n}"] if f"FLAWED-{n}" in text else [], "hook_score": 8,
                   "directives_followed": True, "summary": f"Ravi {words(n * 7, 12)}", "hook": f"door {words(n * 13, 4)}", "time": f"Day {n}",
                   "characters": [{"name": "Ravi", "status": "alive", "note": f"shaken after ep {n}"},
                                  {"name": "Meera", "status": "dead" if n == 12 else "alive", "note": "watching"}],
                   "facts": [{"text": f"Flat {n} belonged to a victim", "tags": ["ravi kumar", "world" if n % 5 == 0 else "tower"]}],
                   "threads_opened": [f"who lived in flat {n}?"] if n % 3 == 0 else [], "threads_advanced": ["P1"], "threads_resolved": []}
        elif "Compress these summaries" in text:
            return "Chapter compressed: " + words(len(text), 20)
        elif "Fix these problems" in text:
            n = num(r"revising episode (\d+) of")
            return episode(n, FLAWS.get(n) == "stubborn")
        else:
            n = num(r"episode (\d+) of")
            return episode(n, n in FLAWS)
        return json.dumps(out)


def main():
    agents.LLM = lambda model, **kw: FakeLLM(model=model)
    tmp = Path(tempfile.mkdtemp())
    s = Settings(stories_dir=tmp, total_episodes=30, max_revisions=1, retry_backoff=0.01)
    story = Story.create(tmp, "A delivery rider realizes every address belongs to someone who died in the same building.", 30)
    eng = Engine(story, s)
    eng.plan()
    assert len(story.state["beats"]) == 30
    assert "the" in eng.stop and len(eng.stop) >= s.stopword_min, "LLM-generated stopwords loaded"
    eng.approve_plan()
    for n in range(1, 19):
        if n == 8:
            eng.s = replace(s, episode_cost_cap=0.0001)
        d = eng.write(n)
        if n == 2:
            assert d["attempts"] == 2 and d["ok"], "fixable issue is revised away"
        if n == 6:
            assert d["attempts"] == 2 and not d["ok"], "stops at max revisions"
        if n == 8:
            assert d["attempts"] == 1 and not d["ok"], "projected cost stops revisions"
            try:
                eng.write(n)
                raise AssertionError("episode cap must block another rewrite")
            except CapReached:
                d = eng.write(n, force=True)
            eng.s = s
        if n == 3:
            entry = eng.steer(n, "slow down the romance")
            assert entry["directive"] == "D1" and entry["changed_beats"] == [4]
        if n == 5:
            eng.reject(d, "too much exposition")
            d = eng.write(n, note="too much exposition")
        eng.approve(d, d["text"] + "\nEdited line." if n == 7 else None)
    story = Story.find(tmp)
    eng = Engine(story, s)
    assert story.next_episode() == 19, "resume point"
    assert eng.episode_cost(5) > 0 and abs(eng.story_cost() - sum(eng.runner.by_ep.values())) < 1e-9, "costs survive resume"
    assert "0" in story.state["chapters"], "chapter summary built after a retried 429"
    assert "D1" in eng.inputs(19)[1]["context"], "directive carries forward"
    assert "DEAD" in eng.inputs(19)[1]["context"], "death is canon"
    path4 = story.episode_path(4)
    before = path4.read_text()
    path4.write_text(before + "\nMeera admits she knows the sender.")
    change, conflicts, steer = eng.retro_edit(4, before)
    assert conflicts[0]["episode"] == 5 and eng.stale() == [5]
    eng.approve(eng.write(5, note="match canon", force=True))
    assert eng.stale() == []

    v = story.versions
    assert v(2)[:2] == ["01_attempt1_draft.md", "02_attempt2_revise.md"], v(2)
    assert "FLAWED-2" in (story.root / "history/ep_002/01_attempt1_draft.md").read_text(), "rejected first draft kept"
    assert any(x.endswith("human_edit.md") for x in v(7)), "human edit kept"
    assert any(x.endswith("before_retro_edit_text.md") for x in v(4)) and any(x.endswith("before_retro_edit_memory_note.json") for x in v(4))
    assert (story.root / "history/ep_004" / next(x for x in v(4) if x.endswith("before_retro_edit_text.md"))).read_text() == before, "old version kept exactly"
    assert any(x.endswith("replaced_text.md") for x in v(5)), "regenerated episode keeps the version it replaced"
    assert sum("_draft" in x for x in v(5)) >= 3 and any(x.endswith("_draft_with_note.md") for x in v(5)), "rejected and stale drafts of ep 5 kept"
    seen = eng.inputs(3)[1]["context"] + eng.inputs(5)[1]["context"]
    assert "FLAWED-2" not in seen, "old drafts never reach the AI"
    assert "Meera admits she knows the sender." in eng.inputs(5)[1]["context"], "AI sees only the latest version"

    trace = story.tracer.read()
    retry = next(e for e in trace if e["event"] == "retry")
    failed_call = next(e for e in trace if e["event"] == "llm" and not e["ok"])
    assert retry["status"] == 429 and failed_call["cost"] > 0, "rate limit retried and its tokens charged"
    llm_events = [e for e in trace if e["event"] == "llm"]
    assert all((story.root / e["call_file"]).exists() for e in llm_events), "every call saved"
    failed_record = json.loads((story.root / failed_call["call_file"]).read_text())
    assert failed_record["error"] and "Compress these summaries" in failed_record["task_prompt"], "failed call keeps its prompt and error"
    ok_record = json.loads((story.root / next(e for e in llm_events if e["step"] == "draft")["call_file"]).read_text())
    assert ok_record["messages_sent"][0]["role"] == "system" and ok_record["raw_output"].startswith("# Episode"), "full conversation and answer saved"
    assert any(e["event"] == "step" and e["name"] == "plan" for e in trace), "plan wall time logged"

    check_trace = tmp / "check.jsonl"
    try:
        Runner(s, Tracer(check_trace)).run("editor", "check", {}, Check)
        raise AssertionError("401 must fail")
    except RuntimeError:
        pass
    assert sum(json.loads(line)["event"] == "llm" for line in check_trace.read_text().splitlines()) == 1, "401 is not retried"
    out, _ = Runner(s, Tracer(check_trace)).run("editor", "check", {}, Check)
    assert out.ok and out.word == "ready"

    summary = evaluate(story, s)
    assert summary["episodes_written"] == 18 and summary["human_actions"]["edited"] == 1
    assert {"pass", "revise", "max_revisions", "cost_cap"} <= set(summary["review_verdicts"]), summary["review_verdicts"]
    assert summary["cap_overrides"] == 2 and summary["retries"] >= 1 and summary["failed_calls"] >= 1
    print(json.dumps(summary, indent=1))
    print(f"OK: offline flow passed ({tmp})")


if __name__ == "__main__":
    main()
