"""插件存储管理：文件目录路径和 KV key 命名。KV 操作直接委托给 Star 实例的 put_kv_data / get_kv_data / delete_kv_data。

get_kv_data(key, False) — 第二个参数是布尔值，非默认回退值。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from astrbot.api.star import Star


class StorageManager:
    """管理文件目录路径、确保目录存在、以及 KV 操作的 key 命名。"""

    def __init__(self, plugin: Star, data_dir: str):
        self._plugin = plugin
        self.assets_dir = f"{data_dir}/plugin_data/astrbot_plugin_lxdx/assets"
        self.cache_dir = f"{data_dir}/plugin_data/astrbot_plugin_lxdx/cache"

    def ensure_dirs(self) -> None:
        """确保 assets 和 cache 目录存在。"""
        os.makedirs(self.assets_dir, exist_ok=True)
        os.makedirs(self.cache_dir, exist_ok=True)

    async def kv_put(self, key: str, value: Any) -> None:
        await self._plugin.put_kv_data(key, value)

    async def kv_get(self, key: str) -> Any:
        try:
            return await self._plugin.get_kv_data(key, False)
        except Exception:
            return None

    async def kv_delete(self, key: str) -> None:
        await self._plugin.delete_kv_data(key)

    def binding_key(self, uid: str) -> str:
        return f"binding:{uid}"

    def chu_binding_key(self, uid: str) -> str:
        return f"chubind:{uid}"

    def token_key(self, uid: str) -> str:
        return f"token:{uid}"
