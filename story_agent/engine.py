import json
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from . import memory
from .agents import Runner
from .models import ArcPlan, BeatList, FillerWords, Impact, Review, Steer

TITLE = re.compile(r"^#+\s*(episode\s*\d+\s*[:\-–—.]\s*)?", re.I)


def note_block(note):
    return f"Human note: {note}" if note else ""


def split(text, n):
    lines = text.strip().splitlines()
    i = next((k for k, line in enumerate(lines) if line.startswith("#")), None)
    if i is None:
        title, body = f"Episode {n}", text.strip()
    else:
        title = TITLE.sub("", lines[i]).strip() or f"Episode {n}"
        body = "\n".join(lines[i + 1 :]).strip()
    return title, body, f"# Episode {n}: {title}\n\n{body}\n"


class CapReached(Exception):
    pass


class Engine:
    def __init__(self, story, settings):
        self.story, self.s, self.st = story, settings, story.state
        prior = defaultdict(float)
        for e in story.tracer.read():
            if e["event"] == "llm":
                prior[e["ep"]] += e["cost"]
        self.prior_cost = sum(prior.values())
        self.runner = Runner(settings, story.tracer, prior, story.root / "calls")
        self.run, self.log = self.runner.run, story.tracer.log
        self.stop_path = story.root / "stopwords.txt"
        self._stop = None

    @property
    def stop(self):
        if self._stop is None:
            if not self.stop_path.exists():
                self.stopwords()
            self._stop = frozenset(w.strip().lower() for w in self.stop_path.read_text(encoding="utf-8").splitlines() if w.strip())
        return self._stop

    def stopwords(self, plan=None):
        plan = plan or self.st["plan"]
        inputs = {"premise": self.st["premise"], "title": plan["title"], "logline": plan["logline"], "count": self.s.stopword_count}
        for _ in range(self.s.llm_retries + 1):
            out, _ = self.run("editor", "stopwords", inputs, FillerWords)
            found = list(dict.fromkeys(w.strip().lower() for w in out.words if w.strip() and " " not in w.strip()))
            if len(found) >= self.s.stopword_min:
                break
            self.log("retry", step="stopwords", reason=f"only {len(found)} words")
        self.stop_path.write_text("\n".join(found) + "\n", encoding="utf-8")
        self.log("decision", step="stopwords", language=out.language, words=len(found))
        self._stop = None

    def story_cost(self):
        return self.prior_cost + self.runner.spent

    def episode_cost(self, n):
        return self.runner.by_ep[n]

    def save(self):
        self.story.save()

    def plan(self, note=""):
        st, total = self.st, self.st["total"]
        start, before = time.perf_counter(), self.story_cost()
        arc, _ = self.run("architect", "arc", {
            "premise": st["premise"], "total": total, "note": note_block(note), "acts": self.s.arc_acts,
            "turning_points": self.s.arc_turning_points, "characters": self.s.arc_characters, "threads": self.s.arc_threads,
        }, ArcPlan)
        plan = self.clean_arc(arc.model_dump(), total)
        text = memory.render_arc(plan)
        ranges = [(a, min(a + self.s.beats_chunk - 1, total)) for a in range(1, total + 1, self.s.beats_chunk)]
        beats = {}
        with ThreadPoolExecutor(1) as side:
            fillers = side.submit(self.stopwords, plan)
            for _ in range(2):
                todo = [r for r in ranges if any(e not in beats for e in range(r[0], r[1] + 1))]
                if not todo:
                    break
                with ThreadPoolExecutor(min(self.s.plan_workers, len(todo))) as pool:
                    for chunk in pool.map(lambda r: self.beats(text, r[0], r[1], note), todo):
                        beats.update(chunk)
            fillers.result()
        missing = [e for e in range(1, total + 1) if e not in beats]
        if missing:
            raise RuntimeError(f"Planner left episodes without beats: {missing[:10]}")
        st.update(plan=plan, beats=[beats[e] for e in range(1, total + 1)], plan_approved=False)
        self.log("decision", step="plan", acts=len(plan["acts"]), characters=len(plan["characters"]), threads=len(plan["threads"]))
        self.log("step", name="plan", seconds=round(time.perf_counter() - start, 2), cost=round(self.story_cost() - before, 6))
        self.save_plan()

    def beats(self, arc_text, start, end, note):
        out, _ = self.run("architect", "beats", {"arc": arc_text, "start": start, "end": end, "note": note_block(note)}, BeatList)
        return {b.episode: {"beat": b.beat, "hook": b.hook} for b in out.beats if start <= b.episode <= end}

    @staticmethod
    def clean_arc(plan, total):
        acts = sorted(plan["acts"], key=lambda a: a["start"])
        for i, a in enumerate(acts):
            a["start"] = 1 if i == 0 else acts[i - 1]["end"] + 1
            a["end"] = total if i == len(acts) - 1 else max(a["start"], min(a["end"], total - (len(acts) - i - 1)))
        clamp = lambda x: min(max(int(x), 1), total)
        for t in plan["turning_points"]:
            t["episode"] = clamp(t["episode"])
        for t in plan["threads"]:
            t["opens"], t["resolves"] = clamp(t["opens"]), clamp(max(t["resolves"], t["opens"]))
        plan["acts"] = acts
        return plan

    def save_plan(self):
        st = self.st
        lines = [f"# {st['plan']['title']}\n", f"Premise: {st['premise']}\n", memory.render_arc(st["plan"]), "\n## Episode beats\n"]
        lines += [f"{i}. {b['beat']} *Hook:* {b['hook']}" + (f" _(revised by {b['revised_by']})_" if b.get("revised_by") else "") for i, b in enumerate(st["beats"], 1)]
        self.story.write_file("plan.md", "\n".join(lines) + "\n")
        self.story.write_file("plan.json", json.dumps({"plan": st["plan"], "beats": st["beats"]}, indent=1, ensure_ascii=False))
        self.save()

    def load_plan_edit(self):
        data = json.loads((self.story.root / "plan.json").read_text(encoding="utf-8"))
        plan = self.clean_arc(ArcPlan.model_validate(data["plan"]).model_dump(), self.st["total"])
        beats = data["beats"]
        if len(beats) != self.st["total"] or not all(b.get("beat") and b.get("hook") for b in beats):
            raise ValueError(f"plan.json must keep exactly {self.st['total']} beats, each with 'beat' and 'hook'")
        self.st.update(plan=plan, beats=beats)
        self.log("human", action="plan_edit")
        self.save_plan()

    def approve_plan(self):
        self.st["plan_approved"] = True
        self.log("human", action="plan_approve")
        self.save()

    def inputs(self, n, note=""):
        s, st = self.s, self.st
        bible = memory.fold(st, n, s)
        beat = st["beats"][n - 1]
        self.repeated = memory.overused(self.story.root, n, s, self.stop)
        base = {
            "n": n, "total": st["total"], "title": st["plan"]["title"],
            "context": memory.context(st, n, s, bible, self.stop, self.story.root, note, self.repeated),
            "target": f"{s.target_min_words} to {s.target_max_words}", "min_words": s.min_words, "max_words": s.max_words,
            "similar": memory.similar(st, n, f"{beat['beat']} {beat['hook']}", s.similar_episodes, self.stop),
        }
        return bible, base

    def write(self, n, note="", force=False):
        s, st = self.s, self.st
        if self.story_cost() + s.episode_cost_cap > s.story_cost_cap:
            raise SystemExit(
                f"Story cost cap: ${self.story_cost():.2f} spent, one more episode may cost up to ${s.episode_cost_cap:.2f} "
                f"and the cap is ${s.story_cost_cap:.2f}. Raise STORY_COST_CAP_USD to continue."
            )
        used = self.episode_cost(n)
        if used >= s.episode_cost_cap and not force:
            raise CapReached(f"Episode {n} has already used ${used:.3f} of its ${s.episode_cost_cap:.2f} cap.")
        if force:
            self.log("human", action="cap_override", ep=n, episode_cost=round(used, 6))
        self.ensure_chapters(n)
        bible, base = self.inputs(n, note)
        spent, start, text, issues = 0.0, time.perf_counter(), "", []
        for attempt in range(s.max_revisions + 1):
            if attempt == 0:
                text, c = self.run("writer", "draft", base, ep=n, attempt=attempt)
            else:
                text, c = self.run("writer", "revise", {**base, "draft": text, "issues": "\n".join(f"- {i}" for i in issues)}, ep=n, attempt=attempt)
            spent += c
            title, body, text = split(text, n)
            draft_file = self.story.keep(n, f"attempt{attempt + 1}_{'draft' if attempt == 0 else 'revise'}{'_with_note' if note else ''}.md", text)
            count, hard, soft = memory.local_flags(body, bible, s, self.repeated)
            review, c = self.run("editor", "review", {**base, "draft": text, "flags": "; ".join(hard + soft) or "none"}, Review, ep=n, attempt=attempt)
            spent += c
            repeats, score = memory.repetition(st, n, review.summary, review.hook, s, self.stop)
            issues = [*hard, *review.issues, *repeats]
            if review.hook_score < s.hook_min:
                issues.append(f"The final hook scored {review.hook_score}/10. End on a sharper, concrete turn.")
            if not review.directives_followed and not any("direction" in i.lower() for i in issues):
                issues.append("A human direction is not followed.")
            verdict = "pass" if not issues else "revise"
            if issues and attempt == s.max_revisions:
                verdict = "max_revisions"
            elif issues and not force and self.episode_cost(n) + spent / (attempt + 1) > s.episode_cost_cap:
                verdict = "cost_cap"
            self.log("decision", step="episode", ep=n, attempt=attempt, verdict=verdict, issues=issues, words=count, draft_file=draft_file,
                     hook_score=review.hook_score, repeat_score=score, cost=round(spent, 6), episode_cost=round(self.episode_cost(n), 6))
            if verdict != "revise":
                break
        draft = {
            "n": n, "title": title, "text": text, "words": count, "issues": issues, "ok": not issues,
            "review": {**review.model_dump(), "final_issues": issues}, "attempts": attempt + 1,
            "cost": spent, "latency": round(time.perf_counter() - start, 2), "repeat_score": score, "note": note, "file": draft_file,
        }
        st["pending"] = draft
        self.log("step", name="episode", ep=n, attempts=attempt + 1, verdict=verdict, seconds=draft["latency"],
                 cost=round(spent, 6), episode_cost=round(self.episode_cost(n), 6))
        self.save()
        return draft

    def extract(self, n, text):
        bible, base = self.inputs(n)
        _, body, _ = split(text, n)
        _, hard, soft = memory.local_flags(body, bible, self.s, self.repeated)
        review, cost = self.run("editor", "review", {**base, "draft": text, "flags": "; ".join(hard + soft) or "none"}, Review, ep=n, attempt=-1)
        return {**review.model_dump(), "final_issues": hard + review.issues}, cost

    def approve(self, draft, text=None):
        n, st = draft["n"], self.st
        review, cost, human = draft["review"], draft["cost"], "approved"
        if text is not None and text.strip() != draft["text"].strip():
            self.story.keep(n, "human_edit.md", text)
            review, c = self.extract(n, text)
            cost, human = cost + c, "edited"
        if str(n) in st["episodes"]:
            self.archive(n, "replaced")
        title, body, text = split(text or draft["text"], n)
        score = memory.repetition(st, n, review["summary"], review["hook"], self.s, self.stop)[1]
        st["episodes"][str(n)] = memory.record(n, title, len(body.split()), review, draft["attempts"], cost, draft["latency"], human, score)
        self.story.episode_path(n).write_text(text, encoding="utf-8")
        st["pending"] = None
        self.invalidate(n)
        self.log("human", action=human, ep=n)
        self.save()
        self.story.write_file("bible.json", json.dumps(memory.fold(st, self.story.next_episode(), self.s), indent=1, ensure_ascii=False))

    def reject(self, draft, reason):
        self.st["feedback"].append({"id": f"F{len(self.st['feedback']) + 1}", "ep": draft["n"], "kind": "reject", "text": reason, "directive": None, "changed_beats": []})
        self.log("human", action="reject", ep=draft["n"], reason=reason, rejected_file=draft.get("file"))
        self.save()

    def steer(self, n, feedback, kind="feedback"):
        st, s = self.st, self.s
        bible = memory.fold(st, n, s)
        upcoming = st["beats"][n - 1 : n - 1 + s.steer_horizon]
        out, _ = self.run("showrunner", "steer", {
            "title": st["plan"]["title"], "n": n, "total": st["total"], "feedback": feedback,
            "directives": "\n".join(f"- {d['id']}: {d['text']}" for d in st["directives"] if d["active"]) or "- none",
            "characters": "\n".join(f"- {k} [{c['status']}] ({c['role']}): {c['note']}" for k, c in bible["characters"].items()),
            "beats": "\n".join(f"- ep {n + i}: {b['beat']} (hook: {b['hook']})" for i, b in enumerate(upcoming)),
        }, Steer, ep=n)
        fid, did = f"F{len(st['feedback']) + 1}", None
        if out.permanent:
            did = f"D{len(st['directives']) + 1}"
            st["directives"].append({"id": did, "text": out.directive, "from": n, "active": True, "source": fid})
        for d in st["directives"]:
            if d["id"] in out.supersedes and d["id"] != did:
                d["active"] = False
        changed = []
        for b in out.beats:
            if n <= b.episode <= st["total"] and str(b.episode) not in st["episodes"]:
                st["beats"][b.episode - 1] = {"beat": b.beat, "hook": b.hook, "revised_by": fid}
                changed.append(b.episode)
        entry = {"id": fid, "ep": n, "kind": kind, "text": feedback, "directive": did, "directive_text": out.directive,
                 "permanent": out.permanent, "superseded": out.supersedes, "changed_beats": sorted(changed)}
        st["feedback"].append(entry)
        self.log("human", action=kind, ep=n, feedback=feedback, directive=did, changed_beats=entry["changed_beats"])
        self.save_plan()
        return entry

    def ensure_chapters(self, n):
        st, size = self.st, self.s.chapter_size
        done = (max(1, n - self.s.recent_window) - 1) // size
        for c in range(done):
            eps = [st["episodes"].get(str(k)) for k in range(c * size + 1, (c + 1) * size + 1)]
            if str(c) in st["chapters"] or not all(eps):
                continue
            text, _ = self.run("editor", "chapter", {
                "title": st["plan"]["title"], "start": c * size + 1, "end": (c + 1) * size, "words": self.s.chapter_words,
                "summaries": "\n".join(f"Ep {e['n']}: {e['summary']}" for e in eps),
            }, ep=n)
            st["chapters"][str(c)] = text
            self.save()

    def invalidate(self, n):
        self.st["chapters"].pop(str((n - 1) // self.s.chapter_size), None)

    def archive(self, n, label, text=None):
        old = self.st["episodes"][str(n)]
        path = self.story.episode_path(n)
        self.story.keep(n, f"{label}_text.md", text if text is not None else path.read_text(encoding="utf-8"))
        self.story.keep(n, f"{label}_memory_note.json", json.dumps(old, indent=1, ensure_ascii=False))

    def retro_edit(self, n, before):
        st = self.st
        old = st["episodes"][str(n)]
        self.archive(n, "before_retro_edit", before)
        edited = self.story.episode_path(n).read_text(encoding="utf-8")
        self.story.keep(n, "retro_edit.md", edited)
        review, cost = self.extract(n, edited)
        title, body, text = split(edited, n)
        st["episodes"][str(n)] = memory.record(n, title, len(body.split()), review, old["attempts"], old["cost"] + cost, old["latency"], "retro-edited", old["repeat_score"])
        self.story.episode_path(n).write_text(text, encoding="utf-8")
        self.invalidate(n)
        st["pending"] = None
        new = st["episodes"][str(n)]
        later = sorted(int(k) for k in st["episodes"] if int(k) > n)
        change, conflicts = f"Episode {n} now reads: {new['summary']}", []
        if later:
            imp, _ = self.run("editor", "impact", {
                "title": st["plan"]["title"], "n": n, "old": old["summary"], "new": new["summary"],
                "facts": "; ".join(f["text"] for f in new["facts"]) or "none",
                "later": "\n".join(f"{k}: {st['episodes'][str(k)]['summary']}" for k in later),
            }, Impact, ep=n)
            change = imp.change
            for c in imp.conflicts:
                if c.episode in later:
                    st["episodes"][str(c.episode)]["stale"] = c.reason
                    conflicts.append({"episode": c.episode, "reason": c.reason})
        self.log("human", action="retro_edit", ep=n, change=change, conflicts=conflicts)
        self.save()
        nxt = self.story.next_episode()
        steer = self.steer(nxt, f"Episode {n} was rewritten by a human: {change} Keep upcoming episodes consistent with it.", "retro_edit") if nxt <= st["total"] else None
        return change, conflicts, steer

    def stale(self):
        return sorted(int(k) for k, e in self.st["episodes"].items() if e.get("stale"))
