# Run report

What happened when I ran the system for real, what I found, and what I couldn't test.

## How I tested it

I didn't have a paid API key, so I used Gemma, a free model running on our own server. All four agents used it. The code also works with paid models such as Claude.

Before the real run, `tests/test_offline.py` ran the whole system with a fake model. It covers every path, including retries, cost caps and editing an old episode.

## The run

Premise, from the brief: *"A delivery rider realizes every address on today's route belongs to someone who died in the same building."* The output is in `stories/demo/`.

| | |
|---|---|
| Plan | 200 beats, 4 acts, built in 218 seconds |
| Episodes | 15 approved, all 400-700 words |
| Hooks | 8.9 out of 10 on average |
| Review | 20 reviews, all passed first time |
| Repeated plots | None |
| Memory at the end | 13 characters, 2 dead, 27 open threads |
| Model calls | 55; one server timeout, fixed by the automatic retry |
| Time | About 44 seconds per episode |
| Writer's prompt | 1,069 tokens at episode 1, 4,323 at episode 15 |

## What I did as the human

| Episode | What I did | What it changed |
|---|---|---|
| Plan | Approved | Writing started |
| 1 | Note: "make it more dramatic" | Rule D1; beats 1-15 rewritten |
| 5 | Rejected: "need some action, like a good movie" | Only that episode was rewritten |
| 7 | Note: "more interesting with new characters" | Rule D2; beats 7-21 rewritten |
| 8 | Note: "add new scenarios in the story" | Rule D3; beats 8-22 rewritten, and episodes 8-15 were written on them |

The rules stayed in every later prompt, and the Editor reported them as followed. I stopped and resumed several times, and it always continued from the right place.

## Problems I found and fixed

1. Two characters with the same first word, like "Detective Miller" and "Detective Rao", were treated as one person. Fixed.
2. The dead Aris Thorne and the hero Elias Thorne share a surname, so every "Thorne" looked like the dead man was back. Fixed.
3. The prompt was growing too fast because the list of open threads had no limit. It now shows the 20 most urgent, which cut episode 16 from about 4,500 tokens to 3,500.
4. My API key was written into the log. It's now hidden, and the old line was cleaned.
5. The writing repeated itself. "It wasn't just X, it was Y" appears in 13 of the 15 episodes, and the Editor still passed everything, because it is the same model. I added rules against it and a check for phrases used too often lately. This came after episode 15, so the demo episodes don't show it yet.

Not fixed yet: Gemma made 5 characters where 6 to 12 were asked for, and no thread has been resolved.

## Editing an old episode

I tried this on a copy, so the demo is untouched. In episode 6, I made Sloane, Elias's ally, secretly work for the building.

The old version was saved. Memory was updated, so Sloane counts as an enemy from episode 6 on. The system found that episode 10, where Sloane rescues Elias, no longer fits, and it re-planned beats 16-30. When I resumed, it rewrote episode 10. The new phrase check sent that rewrite back twice for repeated phrasing before handing it to me.

The limit: only episode 10 was caught, because the check reads episode summaries, not the full text. A short scene with Sloane in another episode could be missed.

## Cost

The cost tracking is built: tokens times price for every call, a cap per episode and per story, and a "yes" needed to go past a cap. But it never ran with real money. Gemma is free, so every cost is $0 and the caps never triggered. They were tested only with the fake model.

What the run does show is the volume. One episode uses about 12,800 tokens, so 200 episodes need about 2.6 million tokens and 2.5 hours. A paid model would cost those tokens times its price.

## Not tested yet

- A paid model, and so real costs and caps
- A different model for each agent
- Editing the plan file on the real story
- Reviewing only every few episodes (`REVIEW_EVERY`) on the real story
- The new phrase rules over several new episodes
- Anything past episode 16; episode 150 was only simulated
