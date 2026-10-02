from typing import Literal

from pydantic import BaseModel, Field


class Character(BaseModel):
    name: str
    role: str
    description: str
    arc: str = Field(description="How this person changes from first to last episode")


class Act(BaseModel):
    name: str
    start: int
    end: int
    purpose: str


class TurningPoint(BaseModel):
    episode: int
    event: str


class PlannedThread(BaseModel):
    question: str
    opens: int
    resolves: int


class ArcPlan(BaseModel):
    title: str
    logline: str
    tone: str
    setting: str
    acts: list[Act]
    turning_points: list[TurningPoint]
    characters: list[Character]
    threads: list[PlannedThread]
    ending: str


class FillerWords(BaseModel):
    language: str
    words: list[str] = Field(description="Lowercase filler/function words of the language the serial is written in")


class Beat(BaseModel):
    episode: int
    beat: str = Field(description="1-2 sentences: what concretely happens and what changes")
    hook: str = Field(description="The planned closing hook")


class BeatList(BaseModel):
    beats: list[Beat]


class CharacterUpdate(BaseModel):
    name: str
    status: Literal["alive", "dead", "missing", "unknown"] = "alive"
    note: str = Field(description="Current situation, goal and relationships after this episode")
    role: str = ""


class Fact(BaseModel):
    text: str
    tags: list[str] = Field(description="Lowercase character/place names involved, or 'world' for global rules")


class Review(BaseModel):
    issues: list[str] = Field(description="Concrete problems only; empty when the episode is clean")
    hook_score: int = Field(ge=1, le=10)
    directives_followed: bool
    summary: str
    hook: str
    time: str = Field(description="In-story time at the end of the episode")
    characters: list[CharacterUpdate]
    facts: list[Fact]
    threads_opened: list[str]
    threads_advanced: list[str]
    threads_resolved: list[str]


class Steer(BaseModel):
    directive: str
    permanent: bool
    supersedes: list[str] = []
    beats: list[Beat]


class Conflict(BaseModel):
    episode: int
    reason: str


class Impact(BaseModel):
    change: str
    conflicts: list[Conflict]


class Check(BaseModel):
    ok: bool
    word: str
