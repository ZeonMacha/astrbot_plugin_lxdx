"""LXNS API 客户端：Token 管理、HTTP 请求、自动重试、数据解析。httpx 懒加载。"""

from typing import Optional, Callable, Awaitable
import time

from astrbot.api import logger

from .auth import LxnsAuth
from .models import (
    PlayerB50,
    PlayerRecord,
    PlayerInfo,
    SongInfo,
    SongRecord,
    TokenInfo,
    UserProfile,
    LxnsError,
    AuthExpiredError,
    AuthRequiredError,
    ApiRequestError,
)

BASE_URL = "https://maimai.lxns.net"


class LxnsClient:
    """LXNS API 客户端，支持 OAuth (PKCE) 和开发者 API Key。

    核心设计：
    - 每次请求创建新的 httpx.AsyncClient（简化生命周期管理）
    - 所有 API 请求最多重试 MAX_RETRIES 次（5xx/超时）
    - OAuth Token 过期时自动刷新（_auth_headers 中触发）
    """

    MAX_RETRIES = 2

    def __init__(
        self,
        auth: LxnsAuth,
        redirect_uri: str = "",
        api_key: str = "",
        debug: bool = False,
        on_token_refresh: Optional[Callable[[str, TokenInfo], Awaitable[None]]] = None,
    ):
        """auth: PKCE 授权管理实例；redirect_uri: OAuth 回调地址；api_key: 开发者 Key（可选）；
        debug: 开启调试日志；on_token_refresh: Token 刷新时将新 Token 持久化到 KV 的回调（uid, TokenInfo）→ awaitable。"""
        self._auth = auth
        self._redirect_uri = redirect_uri
        self._api_key = api_key
        self._debug = debug
        self._on_token_refresh = on_token_refresh

    @staticmethod
    def _get_httpx():
        """懒加载 httpx 模块。未安装时抛出 LxnsError，避免 import 时报错。"""
        try:
            import httpx

            return httpx
        except ImportError:
            raise LxnsError("缺少 httpx 依赖，请安装: pip install httpx")

    @classmethod
    def _http_client(cls):
        """创建新的 AsyncClient（超时 30s）。每次请求独立创建以简化生命周期。"""
        h = cls._get_httpx()
        return h.AsyncClient(timeout=h.Timeout(30.0))

    def _token_payload(self, grant_type: str, **extra) -> dict:
        """构建 OAuth Token 请求体，包含 grant_type / client_id / redirect_uri 基础字段。"""
        p: dict = {
            "grant_type": grant_type,
            "client_id": self._auth.client_id,
            "redirect_uri": self._redirect_uri,
        }
        p.update(extra)
        return p

    @staticmethod
    def _extract_token_data(resp: dict) -> dict:
        """从 Token 接口响应中提取 data 字段，校验必要字段存在。"""
        d = resp.get("data", resp)
        if not isinstance(d, dict):
            raise LxnsError("服务器返回了异常的数据格式")
        for k in ("access_token", "refresh_token"):
            if k not in d:
                raise LxnsError(f"服务器响应缺少必要字段: {k}")
        return d

    @staticmethod
    def _http_error_msg(code: int, body) -> str:
        """将 HTTP 状态码和响应体格式化为人类可读的错误信息。"""
        if isinstance(body, dict):
            m = body.get("message", body.get("error", ""))
            if m:
                return f"[{code}] {m}"
        return f"HTTP {code}"

    async def exchange_code(self, code: str, code_verifier: str) -> TokenInfo:
        """用 OAuth 授权码交换 Token（PKCE 流程第三步）。支持 5xx 和网络超时重试。"""
        h = self._get_httpx()
        pl = self._token_payload(
            "authorization_code", code=code, code_verifier=code_verifier
        )
        for i in range(self.MAX_RETRIES + 1):
            try:
                async with self._http_client() as c:
                    r = await c.post(LxnsAuth.TOKEN_URL, json=pl)
                    if r.status_code >= 500 and i < self.MAX_RETRIES:
                        continue
                    if r.status_code >= 400:
                        try:
                            b = r.json()
                        except Exception:
                            b = r.text
                        raise LxnsError(
                            f"授权码交换失败: {self._http_error_msg(r.status_code, b)}"
                        )
                    d = self._extract_token_data(r.json())
                    return LxnsAuth.make_token_info(
                        d["access_token"], d["refresh_token"], d.get("expires_in", 900)
                    )
            except (h.TimeoutException, h.ConnectError) as e:
                if i < self.MAX_RETRIES:
                    continue
                raise LxnsError(f"连接落雪服务器超时: {e}") from e
            except LxnsError:
                raise
        raise LxnsError("连接落雪服务器失败")

    async def refresh_token(self, refresh_token: str, uid: str = "") -> TokenInfo:
        """用 refresh_token 刷新 OAuth Token。400/401 时清除缓存并抛 AuthExpiredError。"""
        h = self._get_httpx()
        pl = self._token_payload("refresh_token", refresh_token=refresh_token)
        for i in range(self.MAX_RETRIES + 1):
            try:
                async with self._http_client() as c:
                    r = await c.post(LxnsAuth.TOKEN_URL, json=pl)
                    if r.status_code >= 500 and i < self.MAX_RETRIES:
                        continue
                    if r.status_code in (400, 401):
                        if uid:
                            self._auth.remove_tokens(uid)
                        raise AuthExpiredError("登录已过期，请重新授权 /lxdx login")
                    if r.status_code >= 400:
                        try:
                            b = r.json()
                        except Exception:
                            b = r.text
                        if uid:
                            self._auth.remove_tokens(uid)
                        raise AuthExpiredError(
                            f"刷新令牌失败: {self._http_error_msg(r.status_code, b)}"
                        )
                    d = self._extract_token_data(r.json())
                    return LxnsAuth.make_token_info(
                        d["access_token"], d["refresh_token"], d.get("expires_in", 900)
                    )
            except (h.TimeoutException, h.ConnectError) as e:
                if i < self.MAX_RETRIES:
                    continue
                raise LxnsError(f"连接落雪服务器超时: {e}") from e
            except LxnsError:
                raise
        raise LxnsError("连接落雪服务器失败")

    async def _auth_headers(self, uid: str = "") -> dict:
        """构建认证请求头。API Key 模式返回 Authorization；OAuth 模式返回 Bearer Token，过期时自动刷新。"""
        if self._api_key:
            return {"Authorization": self._api_key}
        t = self._auth.get_tokens(uid)
        if not t:
            raise AuthRequiredError("未登录，请使用 /lxdx login 进行 OAuth 授权")
        if LxnsAuth.is_token_expired(t):
            try:
                t = await self.refresh_token(t.refresh_token, uid)
                self._auth.store_tokens(uid, t)
                if self._debug:
                    logger.info(f"[lxdx] token refreshed for uid={uid}")
                if callable(self._on_token_refresh):
                    await self._on_token_refresh(uid, t)
            except AuthExpiredError:
                raise
            except LxnsError as e:
                raise AuthExpiredError(f"令牌刷新失败: {e}") from e
        return {"Authorization": f"Bearer {t.access_token}"}

    async def _api_get(self, url: str, uid: str = "", auth: bool = True) -> dict:
        """通用 GET 请求：auth=False 时不附加认证头（用于公共 API）。"""
        h = self._get_httpx()
        hdrs = await self._auth_headers(uid) if auth else {}
        for i in range(self.MAX_RETRIES + 1):
            try:
                t0 = time.monotonic()
                async with self._http_client() as c:
                    r = await c.get(url, headers=hdrs)
                    elapsed = time.monotonic() - t0
                    if self._debug:
                        logger.info(
                            f"[lxdx] GET {url} -> {r.status_code} ({elapsed:.2f}s)"
                        )
                    if auth and r.status_code == 401:
                        raise AuthExpiredError("登录已过期，请重新授权 /lxdx login")
                    if r.status_code >= 500 and i < self.MAX_RETRIES:
                        continue
                    r.raise_for_status()
                    return r.json()
            except (h.TimeoutException, h.ConnectError) as e:
                if self._debug:
                    logger.info(f"[lxdx] GET {url} -> timeout/connect error: {e}")
                if i < self.MAX_RETRIES:
                    continue
                raise ApiRequestError(f"请求超时: {e}") from e
            except LxnsError:
                raise
            except h.HTTPStatusError as e:
                raise ApiRequestError(f"API 请求失败 [{e.response.status_code}]") from e
        raise ApiRequestError("请求失败")

    def _endpoint_base(self) -> str:
        """根据认证模式选择 API 基础路径：API Key 用 /maimai，OAuth 用 /user/maimai。"""
        return (
            f"{BASE_URL}/api/v0/maimai"
            if self._api_key
            else f"{BASE_URL}/api/v0/user/maimai"
        )

    async def get_song_list(self, uid: str = "") -> list[SongInfo]:
        """获取全曲目列表（公共 API，无需认证）。"""
        d = await self._api_get(f"{BASE_URL}/api/v0/maimai/song/list", auth=False)
        return [self._parse_song(i) for i in d.get("songs", d)]

    async def get_player_info(self, fc: str = "", uid: str = "") -> PlayerInfo:
        """获取玩家基本信息。API Key 模式通过 fc 查询，OAuth 模式通过当前 uid 查询。"""
        b = self._endpoint_base()
        u = f"{b}/player/{fc}" if self._api_key and fc else f"{b}/player"
        d = await self._api_get(u, uid)
        inner = d.get("data", d)
        return PlayerInfo(
            name=inner.get("name", inner.get("nickname", "")),
            rating=inner.get("rating", 0),
            friend_code=inner.get("friend_code", fc),
            class_rank=inner.get("class_rank", 0),
        )

    async def get_b50(self, fc: str = "", uid: str = "") -> PlayerB50:
        """获取 Best 50 数据（新曲 Best 35 + 旧曲 Recent 15）。"""
        b = self._endpoint_base()
        if self._api_key and fc:
            u = f"{b}/player/{fc}/bests"
        elif self._api_key:
            raise ApiRequestError("开发者 API 模式需要提供 friend_code")
        else:
            u = f"{b}/player/bests"
        return self._parse_b50(await self._api_get(u, uid))

    async def get_user_profile(self, uid: str = "") -> UserProfile:
        """获取 LXNS 用户资料（OAuth 模式下的用户主键）。调用 GET /api/v0/user/profile。"""
        d = await self._api_get(f"{BASE_URL}/api/v0/user/profile", uid)
        inner = d.get("data", d)
        return UserProfile(
            id=inner.get("id", 0),
            name=inner.get("name", ""),
            email=inner.get("email", ""),
            avatar=inner.get("avatar", ""),
        )

    async def get_song_records(self, sid: int, uid: str = "") -> SongRecord:
        """获取指定歌曲的游玩记录（当前未使用）。"""
        d = await self._api_get(
            f"{self._endpoint_base()}/player/records?song_id={sid}", uid
        )
        return SongRecord(records=[self._parse_record(i) for i in d.get("records", d)])

    # --- 响应解析器 ---

    @staticmethod
    def _parse_song(item: dict) -> SongInfo:
        """将 API 响应中的歌曲条目转换为 SongInfo 模型。兼容多种响应字段命名。"""
        bi = item.get("basic_info", {})
        dd = item.get("difficulties", {})
        return SongInfo(
            id=item.get("id", 0),
            title=item.get("title", ""),
            artist=item.get("artist", ""),
            genre=item.get("genre", bi.get("genre", "")),
            bpm=item.get("bpm", bi.get("bpm", 0)),
            version=item.get("version", bi.get("from", 0)),
            is_utage=item.get("is_utage", bi.get("is_utage", False)),
            levels=item.get("levels", [0, 0, 0, 0, 0]),
            difficulties=dd.get("standard", [0.0, 0.0, 0.0, 0.0, 0.0]),
            dx_difficulties=dd.get("dx", [0.0, 0.0, 0.0, 0.0, 0.0]),
            notes=item.get("notes", item.get("charts", [])),
            image_url=item.get("image_url", item.get("image", "")),
        )

    @staticmethod
    def _parse_record(item: dict) -> PlayerRecord:
        """将 API 响应中的单条成绩转换为 PlayerRecord 模型。level_index 支持字符串和整数。"""
        li = item.get("level_index", item.get("difficulty", 0))
        if isinstance(li, str):
            try:
                li = int(li)
            except ValueError:
                li = {
                    "basic": 0,
                    "advanced": 1,
                    "expert": 2,
                    "master": 3,
                    "remaster": 4,
                }.get(li.lower(), 0)
        return PlayerRecord(
            song_id=item.get("song_id", item.get("id", 0)),
            level_index=li,
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
        """将 Best50 接口响应转换为 PlayerB50 模型（分离 best 和 recent 成绩列表）。"""
        inner = data.get("data", data)
        best = [
            cls._parse_record(i)
            for i in inner.get("best", inner.get("charts", {}).get("best", []))
        ]
        recent = [
            cls._parse_record(i)
            for i in inner.get("recent", inner.get("charts", {}).get("recent", []))
        ]
        return PlayerB50(
            player_name=inner.get("name", inner.get("nickname", "")),
            rating=inner.get("rating", 0),
            class_rank=inner.get("class_rank", 0),
            friend_code=inner.get("friend_code", ""),
            best=best,
            recent=recent,
        )

    async def close(self) -> None:
        """清理资源（当前无持久连接，预留扩展）。"""
        pass
