"""Cache of the Linn (OpenHome) playlist."""

from __future__ import annotations

from dataclasses import dataclass

from .didl import album_art_uri, parse_duration, parse_metadata
from .openhome import OpenHomeRenderer, PlaylistEntry

READ_CHUNK = 100


@dataclass(slots=True)
class QueueEntry:
    """A track of the renderer playlist with parsed metadata."""

    id: int
    uri: str
    metadata: str
    title: str
    artist: str | None
    album: str | None
    duration: int | None
    art: str | None

    @classmethod
    def from_entry(cls, entry: PlaylistEntry) -> QueueEntry:
        """Parse the DIDL-Lite metadata of a playlist entry."""
        obj = parse_metadata(entry.metadata)
        title = artist = album = art = None
        duration = None
        if obj is not None:
            title = getattr(obj, "title", None)
            artist = getattr(obj, "artist", None) or getattr(obj, "creator", None)
            album = getattr(obj, "album", None)
            art = album_art_uri(obj)
            for res in getattr(obj, "res", None) or []:
                if duration := parse_duration(res.duration):
                    break
        if not title:
            title = entry.uri.rsplit("/", 1)[-1] or f"#{entry.id}"
        return cls(entry.id, entry.uri, entry.metadata, title, artist, album, duration, art)


class PlaylistCache:
    """Mirror of the renderer playlist, refreshed incrementally."""

    def __init__(self) -> None:
        """Start empty."""
        self.ids: list[int] = []
        self.entries: dict[int, QueueEntry] = {}
        self.version = 0

    def __len__(self) -> int:
        """Number of tracks."""
        return len(self.ids)

    def ordered(self) -> list[QueueEntry]:
        """Entries in playlist order."""
        return [self.entries[i] for i in self.ids if i in self.entries]

    def index_of(self, track_id: int) -> int | None:
        """0-based position of a track."""
        try:
            return self.ids.index(track_id)
        except ValueError:
            return None

    def invalidate(self) -> None:
        """Force a reload of the id list on the next refresh."""
        self.ids = []

    async def refresh(self, renderer: OpenHomeRenderer) -> bool:
        """Reload the playlist if it changed. Returns True on change."""
        ids = await renderer.ids()
        if ids == self.ids and all(i in self.entries for i in ids):
            return False
        missing = [i for i in ids if i not in self.entries]
        for start in range(0, len(missing), READ_CHUNK):
            for entry in await renderer.read_list(missing[start : start + READ_CHUNK]):
                self.entries[entry.id] = QueueEntry.from_entry(entry)
        keep = set(ids)
        self.entries = {i: e for i, e in self.entries.items() if i in keep}
        self.ids = [i for i in ids if i in self.entries]
        self.version += 1
        return True
