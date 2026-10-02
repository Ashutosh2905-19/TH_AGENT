import json
from collections import defaultdict

from . import memory
from .config import price


def spans(nums):
    nums, out = sorted(set(nums)), []
    for n in nums:
        if out and n == out[-1][1] + 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in out)


def export_story(story):
    st = story.state
    parts = [f"# {st['plan']['title']}\n\n_{st['plan']['logline']}_\n"]
    for k in sorted(map(int, st["episodes"])):
        parts.append(story.episode_path(k).read_text(encoding="utf-8"))
    story.write_file("story.md", "\n\n---\n\n".join(parts))


def evaluate(story, s):
    st, trace = story.state, story.tracer.read()
    eps = [st["episodes"][k] for k in sorted(st["episodes"], key=int)]
    calls = [e for e in trace if e["event"] == "llm"]
    by_ep, lat_ep, retry_ep = defaultdict(float), defaultdict(float), defaultdict(int)
    for e in trace:
        if e["event"] == "retry":
            retry_ep[e.get("ep", 0)] += 1
    for c in calls:
        by_ep[c["ep"]] += c["cost"]
        lat_ep[c["ep"]] += c["latency"]
    retries = sum(e["event"] == "retry" for e in trace)
    failed = [c for c in calls if not c["ok"]]
    steps = defaultdict(lambda: {"calls": 0, "cost": 0.0, "seconds": 0.0, "tokens_in": 0, "tokens_out": 0, "failed": 0})
    for c in calls:
        b = steps[c["step"]]
        b["calls"] += 1
        b["cost"] += c["cost"]
        b["seconds"] += c["latency"]
        b["tokens_in"] += c["prompt_tokens"]
        b["tokens_out"] += c["completion_tokens"]
        b["failed"] += not c["ok"]
    by_step = {k: {**v, "cost": round(v["cost"], 4), "seconds": round(v["seconds"], 1)} for k, v in sorted(steps.items())}
    plan_steps = [e for e in trace if e["event"] == "step" and e["name"] == "plan"]
    plan_cost = sum(c["cost"] for c in calls if c["step"] in ("arc", "beats", "stopwords"))
    plan_time = plan_steps[-1]["seconds"] if plan_steps else sum(c["latency"] for c in calls if c["step"] in ("arc", "beats"))
    verdicts = defaultdict(int)
    for e in trace:
        if e["event"] == "decision" and e.get("step") == "episode":
            verdicts[e["verdict"]] += 1
    written = [e["n"] for e in eps]
    avg_cost = sum(by_ep[n] for n in written) / len(written) if written else 0
    avg_time = sum(lat_ep[n] for n in written) / len(written) if written else 0
    bible = memory.fold(st, story.next_episode(), s)
    nxt = story.next_episode()
    threads = bible["threads"].values()
    in_range = sum(s.min_words <= e["words"] <= s.max_words for e in eps)

    approved = {e["n"] for e in eps}
    hitl = []
    for f in st["feedback"]:
        after = [e for e in eps if e["n"] >= f["ep"]]
        written = [b for b in f["changed_beats"] if b in approved]
        hitl.append({**f, "written_after": [e["n"] for e in after], "episodes_on_revised_beats": written,
                     "broken_in": [e["n"] for e in after if not e["directives_followed"]],
                     "directive_followed_after": all(e["directives_followed"] for e in after) if after else None})
    retro = []
    for e in trace:
        if e["event"] == "human" and e.get("action") == "retro_edit" and "conflicts" in e:
            replan = next((f for f in st["feedback"] if f["kind"] == "retro_edit" and f["text"].startswith(f"Episode {e['ep']} was rewritten")), None)
            conflicts = [{**c, "fixed": not st["episodes"].get(str(c["episode"]), {}).get("stale")} for c in e["conflicts"]]
            retro.append({"episode": e["ep"], "change": e["change"], "conflicts": conflicts,
                          "replanned_beats": replan["changed_beats"] if replan else [], "rule": replan.get("directive") if replan else None})

    summary = {
        "story": st["id"], "premise": st["premise"], "episodes_written": len(eps), "total_planned": st["total"],
        "plan_approved": st["plan_approved"], "cost_total": round(sum(c["cost"] for c in calls), 4),
        "tokens_in": sum(c["prompt_tokens"] for c in calls), "tokens_out": sum(c["completion_tokens"] for c in calls),
        "llm_calls": len(calls), "retries": retries, "failed_calls": len(failed), "failed_calls_cost": round(sum(c["cost"] for c in failed), 4),
        "unrecovered_failures": sum(e["event"] == "failed" for e in trace), "review_verdicts": dict(verdicts),
        "cap_overrides": sum(e["event"] == "human" and e.get("action") == "cap_override" for e in trace),
        "plan_cost": round(plan_cost, 4), "plan_wall_seconds": round(plan_time, 1),
        "avg_cost_per_episode": round(avg_cost, 4), "avg_seconds_per_episode": round(avg_time, 1),
        "projected_cost_all": round(plan_cost + avg_cost * st["total"], 2),
        "projected_hours_all": round((plan_time + avg_time * st["total"]) / 3600, 2),
        "words_in_range": f"{in_range}/{len(eps)}",
        "avg_hook_score": round(sum(e["hook_score"] for e in eps) / len(eps), 2) if eps else None,
        "first_pass_rate": f"{sum(e['attempts'] == 1 for e in eps)}/{len(eps)}",
        "episodes_with_open_issues": [e["n"] for e in eps if e["issues"]],
        "max_repeat_score": max((e["repeat_score"] for e in eps), default=0),
        "human_actions": {a: sum(e["human"] == a for e in eps) for a in ("approved", "edited", "retro-edited")},
        "characters": len(bible["characters"]), "dead": [k for k, c in bible["characters"].items() if c["status"] == "dead"],
        "open_threads": sum(t["status"] == "open" for t in threads), "resolved_threads": sum(t["status"] == "resolved" for t in threads),
        "overdue_threads": [t["text"] for t in threads if t["due"] and t["due"] < nxt and t["status"] != "resolved"],
        "stale_episodes": [e["n"] for e in eps if e.get("stale")],
        "active_directives": [d["text"] for d in st["directives"] if d["active"]],
    }
    say = {"pass": "passed", "revise": "sent back for a rewrite", "max_revisions": "handed over after the rewrite limit", "cost_cap": "stopped at the cost cap"}
    step_names = {"arc": "Arc plan", "beats": "Episode beats", "stopwords": "Filler words", "draft": "First drafts", "revise": "Rewrites",
                  "review": "Reviews", "steer": "Turning notes into rules", "chapter": "Chapter summaries", "impact": "Checking edits against later episodes", "check": "Connection check"}
    status = {"approved": "approved", "edited": "edited by you", "retro-edited": "rewritten by you later"}
    replaced = {n for n in approved if any("replaced_text" in v for v in story.versions(n))}
    cost_line = f"${summary['cost_total']:.2f} so far, about ${avg_cost:.4f} per episode" if summary["cost_total"] else "$0.00, because the model in use has no price set"
    pace = f"about {summary['projected_hours_all']:.1f} hours of model time" + (f" and ${summary['projected_cost_all']:.2f}" if summary["cost_total"] else "")
    md = [
        f"# {st['plan']['title']}: run report\n",
        f"Story `{st['id']}`, from the premise \"{st['premise'].rstrip('.')}\". The plan covers {st['total']} episodes and {len(eps)} are approved so far. "
        f"Building the plan took {plan_time:.0f} seconds.\n",
        "## How it went\n", "| | |", "|---|---|",
        f"| Episodes approved | {len(eps)} of {st['total']} |",
        f"| Within {s.min_words}-{s.max_words} words | {in_range} of {len(eps)} |",
        f"| Average hook score | {summary['avg_hook_score']:.1f} out of 10 |" if eps else "| Average hook score | none yet |",
        f"| Passed review on the first try | {sum(e['attempts'] == 1 for e in eps)} of {len(eps)} |",
        f"| Review results | {', '.join(f'{v} {say.get(k, k)}' for k, v in verdicts.items()) or 'none yet'} |",
        f"| Model calls | {len(calls)}, with {retries} retried and {summary['unrecovered_failures']} lost |",
        f"| Tokens | {summary['tokens_in']:,} in, {summary['tokens_out']:,} out |",
        f"| Model time | {avg_time:.0f} seconds per episode on average |",
        f"| Cost | {cost_line} |",
        f"| All {st['total']} episodes at this pace | {pace} |",
        "\n## Episodes\n", "| Ep | Title | Words | Hook | Tries | Status | Versions kept | Model time | Problems left |", "|---|---|---|---|---|---|---|---|---|",
        *(f"| {e['n']} | {e['title']} | {e['words']} | {e['hook_score']} | {e['attempts']} | "
          f"{'conflicts with an edit' if e.get('stale') else 'rewritten after an edit' if e['n'] in replaced else status.get(e['human'], e['human'])} | "
          f"{len(story.versions(e['n']))} | {lat_ep[e['n']]:.0f}s | {len(e['issues'])} |" for e in eps),
        "\n## Your notes and what they changed\n",
    ]
    notes = [h for h in hitl if h["kind"] != "retro_edit"]
    for h in notes:
        if h["kind"] == "reject":
            md.append(f"- **Episode {h['ep']}, rejected ({h['id']}):** \"{h['text']}\". Only that episode was rewritten, with the reason as a note.")
            continue
        line = f"- **Episode {h['ep']}, note {h['id']}:** \"{h['text']}\". "
        line += f"It became rule {h['directive']}: \"{h['directive_text']}\" " if h.get("directive") else "It applied to that episode only. "
        if h["changed_beats"]:
            line += f"It rewrote beats {spans(h['changed_beats'])}"
            line += f", and episodes {spans(h['episodes_on_revised_beats'])} have been written on them so far. " if h["episodes_on_revised_beats"] else ", and none of them are written yet. "
        if h["directive_followed_after"] is not None and h.get("directive"):
            line += "The Editor reported the rule as followed in every later episode." if not h["broken_in"] else f"The Editor reported it broken in episode {spans(h['broken_in'])}."
        md.append(line)
    md.append("" if notes else "No notes yet.")
    md.append("## Changes to approved episodes\n")
    for r in retro:
        line = f"- **Episode {r['episode']} was rewritten by hand.** What changed, in the Editor's words: {r['change']} "
        if r["conflicts"]:
            parts = [f"episode {c['episode']} ({c['reason'].rstrip('.')}), {'now rewritten to match' if c['fixed'] else 'still waiting to be rewritten'}" for c in r["conflicts"]]
            line += "Later episodes that contradicted it: " + "; ".join(parts) + ". "
        else:
            line += "No later episode contradicted it. "
        if r["replanned_beats"]:
            line += f"Upcoming beats {spans(r['replanned_beats'])} were re-planned to match" + (f" under rule {r['rule']}." if r["rule"] else ".")
        md.append(line)
    md.append("" if retro else "No approved episode has been rewritten.")
    dead = summary["dead"]
    md += [
        "## What the story remembers now\n",
        f"{summary['characters']} characters, {len(dead)} of them dead{' (' + ', '.join(dead) + ')' if dead else ''}. "
        f"{summary['open_threads']} open threads and {summary['resolved_threads']} resolved. "
        + (f"{len(summary['overdue_threads'])} threads are past their planned payoff. " if summary["overdue_threads"] else "No thread is past its planned payoff. ")
        + (f"Episodes waiting to be rewritten after an edit: {spans(summary['stale_episodes'])}." if summary["stale_episodes"] else "")
        + (f" Active rules: {', '.join(d['id'] for d in st['directives'] if d['active'])}." if st["directives"] else "") + "\n",
        "## Time and tokens by step\n", "| Step | Calls | Failed | Tokens in | Tokens out | Seconds | Cost |", "|---|---|---|---|---|---|---|",
        *(f"| {step_names.get(k, k)} | {v['calls']} | {v['failed']} | {v['tokens_in']:,} | {v['tokens_out']:,} | {v['seconds']:.0f} | ${v['cost']:.4f} |" for k, v in by_step.items()),
    ]
    story.write_file("evaluation.md", "\n".join(md) + "\n")
    story.write_file("evaluation.json", json.dumps({"summary": summary, "by_step": by_step, "episodes": eps, "hitl": hitl, "retro_edits": retro}, indent=1, ensure_ascii=False))
    export_story(story)
    return summary


def estimate(s, total=None):
    total = total or s.total_episodes
    m = lambda x: x.split("/")[-1]
    arc = price(m(s.planner_model), *s.est_arc_tokens)
    beats = -(-total // s.beats_chunk) * price(m(s.planner_model), *s.est_beats_tokens)
    write = price(m(s.writer_model), *s.est_writer_tokens)
    review = price(m(s.editor_model), *s.est_editor_tokens)
    chapter = price(m(s.editor_model), *s.est_chapter_tokens) / s.chapter_size
    per_ep = (write + review) * (1 + s.est_revise_rate) + chapter
    seconds = s.est_plan_seconds + (s.est_writer_seconds + s.est_editor_seconds) * (1 + s.est_revise_rate) * total
    return {
        "assumptions": {
            "writer_tokens_in_out": s.est_writer_tokens, "editor_tokens_in_out": s.est_editor_tokens,
            "revise_rate": s.est_revise_rate, "writer_seconds": s.est_writer_seconds, "editor_seconds": s.est_editor_seconds,
        },
        "plan_cost": round(arc + beats, 2), "per_episode_cost": round(per_ep, 4), "total_cost": round(arc + beats + per_ep * total, 2),
        "total_hours_unattended": round(seconds / 3600, 1),
    }
