import os
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

from lxns import (
    LxnsAuth, LxnsClient, TokenInfo,
    DIFFICULTY_NAMES, DIFFICULTY_SHORT, DIFFICULTY_COLORS,
)
from utils import StorageManager, SongDatabase, AssetManager

BUILTIN_CLIENT_ID = "405bddd9-c6cb-4307-b4fa-dbd48eb6a5db"


@register("astrbot_plugin_lxdx", "Par1y", "国服舞萌DX插件，使用落雪（lxns）接口，支持 b50、曲目信息等功能。", "1.0.0")
class LxdxPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self._config = config or {}

        data_path = self._resolve_data_path(context)
        self._storage = StorageManager(self, data_path)
        self._song_db = SongDatabase(self._storage.cache_dir)
        self._assets = AssetManager(self._storage.assets_dir)

        client_id = self._config.get("client_id", "") or BUILTIN_CLIENT_ID
        self._auth = LxnsAuth(client_id)

        api_key = self._config.get("api_key", "")
        self._client = LxnsClient(self._auth, api_key=api_key)

        self._templates: dict[str, str] = {}
        self._template_dir = Path(__file__).parent / "templates"

    @staticmethod
    def _resolve_data_path(context: Context) -> str:
        try:
            from astrbot.api.star import get_astrbot_data_path
            return get_astrbot_data_path()
        except ImportError:
            pass
        if hasattr(context, "get_data_dir"):
            return context.get_data_dir()
        if hasattr(context, "data_dir"):
            return context.data_dir
        return "data"

    async def initialize(self):
        self._load_templates()
        self._storage.ensure_dirs()

        logger.info("[lxdx] Loading song database...")
        try:
            if self._song_db.load_cache():
                logger.info(f"[lxdx] Loaded {self._song_db.song_count} songs from cache")
            else:
                logger.info("[lxdx] No song cache found, will fetch on first use")
        except Exception as e:
            logger.warning(f"[lxdx] Failed to load song cache: {e}")

        logger.info(f"[lxdx] Plugin initialized (mode: {self._auth_mode()})")

    async def terminate(self):
        await self._client.close()
        logger.info("[lxdx] Plugin terminated")

    def _load_templates(self) -> None:
        for name in ["help", "b50", "song_info"]:
            path = self._template_dir / f"{name}.html"
            if path.exists():
                self._templates[name] = path.read_text(encoding="utf-8")

    def _auth_mode(self) -> str:
        return self._config.get("method", "OAuth")

    async def _ensure_oauth_auth(self, event: AstrMessageEvent) -> str:
        user_id = event.get_sender_id()
        token_data = await self._storage.kv_get(self._storage.token_key(user_id))
        if not token_data:
            return ""
        try:
            token = TokenInfo(**token_data) if isinstance(token_data, dict) else token_data
            self._auth.store_tokens(user_id, token)
            return user_id
        except Exception:
            return ""

    async def _fetch_song_list_if_needed(self, user_id: str = "") -> None:
        if self._song_db.loaded:
            return
        try:
            logger.info("[lxdx] Fetching song list from API...")
            songs = await self._client.get_song_list(user_id)
            self._song_db.load_from_list(songs)
            self._song_db.save_cache()
            logger.info(f"[lxdx] Loaded {self._song_db.song_count} songs")
        except Exception as e:
            logger.warning(f"[lxdx] Failed to fetch song list: {e}")

    async def _lookup_song(self, query: str, user_id: str = "") -> list:
        await self._fetch_song_list_if_needed(user_id)
        if not self._song_db.loaded:
            return []
        try:
            song_id = int(query)
            song = self._song_db.resolve_song_id(song_id)
            return [song] if song else []
        except ValueError:
            return self._song_db.get_by_title(query)

    # ---------- command handler ----------

    @filter.command("lxdx")
    async def lxdx_handler(self, event: AstrMessageEvent):
        parts = event.message_str.strip().split()
        if len(parts) < 2:
            async for result in self._cmd_help(event):
                yield result
            return

        subcommand = parts[1].lower()
        args = parts[2:] if len(parts) > 2 else []

        handlers = {
            "help": self._cmd_help,
            "bind": lambda e, a: self._cmd_bind(e, a),
            "b50": lambda e, a: self._cmd_b50(e, a),
            "song": lambda e, a: self._cmd_song(e, a),
            "login": lambda e, a: self._cmd_login(e),
            "callback": lambda e, a: self._cmd_callback(e, a),
        }
        handler = handlers.get(subcommand)
        if handler is None:
            yield event.plain_result(f"未知指令: {subcommand}，请使用 /lxdx help 查看帮助")
        else:
            async for result in handler(event, args):
                yield result

    async def _cmd_help(self, event: AstrMessageEvent, _args: list = None):
        template = self._templates.get("help", "")
        if template:
            auth_mode = self._auth_mode()
            auth_desc = "已绑定开发者 API Key，直接使用" if auth_mode == "api_key" and self._config.get("api_key") else "使用 OAuth 交互授权，无需手动填写 Key"
            url = await self.html_render(
                template,
                {
                    "plugin_display_name": "落雪DX",
                    "plugin_version": "1.0.0",
                    "auth_mode": auth_mode,
                    "auth_desc": auth_desc,
                    "commands": [
                        {"name": "/lxdx bind <fc>", "desc": "绑定玩家好友码"},
                        {"name": "/lxdx b50 [fc]", "desc": "查询 Best 50 (最佳 35 + 最近 15)"},
                        {"name": "/lxdx song <名称/ID>", "desc": "查询歌曲信息"},
                        {"name": "/lxdx login", "desc": "OAuth 交互授权登录"},
                        {"name": "/lxdx callback <授权码>", "desc": "完成 OAuth 授权 (登录后使用)"},
                    ],
                },
            )
            yield event.image_result(url)
        else:
            yield event.plain_result(
                "指令列表:\n"
                "/lxdx bind <friend_code> - 绑定玩家\n"
                "/lxdx b50 [friend_code] - 查询 Best 50\n"
                "/lxdx song <歌曲名/ID> - 查询歌曲信息\n"
                "/lxdx login - OAuth 授权登录\n"
                "/lxdx callback <授权码> - 完成 OAuth 授权"
            )

    async def _cmd_bind(self, event: AstrMessageEvent, args: list):
        if not args:
            yield event.plain_result("用法: /lxdx bind <friend_code>")
            return

        friend_code = args[0]
        user_id = event.get_sender_id()

        if self._auth_mode() == "api_key":
            try:
                await self._client.get_player_info(friend_code)
            except Exception as e:
                yield event.plain_result(f"绑定失败，无法获取玩家信息: {e}")
                return

        await self._storage.kv_put(self._storage.binding_key(user_id), friend_code)
        yield event.plain_result(f"已绑定好友码: {friend_code}")

    async def _cmd_b50(self, event: AstrMessageEvent, args: list):
        user_id = event.get_sender_id()

        if self._auth_mode() == "api_key":
            friend_code = args[0] if args else await self._storage.kv_get(self._storage.binding_key(user_id))
            if not friend_code:
                yield event.plain_result("请先使用 /lxdx bind <friend_code> 绑定玩家，或直接指定: /lxdx b50 <friend_code>")
                return
            try:
                b50 = await self._client.get_b50(friend_code=friend_code)
            except Exception as e:
                logger.error(f"[lxdx] B50 fetch failed: {e}")
                yield event.plain_result(f"获取 B50 失败: {e}")
                return
        else:
            uid = await self._ensure_oauth_auth(event)
            if not uid:
                yield event.plain_result("请先使用 /lxdx login 进行 OAuth 授权")
                return
            try:
                b50 = await self._client.get_b50(user_id=uid)
            except Exception as e:
                logger.error(f"[lxdx] B50 fetch failed: {e}")
                yield event.plain_result(f"获取 B50 失败: {e}")
                return

        template = self._templates.get("b50", "")
        if template:
            best_rows = self._build_record_rows(b50.best)
            recent_rows = self._build_record_rows(b50.recent)
            url = await self.html_render(
                template,
                {
                    "player_name": b50.player_name,
                    "rating": b50.rating,
                    "friend_code": b50.friend_code,
                    "class_rank": b50.class_rank,
                    "best": best_rows,
                    "recent": recent_rows,
                },
            )
            yield event.image_result(url)
        else:
            yield event.plain_result(self._format_b50_text(b50))

    async def _cmd_song(self, event: AstrMessageEvent, args: list):
        if not args:
            yield event.plain_result("用法: /lxdx song <歌曲名 或 ID>")
            return

        query = " ".join(args)
        user_id = event.get_sender_id()
        if self._auth_mode() != "api_key":
            user_id = await self._ensure_oauth_auth(event) or ""

        results = await self._lookup_song(query, user_id)
        if not results:
            yield event.plain_result(f"未找到歌曲: {query}")
            return

        if len(results) > 1:
            names = "\n".join(f"  · {s.title} (ID: {s.display_id})" for s in results[:10])
            yield event.plain_result(f"找到多个结果:\n{names}\n\n请使用更精确的名称或 ID 查询")
            return

        song = results[0]
        template = self._templates.get("song_info", "")
        if template:
            jacket_path = ""
            try:
                jacket_path = await self._assets.download_jacket(song.id)
            except Exception:
                pass

            diffs = self._build_difficulty_rows(song)
            url = await self.html_render(
                template,
                {
                    "song": {
                        "title": song.title,
                        "artist": song.artist,
                        "genre": song.genre,
                        "bpm": song.bpm,
                        "display_id": song.display_id,
                        "is_utage": song.is_utage,
                    },
                    "jacket_path": jacket_path,
                    "difficulties": diffs,
                },
            )
            yield event.image_result(url)
        else:
            yield event.plain_result(self._format_song_text(song))

    async def _cmd_login(self, event: AstrMessageEvent):
        if self._auth_mode() == "api_key":
            yield event.plain_result("当前为开发者 API Key 模式，无需 OAuth 登录。使用 /lxdx bind <friend_code> 绑定即可。")
            return

        pkce = LxnsAuth.generate_pkce()
        user_id = event.get_sender_id()
        await self._storage.kv_put(
            self._storage.pkce_key(user_id),
            {"verifier": pkce.code_verifier, "state": pkce.state},
        )
        url = self._auth.build_authorize_url(pkce)
        yield event.plain_result(f"请打开以下链接进行授权:\n{url}\n\n授权完成后，将显示的授权码通过 /lxdx callback <授权码> 发送给我")

    async def _cmd_callback(self, event: AstrMessageEvent, args: list):
        if not args:
            yield event.plain_result("用法: /lxdx callback <授权码>")
            return

        code = args[0]
        user_id = event.get_sender_id()
        pkce_data = await self._storage.kv_get(self._storage.pkce_key(user_id))
        if not pkce_data:
            yield event.plain_result("未找到授权会话，请先使用 /lxdx login 开始授权")
            return

        try:
            token = await self._client.exchange_code(code, pkce_data["verifier"])
            self._auth.store_tokens(user_id, token)
            await self._storage.kv_put(self._storage.token_key(user_id), asdict(token))
            await self._storage.kv_delete(self._storage.pkce_key(user_id))

            player_info = await self._client.get_player_info(user_id=user_id)
            await self._storage.kv_put(self._storage.binding_key(user_id), player_info.friend_code)

            yield event.plain_result(f"授权成功！玩家: {player_info.name}  好友码: {player_info.friend_code}  Rating: {player_info.rating}")
        except Exception as e:
            logger.error(f"[lxdx] OAuth callback failed: {e}")
            yield event.plain_result(f"授权失败: {e}")

    # ---------- helpers ----------

    @staticmethod
    def _build_record_rows(records: list) -> list:
        rows = []
        for rec in records:
            idx = rec.level_index
            diff_name = DIFFICULTY_NAMES[idx] if 0 <= idx < len(DIFFICULTY_NAMES) else "?"
            rows.append({
                "title": rec.title,
                "difficulty_short": DIFFICULTY_SHORT[idx] if 0 <= idx < len(DIFFICULTY_SHORT) else "?",
                "difficulty_css": diff_name.lower().replace(":", ""),
                "achievement_pct": rec.achievement_pct,
                "dx_score": rec.dx_score,
                "level_value": rec.level_value,
                "rank": rec.rank_display,
            })
        return rows

    @staticmethod
    def _build_difficulty_rows(song) -> list:
        rows = []
        for i, name in enumerate(DIFFICULTY_NAMES):
            level = song.levels[i] if i < len(song.levels) else 0
            difficulty = song.difficulties[i] if i < len(song.difficulties) else 0.0
            notes = song.notes[i] if i < len(song.notes) else None
            if level == 0 and not notes:
                continue
            rows.append({
                "name": name,
                "css_class": name.lower().replace(":", ""),
                "level": level,
                "difficulty": difficulty if difficulty > 0 else None,
                "notes": notes,
            })
        return rows

    @staticmethod
    def _format_b50_text(b50) -> str:
        lines = [f"{b50.player_name} - Rating: {b50.rating}"]
        lines.append("= Best 35 =")
        for i, rec in enumerate(b50.best, 1):
            diff = DIFFICULTY_SHORT[rec.level_index] if rec.level_index < len(DIFFICULTY_SHORT) else "?"
            lines.append(f"  #{i} {rec.title} [{diff}] {rec.achievement_pct:.4f}%  DX: {rec.dx_score}")
        lines.append("= Recent 15 =")
        for i, rec in enumerate(b50.recent, 1):
            diff = DIFFICULTY_SHORT[rec.level_index] if rec.level_index < len(DIFFICULTY_SHORT) else "?"
            lines.append(f"  #{i} {rec.title} [{diff}] {rec.achievement_pct:.4f}%  DX: {rec.dx_score}")
        return "\n".join(lines)

    @staticmethod
    def _format_song_text(song) -> str:
        lines = [
            f"{song.title}  [{song.genre}]",
            f"艺术家: {song.artist}  BPM: {song.bpm}  ID: {song.display_id}" + (" (宴)" if song.is_utage else ""),
            "",
            "难度        等级  定数",
        ]
        for i, name in enumerate(DIFFICULTY_NAMES):
            level = song.levels[i] if i < len(song.levels) else 0
            difficulty = song.difficulties[i] if i < len(song.difficulties) else 0.0
            if level == 0:
                continue
            diff_str = f"{difficulty:.1f}" if difficulty > 0 else "-"
            lines.append(f"  {name:<8}  {level:>3}   {diff_str}")
        return "\n".join(lines)
