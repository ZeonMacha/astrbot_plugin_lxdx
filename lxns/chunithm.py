"""中二节奏 查分功能"""

from astrbot.api.event import AstrMessageEvent
from astrbot.api import logger

from .models import (
    LxnsError,
    AuthExpiredError,
    CHU_DIFFICULTY_NAMES,
    CHU_DIFFICULTY_SHORT,
    JINJA_OPTIONS,
)


class ChunithmHandler:
    def __init__(self, plugin):
        self._p = plugin

    @staticmethod
    def _args(ev: AstrMessageEvent, n: int) -> list:
        return ev.message_str.strip().split()[n:]

    # --- song helpers ---

    async def _ensure_chu_songs(self, uid: str = ""):
        if self._p._chu_sdb.loaded:
            return
        try:
            logger.info("[lxdx] fetching Chunithm song list...")
            res = await self._p._chu_client.get_song_list(uid)
            self._p._chu_sdb.load_from_list(res.songs)
            self._p._chu_sdb.save_cache()
            logger.info(f"[lxdx] loaded {self._p._chu_sdb.song_count} Chunithm songs")
        except Exception as e:
            logger.warning(f"[lxdx] Chunithm song list fetch failed: {e}")

    async def _chu_lookup(self, q: str, uid: str = "") -> list:
        await self._ensure_chu_songs(uid)
        if not self._p._chu_sdb.loaded:
            return []
        try:
            sid = int(q)
            if s := self._p._chu_sdb.get_by_id(sid):
                return [s]
            return []
        except ValueError:
            return self._p._chu_sdb.get_by_title(q)

    # --- static helpers ---

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

    # --- /lxchu help ---

    async def help(self, ev: AstrMessageEvent):
        """显示中二节奏命令列表和当前授权模式。优先使用 HTML 模板渲染图片，无模板时回退纯文本。"""
        t = self._p._tmpl.get("chunithm_help")
        if t:
            if self._p._debug:
                logger.info("[lxdx] rendering help (Chunithm)")
            desc = (
                "已绑定开发者 API Key"
                if not self._p._is_oauth
                else "OAuth(PKCE) 交互授权"
            )
            url = await self._p.render_html(
                t,
                {
                    "plugin_display_name": "落雪DX (中二节奏)",
                    "plugin_version": "1.1.0",
                    "auth_mode": "OAuth(PKCE)" if self._p._is_oauth else "api_key",
                    "auth_desc": desc,
                    "commands": [
                        {"name": "/lxchu bind <fc>", "desc": "绑定玩家好友码"},
                        {
                            "name": "/lxchu bests [fc]",
                            "desc": "Best 30 + Selection 10 + New 20",
                        },
                        {
                            "name": "/lxchu recent [fc]",
                            "desc": "Recent 50 最近游玩",
                        },
                        {"name": "/lxchu song <名称/ID>", "desc": "查询歌曲信息"},
                        {
                            "name": "/lxchu login [<码>]",
                            "desc": "OAuth 授权登录 / 完成回调",
                        },
                    ],
                },
                options=JINJA_OPTIONS,
            )
            yield ev.image_result(url)
        else:
            yield ev.plain_result(
                "/lxchu help|bind <fc>|bests [fc]|recent [fc]|song <名称/ID>|login [<码>]"
            )

    # --- /lxchu bind ---

    async def bind(self, ev: AstrMessageEvent):
        """绑定中二节奏好友码：/lxchu bind <friend_code>。API Key 模式会验证好友码有效性。"""
        args = self._args(ev, 2)
        if not args:
            yield ev.plain_result("用法: /lxchu bind <friend_code>")
            return
        fc = args[0]
        uid = ev.get_sender_id()
        if not self._p._is_oauth:
            try:
                await self._p._chu_client.get_player_info(fc=int(fc))
            except Exception as e:
                yield ev.plain_result(f"绑定失败: {e}")
                return
        await self._p._st.kv_put(self._p._st.chu_binding_key(uid), fc)
        yield ev.plain_result(f"已绑定中二节奏好友码: {fc}")

    # --- /lxchu bests ---

    async def bests(self, ev: AstrMessageEvent):
        """查询中二节奏 Best 30 + Selection 10 + New 20：/lxchu bests [friend_code]。"""
        args = self._args(ev, 2)
        uid = ev.get_sender_id()
        if not self._p._is_oauth:
            fc_raw = (
                args[0]
                if args
                else await self._p._st.kv_get(self._p._st.chu_binding_key(uid))
            )
            if not fc_raw:
                yield ev.plain_result("请先 /lxchu bind <fc> 或 /lxchu bests <fc>")
                return
            fc = int(fc_raw)
            try:
                bests = await self._p._chu_client.get_bests(fc=fc)
                pi = await self._p._chu_client.get_player_info(fc=fc)
            except LxnsError as e:
                yield ev.plain_result(str(e))
                return
        else:
            u = await self._p._restore_token(ev)
            if not u:
                yield ev.plain_result("请先 /lxchu login 授权")
                return
            try:
                bests = await self._p._chu_client.get_bests(uid=u)
                pi = await self._p._chu_client.get_player_info(uid=u)
            except AuthExpiredError as e:
                await self._p._st.kv_delete(self._p._st.token_key(u))
                self._p._auth.remove_tokens(u)
                yield ev.plain_result(str(e))
                return
            except LxnsError as e:
                yield ev.plain_result(str(e))
                return

        t = self._p._tmpl.get("chunithm_bests")
        if t:
            if self._p._debug:
                logger.info("[lxdx] rendering chunithm_bests")
            url = await self._p.render_html(
                t,
                {
                    "player_name": pi.name,
                    "rating": pi.rating,
                    "friend_code": pi.friend_code,
                    "bests": self._chu_score_rows(bests.bests),
                    "selections": self._chu_score_rows(bests.selections),
                    "new_bests": self._chu_score_rows(bests.new_bests),
                },
                options=JINJA_OPTIONS,
            )
            yield ev.image_result(url)
        else:
            yield ev.plain_result(self._chu_bests_text(pi, bests))

    # --- /lxchu recent ---

    async def recent(self, ev: AstrMessageEvent):
        """查询中二节奏 Recent 50：/lxchu recent [friend_code]。"""
        args = self._args(ev, 2)
        uid = ev.get_sender_id()
        if not self._p._is_oauth:
            fc_raw = (
                args[0]
                if args
                else await self._p._st.kv_get(self._p._st.chu_binding_key(uid))
            )
            if not fc_raw:
                yield ev.plain_result("请先 /lxchu bind <fc> 或 /lxchu recent <fc>")
                return
            fc = int(fc_raw)
            try:
                recents = await self._p._chu_client.get_recents(fc=fc)
                pi = await self._p._chu_client.get_player_info(fc=fc)
            except LxnsError as e:
                yield ev.plain_result(str(e))
                return
        else:
            u = await self._p._restore_token(ev)
            if not u:
                yield ev.plain_result("请先 /lxchu login 授权")
                return
            try:
                recents = await self._p._chu_client.get_recents(uid=u)
                pi = await self._p._chu_client.get_player_info(uid=u)
            except AuthExpiredError as e:
                await self._p._st.kv_delete(self._p._st.token_key(u))
                self._p._auth.remove_tokens(u)
                yield ev.plain_result(str(e))
                return
            except LxnsError as e:
                yield ev.plain_result(str(e))
                return

        t = self._p._tmpl.get("chunithm_recent")
        if t:
            if self._p._debug:
                logger.info("[lxdx] rendering chunithm_recent")
            url = await self._p.render_html(
                t,
                {
                    "player_name": pi.name,
                    "friend_code": pi.friend_code,
                    "recent": self._chu_score_rows(recents),
                },
                options=JINJA_OPTIONS,
            )
            yield ev.image_result(url)
        else:
            yield ev.plain_result(self._chu_recent_text(pi, recents))

    # --- /lxchu song ---

    async def song(self, ev: AstrMessageEvent):
        """查询中二节奏歌曲信息：/lxchu song <名称 或 ID>。多个匹配时返回列表。"""
        args = self._args(ev, 2)
        if not args:
            yield ev.plain_result("用法: /lxchu song <名称 或 ID>")
            return
        q = " ".join(args)
        uid = ev.get_sender_id()
        if self._p._is_oauth:
            uid = await self._p._restore_token(ev) or ""
        res = await self._chu_lookup(q, uid)
        if not res:
            yield ev.plain_result(f"未找到: {q}")
            return
        if len(res) > 1:
            ns = "\n".join(f"  · {s.title} (ID:{s.id})" for s in res[:10])
            yield ev.plain_result(f"多个结果:\n{ns}")
            return
        s = res[0]
        t = self._p._tmpl.get("chunithm_song_info")
        if t:
            if self._p._debug:
                logger.info(f"[lxdx] rendering chunithm_song_info for {s.title}")
            uri = await self._p._am.get_chunithm_jacket_data_uri(s.id) or ""
            url = await self._p.render_html(
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
                    "jacket_data_uri": uri,
                    "difficulties": self._chu_diff_rows(s),
                },
                options=JINJA_OPTIONS,
            )
            yield ev.image_result(url)
        else:
            yield ev.plain_result(self._chu_song_text(s))
