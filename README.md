# Agentic Serial Story Writer

You give it one line, and it plans a 200-episode serial and writes it one episode at a time. Each episode is 400-700 words and ends on a hook. A person stays in charge the whole way: they approve or change the plan, then approve, edit, reject or give notes on episodes, and those notes change the episodes that follow.

It is built with CrewAI and runs in the terminal. It works with any OpenAI-compatible model server, and the demo in `stories/demo/` was written by Gemma, a free open-weight model running on our own server. It also works with hosted providers such as Anthropic.

The design reasoning, including what breaks first, is in [DECISIONS.md](DECISIONS.md). What happened in the real test run, and what was and wasn't tested, is in [RUN_REPORT.md](RUN_REPORT.md).

## How it works

Four agents share the work. The Architect plans the arc and a beat for each of the 200 episodes. The Writer drafts an episode. The Continuity Editor checks it against what has happened so far and records a short memory note: who appeared, what changed, what is still open. The Showrunner turns a human note into a standing rule and rewrites the upcoming beats to match.

A plain Python loop runs them in order and decides when to stop:

```
premise → plan the arc and 200 beats → you approve or change the plan
  for each episode:
    rebuild memory from earlier notes → Writer drafts → free code checks → Editor review
    problems found? the Writer fixes them, at most twice and within the cost cap
    you approve, edit, reject, or give a note that carries forward
    the episode's memory note is saved; every 10 episodes are summarised into one paragraph
```

The Writer never rereads old episodes. It gets its place in the plan, your rules, recent summaries, the end of the last episode, the characters in this scene, the most relevant facts, the most urgent open threads, and a list of phrases it has been overusing lately. That keeps the prompt small at episode 150.

## Setup

You need Python 3.10 or newer.

```bash
cd TH_AGENT
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python tests/test_offline.py
```

The last command runs the whole system with a fake model, so no key is needed. It should end with `OK`.

Then create a file called `.env` in the project folder. For your own model server, take the address from its curl example and drop the `/chat/completions` part:

```
LLM_BASE_URL=http://your-server:port/v1
LLM_API_KEY=none
PLANNER_MODEL=openai/gemma
WRITER_MODEL=openai/gemma
EDITOR_MODEL=openai/gemma
MODEL_PRICES={"gemma": [0, 0]}
PLAN_WORKERS=2
```

`openai/` only means the server speaks the OpenAI chat format, and `gemma` is the model name it expects. Put a real key in `LLM_API_KEY` if your server needs one; it cannot be left empty.

For Anthropic instead, write `ANTHROPIC_API_KEY=...` and leave the model lines out, since the defaults are Claude models with their prices built in.

Check the connection with one tiny call per role:

```bash
python main.py check
```

If you move the project folder, delete `.venv` and set it up again, because a virtual environment remembers its folder path.

## Writing a story

```bash
python main.py new "A delivery rider realizes every address on today's route belongs to someone who died in the same building."
```

It plans the whole serial and shows you the plan. `plan.md` in the story folder has all 200 beats. Press `a` to approve, `e` to edit the plan file, or `f` to have it re-planned with a note.

Then it writes episode 1 and stops for you. Below the episode is a small table with its word count, hook score, how much the memory grew, the size of the Writer's prompt, how far your notes have reached, and the cost. Your choices:

- `a` approve it
- `e` edit it yourself; your version becomes canon
- `r` reject it with a reason; it is rewritten once with that reason
- `f` give a note for the rest of the story, such as "slow down the romance"; it becomes a rule for every later episode and rewrites the next 15 beats
- `b` look at the beats coming up
- `q` stop for now

Everything is saved after every step, so you can stop at any point and pick up again with:

```bash
python main.py resume
```

A few settings in `.env` control the pace. `REVIEW_EVERY=4` stops for you only every fourth episode, and approves the passing ones in between. An episode that fails a check always stops. `AUTO_APPROVE=true` stops only on failures, and `RUN_UNTIL=15` stops after episode 15. The command line versions are `--auto` and `--until 15`.

To rewrite an episode that is already approved:

```bash
python main.py edit <story-id> 40
```

The old version is kept, memory is rebuilt, later episodes that now contradict it are marked, and the next `resume` offers to rewrite them.

Other commands:

```bash
python main.py report            # writes evaluation.md, evaluation.json and story.md
python main.py status            # where the story is, its rules, its cost
python main.py list              # all stories
python main.py estimate          # rough cost and time for 200 episodes before running
python tests/test_scale.py       # how memory and prompt size grow over 200 simulated episodes
python tests/test_scale.py --story <id>   # the same table for a real story
```

## What a story folder contains

Each story gets its own folder in `stories/`.

