"""静态资源下载：jacket 封面图片，信号量控制并发（最大 3 同时下载）。"""

import asyncio
from pathlib import Path

from astrbot.api import logger


class AssetManager:
    """管理 jacket 封面本地缓存下载。下载的图片存放于 assets_dir/jacket_{id}.png。"""

    CDN = "https://maimai.lxns.net"

    def __init__(self, assets_dir: str):
        self._dir = Path(assets_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._sem = asyncio.Semaphore(3)  # 并发限制：最多 3 个同时下载

    def _exists(self, name: str) -> bool:
        """检查本地是否已有该文件。"""
        return (self._dir / name).exists()

    async def download(self, url: str, name: str) -> str:
        """下载单个资源，已存在则跳过。返回本地路径，失败返回空字符串。"""
        local = self._dir / name
        if local.exists(): return str(local)
        async with self._sem:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as c:
                    r = await c.get(url); r.raise_for_status()
                    local.write_bytes(r.content)
                    return str(local)
            except Exception as e:
                logger.warning(f"[lxdx] download {name} failed: {e}")
                return ""

    async def download_batch(self, urls: dict[str, str]) -> dict[str, str]:
        """批量下载（文件名 → URL），并发执行，异常项返回空字符串。"""
        tasks = [self.download(u, n) for n, u in urls.items()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return {n: ("" if isinstance(r, Exception) else r) for n, r in zip(urls, results)}

    async def download_jacket(self, song_id: int) -> str:
        """下载指定歌曲 ID 的封面图。"""
        return await self.download(f"{self.CDN}/api/v0/maimai/asset/jacket/{song_id}", f"jacket_{song_id}.png")

    async def download_jackets_batch(self, song_ids: list[int]) -> dict[int, str]:
        """批量下载封面图（song_id 列表），返回 {song_id: 本地路径} 字典。"""
        tasks = [asyncio.create_task(self.download_jacket(s)) for s in song_ids]
        results = {s: ("" if isinstance(r, Exception) else r)
                   for s, r in zip(song_ids, await asyncio.gather(*tasks, return_exceptions=True))}
        return results
