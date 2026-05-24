"""LXNS API 数据模型和自定义异常。

数据模型均为 dataclass，直接从 API JSON 反序列化而来。
异常层级：LxnsError(基础) → AuthExpiredError / AuthRequiredError / ApiRequestError
"""

from dataclasses import dataclass, field
from typing import Optional
from enum import IntEnum


class DifficultyIndex(IntEnum):
    """难度索引：0=BASIC, 1=ADVANCED, 2=EXPERT, 3=MASTER, 4=Re:MASTER"""
    BASIC = 0
    ADVANCED = 1
    EXPERT = 2
    MASTER = 3
    RE_MASTER = 4


# 难度显示常量（用于模板生成和纯文本渲染）
DIFFICULTY_NAMES = ["BASIC", "ADVANCED", "EXPERT", "MASTER", "Re:MASTER"]  # 全称
DIFFICULTY_COLORS = {"BASIC": "#22bb5b", "ADVANCED": "#f6ba31", "EXPERT": "#f35871", "MASTER": "#9a69e7", "Re:MASTER": "#ba67f9"}  # 对应 HTML 颜色
DIFFICULTY_SHORT = ["Bas", "Adv", "Exp", "Mas", "ReM"]  # 缩写（3 字母）


# --- 歌曲 & 成绩 ---

@dataclass
class SongInfo:
    """歌曲信息。dx_difficulties 为 DX 谱面定数（当前未在前端展示）。"""
    id: int
    title: str
    artist: str
    genre: str
    bpm: int
    version: int
    is_utage: bool
    levels: list[int]            # 五个难度的等级（整数 1-15）
    difficulties: list[float]    # 标准谱面定数
    dx_difficulties: list[float] # DX 谱面定数
    notes: list[dict]            # note 详情（TAP/HOLD/SLIDE/TOUCH/BREAK 数量）
    image_url: Optional[str] = None

    @property
    def display_id(self) -> int:
        """显示用 ID：DX 曲 ID > 10000 取模显示，宴曲 ID 保留原始值。"""
        if self.is_utage:
            return self.id
        if self.id > 100000:
            return self.id
        if self.id > 10000:
            return self.id % 10000
        return self.id


@dataclass
class PlayerRecord:
    """单曲成绩。achievement_pct 属性自动将万分率（0-10100）转换为百分率（0-101%）。"""
    song_id: int
    level_index: int
    title: str
    difficulty: str
    level_value: float          # 定数
    achievement: float          # 原始达成率（可能是万分率 0~10100，也可能是百分率 0~101）
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
        """将 achievement 统一转换为百分率：>100 视为万分率除以 10000，否则直接返回。"""
        return self.achievement / 10000.0 if self.achievement > 100 else self.achievement

    @property
    def rank_display(self) -> str:
        """根据达成率返回评级显示（D ~ AP+）。"""
        a = self.achievement_pct
        if a >= 100.5: return "AP+"
        if a >= 100.0: return "AP"
        if a >= 99.5: return "SSS+"
        if a >= 99.0: return "SSS"
        if a >= 98.0: return "SS+"
        if a >= 97.0: return "SS"
        if a >= 94.0: return "S+"
        if a >= 90.0: return "S"
        if a >= 80.0: return "AAA"
        if a >= 70.0: return "AA"
        if a >= 60.0: return "A"
        if a >= 50.0: return "B"
        if a >= 40.0: return "C"
        return "D"


@dataclass
class PlayerB50:
    """玩家 Best 50 数据：best 为新曲 Best 35，recent 为旧曲 Recent 15。"""
    player_name: str
    rating: int
    class_rank: int
    friend_code: str
    best: list[PlayerRecord]
    recent: list[PlayerRecord]
    course_rank: int = 0
    rank: int = 0


# --- 认证 ---

@dataclass
class TokenInfo:
    """OAuth Token。expires_at 为 Unix 时间戳（生成时的绝对时间）。"""
    access_token: str
    refresh_token: str
    expires_at: float


@dataclass
class PlayerInfo:
    """玩家基本信息。"""
    name: str
    rating: int
    friend_code: str
    class_rank: int


@dataclass
class SongRecord:
    """单曲成绩记录列表（含可选歌曲信息，当前未使用）。"""
    records: list[PlayerRecord] = field(default_factory=list)
    song: Optional[SongInfo] = None


# --- 自定义异常 ---

class LxnsError(Exception):
    """LXNS 基础异常。message 为面向用户的中文描述。"""
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)
    def __str__(self): return self.message

class AuthExpiredError(LxnsError):
    """Token 过期（需重新登录）。"""

class AuthRequiredError(LxnsError):
    """未登录（需先执行 OAuth 授权）。"""

class ApiRequestError(LxnsError):
    """API 请求失败（网络/服务端错误）。"""
