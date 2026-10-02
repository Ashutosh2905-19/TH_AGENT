import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_PRICES = '{"claude-opus-5-5": [4, 20], "claude-sonnet-5-5": [2, 10], "claude-haiku-4-5": [1, 5]}'
PRICES = {k: tuple(v) for k, v in json.loads(os.getenv("MODEL_PRICES") or DEFAULT_PRICES).items()}


def env(key, default, cast=str):
    value = os.getenv(key)
    return cast(value) if value not in (None, "") else default


def flag(text):
    return str(text).strip().lower() in ("1", "true", "yes", "on")


def pair(text):
    a, b = text.split(",")
    return int(a), int(b)


@dataclass(frozen=True)
class Settings:
    planner_model: str = env("PLANNER_MODEL", "anthropic/claude-opus-5-5")
    writer_model: str = env("WRITER_MODEL", "anthropic/claude-opus-5-5")
    editor_model: str = env("EDITOR_MODEL", "anthropic/claude-haiku-4-5")
    llm_base_url: str = env("LLM_BASE_URL", "")
    llm_api_key: str = env("LLM_API_KEY", "")
    planner_max_tokens: int = env("PLANNER_MAX_TOKENS", 16000, int)
    writer_max_tokens: int = env("WRITER_MAX_TOKENS", 8000, int)
    editor_max_tokens: int = env("EDITOR_MAX_TOKENS", 6000, int)
    total_episodes: int = env("TOTAL_EPISODES", 200, int)
    review_every: int = env("REVIEW_EVERY", 1, int)
    auto_approve: bool = env("AUTO_APPROVE", False, flag)
    run_until: int = env("RUN_UNTIL", 0, int)
    show_stats: bool = env("SHOW_STATS", True, flag)
    min_words: int = env("MIN_WORDS", 400, int)
    max_words: int = env("MAX_WORDS", 700, int)
    max_revisions: int = env("MAX_REVISIONS", 2, int)
    hook_min: int = env("HOOK_MIN_SCORE", 7, int)
    episode_cost_cap: float = env("EPISODE_COST_CAP_USD", 0.30, float)
    story_cost_cap: float = env("STORY_COST_CAP_USD", 40.0, float)
    beats_chunk: int = env("BEATS_CHUNK", 25, int)
    plan_workers: int = env("PLAN_WORKERS", 8, int)
    chapter_size: int = env("CHAPTER_SIZE", 10, int)
    recent_window: int = env("RECENT_WINDOW", 5, int)
    steer_horizon: int = env("STEER_HORIZON", 15, int)
    fact_limit: int = env("FACT_LIMIT", 30, int)
    thread_limit: int = env("THREAD_LIMIT", 20, int)
    repeat_threshold: float = env("REPEAT_THRESHOLD", 0.35, float)
    target_min_words: int = env("TARGET_MIN_WORDS", 480, int)
    target_max_words: int = env("TARGET_MAX_WORDS", 620, int)
    min_name_part: int = env("MIN_NAME_PART_LETTERS", 3, int)
    stopword_count: str = env("STOPWORD_COUNT", "100 to 150")
    stopword_min: int = env("STOPWORD_MIN", 40, int)
    lookahead_beats: int = env("LOOKAHEAD_BEATS", 2, int)
    active_recent: int = env("ACTIVE_RECENT_EPISODES", 2, int)
    stale_after: int = env("STALE_THREAD_AFTER", 15, int)
    dormant_after: int = env("DORMANT_CHARACTER_AFTER", 20, int)
    dormant_shown: int = env("DORMANT_SHOWN", 4, int)
    world_facts: int = env("WORLD_FACT_LIMIT", 10, int)
    last_scene_words: int = env("LAST_SCENE_WORDS", 160, int)
    similar_episodes: int = env("SIMILAR_EPISODES", 5, int)
    style_window: int = env("STYLE_WINDOW", 5, int)
    style_min_episodes: int = env("STYLE_MIN_EPISODES", 3, int)
    style_shown: int = env("STYLE_SHOWN", 15, int)
    style_max_reuse: int = env("STYLE_MAX_REUSE", 3, int)
    hook_window: int = env("HOOK_REPEAT_WINDOW", 10, int)
    hook_repeat: float = env("HOOK_REPEAT_THRESHOLD", 0.6, float)
    chapter_words: int = env("CHAPTER_WORDS", 120, int)
    arc_acts: str = env("ARC_ACTS", "4 to 6")
    arc_turning_points: str = env("ARC_TURNING_POINTS", "8 to 14")
    arc_characters: str = env("ARC_CHARACTERS", "6 to 12")
    arc_threads: str = env("ARC_THREADS", "8 to 16")
    llm_retries: int = env("LLM_RETRIES", 3, int)
    llm_timeout: float = env("LLM_TIMEOUT_SECONDS", 300.0, float)
    save_calls: bool = env("SAVE_CALLS", True, flag)
    retry_backoff: float = env("RETRY_BACKOFF_SECONDS", 2.0, float)
    agent_max_iter: int = env("AGENT_MAX_ITER", 3, int)
    est_arc_tokens: tuple = pair(env("EST_ARC_TOKENS", "1500,5000"))
    est_beats_tokens: tuple = pair(env("EST_BEATS_TOKENS", "3500,5000"))
    est_writer_tokens: tuple = pair(env("EST_WRITER_TOKENS", "6500,2000"))
    est_editor_tokens: tuple = pair(env("EST_EDITOR_TOKENS", "8000,1200"))
    est_chapter_tokens: tuple = pair(env("EST_CHAPTER_TOKENS", "1000,250"))
    est_revise_rate: float = env("EST_REVISE_RATE", 0.5, float)
    est_plan_seconds: int = env("EST_PLAN_SECONDS", 90, int)
    est_writer_seconds: int = env("EST_WRITER_SECONDS", 45, int)
    est_editor_seconds: int = env("EST_EDITOR_SECONDS", 15, int)
    stories_dir: Path = field(default_factory=lambda: Path(env("STORIES_DIR", str(ROOT / "stories"))))


def rates(model):
    return PRICES.get(model) or PRICES.get(model.split("/")[-1])


def price(model, prompt_tokens, completion_tokens):
    r = rates(model)
    if not r:
        return 0.0
    return (prompt_tokens * r[0] + completion_tokens * r[1]) / 1_000_000
