from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from astrbot.api.star import Star


class StorageManager:
    def __init__(
        self,
        plugin: Star,
        data_dir: str,
    ):
        self._plugin = plugin
        self._data_dir = data_dir
        self.assets_dir = f"{data_dir}/plugin_data/astrbot_plugin_lxdx/assets"
        self.cache_dir = f"{data_dir}/plugin_data/astrbot_plugin_lxdx/cache"

    def ensure_dirs(self) -> None:
        os.makedirs(self.assets_dir, exist_ok=True)
        os.makedirs(self.cache_dir, exist_ok=True)

    async def kv_put(self, key: str, value: Any) -> None:
        await self._plugin.put_kv_data(key, value)

    async def kv_get(self, key: str, default: Any = None) -> Any:
        try:
            result = await self._plugin.get_kv_data(key)
            if result is None:
                return default
            return result
        except Exception:
            return default

    async def kv_delete(self, key: str) -> None:
        await self._plugin.delete_kv_data(key)

    def binding_key(self, user_id: str) -> str:
        return f"binding:{user_id}"

    def token_key(self, user_id: str) -> str:
        return f"token:{user_id}"

    def pkce_key(self, user_id: str) -> str:
        return f"pkce:{user_id}"