| File | What it holds |
|---|---|
| `plan.md`, `plan.json` | The arc and all 200 beats. Beats changed by a note are marked with that note's number |
| `episodes/` | The approved episodes |
| `story.md`, `evaluation.md`, `evaluation.json` | All episodes in one file, and the report (written by `report`) |
| `state.json` | Everything needed to continue: plan, beats, each episode's memory note, rules, notes, any waiting draft |
| `bible.json` | What the system currently remembers: characters, facts, open threads, story time |
| `history/` | Every version of every episode, including rejected drafts and the version before an edit. The model never sees these |
| `calls/` | The exact prompt and raw answer of every model call |
| `trace.jsonl` | One line per event: model calls with tokens and time, retries, review results, and every human action |
| `stopwords.txt` | Filler words of the story's language, written by the model and used by the repetition check |

The story content is all written by the model during the run. The code only decides the structure and saves what comes back.

## The demo

`stories/demo/` is a real run on the example premise from the brief, written by Gemma. It has the full 200-episode plan and 15 approved episodes. Along the way I gave three notes and one rejection: "make it more dramatic" at episode 1, "need to make story line more interesting with new characters" at episode 7, and "add new scenarios in the story" at episode 8. Each note became a rule, and `evaluation.md` shows which later episodes were written on the beats it changed.

## Settings

Everything the system uses is a setting with a default in `story_agent/config.py`. A `.env` line overrides it, and anything left out keeps its default. The ones you are most likely to touch:

| Setting | Default | What it changes |
|---|---|---|
| `PLANNER_MODEL`, `WRITER_MODEL`, `EDITOR_MODEL` | Claude models | Which model each role uses |
| `LLM_BASE_URL`, `LLM_API_KEY` | empty | Your own model server |
| `MODEL_PRICES` | Claude prices | Price per million tokens, in and out; every model in use must be listed |
| `REVIEW_EVERY`, `AUTO_APPROVE`, `RUN_UNTIL`, `SHOW_STATS` | 1, false, 0, true | How often it stops for you, and the table under each episode |
| `MIN_WORDS`, `MAX_WORDS` | 400, 700 | Episode length |
| `MAX_REVISIONS`, `HOOK_MIN_SCORE` | 2, 7 | How hard the automatic review pushes |
| `EPISODE_COST_CAP_USD`, `STORY_COST_CAP_USD` | 0.30, 40 | Spending limits |
| `STEER_HORIZON` | 15 | How many upcoming beats one note can rewrite |
| `THREAD_LIMIT`, `FACT_LIMIT` | 20, 30 | How many open threads and facts go into the prompt |
| `PLAN_WORKERS`, `LLM_TIMEOUT_SECONDS`, `LLM_RETRIES` | 8, 300, 3 | Useful for a slow or busy server |

Memory sizes, repetition and style thresholds, and the numbers behind `estimate` are in `config.py`, all adjustable the same way.

## Cost and time

On the demo, planning took 218 seconds. Each episode took about 44 seconds of model time and about 12,800 tokens, so all 200 would take about 2.6 million tokens and 2.5 hours, plus the time spent reviewing. Gemma ran on our own server, so the money cost was zero. With a paid model, put its price in `MODEL_PRICES` and the report turns the same token counts into money; that path is built and tested with a fake model but has not been run against a paid API, as RUN_REPORT.md explains. Planning splits the 200 beats into parallel calls, the review and the memory note share one call, and the length, repetition and style checks are plain code that costs nothing.

## Project layout

```
TH_AGENT/
├── main.py                  starts the program
├── requirements.txt
├── story_agent/
│   ├── config/agents.yaml   the four agents
│   ├── config/tasks.yaml    every prompt
│   ├── agents.py            every model call: CrewAI agent and task, retries, cost, saved call
│   ├── engine.py            planning, the write and review loop, notes, rewrites of old episodes
│   ├── memory.py            rebuilding memory, building the Writer's prompt, the free checks
│   ├── models.py            the shapes the model has to answer in
│   ├── store.py             saving, the log, episode history
│   ├── report.py            the report and the estimate
│   └── cli.py               the terminal screens and commands
├── tests/
│   ├── test_offline.py      the whole system with a fake model
│   └── test_scale.py        memory and prompt growth table
├── stories/demo/            the demo run
├── DECISIONS.md
├── RUN_REPORT.md
└── README.md
```

## Known limits

The short version is in DECISIONS.md. When one model both writes and reviews, the review is too kind: every demo episode passed first time while the endings repeated the same phrasing, so I added a check for phrases overused across recent episodes. Memory mistakes are trusted once made, open threads pile up, nicknames slip past the name lookup, and a rewrite of an old episode is checked one level deep.
