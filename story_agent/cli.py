import argparse
import json
import os
import shlex
import subprocess
import textwrap
from dataclasses import asdict

from . import memory
from .agents import Runner
from .config import Settings, rates
from .engine import CapReached, Engine
from .models import Check
from .report import estimate, evaluate
from .store import Story, Tracer

LINE = "-" * 78


def ask(prompt, options):
    while True:
        choice = input(f"\n{prompt} ").strip().lower()[:1]
        if choice in options:
            return choice
        print(f"Choose one of: {', '.join(options)}")


def open_in_editor(path):
    editor = os.getenv("EDITOR")
    if editor:
        subprocess.call([*shlex.split(editor), str(path)])
    else:
        input(f"Edit {path} in any editor, save it, then press Enter here... ")


def show_plan(st, beats=12):
    p = st["plan"]
    print(f"\n{LINE}\n{p['title']}\n{p['logline']}\nTone: {p['tone']} | Setting: {p['setting']}\n{LINE}")
    for a in p["acts"]:
        print(f"Act {a['name']} (eps {a['start']}-{a['end']}): {a['purpose']}")
    print("\nTurning points:")
    for t in p["turning_points"]:
        print(f"  ep {t['episode']:>3}: {t['event']}")
    print("\nCharacters:")
    for c in p["characters"]:
        print(f"  {c['name']} ({c['role']}): {c['arc']}")
    print("\nThreads:")
    for t in p["threads"]:
        print(f"  {t['question']} (ep {t['opens']} -> {t['resolves']})")
    print(f"\nEnding: {p['ending']}\n\nFirst {beats} beats:")
    for i, b in enumerate(st["beats"][:beats], 1):
        print(f"  {i:>3}. {b['beat']}")


def review_plan(eng):
    st = eng.st
    while not st["plan_approved"]:
        show_plan(st)
        print(f"\nFull plan with all {st['total']} beats: {eng.story.root / 'plan.md'}")
        c = ask("[a]pprove  [e]dit plan.json  [f]eedback and re-plan  [q]uit >", "aefq")
        if c == "a":
            eng.approve_plan()
        elif c == "e":
            open_in_editor(eng.story.root / "plan.json")
            try:
                eng.load_plan_edit()
            except (ValueError, json.JSONDecodeError) as e:
                print(f"Edit rejected: {e}")
        elif c == "f":
            note = input("What should change in the plan? ").strip()
            eng.log("human", action="plan_feedback", feedback=note)
            print("Re-planning...")
            eng.plan(note)
        else:
            return False
    return True


def generate(eng, n, note=""):
    try:
        return eng.write(n, note=note)
    except CapReached as e:
        print(f"\n{e}")
        if ask("Spend more on this episode anyway? [y/n] >", "yn") == "y":
            return eng.write(n, note=note, force=True)
        return None


def box(title, blocks, width=78):
    inner = width - 2
    out = ["┌─ " + title + " " + "─" * max(0, inner - len(title) - 3) + "┐"]
    for i, rows in enumerate(blocks):
        if i:
            out.append("├" + "─" * inner + "┤")
        cols = max(len(r) for r in rows)
        widths = [max(len(r[c]) if c < len(r) else 0 for r in rows) for c in range(cols)]
        widths[-1] += max(0, inner - sum(widths) - 3 * (cols - 1) - 2)
        out += ["│ " + " │ ".join((r[c] if c < len(r) else "").ljust(widths[c]) for c in range(cols)) + " │" for r in rows]
    out.append("└" + "─" * inner + "┘")
    return "\n".join(out)


def show_draft(d, eng):
    n, s = d["n"], eng.s
    print(f"\n{LINE}\n{d['text']}\n{LINE}")
    print(textwrap.fill(f"Beat: {eng.st['beats'][n - 1]['beat']}", 78, subsequent_indent="      "))
    head = [[f"{d['words']} words", f"hook {d['review']['hook_score']}/10", f"{d['attempts']} attempt(s)", f"{d['latency']:.0f}s", f"${d['cost']:.4f}"]]
    money = [["Episode cost", f"${eng.episode_cost(n):.4f} of ${s.episode_cost_cap:.2f} cap"],
             ["Story cost", f"${eng.story_cost():.2f} of ${s.story_cost_cap:.2f} cap"]]
    if s.show_stats:
        memory_rows, progress = stats_blocks(eng, d)
        blocks = [head, memory_rows, progress + money]
    else:
        blocks = [head, money]
    print(box(f"Episode {n}", blocks))
    if d["issues"]:
        print("Editor still flags:\n" + "\n".join(textwrap.fill(i, 76, initial_indent="  - ", subsequent_indent="    ") for i in d["issues"]))


