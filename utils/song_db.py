import json
import os
from pathlib import Path
from typing import Optional

from lxns.models import SongInfo


class SongDatabase:
    def __init__(self, cache_dir: str):
        self._cache_dir = Path(cache_dir)
        self._by_id: dict[int, SongInfo] = {}
        self._by_title: dict[str, list[SongInfo]] = {}
        self._by_alias: dict[str, list[SongInfo]] = {}
        self._loaded = False

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def song_count(self) -> int:
        return len(self._by_id)

    def get_by_id(self, song_id: int) -> SongInfo | None:
        return self._by_id.get(song_id)

    def get_by_title(self, title: str) -> list[SongInfo]:
        results = []
        lowered = title.lower().strip()
        for key, songs in self._by_title.items():
            if lowered in key:
                results.extend(songs)
        for key, songs in self._by_alias.items():
            if lowered in key:
                results.extend(songs)
        if not results:
            for key, songs in self._by_title.items():
                if all(char in key for char in lowered if char.strip()):
                    results.extend(songs)
        return results

    def load_from_list(self, songs: list[SongInfo]) -> None:
        self._by_id.clear()
        self._by_title.clear()
        self._by_alias.clear()
        for song in songs:
            self._by_id[song.id] = song
            key = song.title.lower().strip()
            self._by_title.setdefault(key, []).append(song)
        self._loaded = True

    def save_cache(self) -> None:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        songs_data = [
            {
                "id": s.id,
                "title": s.title,
                "artist": s.artist,
                "genre": s.genre,
                "bpm": s.bpm,
                "version": s.version,
                "is_utage": s.is_utage,
                "levels": s.levels,
                "difficulties": s.difficulties,
                "dx_difficulties": s.dx_difficulties,
                "notes": s.notes,
                "image_url": s.image_url,
            }
            for s in self._by_id.values()
        ]
        cache_path = self._cache_dir / "songs.json"
        cache_path.write_text(json.dumps(songs_data, ensure_ascii=False), encoding="utf-8")

    def load_cache(self) -> bool:
        cache_path = self._cache_dir / "songs.json"
        if not cache_path.exists():
            return False
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            songs = [SongInfo(**item) for item in data]
            self.load_from_list(songs)
            return True
        except Exception:
            return False

    def resolve_song_id(self, raw_id: int) -> SongInfo | None:
        song = self._by_id.get(raw_id)
        if song:
            return song
        for display_id in (raw_id, raw_id + 10000, raw_id % 10000):
            s = self._by_id.get(display_id)
            if s:
                return s
        return None
