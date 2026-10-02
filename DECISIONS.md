# Design decisions

Four CrewAI agents do the work: the Architect plans, the Writer drafts, the Continuity Editor reviews and records what changed, and the Showrunner turns human notes into rules. A small Python loop decides the order, retries and cost caps, so stopping and spending stay predictable. The demo runs every role on Gemma, a free open-weight model on our own server.

## How does it remember the story at episode 150?

It never rereads old episodes. Each approved episode leaves a short memory note: who appeared and their status, new facts tagged with names and places, threads opened or closed, and the story time. Memory is rebuilt from these notes as of any episode, in four layers: the arc plan, rolling summaries, a character and fact store, and open-thread tracking.

Always in context at episode 150: its place in the arc plan (act, next turning point, this beat and the next two), every active human rule, the last five episode summaries and the last 160 words of episode 149. Summarized: episodes 1 to 144 arrive as one paragraph per ten. Retrieved: full notes for the characters in this scene and one line for everyone else, the dead always marked; about 30 facts matched by name and place; the 20 most urgent open threads, flagged when stale or overdue.

The real prompt grew from 1,069 tokens to 4,323 by episode 15, because the thread list had no limit; capping it brought episode 16 to about 3,500. Scaling the `tests/test_scale.py` simulation by real note sizes gives about 10,000 tokens at episode 150, against roughly 110,000 for rereading everything.

## Where does the human step in, and why there?

At the plan, because one edit there reshapes 200 episodes. Then after each episode, once the automatic checks have run, so my time goes on direction and taste. I don't step in mid-draft or between rewrites, because the checks fix mechanical problems faster than I would. `REVIEW_EVERY` can space reviews out; a failing episode always stops.

A note like "make it more dramatic" becomes a standing rule: it goes into every later prompt, the Editor fails any draft that breaks it, and up to 15 upcoming beats are rewritten. In the demo, three notes became rules D1 to D3, and episodes 8 to 15 were written on beats D3 changed.

A person can also rewrite an approved episode, say episode 40. The old version is archived, a new note is extracted and memory is rebuilt, so facts from the old version disappear. The Editor marks later episodes that now contradict it, the upcoming beats are re-planned, and resume offers to rewrite each marked episode, which sees memory only up to its own position.

## How do you catch inconsistency or repetition before a human has to?

Free code checks run first: word count, a dead character being mentioned, overlap of the new summary with every earlier one and of the hook with the last ten, and phrases overused in the last five episodes. The Editor then checks the draft against the retrieved facts and the five most similar episodes for contradictions, impossible knowledge, timeline slips, broken rules, a missed beat or a weak hook. Problems go back to the Writer as a precise list, at most twice, and the dead stay dead. The real run exposed two name-matching bugs of mine, both fixed.

## What breaks first as the story grows, and how would you fix it?

The reviewer. All 15 episodes passed first time and 14 scored 9 out of 10, yet the endings keep using the same "it wasn't just X, it was Y" move. One model writing and reviewing is mostly grading itself. I added a check for phrases overused in recent episodes; the real fix is a different, stronger Editor model.

Next, a wrong fact in memory is trusted forever, so a periodic re-check of old notes would catch drift. Threads pile up (27 open, none closed), so payoffs should be forced. Later, nicknames slip past the name lookup, which per-character alias lists would fix, and a rewrite of episode 40 is checked only one level deep, so the conflict check should repeat until nothing new is flagged.

## Cost and time

The model is free and self-hosted, so the money cost is zero; time and tokens are the real cost. Planning took 218 seconds, and an episode averages 44 seconds and about 12,800 tokens, so 200 episodes need about 2.6 million tokens and 2.5 hours, plus review. Every call is logged, an episode stops after two revisions or at its cost cap, and a paid model's price in `MODEL_PRICES` turns the logs into money.
