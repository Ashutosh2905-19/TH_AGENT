import re
from collections import Counter

WORD = re.compile(r"[^\s.,;:!?\"'“”‘’()\[\]{}<>…—–/|*#_।॥-]+")


def words(text, stop):
    return [w for w in WORD.findall(text.lower()) if w not in stop]


def bigrams(text, stop):
    w = words(text, stop)
    return set(zip(w, w[1:]))


def jaccard(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


def name_keys(name, min_part):
    low = name.lower()
    return {low, *(p for p in WORD.findall(low) if len(p) >= min_part)}


def distinct_keys(names, min_part):
    parts = {n: name_keys(n, min_part) for n in names}
    shared = Counter(k for ks in parts.values() for k in ks)
    return {n: {k for k in ks if shared[k] == 1 or k == n.lower()} for n, ks in parts.items()}


def mentions(keys, text_low):
    return any(re.search(rf"(?<!\w){re.escape(k)}(?!\w)", text_low) for k in keys)


def resolve(name, known):
    if name in known:
        return name
    mine = set(WORD.findall(name.lower()))
    for k in known:
        theirs = set(WORD.findall(k.lower()))
        if mine and theirs and (mine <= theirs or theirs <= mine):
            return k
    return name


def fold(state, upto, s):
    chars = {
        c["name"]: {"role": c["role"], "status": "alive", "note": c["description"], "arc": c["arc"], "last": 0}
        for c in state["plan"]["characters"]
    }
    threads = {
        f"P{i}": {"text": t["question"], "opened": t["opens"], "due": t["resolves"], "status": "planned", "last": 0}
        for i, t in enumerate(state["plan"]["threads"], 1)
    }
    facts, index, clock = [], {}, ""
    for n in range(1, upto):
        e = state["episodes"].get(str(n))
        if not e:
            continue
        for c in e["characters"]:
            key = resolve(c["name"], chars)
            cur = chars.setdefault(key, {"role": c.get("role", ""), "status": "alive", "note": "", "arc": "", "last": n})
            status = "dead" if cur["status"] == "dead" else c["status"]
            cur.update(status=status, note=c["note"] or cur["note"], last=n)
            cur["role"] = cur["role"] or c.get("role", "")
        for f in e["facts"]:
            facts.append({"text": f["text"], "ep": n})
            for tag in f["tags"]:
                for k in name_keys(tag, s.min_name_part):
                    index.setdefault(k, []).append(len(facts) - 1)
        for tid, text in e["opened"].items():
            threads[tid] = {"text": text, "opened": n, "due": None, "status": "open", "last": n}
        for tid in e["advanced"]:
            if tid in threads and threads[tid]["status"] != "resolved":
                threads[tid].update(status="open", last=n)
        for tid in e["resolved"]:
            if tid in threads:
                threads[tid].update(status="resolved", last=n)
        clock = e.get("time") or clock
    return {"characters": chars, "facts": facts, "index": index, "threads": threads, "time": clock}


def render_arc(plan):
    acts = "\n".join(f"- Act {a['name']} (eps {a['start']}-{a['end']}): {a['purpose']}" for a in plan["acts"])
    turns = "\n".join(f"- Ep {t['episode']}: {t['event']}" for t in plan["turning_points"])
    cast = "\n".join(f"- {c['name']} ({c['role']}): {c['description']} Arc: {c['arc']}" for c in plan["characters"])
    threads = "\n".join(f"- {t['question']} (opens ep {t['opens']}, pays off ep {t['resolves']})" for t in plan["threads"])
    return (
        f"Title: {plan['title']}\nLogline: {plan['logline']}\nTone: {plan['tone']}\nSetting: {plan['setting']}\n"
        f"Acts:\n{acts}\nTurning points:\n{turns}\nCharacters:\n{cast}\nThreads:\n{threads}\nEnding: {plan['ending']}"
    )


def act_for(plan, n):
    return next((a for a in plan["acts"] if a["start"] <= n <= a["end"]), plan["acts"][-1])


def story_so_far(state, n, size, window):
    cutoff = max(1, n - window)
    done = (cutoff - 1) // size
    parts = []
    for c in range(done):
        text = state["chapters"].get(str(c))
        if text:
            parts.append(f"Eps {c * size + 1}-{(c + 1) * size}: {text}")
        else:
            parts += [f"Ep {k}: {state['episodes'][str(k)]['summary']}" for k in range(c * size + 1, (c + 1) * size + 1) if str(k) in state["episodes"]]
    parts += [f"Ep {k}: {state['episodes'][str(k)]['summary']}" for k in range(done * size + 1, cutoff) if str(k) in state["episodes"]]
    return "\n".join(parts) or "This is the opening of the serial."


def context(state, n, s, bible, stop, story_dir=None, note="", repeated=()):
    plan, beats, eps = state["plan"], state["beats"], state["episodes"]
    beat = beats[n - 1]
    act = act_for(plan, n)
    turn = next((t for t in sorted(plan["turning_points"], key=lambda t: t["episode"]) if t["episode"] >= n), None)
    recent = [eps[str(k)] for k in range(max(1, n - s.recent_window), n) if str(k) in eps]
    chars, threads = bible["characters"], bible["threads"]

    focus_text = f"{beat['beat']} {beat['hook']} " + " ".join(b["beat"] for b in beats[n : n + s.lookahead_beats])
    low = focus_text.lower()
    keys_of = distinct_keys(chars, s.min_name_part)
    active = {k for k in chars if mentions(keys_of[k], low)}
    active |= {resolve(c["name"], chars) for e in recent[-s.active_recent:] for c in e["characters"]}

    cast = []
    for name, c in sorted(chars.items(), key=lambda kv: -kv[1]["last"]):
        tag = "DEAD" if c["status"] == "dead" else c["status"]
        if name in active or c["status"] == "dead":
            cast.append(f"- {name} [{tag}] ({c['role']}): {c['note']} (last seen ep {c['last'] or '-'})")
        else:
            cast.append(f"- {name} [{tag}] ({c['role']}), last seen ep {c['last'] or '-'}")
    dormant = [k for k, c in chars.items() if c["status"] == "alive" and c["last"] and n - c["last"] > s.dormant_after]

    keys = set().union(*(keys_of[k] for k in active)) | set(words(focus_text, stop))
    hits = sorted({i for k in keys for i in bible["index"].get(k, ())}, reverse=True)[: s.fact_limit]
    chosen = sorted(set(hits) | set(bible["index"].get("world", [])[-s.world_facts:]))
    facts = [f"- (ep {bible['facts'][i]['ep']}) {bible['facts'][i]['text']}" for i in chosen]

    open_threads = []
    for tid, t in threads.items():
        if t["status"] == "resolved" or (t["status"] == "planned" and t["opened"] > n + s.lookahead_beats):
            continue
        flags = []
        if t["due"]:
            flags.append(f"planned payoff ep {t['due']}")
        if t["status"] == "planned":
            flags.append(f"planned to open ep {t['opened']}")
        elif n - t["last"] > s.stale_after:
            flags.append(f"STALE since ep {t['last']}")
        if t["due"] and t["due"] < n:
            flags.append("OVERDUE")
        overdue, due_soon, stale = t["due"] and t["due"] < n, t["due"] and t["due"] <= n + s.stale_after, "STALE" in "".join(flags)
        rank = (not overdue, not due_soon, not stale, -t["last"])
        open_threads.append((rank, f"- [{tid}] {t['text']} ({', '.join(flags) or 'open'})"))
    open_threads.sort()
    hidden = len(open_threads) - s.thread_limit
    open_threads = [line for _, line in open_threads[: s.thread_limit]] + ([f"- ({hidden} older open threads not shown)"] if hidden > 0 else [])

    directives = [f"- {d['id']}: {d['text']} (since ep {d['from']})" for d in state["directives"] if d["active"]]
    last_scene = ""
    if story_dir and n > 1:
        path = story_dir / "episodes" / f"ep_{n - 1:03d}.md"
        if path.exists():
            last_scene = " ".join(path.read_text(encoding="utf-8").split()[-s.last_scene_words:])

    sections = [
        f"SERIES: {plan['title']} | {plan['logline']} | Tone: {plan['tone']} | Setting: {plan['setting']}",
        f"ARC POSITION: Act {act['name']} (eps {act['start']}-{act['end']}): {act['purpose']}"
        + (f"\nNext turning point: ep {turn['episode']}: {turn['event']}" if turn else ""),
        f"THIS EPISODE'S BEAT: {beat['beat']}\nPLANNED HOOK: {beat['hook']}",
        "LATER BEATS (set up, do not reach): " + " | ".join(f"ep {n + i + 1}: {b['beat']}" for i, b in enumerate(beats[n : n + s.lookahead_beats])),
        "HUMAN DIRECTIONS (must follow):\n" + ("\n".join(directives) or "- none"),
        "STORY SO FAR:\n" + story_so_far(state, n, s.chapter_size, s.recent_window),
        "RECENT EPISODES:\n" + ("\n".join(f"- Ep {e['n']}: {e['summary']}" for e in recent) or "- none"),
        "LAST SCENE ENDED WITH:\n" + (last_scene or "(first episode)"),
        f"IN-STORY TIME NOW: {bible['time'] or 'start of the story'}",
        "CHARACTERS:\n" + "\n".join(cast),
        "CANON FACTS:\n" + ("\n".join(facts) or "- none yet"),
        "OPEN THREADS:\n" + ("\n".join(open_threads) or "- none"),
        "RECENT HOOKS (vary the type and the sentence shape):\n" + ("\n".join(f"- {e['hook']}" for e in recent) or "- none"),
        "OVERUSED PHRASES IN RECENT EPISODES (do not reuse):\n" + ("\n".join(f"- {p}" for p in repeated) or "- none"),
    ]
    if dormant:
        sections.append("DORMANT CHARACTERS (bring back when natural): " + ", ".join(dormant[: s.dormant_shown]))
    if note:
        sections.append(f"HUMAN NOTE FOR THIS EPISODE: {note}")
    return "\n\n".join(sections)


def similar(state, n, text, k, stop):
    probe = set(words(text, stop))
    scored = []
    for key, e in state["episodes"].items():
        if int(key) < n:
            scored.append((jaccard(probe, set(words(e["summary"], stop))), int(key), e["summary"]))
    scored.sort(reverse=True)
    return "\n".join(f"- Ep {m}: {summ}" for _, m, summ in scored[:k]) or "- none"


def repetition(state, n, summary, hook, s, stop):
    sig, hook_words = bigrams(summary, stop), set(words(hook, stop))
    issues = []
    best = max(((jaccard(sig, bigrams(e["summary"], stop)), int(k)) for k, e in state["episodes"].items() if int(k) < n), default=(0, 0))
    if best[0] >= s.repeat_threshold:
        issues.append(f"Repeats the plot of episode {best[1]} (overlap {best[0]:.2f}): {state['episodes'][str(best[1])]['summary']}")
    for k in range(max(1, n - s.hook_window), n):
        e = state["episodes"].get(str(k))
        if e and jaccard(hook_words, set(words(e["hook"], stop))) >= s.hook_repeat:
            issues.append(f"Final hook is nearly the same as episode {k}'s hook: {e['hook']}")
            break
    return issues, round(best[0], 3)


PHRASE_WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")


def phrase_words(text):
    return [w.replace("’", "'") for w in PHRASE_WORD.findall(text.lower())]


def phrases(text, stop, size=3):
    w = phrase_words(text)
    return {" ".join(w[i : i + size]) for i in range(len(w) - size + 1) if not all(x in stop for x in w[i : i + size])}


def overused(story_dir, n, s, stop):
    seen = Counter()
    for k in range(max(1, n - s.style_window), n):
        path = story_dir / "episodes" / f"ep_{k:03d}.md"
        if path.exists():
            seen.update(phrases(path.read_text(encoding="utf-8").split("\n", 1)[-1], stop))
    return [p for p, c in seen.most_common() if c >= s.style_min_episodes][: s.style_shown]


def local_flags(body, bible, s, repeated=()):
    count = len(body.split())
    hard = [] if s.min_words <= count <= s.max_words else [f"Length is {count} words; it must be {s.min_words}-{s.max_words}."]
    text = " " + " ".join(phrase_words(body)) + " "
    reused = [p for p in repeated if f" {p} " in text]
    if len(reused) >= s.style_max_reuse:
        hard.append(f"Reuses {len(reused)} phrases overused in recent episodes ({'; '.join(reused)}). Rephrase them.")
    low = body.lower()
    keys_of = distinct_keys(bible["characters"], s.min_name_part)
    dead = [k for k, c in bible["characters"].items() if c["status"] == "dead" and mentions(keys_of[k], low)]
    soft = [f"dead character {k} is mentioned, so check it is memory or flashback only" for k in dead]
    if reused and len(reused) < s.style_max_reuse:
        soft.append(f"reuses overused phrases: {'; '.join(reused)}")
    return count, hard, soft


def record(n, title, count, review, attempts, cost, latency, human, repeat_score):
    return {
        "n": n,
        "title": title,
        "words": count,
        "summary": review["summary"],
        "hook": review["hook"],
        "time": review["time"],
        "characters": review["characters"],
        "facts": review["facts"],
        "opened": {f"{n}.{i}": t for i, t in enumerate(review["threads_opened"], 1)},
        "advanced": review["threads_advanced"],
        "resolved": review["threads_resolved"],
        "hook_score": review["hook_score"],
        "directives_followed": review["directives_followed"],
        "issues": review.get("final_issues", review["issues"]),
        "repeat_score": repeat_score,
        "attempts": attempts,
        "cost": round(cost, 5),
        "latency": round(latency, 2),
        "human": human,
        "stale": None,
    }
