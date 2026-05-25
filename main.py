"""落雪DX 插件入口。国服舞萌 DX / 中二节奏查分，支持 OAuth(PKCE) 和开发者 API Key。"""

from dataclasses import asdict
from pathlib import Path

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

from .lxns.auth import LxnsAuth
from .lxns.client import LxnsClient
from .lxns.chunithm_client import ChunithmClient
from .lxns.models import (
    TokenInfo,
    LxnsError,
    AuthExpiredError,
    AuthRequiredError,
    DIFFICULTY_NAMES,
    DIFFICULTY_SHORT,
    DIFFICULTY_COLORS,
    CHU_DIFFICULTY_NAMES,
    CHU_DIFFICULTY_SHORT,
    CHU_DIFFICULTY_COLORS,
)
from .utils.storage import StorageManager
from .utils.song_db import SongDatabase
from .utils.chunithm_song_db import ChuSongDatabase
from .utils.assets import AssetManager

SCOPE = "read_player read_user_profile"


@register(
    "astrbot_plugin_lxdx",
    "Par1y",
    "国服舞萌DX/中二节奏插件，使用落雪接口，支持 b50、bests、曲目信息等功能。",
    "1.1.0",
)
class LxdxPlugin(Star):
    """国服舞萌 DX / 中二节奏查分插件。

    支持两种认证模式：
    - OAuth(PKCE): 用户通过浏览器授权，适合多用户使用（无需开发者API Key）
    - API Key: 开发者直接使用落雪 API Key，无需 OAuth 授权流程
    """

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        c = config or {}

        dp = self._data_path(context)
        self._st = StorageManager(self, dp)  # 文件路径 + KV 存储
        self._sdb = SongDatabase(self._st.cache_dir)  # 舞萌歌曲索引缓存
        self._chu_sdb = ChuSongDatabase(self._st.cache_dir)  # 中二歌曲索引缓存
        self._am = AssetManager(self._st.assets_dir)  # 封面图片缓存

        self._auth = LxnsAuth(c.get("client_id", ""))
        self._ru = c.get("redirect_uri", "")
        self._method = c.get("method", "OAuth")  # 配置中的授权模式选择
        self._api_key = c.get("api_key", "")
        self._client = LxnsClient(
            self._auth,
            redirect_uri=self._ru,
            api_key=self._api_key,
            on_token_refresh=self._persist_token,
        )
        self._chu_client = ChunithmClient(
            self._auth,
            redirect_uri=self._ru,
            api_key=self._api_key,
            on_token_refresh=self._persist_token,
        )

        self._pkce: dict[str, dict] = {}  # 内存 PKCE 参数 (uid → {verifier})
        self._tmpl: dict[str, str] = {}  # 内存中缓存的 HTML 模板
        self._tdir = Path(__file__).parent / "templates"

    @staticmethod
    def _data_path(ctx: Context) -> str:
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path

            return get_astrbot_data_path()
        except ImportError:
            pass
        return getattr(ctx, "data_dir", str(Path(__file__).parent / "data"))

    async def initialize(self):
        """AstrBot 插件生命周期：初始化时加载模板和缓存目录。"""
        self._load_tmpl()
        self._st.ensure_dirs()
        if self._sdb.load_cache():
            logger.info(f"[lxdx] loaded {self._sdb.song_count} Maimai songs from cache")
        else:
            logger.info("[lxdx] no Maimai song cache, will fetch on first use")
        if self._chu_sdb.load_cache():
            logger.info(
                f"[lxdx] loaded {self._chu_sdb.song_count} Chunithm songs from cache"
            )
        else:
            logger.info("[lxdx] no Chunithm song cache, will fetch on first use")
        mode_label = "OAuth(PKCE)" if self._is_oauth else "api_key"
        logger.info(f"[lxdx] init done, mode={mode_label}")

    async def terminate(self):
        """AstrBot 插件生命周期：清理资源。"""
        await self._client.close()
        await self._chu_client.close()
        logger.info("[lxdx] terminated")

    def _load_tmpl(self):
        """将 templates/ 下的 HTML 文件读入内存字典，供 html_render 使用。"""
        for n in (
            "help",
            "b50",
            "song_info",
            "chunithm_help",
            "chunithm_bests",
            "chunithm_song_info",
            "chunithm_recent",
        ):
            p = self._tdir / f"{n}.html"
            if p.exists():
                self._tmpl[n] = p.read_text("utf-8")

    @property
    def _is_oauth(self) -> bool:
        return self._method != "api_key"

    # --- auth helpers ---

    async def _persist_token(self, uid: str, token: TokenInfo) -> None:
        """Token 刷新回调：将新 Token 持久化到 KV。"""
        await self._st.kv_put(self._st.token_key(uid), asdict(token))

    async def _restore_token(self, ev: AstrMessageEvent) -> str:
        """从 KV 恢复用户 OAuth Token 到内存缓存（LxnsAuth）。成功返回 uid，失败返回空字符串。"""
        uid = ev.get_sender_id()
        td = await self._st.kv_get(self._st.token_key(uid))
        if not td or not isinstance(td, dict):
            return ""
        try:
            t = TokenInfo(**td)
            self._auth.store_tokens(uid, t)
            return uid
        except Exception:
            await self._st.kv_delete(self._st.token_key(uid))
            return ""

    # --- song helpers ---

    async def _ensure_songs(self, uid: str = ""):
        """确保歌曲数据库已加载；未加载时从 API 获取并缓存到本地 JSON。"""
        if self._sdb.loaded:
            return
        try:
            logger.info("[lxdx] fetching song list...")
            self._sdb.load_from_list(await self._client.get_song_list(uid))
            self._sdb.save_cache()
            logger.info(f"[lxdx] loaded {self._sdb.song_count} songs")
        except Exception as e:
            logger.warning(f"[lxdx] song list fetch failed: {e}")

    async def _lookup(self, q: str, uid: str = "") -> list:
        """查找歌曲：整数按 ID 解析，字符串按标题模糊搜索。"""
        await self._ensure_songs(uid)
        if not self._sdb.loaded:
            return []
        try:
            sid = int(q)
            if s := self._sdb.resolve_song_id(sid):
                return [s]
            return []
        except ValueError:
            return self._sdb.get_by_title(q)

    # --- command router ---

    @filter.command("lxdx")
    async def lxdx(self, ev: AstrMessageEvent):
        """主命令入口：/lxdx <help|bind|b50|song|login> [...]"""
        ps = ev.message_str.strip().split()
        if len(ps) < 2:
            async for r in self._help(ev):
                yield r
            return
        sub, args = ps[1].lower(), ps[2:]
        h = {
            "help": self._help,
            "bind": self._bind,
            "b50": self._b50,
            "song": self._song,
            "login": self._login,
        }
        fn = h.get(sub)
        if fn is None:
            yield ev.plain_result(f"未知指令: {sub}，使用 /lxdx help 查看帮助")
        else:
            async for r in fn(ev, args):
                yield r

    @filter.command("lxchu")
    async def lxchu(self, ev: AstrMessageEvent):
        """中二节奏命令入口：/lxchu <help|bind|bests|song|recent|login> [...]"""
        ps = ev.message_str.strip().split()
        if len(ps) < 2:
            async for r in self._chu_help(ev):
                yield r
            return
        sub, args = ps[1].lower(), ps[2:]
        h = {
            "help": self._chu_help,
            "bind": self._chu_bind,
            "bests": self._chu_bests,
            "song": self._chu_song,
            "recent": self._chu_recent,
            "login": self._chu_login,
        }
        fn = h.get(sub)
        if fn is None:
            yield ev.plain_result(f"未知指令: {sub}，使用 /lxchu help 查看帮助")
        else:
            async for r in fn(ev, args):
                yield r

    # --- /lxdx help ---

    async def _help(self, ev: AstrMessageEvent, _=None):
        """显示命令列表和当前授权模式。优先使用 HTML 模板渲染图片，无模板时回退纯文本。"""
        t = self._tmpl.get("help")
        if t:
            desc = (
                "已绑定开发者 API Key" if not self._is_oauth else "OAuth(PKCE) 交互授权"
            )
            url = await self.html_render(
                t,
                {
                    "plugin_display_name": "落雪DX",
                    "plugin_version": "1.0.0",
                    "auth_mode": "OAuth(PKCE)" if self._is_oauth else "api_key",
                    "auth_desc": desc,
                    "commands": [
                        {"name": "/lxdx bind <fc>", "desc": "绑定玩家好友码"},
                        {"name": "/lxdx b50 [fc]", "desc": "Best 50 (最佳35 + 最近15)"},
                        {"name": "/lxdx song <名称/ID>", "desc": "查询歌曲信息"},
                        {
                            "name": "/lxdx login [<码>]",
                            "desc": "OAuth 授权登录 / 完成回调",
                        },
                    ],
                },
            )
            yield ev.image_result(url)
        else:
            yield ev.plain_result(
                "/lxdx help|bind <fc>|b50 [fc]|song <名称/ID>|login [<码>]"
            )

    # --- /lxdx bind ---

    async def _bind(self, ev: AstrMessageEvent, args: list):
        """绑定好友码：/lxdx bind <friend_code>。API Key 模式会验证好友码有效性。"""
        if not args:
            yield ev.plain_result("用法: /lxdx bind <friend_code>")
            return
        fc = args[0]
        uid = ev.get_sender_id()
        if not self._is_oauth:
            try:
                await self._client.get_player_info(fc)
            except Exception as e:
                yield ev.plain_result(f"绑定失败: {e}")
                return
        await self._st.kv_put(self._st.binding_key(uid), fc)
        yield ev.plain_result(f"已绑定好友码: {fc}")

    # --- /lxdx b50 ---

    async def _b50(self, ev: AstrMessageEvent, args: list):
        """查询 Best 50：/lxdx b50 [friend_code]。API Key 模式必须提供或已绑定 fc。"""
        uid = ev.get_sender_id()
        if not self._is_oauth:
            fc = args[0] if args else await self._st.kv_get(self._st.binding_key(uid))
            if not fc:
                yield ev.plain_result("请先 /lxdx bind <fc> 或 /lxdx b50 <fc>")
                return
            try:
                b50 = await self._client.get_b50(fc=fc)
            except LxnsError as e:
                yield ev.plain_result(str(e))
                return
        else:
            u = await self._restore_token(ev)
            if not u:
                yield ev.plain_result("请先 /lxdx login 授权")
                return
            try:
                b50 = await self._client.get_b50(uid=u)
            except AuthExpiredError as e:
                await self._st.kv_delete(self._st.token_key(u))
                self._auth.remove_tokens(u)
                yield ev.plain_result(str(e))
                return
            except LxnsError as e:
                yield ev.plain_result(str(e))
                return

        t = self._tmpl.get("b50")
        if t:
            url = await self.html_render(
                t,
                {
                    "player_name": b50.player_name,
                    "rating": b50.rating,
                    "friend_code": b50.friend_code,
                    "class_rank": b50.class_rank,
                    "best": self._rec_rows(b50.best),
                    "recent": self._rec_rows(b50.recent),
                },
            )
            yield ev.image_result(url)
        else:
            yield ev.plain_result(self._b50_text(b50))

    # --- /lxdx song ---

    async def _song(self, ev: AstrMessageEvent, args: list):
        """查询歌曲信息：/lxdx song <名称 或 ID>。多个匹配时返回列表。"""
        if not args:
            yield ev.plain_result("用法: /lxdx song <名称 或 ID>")
            return
        q = " ".join(args)
        uid = ev.get_sender_id()
        if self._is_oauth:
            uid = await self._restore_token(ev) or ""
        res = await self._lookup(q, uid)
        if not res:
            yield ev.plain_result(f"未找到: {q}")
            return
        if len(res) > 1:
            ns = "\n".join(f"  · {s.title} (ID:{s.display_id})" for s in res[:10])
            yield ev.plain_result(f"多个结果:\n{ns}")
            return
        s = res[0]
        t = self._tmpl.get("song_info")
        if t:
            jp = await self._am.download_jacket(s.id) or ""
            url = await self.html_render(
                t,
                {
                    "song": {
                        "title": s.title,
                        "artist": s.artist,
                        "genre": s.genre,
                        "bpm": s.bpm,
                        "display_id": s.display_id,
                        "is_utage": s.is_utage,
                    },
                    "jacket_path": jp,
                    "difficulties": self._diff_rows(s),
                },
            )
            yield ev.image_result(url)
        else:
            yield ev.plain_result(self._song_text(s))

    # --- /lxdx login ---

    async def _login(self, ev: AstrMessageEvent, args: list = None):
        """OAuth(PKCE) 登录：/lxdx login 启动授权流程，/lxdx login <授权码> 完成回调交换 Token。"""
        if not self._is_oauth:
            yield ev.plain_result("API Key 模式无需 OAuth，直接 /lxdx bind <fc>")
            return
        uid = ev.get_sender_id()

        if args:
            code = args[0]
            pk = self._pkce.get(uid)
            if not pk:
                yield ev.plain_result("未找到授权会话，请先 /lxdx login")
                return
            try:
                tok = await self._client.exchange_code(code, pk["verifier"])
                self._auth.store_tokens(uid, tok)
                await self._st.kv_put(self._st.token_key(uid), asdict(tok))
                del self._pkce[uid]

                user_name = ""
                try:
                    profile = await self._client.get_user_profile(uid)
                    user_name = profile.name
                except Exception as e:
                    logger.warning(f"[lxdx] get_user_profile failed: {e}")

                fc_info = ""
                try:
                    pi = await self._client.get_player_info(uid=uid)
                    await self._st.kv_put(self._st.binding_key(uid), pi.friend_code)
                    fc_info = f" 好友码:{pi.friend_code} Rating:{pi.rating}"
                except Exception as e:
                    logger.warning(f"[lxdx] player info fetch failed: {e}")
                    fc_info = " (可稍后 /lxdx b50)"

                msg = "授权成功！"
                if user_name:
                    msg += f" 用户名: {user_name}"
                msg += fc_info
                yield ev.plain_result(msg)
            except LxnsError as e:
                self._pkce.pop(uid, None)
                logger.error(f"[lxdx] login callback failed: {e}")
                yield ev.plain_result(f"授权失败: {e}")
            except Exception as e:
                self._pkce.pop(uid, None)
                logger.error(f"[lxdx] login callback error: {e}")
                yield ev.plain_result("授权过程中发生未知错误")
        else:
            pk = LxnsAuth.generate_pkce()
            self._pkce[uid] = {"verifier": pk.code_verifier}
            url = self._auth.build_authorize_url(pk, self._ru, SCOPE)
            yield ev.plain_result(
                f"请打开链接授权:\n{url}\n\n完成后用 /lxdx login <授权码> 发送给我"
            )

    # --- helpers ---

    @staticmethod
    def _rec_rows(recs: list) -> list:
        """将 PlayerRecord 列表转为模板渲染所需的 dict 列表，包含难度简称、达成率、评级等。"""
        return [
            {
                "title": r.title,
                "difficulty_short": DIFFICULTY_SHORT[r.level_index]
                if r.level_index < 5
                else "?",
                "difficulty_css": DIFFICULTY_NAMES[r.level_index]
                .lower()
                .replace(":", "")
                if r.level_index < 5
                else "",
                "achievement_pct": r.achievement_pct,
                "dx_score": r.dx_score,
                "level_value": r.level_value,
                "rank": r.rank_display,
            }
            for r in recs
        ]

    @staticmethod
    def _diff_rows(song):
        """将 SongInfo 的各难度等级、定数、note 数转为模板渲染用的 dict 列表。"""
        rows = []
        for i, n in enumerate(DIFFICULTY_NAMES):
            lv = song.levels[i] if i < len(song.levels) else 0
            df = song.difficulties[i] if i < len(song.difficulties) else 0.0
            nt = song.notes[i] if i < len(song.notes) else None
            if lv == 0 and not nt:
                continue
            rows.append(
                {
                    "name": n,
                    "css_class": n.lower().replace(":", ""),
                    "level": lv,
                    "difficulty": df if df > 0 else None,
                    "notes": nt,
                }
            )
        return rows

    @staticmethod
    def _b50_text(b50) -> str:
        """纯文本格式的 B50 输出（无 HTML 模板时的回退方案）。"""
        ls = [f"{b50.player_name}  Rating: {b50.rating}", "= Best 35 ="]
        for i, r in enumerate(b50.best, 1):
            d = DIFFICULTY_SHORT[r.level_index] if r.level_index < 5 else "?"
            ls.append(f"#{i} {r.title} [{d}] {r.achievement_pct:.4f}% DX:{r.dx_score}")
        ls.append("= Recent 15 =")
        for i, r in enumerate(b50.recent, 1):
            d = DIFFICULTY_SHORT[r.level_index] if r.level_index < 5 else "?"
            ls.append(f"#{i} {r.title} [{d}] {r.achievement_pct:.4f}% DX:{r.dx_score}")
        return "\n".join(ls)

    @staticmethod
    def _song_text(song) -> str:
        """纯文本格式的歌曲详情输出（无 HTML 模板时的回退方案）。"""
        ls = [
            f"{song.title}  [{song.genre}]",
            f"艺术家:{song.artist}  BPM:{song.bpm}  ID:{song.display_id}"
            + (" (宴)" if song.is_utage else ""),
            "",
            "难度        等级  定数",
        ]
        for i, n in enumerate(DIFFICULTY_NAMES):
            lv = song.levels[i] if i < len(song.levels) else 0
            df = song.difficulties[i] if i < len(song.difficulties) else 0.0
            if lv == 0:
                continue
            ls.append(
                f"  {n:<8} {lv:>4}  {df:.1f}" if df > 0 else f"  {n:<8} {lv:>4}  -"
            )
        return "\n".join(ls)

    # --- Chunithm song helpers ---

    async def _ensure_chu_songs(self, uid: str = ""):
        if self._chu_sdb.loaded:
            return
        try:
            logger.info("[lxdx] fetching Chunithm song list...")
            res = await self._chu_client.get_song_list(uid)
            self._chu_sdb.load_from_list(res.songs)
            self._chu_sdb.save_cache()
            logger.info(f"[lxdx] loaded {self._chu_sdb.song_count} Chunithm songs")
        except Exception as e:
            logger.warning(f"[lxdx] Chunithm song list fetch failed: {e}")

    async def _chu_lookup(self, q: str, uid: str = "") -> list:
        await self._ensure_chu_songs(uid)
        if not self._chu_sdb.loaded:
            return []
        try:
            sid = int(q)
            if s := self._chu_sdb.get_by_id(sid):
                return [s]
            return []
        except ValueError:
            return self._chu_sdb.get_by_title(q)

    @staticmethod
    def _chu_score_rows(scores: list) -> list:
        result = []
        for r in scores:
            if r.level_index < len(CHU_DIFFICULTY_SHORT):
                d_short = CHU_DIFFICULTY_SHORT[r.level_index]
                d_css = (
                    CHU_DIFFICULTY_NAMES[r.level_index]
                    .lower()
                    .replace("'", "")
                    .replace(" ", "-")
                )
            else:
                d_short = "?"
                d_css = ""
            result.append(
                {
                    "song_name": r.song_name,
                    "difficulty_short": d_short,
                    "difficulty_css": d_css,
                    "level": r.level,
                    "score": r.score,
                    "rating": r.rating,
                    "rank": r.rank,
                    "rank_display": r.rank_display,
                    "clear": r.clear,
                    "clear_display": r.clear_display,
                    "full_combo": r.full_combo,
                    "fc_display": r.fc_display,
                    "full_chain": r.full_chain,
                    "play_time": r.play_time or "",
                }
            )
        return result

    @staticmethod
    def _chu_diff_rows(song):
        rows = []
        for diff in song.difficulties:
            idx = diff.difficulty
            name = (
                CHU_DIFFICULTY_NAMES[idx]
                if idx < len(CHU_DIFFICULTY_NAMES)
                else f"LV{idx}"
            )
            css = name.lower().replace("'", "").replace(" ", "-")
            notes = (
                {
                    "tap": diff.notes.tap if diff.notes else 0,
                    "hold": diff.notes.hold if diff.notes else 0,
                    "slide": diff.notes.slide if diff.notes else 0,
                    "air": diff.notes.air if diff.notes else 0,
                    "flick": diff.notes.flick if diff.notes else 0,
                }
                if diff.notes
                else None
            )
            rows.append(
                {
                    "name": name,
                    "css_class": css,
                    "level": diff.level,
                    "level_value": diff.level_value,
                    "note_designer": diff.note_designer,
                    "notes": notes,
                }
            )
        return rows

    # --- /lxchu help ---

    async def _chu_help(self, ev: AstrMessageEvent, _=None):
        t = self._tmpl.get("chunithm_help")
        if t:
            desc = (
                "已绑定开发者 API Key" if not self._is_oauth else "OAuth(PKCE) 交互授权"
            )
            url = await self.html_render(
                t,
                {
                    "plugin_display_name": "落雪DX (中二节奏)",
                    "plugin_version": "1.1.0",
                    "auth_mode": "OAuth(PKCE)" if self._is_oauth else "api_key",
                    "auth_desc": desc,
                    "commands": [
                        {"name": "/lxchu bind <fc>", "desc": "绑定玩家好友码"},
                        {
                            "name": "/lxchu bests [fc]",
                            "desc": "Best 30 + Selection 10 + New 20",
                        },
                        {"name": "/lxchu recent [fc]", "desc": "Recent 50 最近游玩"},
                        {"name": "/lxchu song <名称/ID>", "desc": "查询歌曲信息"},
                        {
                            "name": "/lxchu login [<码>]",
                            "desc": "OAuth 授权登录 / 完成回调",
                        },
                    ],
                },
            )
            yield ev.image_result(url)
        else:
            yield ev.plain_result(
                "/lxchu help|bind <fc>|bests [fc]|recent [fc]|song <名称/ID>|login [<码>]"
            )

    # --- /lxchu bind ---

    async def _chu_bind(self, ev: AstrMessageEvent, args: list):
        if not args:
            yield ev.plain_result("用法: /lxchu bind <friend_code>")
            return
        fc = args[0]
        uid = ev.get_sender_id()
        if not self._is_oauth:
            try:
                await self._chu_client.get_player_info(fc=int(fc))
            except Exception as e:
                yield ev.plain_result(f"绑定失败: {e}")
                return
        await self._st.kv_put(self._st.chu_binding_key(uid), fc)
        yield ev.plain_result(f"已绑定中二节奏好友码: {fc}")

    # --- /lxchu bests ---

    async def _chu_bests(self, ev: AstrMessageEvent, args: list):
        uid = ev.get_sender_id()
        if not self._is_oauth:
            fc_raw = (
                args[0]
                if args
                else await self._st.kv_get(self._st.chu_binding_key(uid))
            )
            if not fc_raw:
                yield ev.plain_result("请先 /lxchu bind <fc> 或 /lxchu bests <fc>")
                return
            fc = int(fc_raw)
            try:
                bests = await self._chu_client.get_bests(fc=fc)
                pi = await self._chu_client.get_player_info(fc=fc)
            except LxnsError as e:
                yield ev.plain_result(str(e))
                return
        else:
            u = await self._restore_token(ev)
            if not u:
                yield ev.plain_result("请先 /lxchu login 授权")
                return
            try:
                bests = await self._chu_client.get_bests(uid=u)
                pi = await self._chu_client.get_player_info(uid=u)
            except AuthExpiredError as e:
                await self._st.kv_delete(self._st.token_key(u))
                self._auth.remove_tokens(u)
                yield ev.plain_result(str(e))
                return
            except LxnsError as e:
                yield ev.plain_result(str(e))
                return

        t = self._tmpl.get("chunithm_bests")
        if t:
            url = await self.html_render(
                t,
                {
                    "player_name": pi.name,
                    "rating": pi.rating,
                    "friend_code": pi.friend_code,
                    "bests": self._chu_score_rows(bests.bests),
                    "selections": self._chu_score_rows(bests.selections),
                    "new_bests": self._chu_score_rows(bests.new_bests),
                },
            )
            yield ev.image_result(url)
        else:
            yield ev.plain_result(self._chu_bests_text(pi, bests))

    # --- /lxchu recent ---

    async def _chu_recent(self, ev: AstrMessageEvent, args: list):
        uid = ev.get_sender_id()
        if not self._is_oauth:
            fc_raw = (
                args[0]
                if args
                else await self._st.kv_get(self._st.chu_binding_key(uid))
            )
            if not fc_raw:
                yield ev.plain_result("请先 /lxchu bind <fc> 或 /lxchu recent <fc>")
                return
            fc = int(fc_raw)
            try:
                recents = await self._chu_client.get_recents(fc=fc)
                pi = await self._chu_client.get_player_info(fc=fc)
            except LxnsError as e:
                yield ev.plain_result(str(e))
                return
        else:
            u = await self._restore_token(ev)
            if not u:
                yield ev.plain_result("请先 /lxchu login 授权")
                return
            try:
                recents = await self._chu_client.get_recents(uid=u)
                pi = await self._chu_client.get_player_info(uid=u)
            except AuthExpiredError as e:
                await self._st.kv_delete(self._st.token_key(u))
                self._auth.remove_tokens(u)
                yield ev.plain_result(str(e))
                return
            except LxnsError as e:
                yield ev.plain_result(str(e))
                return

        t = self._tmpl.get("chunithm_recent")
        if t:
            url = await self.html_render(
                t,
                {
                    "player_name": pi.name,
                    "friend_code": pi.friend_code,
                    "recent": self._chu_score_rows(recents),
                },
            )
            yield ev.image_result(url)
        else:
            yield ev.plain_result(self._chu_recent_text(pi, recents))

    # --- /lxchu song ---

    async def _chu_song(self, ev: AstrMessageEvent, args: list):
        if not args:
            yield ev.plain_result("用法: /lxchu song <名称 或 ID>")
            return
        q = " ".join(args)
        uid = ev.get_sender_id()
        if self._is_oauth:
            uid = await self._restore_token(ev) or ""
        res = await self._chu_lookup(q, uid)
        if not res:
            yield ev.plain_result(f"未找到: {q}")
            return
        if len(res) > 1:
            ns = "\n".join(f"  · {s.title} (ID:{s.id})" for s in res[:10])
            yield ev.plain_result(f"多个结果:\n{ns}")
            return
        s = res[0]
        t = self._tmpl.get("chunithm_song_info")
        if t:
            jp = await self._am.download_chunithm_jacket(s.id) or ""
            url = await self.html_render(
                t,
                {
                    "song": {
                        "title": s.title,
                        "artist": s.artist,
                        "genre": s.genre,
                        "bpm": s.bpm,
                        "id": s.id,
                        "map": s.map,
                    },
                    "jacket_path": jp,
                    "difficulties": self._chu_diff_rows(s),
                },
            )
            yield ev.image_result(url)
        else:
            yield ev.plain_result(self._chu_song_text(s))

    # --- /lxchu login ---

    async def _chu_login(self, ev: AstrMessageEvent, args: list = None):
        if not self._is_oauth:
            yield ev.plain_result("API Key 模式无需 OAuth，直接 /lxchu bind <fc>")
            return
        uid = ev.get_sender_id()

        if args:
            code = args[0]
            pk = self._pkce.get(uid)
            if not pk:
                yield ev.plain_result("未找到授权会话，请先 /lxchu login")
                return
            try:
                tok = await self._client.exchange_code(code, pk["verifier"])
                self._auth.store_tokens(uid, tok)
                await self._st.kv_put(self._st.token_key(uid), asdict(tok))
                del self._pkce[uid]

                fc_info = ""
                try:
                    pi = await self._chu_client.get_player_info(uid=uid)
                    await self._st.kv_put(
                        self._st.chu_binding_key(uid), str(pi.friend_code)
                    )
                    fc_info = f" 好友码:{pi.friend_code} Rating:{pi.rating:.2f}"
                except Exception as e:
                    logger.warning(f"[lxdx] Chunithm player info fetch failed: {e}")
                    fc_info = " (可稍后 /lxchu bests)"

                msg = "授权成功！"
                msg += fc_info
                yield ev.plain_result(msg)
            except LxnsError as e:
                self._pkce.pop(uid, None)
                logger.error(f"[lxdx] Chunithm login callback failed: {e}")
                yield ev.plain_result(f"授权失败: {e}")
            except Exception as e:
                self._pkce.pop(uid, None)
                logger.error(f"[lxdx] Chunithm login callback error: {e}")
                yield ev.plain_result("授权过程中发生未知错误")
        else:
            pk = LxnsAuth.generate_pkce()
            self._pkce[uid] = {"verifier": pk.code_verifier}
            url = self._auth.build_authorize_url(pk, self._ru, SCOPE)
            yield ev.plain_result(
                f"请打开链接授权:\n{url}\n\n完成后用 /lxchu login <授权码> 发送给我"
            )

    # --- Chunithm text fallback helpers ---

    @staticmethod
    def _chu_bests_text(pi, bests) -> str:
        ls = [f"{pi.name}  Rating: {pi.rating:.2f}", "= Best 30 ="]
        for i, r in enumerate(bests.bests, 1):
            d = (
                CHU_DIFFICULTY_SHORT[r.level_index]
                if r.level_index < len(CHU_DIFFICULTY_SHORT)
                else "?"
            )
            ls.append(
                f"#{i} {r.song_name} [{d}{r.level}] {r.score} {r.rank_display} {r.clear_display}"
            )
        ls.append("= Selection 10 =")
        for i, r in enumerate(bests.selections, 1):
            d = (
                CHU_DIFFICULTY_SHORT[r.level_index]
                if r.level_index < len(CHU_DIFFICULTY_SHORT)
                else "?"
            )
            ls.append(
                f"#{i} {r.song_name} [{d}{r.level}] {r.score} {r.rank_display} {r.clear_display}"
            )
        ls.append("= New 20 =")
        for i, r in enumerate(bests.new_bests, 1):
            d = (
                CHU_DIFFICULTY_SHORT[r.level_index]
                if r.level_index < len(CHU_DIFFICULTY_SHORT)
                else "?"
            )
            ls.append(
                f"#{i} {r.song_name} [{d}{r.level}] {r.score} {r.rank_display} {r.clear_display}"
            )
        return "\n".join(ls)

    @staticmethod
    def _chu_recent_text(pi, recents) -> str:
        ls = [f"{pi.name}  好友码: {pi.friend_code}", "= Recent 50 ="]
        for i, r in enumerate(recents, 1):
            d = (
                CHU_DIFFICULTY_SHORT[r.level_index]
                if r.level_index < len(CHU_DIFFICULTY_SHORT)
                else "?"
            )
            ls.append(
                f"#{i} {r.song_name} [{d}{r.level}] {r.score} {r.rank_display} {r.clear_display}"
            )
        return "\n".join(ls)

    @staticmethod
    def _chu_song_text(song) -> str:
        ls = [
            f"{song.title}  [{song.genre}]",
            f"艺术家:{song.artist}  BPM:{song.bpm}  ID:{song.id}",
            "",
            "难度          等级  定数  谱师",
        ]
        for diff in song.difficulties:
            idx = diff.difficulty
            name = (
                CHU_DIFFICULTY_NAMES[idx]
                if idx < len(CHU_DIFFICULTY_NAMES)
                else f"LV{idx}"
            )
            lv = diff.level_value
            ls.append(
                f"  {name:<10} {diff.level:>4}  {lv:.1f}"
                if lv > 0
                else f"  {name:<10} {diff.level:>4}  -"
            )
        return "\n".join(ls)
