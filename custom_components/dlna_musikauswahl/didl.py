"""Helpers for DIDL-Lite objects returned by DLNA media servers."""

from __future__ import annotations

from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

from didl_lite import didl_lite

NS_DIDL = "urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/"
NS_DC = "http://purl.org/dc/elements/1.1/"
NS_UPNP = "urn:schemas-upnp-org:metadata-1-0/upnp/"
NS_DLNA = "urn:schemas-dlna-org:metadata-1-0/"

_PREFIXES: dict[str, str] = {NS_DIDL: "", NS_DC: "dc", NS_UPNP: "upnp", NS_DLNA: "dlna"}

_DIDL_HEADER = (
    f'<DIDL-Lite xmlns="{NS_DIDL}" xmlns:dc="{NS_DC}" '
    f'xmlns:upnp="{NS_UPNP}" xmlns:dlna="{NS_DLNA}">'
)
_DIDL_FOOTER = "</DIDL-Lite>"

AUDIO_MIME_PREFIXES = ("audio/", "application/ogg", "application/flac")


def upnp_class_of(obj: didl_lite.DidlObject) -> str:
    """Return the normalised (stripped, lower case) UPnP class of an object."""
    return str(getattr(obj, "upnp_class", "") or "").strip().lower()


def is_container(obj: didl_lite.DidlObject) -> bool:
    """Return True if the object is a container (folder, album, ...)."""
    return isinstance(obj, didl_lite.Container) or upnp_class_of(obj).startswith(
        "object.container"
    )


def describe(obj: didl_lite.DidlObject) -> str:
    """Short description of an object for log messages."""
    resources = [
        f"{res.protocol_info!r} -> {res.uri!r}" for res in getattr(obj, "res", None) or []
    ]
    return (
        f"type={type(obj).__name__} id={getattr(obj, 'id', None)!r} "
        f"class={getattr(obj, 'upnp_class', None)!r} res={resources}"
    )


def pick_audio_resource(obj: didl_lite.DidlObject) -> didl_lite.Resource | None:
    """Return the first streamable audio resource of an item."""
    fallback: didl_lite.Resource | None = None
    for res in getattr(obj, "res", None) or []:
        if not res.uri:
            continue
        parts = (res.protocol_info or "").strip().split(":")
        protocol = parts[0].strip().lower() if parts else ""
        mime = parts[2].strip().lower() if len(parts) > 2 else ""
        if protocol not in ("http-get", "http", "*", ""):
            continue
        if mime.startswith(AUDIO_MIME_PREFIXES):
            return res
        if fallback is None and not mime.startswith(("image/", "video/", "text/")):
            fallback = res
    if fallback is not None and upnp_class_of(obj).startswith("object.item.audioitem"):
        return fallback
    return None


def is_audio_class_item(obj: didl_lite.DidlObject) -> bool:
    """Return True for audio items, even if the server sent no <res> for them."""
    return not is_container(obj) and upnp_class_of(obj).startswith("object.item.audioitem")


def is_playable_audio_item(obj: didl_lite.DidlObject) -> bool:
    """Return True if the object is an item with an audio stream."""
    return not is_container(obj) and pick_audio_resource(obj) is not None


def album_art_uri(obj: didl_lite.DidlObject) -> str | None:
    """Return the album art URI of an object, if any."""
    art = getattr(obj, "album_art_uri", None)
    if isinstance(art, str) and art.strip():
        return art.strip()
    # Some servers deliver the cover as an image resource
    for res in getattr(obj, "res", None) or []:
        parts = (res.protocol_info or "").split(":")
        if len(parts) > 2 and parts[2].lower().startswith("image/") and res.uri:
            return res.uri
    return None


def _split_tag(tag: str) -> tuple[str, str]:
    if tag.startswith("{"):
        namespace, _, local = tag[1:].partition("}")
        return namespace, local
    return NS_DIDL, tag


def _serialize(element: ET.Element) -> str:
    namespace, local = _split_tag(element.tag)
    if namespace not in _PREFIXES:
        # unknown vendor extensions are dropped, renderers don't need them
        return ""
    prefix = _PREFIXES[namespace]
    name = f"{prefix}:{local}" if prefix else local

    attributes: list[str] = []
    for key, value in element.attrib.items():
        if key.startswith("{"):
            attr_ns, attr_local = _split_tag(key)
            attr_prefix = _PREFIXES.get(attr_ns)
            if not attr_prefix:
                continue
            key = f"{attr_prefix}:{attr_local}"
        attributes.append(f" {key}={quoteattr(value)}")

    children = "".join(_serialize(child) for child in element)
    text = escape(element.text) if element.text else ""
    return f"<{name}{''.join(attributes)}>{text}{children}</{name}>"


def item_metadata(obj: didl_lite.DidlObject) -> str:
    """Return a DIDL-Lite document describing a single item.

    The original XML of the media server is used, so that all details
    (duration, sample rate, bit depth, cover art, ...) reach the renderer.
    """
    if obj.xml_el is not None:
        return _DIDL_HEADER + _serialize(obj.xml_el) + _DIDL_FOOTER
    return didl_lite.to_xml_string(obj).decode("utf-8")


def parse_metadata(metadata: str | None) -> didl_lite.DidlObject | None:
    """Parse a DIDL-Lite document (e.g. from the Linn playlist) into an object."""
    if not metadata:
        return None
    try:
        objects = didl_lite.from_xml_string(metadata, strict=False)
    except (ET.ParseError, didl_lite.DidlLiteException, ValueError):
        return None
    for obj in objects:
        if isinstance(obj, didl_lite.DidlObject):
            return obj
    return None


def parse_duration(value: str | None) -> int | None:
    """Parse a DIDL duration string (H+:MM:SS[.F+]) into seconds."""
    if not value:
        return None
    try:
        parts = value.split(".")[0].split(":")
        seconds = 0
        for part in parts:
            seconds = seconds * 60 + int(part)
    except ValueError:
        return None
    return seconds
