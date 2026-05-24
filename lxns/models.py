from dataclasses import dataclass, field
from typing import Optional
from enum import IntEnum


class DifficultyIndex(IntEnum):
    BASIC = 0
    ADVANCED = 1
    EXPERT = 2
    MASTER = 3
    RE_MASTER = 4


DIFFICULTY_NAMES = ["BASIC", "ADVANCED", "EXPERT", "MASTER", "Re:MASTER"]

DIFFICULTY_COLORS = {
    "BASIC": "#22bb5b",
    "ADVANCED": "#f6ba31",
    "EXPERT": "#f35871",
    "MASTER": "#9a69e7",
    "Re:MASTER": "#ba67f9",
}

DIFFICULTY_SHORT = ["Bas", "Adv", "Exp", "Mas", "ReM"]


@dataclass
class SongInfo:
    id: int
    title: str
    artist: str
    genre: str
    bpm: int
    version: int
    is_utage: bool
    levels: list[int]
    difficulties: list[float]
    dx_difficulties: list[float]
    notes: list[dict]
    image_url: Optional[str] = None

    @property
    def display_id(self) -> int:
        if self.is_utage:
            return self.id
        if self.id > 100000:
            return self.id
        if self.id > 10000:
            return self.id % 10000
        return self.id


@dataclass
class PlayerRecord:
    song_id: int
    level_index: int
    title: str
    difficulty: str
    level_value: float
    achievement: float
    dx_score: int
    dx_rating: int
    fc: str = ""
    fs: str = ""
    rate: str = ""
    combo_status: str = ""
    sync_status: str = ""
    play_time: Optional[str] = None

    @property
    def achievement_pct(self) -> float:
        return self.achievement / 10000.0 if self.achievement > 100 else self.achievement

    @property
    def rank_display(self) -> str:
        ach = self.achievement_pct
        if ach >= 100.5:
            return "AP+"
        if ach >= 100.0:
            return "AP"
        if ach >= 99.5:
            return "SSS+"
        if ach >= 99.0:
            return "SSS"
        if ach >= 98.0:
            return "SS+"
        if ach >= 97.0:
            return "SS"
        if ach >= 94.0:
            return "S+"
        if ach >= 90.0:
            return "S"
        if ach >= 80.0:
            return "AAA"
        if ach >= 70.0:
            return "AA"
        if ach >= 60.0:
            return "A"
        if ach >= 50.0:
            return "B"
        if ach >= 40.0:
            return "C"
        return "D"


@dataclass
class PlayerB50:
    player_name: str
    rating: int
    class_rank: int
    friend_code: str
    best: list[PlayerRecord]
    recent: list[PlayerRecord]
    course_rank: int = 0
    rank: int = 0


@dataclass
class TokenInfo:
    access_token: str
    refresh_token: str
    expires_at: float


@dataclass
class PlayerInfo:
    name: str
    rating: int
    friend_code: str
    class_rank: int


@dataclass
class SongRecord:
    records: list[PlayerRecord] = field(default_factory=list)
    song: Optional[SongInfo] = None
