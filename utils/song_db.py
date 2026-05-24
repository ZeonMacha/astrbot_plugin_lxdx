"""歌曲数据库：API 获取全曲目列表后缓存为 songs.json，支持 ID/标题查询。

load_from_list / load_cache    从 API 数据或本地 JSON 构建内存索引
save_cache                     把当前内存索引写入 songs.json
get_by_id / get_by_title       按 ID 精确或标题模糊查找
resolve_song_id                兼容 DX/标准曲三种 ID 格式
"""

import json
from pathlib import Path

from ..lxns.models import SongInfo


class SongDatabase:
    """歌曲索引（内存 + JSON 缓存文件）。

    两个索引结构：
    - _by_id: 按歌曲原始 ID 快速查找
    - _by_title: 按小写标题分组（同名歌曲可能有多个，如不同版本）
    """

    def __init__(self, cache_dir: str):
        self._dir = Path(cache_dir)
        self._by_id: dict[int, SongInfo] = {}
        self._by_title: dict[str, list[SongInfo]] = {}
        self._loaded = False

    @property
    def loaded(self) -> bool: return self._loaded

    @property
    def song_count(self) -> int: return len(self._by_id)

    def get_by_id(self, sid: int) -> SongInfo | None:
        """按 ID 精确查找歌曲。"""
        return self._by_id.get(sid)

    def get_by_title(self, title: str) -> list[SongInfo]:
        """模糊搜索：子字符串匹配 → 多字符容错匹配（全部非空字符同时存在于标题中）。"""
        q = title.lower().strip()
        res = [s for k, ss in self._by_title.items() if q in k for s in ss]
        if not res:
            res = [s for k, ss in self._by_title.items()
                   if all(c in k for c in q if c.strip()) for s in ss]
        return res

    def load_from_list(self, songs: list[SongInfo]) -> None:
        """用 API 返回的 SongInfo 列表替换整个内存索引（清空旧数据）。"""
        self._by_id.clear(); self._by_title.clear()
        for s in songs:
            self._by_id[s.id] = s
            self._by_title.setdefault(s.title.lower().strip(), []).append(s)
        self._loaded = True

    def save_cache(self) -> None:
        """将当前内存索引序列化为 JSON 缓存在 songs.json。"""
        self._dir.mkdir(parents=True, exist_ok=True)
        data = [{"id": s.id, "title": s.title, "artist": s.artist, "genre": s.genre,
                 "bpm": s.bpm, "version": s.version, "is_utage": s.is_utage,
                 "levels": s.levels, "difficulties": s.difficulties,
                 "dx_difficulties": s.dx_difficulties, "notes": s.notes,
                 "image_url": s.image_url} for s in self._by_id.values()]
        (self._dir / "songs.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def load_cache(self) -> bool:
        """从 songs.json 加载缓存的歌曲索引。返回是否成功。"""
        p = self._dir / "songs.json"
        if not p.exists(): return False
        try:
            self.load_from_list([SongInfo(**i) for i in json.loads(p.read_text("utf-8"))])
            return True
        except Exception: return False

    def resolve_song_id(self, raw: int) -> SongInfo | None:
        """兼容 DX/标准曲三种 ID 格式：原始 → raw+10000 → raw%10000。"""
        for did in (raw, raw + 10000, raw % 10000):
            if (s := self._by_id.get(did)): return s
        return None
