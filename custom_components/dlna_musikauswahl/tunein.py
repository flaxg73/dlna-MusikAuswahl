"""Small client for the public TuneIn (RadioTime) OPML directory."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse

import aiohttp

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://opml.radiotime.com"
TIMEOUT = aiohttp.ClientTimeout(total=15)
PLAYLIST_SUFFIXES = (".pls", ".m3u", ".m3u8", ".asx")


class TuneInError(Exception):
    """TuneIn could not be reached or answered with an error."""


@dataclass(slots=True)
class RadioEntry:
    """A folder (link) or a station / show of the TuneIn directory."""

    kind: str  # "link" or "audio"
    title: str
    query: str | None = None  # links: query of Browse.ashx
    guide_id: str | None = None  # audio: TuneIn id (s…, p…, t…)
    subtitle: str | None = None
    image: str | None = None
    bitrate: str | None = None
    formats: str | None = None
    section: str | None = None


@dataclass(slots=True)
class RadioPage:
    """A page of the directory."""

    title: str
    entries: list[RadioEntry] = field(default_factory=list)


def https(url: str | None) -> str | None:
    """TuneIn logos are available via https; avoids mixed content in the UI."""
    if url and url.startswith("http://") and "tunein.com" in url:
        return "https://" + url[len("http://") :]
    return url


def _query_of(url: str) -> str:
    params = [(k, v) for k, v in parse_qsl(urlparse(url).query) if k not in ("render", "formats")]
    return urlencode(params)


def _parse_outlines(outlines: list[dict[str, Any]], section: str | None, out: list[RadioEntry]) -> None:
    for item in outlines:
        children = item.get("children")
        text = str(item.get("text") or "")
        if children:
            _parse_outlines(children, text or section, out)
            continue
        kind = item.get("type")
        if kind == "link" and item.get("URL"):
            out.append(
                RadioEntry(
                    kind="link",
                    title=text,
                    query=_query_of(item["URL"]),
                    subtitle=item.get("subtext"),
                    image=https(item.get("image")),
                    section=section,
                )
            )
        elif kind == "audio" and (item.get("guide_id") or item.get("URL")):
            guide_id = item.get("guide_id") or dict(parse_qsl(urlparse(item["URL"]).query)).get("id")
            if not guide_id:
                continue
            out.append(
                RadioEntry(
                    kind="audio",
                    title=text,
                    guide_id=guide_id,
                    subtitle=item.get("subtext") or item.get("playing"),
                    image=https(item.get("image")),
                    bitrate=item.get("bitrate"),
                    formats=item.get("formats"),
                    section=section,
                )
            )


class TuneInClient:
    """Browse, search and resolve TuneIn stations."""

    def __init__(self, session: aiohttp.ClientSession, language: str = "de") -> None:
        """Use the Home Assistant client session."""
        self._session = session
        self._language = language

    async def _get_json(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        params = {**params, "render": "json", "formats": "mp3,aac,ogg,flac,hls"}
        try:
            async with self._session.get(
                f"{BASE_URL}/{path}",
                params=params,
                headers={"Accept-Language": self._language},
                timeout=TIMEOUT,
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise TuneInError(f"TuneIn not reachable: {err}") from err
        status = str(data.get("head", {}).get("status", "200"))
        if status != "200":
            raise TuneInError(data.get("head", {}).get("fault") or f"TuneIn status {status}")
        return data

    async def browse(self, query: str | None = None) -> RadioPage:
        """Browse a folder of the directory (root when query is empty)."""
        data = await self._get_json("Browse.ashx", dict(parse_qsl(query or "")))
        entries: list[RadioEntry] = []
        _parse_outlines(data.get("body") or [], None, entries)
        return RadioPage(str(data.get("head", {}).get("title") or "TuneIn"), entries)

    async def search(self, text: str) -> list[RadioEntry]:
        """Search stations and shows."""
        data = await self._get_json("Search.ashx", {"query": text})
        entries: list[RadioEntry] = []
        _parse_outlines(data.get("body") or [], None, entries)
        return [e for e in entries if e.kind == "audio"]

    async def describe(self, guide_id: str) -> RadioEntry | None:
        """Name and logo of a station."""
        try:
            data = await self._get_json("Describe.ashx", {"id": guide_id})
        except TuneInError:
            return None
        for item in data.get("body") or []:
            if item.get("element") == "station" or item.get("name"):
                return RadioEntry(
                    kind="audio",
                    title=str(item.get("name") or guide_id),
                    guide_id=guide_id,
                    image=https(item.get("logo")),
                )
        return None

    async def resolve(self, guide_id: str) -> str:
        """Return a stream URL the renderer can play."""
        data = await self._get_json("Tune.ashx", {"id": guide_id})
        streams = [s for s in data.get("body") or [] if s.get("url")]
        if not streams:
            raise TuneInError(f"No stream for {guide_id}")
        direct = [s for s in streams if s.get("is_direct")]
        stream = (direct or streams)[0]
        url = str(stream["url"])
        if not stream.get("is_direct") and urlparse(url).path.lower().endswith(PLAYLIST_SUFFIXES):
            url = await self._first_playlist_entry(url) or url
        return url

    async def _first_playlist_entry(self, url: str) -> str | None:
        try:
            async with self._session.get(url, timeout=TIMEOUT) as resp:
                text = await resp.text(errors="replace")
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.debug("Cannot read playlist %s: %s", url, err)
            return None
        for line in text.splitlines():
            line = line.strip()
            if line.lower().startswith("file") and "=" in line:
                line = line.split("=", 1)[1].strip()
            if line.startswith(("http://", "https://")):
                return line
        return None