def stats_blocks(eng, d):
    st, n, r = eng.st, d["n"], d["review"]
    bible = memory.fold(st, n, eng.s)
    chars = bible["characters"]
    dead = sum(c["status"] == "dead" for c in chars.values())
    new = len({memory.resolve(c["name"], chars) for c in r["characters"]} - set(chars))
    open_threads = sum(t["status"] == "open" for t in bible["threads"].values())
    rules = [x["id"] for x in st["directives"] if x["active"]]
    calls = [e for e in eng.story.tracer.read() if e["event"] == "llm"]
    drafts = {e["ep"]: e["prompt_tokens"] for e in calls if e["step"] in ("draft", "revise") and e["ok"]}
    first = drafts[min(drafts)] if drafts else 0
    tokens = sum(e["prompt_tokens"] + e["completion_tokens"] for e in calls)
    minutes = sum(e["latency"] for e in calls) / 60
    memory_rows = [
        ["Memory", f"Before episode {n}", "This episode adds"],
        ["Characters", f"{len(chars)} ({dead} dead)", f"{new} new"],
        ["Facts", str(len(bible["facts"])), f"+{len(r['facts'])}"],
        ["Open mysteries", str(open_threads), f"+{len(r['threads_opened'])} new, {len(r['threads_resolved'])} resolved"],
        ["Active rules", ", ".join(rules) or "none", ""],
    ]
    progress = [
        ["Writer prompt", f"{drafts.get(n, 0):,} tokens (episode {min(drafts) if drafts else n}: {first:,})"],
        ["Story so far", f"{len(st['episodes'])} approved, {tokens:,} tokens, {minutes:.1f} min AI time"],
    ]
    for f in st["feedback"]:
        if f.get("directive") in rules and f["changed_beats"]:
            b = f["changed_beats"]
            span = f"{b[0]}-{b[-1]}" if len(b) > 1 and b == list(range(b[0], b[-1] + 1)) else ", ".join(map(str, b))
            done = sum(str(x) in st["episodes"] for x in b)
            label = f"Episode edit {f['id']}" if f["kind"] == "retro_edit" else f"Feedback {f['id']}"
            progress.append([label, f"{f['directive']} changed beats {span}, {done} written"])
    return [memory_rows, progress]


def handle(eng, d, auto):
    while True:
        show_draft(d, eng)
        if auto and d["ok"]:
            eng.approve(d)
            print(f"Episode {d['n']} passed every check and was approved automatically.")
            return True
        c = ask("[a]pprove  [e]dit  [r]eject+rewrite  [f]eedback (carries forward)  [b]eats ahead  [q]uit >", "aerfbq")
        if c == "a":
            eng.approve(d)
            return True
        if c == "e":
            path = eng.story.root / "draft_edit.md"
            path.write_text(d["text"], encoding="utf-8")
            open_in_editor(path)
            eng.approve(d, path.read_text(encoding="utf-8"))
            path.unlink()
            return True
        if c == "r":
            reason = input("What is wrong with this episode? ").strip()
            eng.reject(d, reason)
            print("Rewriting...")
            d = generate(eng, d["n"], reason) or d
        elif c == "f":
            note = input("Feedback for the story going forward: ").strip()
            entry = eng.steer(d["n"], note)
            print(f"Directive {entry['directive'] or '(this episode only)'}: {entry['directive_text']}")
            print(f"Rewrote beats: {entry['changed_beats'] or 'none'}")
            if ask("Rewrite this episode with the feedback now? [y/n] >", "yn") == "y":
                d = generate(eng, d["n"], note) or d
        elif c == "b":
            n = d["n"]
            for i, b in enumerate(eng.st["beats"][n : n + 10], n + 1):
                print(f"  {i:>3}. {b['beat']}" + (f" ({b['revised_by']})" if b.get("revised_by") else ""))
        else:
            return False


def write_loop(eng, until, auto):
    for k in eng.stale():
        reason = eng.st["episodes"][str(k)]["stale"]
        print(f"\nEpisode {k} conflicts with edited canon: {reason}")
        if ask("Regenerate it now? [y/n] >", "yn") == "y":
            d = generate(eng, k, f"Must match the revised canon: {reason}")
            if not d or not handle(eng, d, False):
                return
    s = eng.s
    last = min(until or s.run_until or eng.st["total"], eng.st["total"])
    auto = auto or s.auto_approve
    every = max(1, s.review_every)
    if auto or every > 1:
        print(f"Review mode: {'only episodes that fail a check' if auto else f'every {every} episodes, plus any episode that fails a check'}.")
    while (n := eng.story.next_episode()) <= last:
        pending = eng.st.get("pending")
        if pending and pending["n"] == n:
            d = pending
        else:
            print(f"\nWriting episode {n}/{eng.st['total']}...")
            d = generate(eng, n)
            if not d:
                print(f"Saved. Resume with: python main.py resume {eng.st['id']}")
                return
        if not handle(eng, d, auto or n % every != 0):
            print(f"Saved. Resume with: python main.py resume {eng.st['id']}")
            return
    print(f"\nStopped after episode {eng.story.next_episode() - 1}. Resume with: python main.py resume {eng.st['id']}")


