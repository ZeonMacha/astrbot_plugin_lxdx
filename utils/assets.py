"""静态资源下载：jacket 封面图片，信号量控制并发（最大 3 同时下载）。"""

import asyncio
import base64
from pathlib import Path

from astrbot.api import logger

_DATA_URI_PREFIX = "data:image/png;base64,"


def _get_filetype():
    try:
        import filetype

        return filetype
    except ImportError:
        raise ImportError("缺少 filetype 依赖，请安装: pip install filetype")


def _is_valid_png(data: bytes) -> bool:
    ft = _get_filetype()
    kind = ft.guess(data)
    return kind is not None and kind.mime == "image/png"


class AssetManager:
    """管理 jacket 封面本地缓存下载。下载的图片存放于 assets_dir/jacket_{id}.png。"""

    def __init__(self, assets_dir: str, debug: bool = False):
        self._dir = Path(assets_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._sem = asyncio.Semaphore(3)
        self._debug = debug

    def _exists(self, name: str) -> bool:
        """检查本地是否已有该文件。"""
        return (self._dir / name).exists()

    async def download(self, url: str, name: str) -> str:
        """下载单个资源，已存在则跳过。返回本地路径，失败返回空字符串。"""
        local = self._dir / name
        if local.exists() and _is_valid_png(local.read_bytes()):
            if self._debug:
                logger.info(f"[lxdx] cache hit {name}")
            return str(local)
        if local.exists():
            logger.warning(f"[lxdx] cache {name} invalid PNG, re-downloading")
            local.unlink(missing_ok=True)
        async with self._sem:
            try:
                import httpx

                if self._debug:
                    logger.info(f"[lxdx] downloading {name} from {url}")
                headers = {
                    "User-Agent": "AstrBot-Lxdx/1.0",
                }
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(30), headers=headers
                ) as c:
                    r = await c.get(url)
                    r.raise_for_status()
                    if not _is_valid_png(r.content):
                        logger.warning(
                            f"[lxdx] download {name} rejected: not a valid PNG"
                        )
                        return ""
                    local.write_bytes(r.content)
                    if self._debug:
                        logger.info(
                            f"[lxdx] downloaded {name} ({len(r.content)} bytes)"
                        )
                    return str(local)
            except Exception as e:
                logger.warning(f"[lxdx] download {name} failed: {e}")
                return ""

    async def download_batch(self, urls: dict[str, str]) -> dict[str, str]:
        """批量下载（文件名 → URL），并发执行，异常项返回空字符串。"""
        tasks = [self.download(u, n) for n, u in urls.items()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return {
            n: ("" if isinstance(r, Exception) else r) for n, r in zip(urls, results)
        }

    async def download_jacket(self, song_id: int) -> str:
        """下载指定歌曲 ID 的封面图（舞萌 DX）。"""
        return await self.download(
            f"https://assets2.lxns.net/maimai/jacket/{song_id}.png",
            f"jacket_{song_id}.png",
        )

    async def download_chunithm_jacket(self, song_id: int) -> str:
        """下载指定歌曲 ID 的封面图（中二节奏）。"""
        return await self.download(
            f"https://assets2.lxns.net/chunithm/jacket/{song_id}.png",
            f"chu_jacket_{song_id}.png",
        )

    def serialize_to_data_uri(self, name: str) -> str:
        """读取缓存文件并返回 base64 Data URI。仅接受有效 PNG，否则清除缓存并返回空字符串。"""
        local = self._dir / name
        if not local.exists():
            return ""
        data = local.read_bytes()
        if not _is_valid_png(data):
            logger.warning(f"[lxdx] {name} not a valid PNG, clearing cache")
            local.unlink(missing_ok=True)
            return ""
        return _DATA_URI_PREFIX + base64.b64encode(data).decode()

    async def get_jacket_data_uri(self, song_id: int) -> str:
        """下载并返回舞萌封面 base64 Data URI。"""
        path = await self.download_jacket(song_id)
        return self.serialize_to_data_uri(Path(path).name) if path else ""

    async def get_chunithm_jacket_data_uri(self, song_id: int) -> str:
        """下载并返回中二节奏封面 base64 Data URI。"""
        path = await self.download_chunithm_jacket(song_id)
        return self.serialize_to_data_uri(Path(path).name) if path else ""

    async def download_jackets_batch(self, song_ids: list[int]) -> dict[int, str]:
        """批量下载封面图（song_id 列表），返回 {song_id: 本地路径} 字典。"""
        tasks = [asyncio.create_task(self.download_jacket(s)) for s in song_ids]
        results = {
            s: ("" if isinstance(r, Exception) else r)
            for s, r in zip(
                song_ids, await asyncio.gather(*tasks, return_exceptions=True)
            )
        }
        return results
