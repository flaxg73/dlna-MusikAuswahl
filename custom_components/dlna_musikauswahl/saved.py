"""Playlists saved in Home Assistant (the Linn keeps only one playlist)."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

STORAGE_VERSION = 1


class SavedPlaylists:
    """Named playlists (uri + DIDL metadata per track) per Linn renderer."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Create the store; call async_load before use."""
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.playlists.{entry_id}"
        )
        self._playlists: dict[str, dict[str, Any]] = {}

    async def async_load(self) -> None:
        """Read the stored playlists."""
        data = await self._store.async_load() or {}
        self._playlists = dict(data.get("playlists", {}))

    async def async_remove(self) -> None:
        """Delete the storage file (integration removed)."""
        await self._store.async_remove()

    def summary(self) -> list[dict[str, Any]]:
        """Saved playlists without their tracks, newest first."""
        items = [
            {
                "name": name,
                "count": len(pl["tracks"]),
                "duration": pl.get("duration"),
                "image": pl.get("image"),
                "saved_at": pl.get("saved_at"),
            }
            for name, pl in self._playlists.items()
        ]
        return sorted(items, key=lambda item: item["saved_at"] or "", reverse=True)

    def tracks(self, name: str) -> list[dict[str, str]] | None:
        """Tracks (uri, metadata) of a saved playlist."""
        playlist = self._playlists.get(name)
        return list(playlist["tracks"]) if playlist else None

    async def async_save(
        self,
        name: str,
        tracks: list[dict[str, str]],
        duration: int | None,
        image: str | None,
    ) -> None:
        """Store (or overwrite) a playlist."""
        self._playlists[name] = {
            "tracks": tracks,
            "duration": duration,
            "image": image,
            "saved_at": dt_util.utcnow().isoformat(),
        }
        await self._store.async_save({"playlists": self._playlists})

    async def async_delete(self, name: str) -> bool:
        """Delete a playlist; returns False if it did not exist."""
        if self._playlists.pop(name, None) is None:
            return False
        await self._store.async_save({"playlists": self._playlists})
        return True
