import httpx

from .auth import LxnsAuth
from .models import (
    PlayerB50,
    PlayerRecord,
    PlayerInfo,
    SongInfo,
    SongRecord,
    TokenInfo,
)

BASE_URL = "https://maimai.lxns.net"


class LxnsClient:
    def __init__(
        self,
        auth: LxnsAuth,
        api_key: str = "",
    ):
        self._auth = auth
        self._api_key = api_key

    @staticmethod
    def _http_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=httpx.Timeout(30.0))

    async def exchange_code(self, code: str, code_verifier: str) -> TokenInfo:
        payload = {
            "grant_type": "authorization_code",
            "client_id": self._auth.client_id(),
            "code": code,
            "code_verifier": code_verifier,
        }

        async with self._http_client() as client:
            resp = await client.post(LxnsAuth.TOKEN_URL, json=payload)
            resp.raise_for_status()
            resp_data = resp.json()
            data = resp_data.get("data", resp_data)
            return LxnsAuth.make_token_info(
                access_token=data["access_token"],
                refresh_token=data["refresh_token"],
                expires_in=data.get("expires_in", 900),
            )

    async def refresh_token(self, refresh_token: str) -> TokenInfo:
        payload = {
            "grant_type": "refresh_token",
            "client_id": self._auth.client_id(),
            "refresh_token": refresh_token,
        }
        async with self._http_client() as client:
            resp = await client.post(LxnsAuth.TOKEN_URL, json=payload)
            resp.raise_for_status()
            resp_data = resp.json()
            data = resp_data.get("data", resp_data)
            return LxnsAuth.make_token_info(
                access_token=data["access_token"],
                refresh_token=data["refresh_token"],
                expires_in=data.get("expires_in", 900),
            )

    async def _auth_headers(self, user_id: str = "") -> dict:
        if self._api_key:
            return {"X-API-Key": self._api_key}
        token = self._auth.get_tokens(user_id)
        if not token:
            raise ValueError("未登录，请使用 /lxdx login 进行 OAuth 授权")
        if LxnsAuth.is_token_expired(token):
            token = await self.refresh_token(token.refresh_token)
            self._auth.store_tokens(user_id, token)
        return {"Authorization": f"Bearer {token.access_token}"}

    def _endpoint_base(self) -> str:
        if self._api_key:
            return f"{BASE_URL}/api/v0/maimai"
        return f"{BASE_URL}/api/v0/user/maimai"

    async def get_song_list(self, user_id: str = "") -> list[SongInfo]:
        headers = await self._auth_headers(user_id)
        base = self._endpoint_base()
        async with self._http_client() as client:
            resp = await client.get(f"{base}/song/list", headers=headers)
            resp.raise_for_status()
            data = resp.json()
        return [self._parse_song(item) for item in data.get("songs", data)]

    async def get_player_info(self, friend_code: str = "", user_id: str = "") -> PlayerInfo:
        headers = await self._auth_headers(user_id)
        base = self._endpoint_base()
        url = f"{base}/player/{friend_code}" if self._api_key and friend_code else f"{base}/player"
        async with self._http_client() as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        return PlayerInfo(
            name=data.get("name", data.get("nickname", "")),
            rating=data.get("rating", 0),
            friend_code=data.get("friend_code", friend_code),
            class_rank=data.get("class_rank", 0),
        )

    async def get_b50(self, friend_code: str = "", user_id: str = "") -> PlayerB50:
        headers = await self._auth_headers(user_id)
        base = self._endpoint_base()
        if self._api_key and friend_code:
            url = f"{base}/player/{friend_code}/bests"
        elif self._api_key:
            raise ValueError("开发者 API 模式需要提供 friend_code")
        else:
            url = f"{base}/player/bests"
        async with self._http_client() as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        return self._parse_b50(data)

    async def get_records(self, song_id: int, user_id: str = "") -> list[PlayerRecord]:
        headers = await self._auth_headers(user_id)
        base = self._endpoint_base()
        url = f"{base}/player/records?song_id={song_id}"
        async with self._http_client() as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        return [self._parse_record(item) for item in data.get("records", data)]

    async def get_song_records(self, song_id: int, user_id: str = "") -> SongRecord:
        headers = await self._auth_headers(user_id)
        base = self._endpoint_base()
        url = f"{base}/player/records?song_id={song_id}"
        async with self._http_client() as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        records = [self._parse_record(item) for item in data.get("records", data)]
        return SongRecord(records=records)

    @staticmethod
    def _parse_song(item: dict) -> SongInfo:
        basic = item.get("basic_info", {})
        diff_data = item.get("difficulties", {})
        return SongInfo(
            id=item["id"],
            title=item.get("title", ""),
            artist=item.get("artist", ""),
            genre=item.get("genre", basic.get("genre", "")),
            bpm=item.get("bpm", basic.get("bpm", 0)),
            version=item.get("version", basic.get("from", 0)),
            is_utage=item.get("is_utage", basic.get("is_utage", False)),
            levels=item.get("levels", diff_data.get("standard", [0, 0, 0, 0, 0])),
            difficulties=diff_data.get("standard", [0.0, 0.0, 0.0, 0.0, 0.0]),
            dx_difficulties=diff_data.get("dx", [0.0, 0.0, 0.0, 0.0, 0.0]),
            notes=item.get("notes", item.get("charts", [])),
            image_url=item.get("image_url", item.get("image", "")),
        )

    @staticmethod
    def _parse_record(item: dict) -> PlayerRecord:
        level_index = item.get("level_index", item.get("difficulty", 0))
        if isinstance(level_index, str):
            try:
                level_index = int(level_index)
            except ValueError:
                level_index_map = {"basic": 0, "advanced": 1, "expert": 2, "master": 3, "remaster": 4}
                level_index = level_index_map.get(level_index.lower(), 0)

        return PlayerRecord(
            song_id=item.get("song_id", item.get("id", 0)),
            level_index=level_index,
            title=item.get("title", item.get("song_name", "")),
            difficulty="",
            level_value=item.get("level_value", item.get("level", 0.0)),
            achievement=item.get("achievements", item.get("achievement", 0)),
            dx_score=item.get("dx_score", item.get("delux_score", 0)),
            dx_rating=item.get("dx_rating", item.get("rating", 0)),
            fc=item.get("fc", ""),
            fs=item.get("fs", ""),
            rate=item.get("rate", ""),
            combo_status=item.get("combo_status", ""),
            sync_status=item.get("sync_status", ""),
            play_time=item.get("play_time", None),
        )

    @classmethod
    def _parse_b50(cls, data: dict) -> PlayerB50:
        inner = data.get("data", data)
        best = [cls._parse_record(item) for item in inner.get("best", inner.get("charts", {}).get("best", []))]
        recent = [cls._parse_record(item) for item in inner.get("recent", inner.get("charts", {}).get("recent", []))]
        return PlayerB50(
            player_name=inner.get("name", inner.get("nickname", "")),
            rating=inner.get("rating", 0),
            class_rank=inner.get("class_rank", 0),
            friend_code=inner.get("friend_code", ""),
            best=best,
            recent=recent,
            course_rank=inner.get("course_rank", 0),
            rank=inner.get("rank", 0),
        )

    async def close(self) -> None:
        pass
