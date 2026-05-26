"""舞萌DX 查分功能"""

from astrbot.api.event import AstrMessageEvent
from astrbot.api import logger

from .models import (
    LxnsError,
    AuthExpiredError,
    DIFFICULTY_NAMES,
    DIFFICULTY_SHORT,
    JINJA_OPTIONS,
)


class MaimaiHandler:
    def __init__(self, plugin):
        self._p = plugin

    @staticmethod
    def _args(ev: AstrMessageEvent, n: int) -> list:
        return ev.message_str.strip().split()[n:]

    # --- song helpers ---

    async def _ensure_songs(self, uid: str = ""):
        if self._p._sdb.loaded:
            return
        try:
            logger.info("[lxdx] fetching song list...")
            self._p._sdb.load_from_list(await self._p._client.get_song_list(uid))
            logger.info(f"[lxdx] loaded {self._p._sdb.song_count} songs")
            logger.info("[lxdx] fetching aliases...")
            alias_map = await self._p._client.get_alias_list()
            self._p._sdb.load_aliases(alias_map)
            logger.info(f"[lxdx] loaded {len(alias_map)} alias groups")
            self._p._sdb.save_cache()
        except Exception as e:
            logger.warning(f"[lxdx] song list fetch failed: {e}")

    async def _lookup(self, q: str, uid: str = "") -> list:
        await self._ensure_songs(uid)
        if not self._p._sdb.loaded:
            return []
        try:
            sid = int(q)
            if s := self._p._sdb.resolve_song_id(sid):
                return [s]
            return []
        except ValueError:
            return self._p._sdb.get_by_title(q)

    # --- /lxdx help ---

    async def help(self, ev: AstrMessageEvent):
        """显示命令列表和当前授权模式。优先使用 HTML 模板渲染图片，无模板时回退纯文本。"""
        t = self._p._tmpl.get("help")
        if t:
            if self._p._debug:
                logger.info("[lxdx] rendering help (Maimai)")
            desc = (
                "已绑定开发者 API Key"
                if not self._p._is_oauth
                else "OAuth(PKCE) 交互授权"
            )
            url = await self._p.render_html(
                t,
                {
                    "plugin_display_name": "落雪DX",
                    "plugin_version": "1.0.0",
                    "auth_mode": "OAuth(PKCE)" if self._p._is_oauth else "api_key",
                    "auth_desc": desc,
                    "commands": [
                        {"name": "/lxdx bind <fc>", "desc": "绑定玩家好友码"},
                        {
                            "name": "/lxdx b50 [fc]",
                            "desc": "Best 50 (最佳35 + 最近15)",
                        },
                        {"name": "/lxdx song <名称/ID>", "desc": "查询歌曲信息"},
                        {
                            "name": "/lxdx login [<码>]",
                            "desc": "OAuth 授权登录 / 完成回调",
                        },
                    ],
                },
                options=JINJA_OPTIONS,
            )
            yield ev.image_result(url)
        else:
            yield ev.plain_result(
                "/lxdx help|bind <fc>|b50 [fc]|song <名称/ID>|login [<码>]"
            )

    # --- /lxdx bind ---

    async def bind(self, ev: AstrMessageEvent):
        """绑定好友码：/lxdx bind <friend_code>。API Key 模式会验证好友码有效性。"""
        args = self._args(ev, 2)
        if not args:
            yield ev.plain_result("用法: /lxdx bind <friend_code>")
            return
        fc = args[0]
        uid = ev.get_sender_id()
        if not self._p._is_oauth:
            try:
                await self._p._client.get_player_info(fc)
            except Exception as e:
                yield ev.plain_result(f"绑定失败: {e}")
                return
        await self._p._st.kv_put(self._p._st.binding_key(uid), fc)
        yield ev.plain_result(f"已绑定好友码: {fc}")

    # --- /lxdx b50 ---

    async def b50(self, ev: AstrMessageEvent):
        """查询 Best 50：/lxdx b50 [friend_code]。API Key 模式必须提供或已绑定 fc。"""
        args = self._args(ev, 2)
        uid = ev.get_sender_id()
        if not self._p._is_oauth:
            fc = (
                args[0]
                if args
                else await self._p._st.kv_get(self._p._st.binding_key(uid))
            )
            if not fc:
                yield ev.plain_result("请先 /lxdx bind <fc> 或 /lxdx b50 <fc>")
                return
            try:
                b50 = await self._p._client.get_b50(fc=fc)
            except LxnsError as e:
                yield ev.plain_result(str(e))
                return
        else:
            u = await self._p._restore_token(ev)
            if not u:
                yield ev.plain_result("请先 /lxdx login 授权")
                return
            try:
                b50 = await self._p._client.get_b50(uid=u)
            except AuthExpiredError as e:
                await self._p._st.kv_delete(self._p._st.token_key(u))
                self._p._auth.remove_tokens(u)
                yield ev.plain_result(str(e))
                return
            except LxnsError as e:
                yield ev.plain_result(str(e))
                return

        t = self._p._tmpl.get("b50")
        if t:
            if self._p._debug:
                logger.info("[lxdx] rendering b50")
            url = await self._p.render_html(
                t,
                {
                    "player_name": b50.player_name,
                    "rating": b50.rating,
                    "friend_code": b50.friend_code,
                    "class_rank": b50.class_rank,
                    "best": self._rec_rows(b50.best),
                    "recent": self._rec_rows(b50.recent),
                },
                options=JINJA_OPTIONS,
            )
            yield ev.image_result(url)
        else:
            yield ev.plain_result(self._b50_text(b50))

    # --- /lxdx song ---

    async def song(self, ev: AstrMessageEvent):
        """查询歌曲信息：/lxdx song <名称 或 ID>。多个匹配时返回列表。"""
        args = self._args(ev, 2)
        if not args:
            yield ev.plain_result("用法: /lxdx song <名称 或 ID>")
            return
        q = " ".join(args)
        uid = ev.get_sender_id()
        if self._p._is_oauth:
            uid = await self._p._restore_token(ev) or ""
        res = await self._lookup(q, uid)
        if not res:
            yield ev.plain_result(f"未找到: {q}")
            return
        if len(res) > 1:
            ns = "\n".join(f"  · {s.title} (ID:{s.display_id})" for s in res[:10])
            yield ev.plain_result(f"多个结果:\n{ns}")
            return
        s = res[0]
        try:
            s = await self._p._client.get_song(s.id)
        except Exception as e:
            logger.warning(f"[lxdx] failed to fetch detailed song info: {e}")
        t = self._p._tmpl.get("song_info")
        if t:
            if self._p._debug:
                logger.info(f"[lxdx] rendering song_info for {s.title}")
            uri = await self._p._am.get_jacket_data_uri(s.id) or ""
            url = await self._p.render_html(
                t,
                {
                    "song": {
                        "title": s.title,
                        "artist": s.artist,
                        "genre": s.genre,
                        "bpm": s.bpm,
                        "display_id": s.display_id,
                        "is_utage": s.is_utage,
                        "version": s.version,
                        "map": s.map,
                    },
                    "jacket_data_uri": uri,
                    "difficulties": self._diff_rows(s),
                },
                options=JINJA_OPTIONS,
            )
            yield ev.image_result(url)
        else:
            yield ev.plain_result(self._song_text(s))

    # --- helpers ---

    @staticmethod
    def _rec_rows(recs: list) -> list:
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
        return [
            {
                "type": d["type"],
                "name": (
                    DIFFICULTY_NAMES[d["difficulty"]]
                    if d["difficulty"] < len(DIFFICULTY_NAMES)
                    else f"LV{d['difficulty']}"
                ),
                "css_class": (
                    DIFFICULTY_NAMES[d["difficulty"]].lower().replace(":", "")
                    if d["difficulty"] < len(DIFFICULTY_NAMES)
                    else ""
                ),
                "level": d["level"],
                "level_value": d["level_value"],
                "note_designer": d["note_designer"],
                "notes": d["notes"],
            }
            for d in song.difficulty_details
        ]

    @staticmethod
    def _b50_text(b50) -> str:
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
        ls = [
            f"{song.title}  [{song.genre}]",
            f"艺术家:{song.artist}  BPM:{song.bpm}  ID:{song.display_id}"
            + (" (宴)" if song.is_utage else ""),
            "",
            "类型  难度        等级  定数  谱师",
        ]
        for d in song.difficulty_details:
            idx = d["difficulty"]
            name = DIFFICULTY_NAMES[idx] if idx < len(DIFFICULTY_NAMES) else f"LV{idx}"
            lv = d["level_value"]
            chart = "DX" if d["type"] == "dx" else "STD"
            designer = d["note_designer"] or "-"
            ls.append(
                f"  {chart:<3} {name:<8} {d['level']:>4}  {lv:.1f}  {designer}"
                if lv > 0
                else f"  {chart:<3} {name:<8} {d['level']:>4}  -  {designer}"
            )
        return "\n".join(ls)
