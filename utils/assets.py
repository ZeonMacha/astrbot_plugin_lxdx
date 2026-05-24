import asyncio
import os
from pathlib import Path

import httpx

from astrbot.api import logger


class AssetManager:
    CDN_BASE = "https://maimai.lxns.net"

    def __init__(self, assets_dir: str):
        self._assets_dir = Path(assets_dir)
        self._assets_dir.mkdir(parents=True, exist_ok=True)
        self._semaphore = asyncio.Semaphore(3)

    def local_path(self, asset_name: str) -> Path:
        return self._assets_dir / asset_name

    def asset_exists(self, asset_name: str) -> bool:
        return self.local_path(asset_name).exists()

    async def download_asset(self, url: str, asset_name: str) -> str:
        local = self.local_path(asset_name)
        if local.exists():
            return str(local)
        async with self._semaphore:
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    local.write_bytes(resp.content)
                    return str(local)
            except Exception as e:
                logger.warning(f"Failed to download asset {asset_name}: {e}")
                return ""

    async def download_assets_batch(self, urls: dict[str, str]) -> dict[str, str]:
        tasks = [self.download_asset(url, name) for name, url in urls.items()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        result_map = {}
        for name, result in zip(urls.keys(), results):
            if isinstance(result, Exception):
                result_map[name] = ""
            else:
                result_map[name] = result
        return result_map

    async def download_jacket(self, song_id: int) -> str:
        url = f"{self.CDN_BASE}/api/v0/maimai/asset/jacket/{song_id}"
        name = f"jacket_{song_id}.png"
        return await self.download_asset(url, name)

    async def download_jackets_batch(self, song_ids: list[int]) -> dict[int, str]:
        urls = {str(sid): f"{self.CDN_BASE}/api/v0/maimai/asset/jacket/{sid}" for sid in song_ids}
        names = {str(sid): f"jacket_{sid}.png" for sid in song_ids}

        async def download_one(sid: int) -> str:
            url = f"{self.CDN_BASE}/api/v0/maimai/asset/jacket/{sid}"
            name = f"jacket_{sid}.png"
            return await self.download_asset(url, name)

        tasks = [download_one(sid) for sid in song_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        result_map: dict[int, str] = {}
        for sid, result in zip(song_ids, results):
            if isinstance(result, Exception):
                result_map[sid] = ""
            else:
                result_map[sid] = result
        return result_map
