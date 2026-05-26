"""Chunithm 歌曲数据库：API 获取全曲目列表后缓存为 chunithm_songs.json，支持 ID/标题/别名查询。"""

import json
from pathlib import Path

from ..lxns.models import ChuSongInfo


class ChuSongDatabase:
    def __init__(self, cache_dir: str):
        self._dir = Path(cache_dir)
        self._by_id: dict[int, ChuSongInfo] = {}
        self._by_title: dict[str, list[ChuSongInfo]] = {}
        self._by_alias: dict[str, ChuSongInfo] = {}
        self._loaded = False

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def song_count(self) -> int:
        return len(self._by_id)

    def get_by_id(self, sid: int) -> ChuSongInfo | None:
        return self._by_id.get(sid)

    def get_by_title(self, title: str) -> list[ChuSongInfo]:
        """模糊搜索：先别名精确 → 标题子串 → 多字符容错。"""
        q = title.lower().strip()
        if s := self._by_alias.get(q):
            return [s]
        res = [s for k, ss in self._by_title.items() if q in k for s in ss]
        if not res:
            res = [
                s
                for k, ss in self._by_title.items()
                if all(c in k for c in q if c.strip())
                for s in ss
            ]
            if not res:
                for a, s in self._by_alias.items():
                    if all(c in a for c in q if c.strip()):
                        res.append(s)
        return res

    def load_from_list(self, songs: list[ChuSongInfo]) -> None:
        self._by_id.clear()
        self._by_title.clear()
        self._by_alias.clear()
        for s in songs:
            self._by_id[s.id] = s
            self._by_title.setdefault(s.title.lower().strip(), []).append(s)
            for a in s.aliases:
                self._by_alias[a.lower().strip()] = s
        self._loaded = True

    def load_aliases(self, alias_map: dict[int, list[str]]) -> None:
        for sid, aliases in alias_map.items():
            s = self._by_id.get(sid)
            if s:
                s.aliases = aliases
                for a in aliases:
                    self._by_alias[a.lower().strip()] = s

    def save_cache(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        data = [
            {
                "id": s.id,
                "title": s.title,
                "artist": s.artist,
                "genre": s.genre,
                "bpm": s.bpm,
                "version": s.version,
                "map": s.map,
                "rights": s.rights,
                "locked": s.locked,
                "disabled": s.disabled,
                "aliases": s.aliases,
                "difficulties": [
                    {
                        "difficulty": d.difficulty,
                        "level": d.level,
                        "level_value": d.level_value,
                        "note_designer": d.note_designer,
                        "version": d.version,
                        "notes": {
                            "total": d.notes.total,
                            "tap": d.notes.tap,
                            "hold": d.notes.hold,
                            "slide": d.notes.slide,
                            "air": d.notes.air,
                            "flick": d.notes.flick,
                        }
                        if d.notes
                        else None,
                        "origin_id": d.origin_id,
                        "kanji": d.kanji,
                        "star": d.star,
                    }
                    for d in s.difficulties
                ],
            }
            for s in self._by_id.values()
        ]
        (self._dir / "chunithm_songs.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )

    def load_cache(self) -> bool:
        p = self._dir / "chunithm_songs.json"
        if not p.exists():
            return False
        try:
            from ..lxns.models import ChuSongDifficulty, ChuNotes

            raw = json.loads(p.read_text("utf-8"))
            songs = []
            for item in raw:
                diffs = []
                for d in item.get("difficulties", []):
                    notes = None
                    if d.get("notes"):
                        notes = ChuNotes(**d["notes"])
                    diffs.append(
                        ChuSongDifficulty(
                            difficulty=d.get("difficulty", 0),
                            level=d.get("level", ""),
                            level_value=d.get("level_value", 0.0),
                            note_designer=d.get("note_designer", ""),
                            version=d.get("version", 0),
                            notes=notes,
                            origin_id=d.get("origin_id", 0),
                            kanji=d.get("kanji", ""),
                            star=d.get("star", 0),
                        )
                    )
                item.pop("difficulties", None)
                item.setdefault("aliases", [])
                songs.append(ChuSongInfo(difficulties=diffs, **item))
            self.load_from_list(songs)
            return True
        except Exception:
            return False