def main():
    s = Settings()
    s.stories_dir.mkdir(parents=True, exist_ok=True)
    ap = argparse.ArgumentParser(prog="main.py", description="Agentic serial story writer (CrewAI) with human in the loop")
    sub = ap.add_subparsers(dest="cmd", required=True)
    new = sub.add_parser("new", help="start a story from a one-line premise")
    new.add_argument("premise")
    new.add_argument("--episodes", type=int, default=s.total_episodes)
    for name in ("resume", "report", "status"):
        p = sub.add_parser(name)
        p.add_argument("story", nargs="?")
    for p in (new, sub.choices["resume"]):
        p.add_argument("--until", type=int, help="stop after this episode")
        p.add_argument("--auto", action="store_true", help="auto-approve episodes that pass every check")
    ed = sub.add_parser("edit", help="retroactively edit an approved episode")
    ed.add_argument("story")
    ed.add_argument("episode", type=int)
    sub.add_parser("list")
    sub.add_parser("estimate")
    sub.add_parser("check", help="one tiny real call per configured model to verify key, model names and structured output")
    a = ap.parse_args()
    unpriced = sorted({m for m in (s.planner_model, s.writer_model, s.editor_model) if not rates(m)})
    if unpriced and a.cmd in ("new", "resume", "edit", "estimate", "check"):
        raise SystemExit(f"No price for {unpriced}. Add them to MODEL_PRICES in .env so cost tracking and cost caps work.")

    if a.cmd == "check":
        runner = Runner(s, Tracer(s.stories_dir / "check.jsonl"), calls_dir=s.stories_dir / "check_calls")
        failed = False
        for role, model in (("architect", s.planner_model), ("writer", s.writer_model), ("editor", s.editor_model)):
            try:
                out, cost = runner.run(role, "check", {}, Check, model=model)
                print(f"OK    {role:<10} {model}  ok={out.ok} word={out.word!r}  ${cost:.5f}")
            except RuntimeError as e:
                failed = True
                print(f"FAIL  {role:<10} {model}  {e}")
        print(f"Details: {s.stories_dir / 'check.jsonl'}")
        raise SystemExit(1 if failed else 0)
    if a.cmd == "estimate":
        print(json.dumps(estimate(s), indent=1))
        return
    if a.cmd == "list":
        for p in sorted(s.stories_dir.glob("*/state.json")):
            st = Story(p.parent).state
            print(f"{st['id']}  eps={len(st['episodes'])}/{st['total']}  plan_approved={st['plan_approved']}")
        return
    story = Story.create(s.stories_dir, a.premise, a.episodes) if a.cmd == "new" else Story.find(s.stories_dir, a.story)
    eng = Engine(story, s)
    eng.log("session", command=a.cmd, args={k: v for k, v in vars(a).items() if k != "cmd"},
            settings={k: ("***" if v else "") if "key" in k else str(v) if not isinstance(v, (int, float, str, tuple)) else v for k, v in asdict(s).items()})
    if a.cmd == "report":
        print(json.dumps(evaluate(story, s), indent=1))
        print(f"\nWritten: {story.root / 'evaluation.md'}, evaluation.json, story.md, plan.md")
        return
    if a.cmd == "status":
        st = story.state
        print(f"{st['id']}\nnext episode {story.next_episode()} of {st['total']} | cost ${eng.story_cost():.3f} | stale {eng.stale()}")
        print("Active directions:\n" + "\n".join(f"  {d['id']}: {d['text']}" for d in st["directives"] if d["active"]))
        return
    if a.cmd == "edit":
        path = story.episode_path(a.episode)
        if str(a.episode) not in story.state["episodes"]:
            raise SystemExit(f"Episode {a.episode} is not approved yet, so there is nothing to edit.")
        before = path.read_text(encoding="utf-8")
        open_in_editor(path)
        if path.read_text(encoding="utf-8").strip() == before.strip():
            raise SystemExit("No changes saved, so nothing was updated.")
        change, conflicts, steer = eng.retro_edit(a.episode, before)
        print(f"Change: {change}\nConflicting later episodes: {conflicts or 'none'}")
        if steer:
            print(f"Upcoming beats rewritten: {steer['changed_beats'] or 'none'}")
        print(f"Run `python main.py resume {story.state['id']}` to regenerate conflicting episodes and continue.")
        return
    print(f"Story: {story.state['id']}")
    if not story.state["plan"]:
        print(f"Planning {story.state['total']} episodes...")
        eng.plan()
    if review_plan(eng):
        write_loop(eng, a.until, a.auto)
