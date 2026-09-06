"""Recognising and addressing product pictures.

The content type a browser sends with an upload is a claim, not evidence. It
is trivially forged, and trusting it means the API will happily hand back
whatever it was given, labelled however the uploader chose - which is how a
file store becomes a way to serve arbitrary content from your own origin.

So the format is decided here, from the file's own leading bytes, and the type
stored is the one the content actually has.
"""

from __future__ import annotations

import uuid
from typing import Final

# The prefix each format begins with. These are fixed by the file formats
# themselves, which is what makes them worth checking.
_JPEG: Final = b"\xff\xd8\xff"
_PNG: Final = b"\x89PNG\r\n\x1a\n"
_GIF87: Final = b"GIF87a"
_GIF89: Final = b"GIF89a"
_RIFF: Final = b"RIFF"
_WEBP: Final = b"WEBP"

#: Formats every browser renders, and nothing else. Notably absent: SVG, which
#: is a document that can carry script, not a picture.
SUPPORTED_CONTENT_TYPES: Final = ("image/jpeg", "image/png", "image/gif", "image/webp")

#: Where the API serves pictures from. Kept beside the code that builds the
#: URL; a test asserts it still matches ``Settings.api_v1_prefix``.
API_PREFIX: Final = "/api/v1"


def detect_image_type(data: bytes) -> str | None:
    """Return the content type implied by the bytes, or None if unsupported."""
    if data.startswith(_JPEG):
        return "image/jpeg"
    if data.startswith(_PNG):
        return "image/png"
    if data.startswith((_GIF87, _GIF89)):
        return "image/gif"
    # WebP is a RIFF container: "RIFF", four bytes of length, then "WEBP".
    if data.startswith(_RIFF) and data[8:12] == _WEBP:
        return "image/webp"
    return None


def image_url_for(product_id: uuid.UUID, content_type: str | None) -> str | None:
    """The URL a client should use, or None when there is no picture.

    A path rather than an absolute URL: the frontend is served from the same
    origin (through a proxy in development), so hardcoding a host here would
    only be wrong in some deployment.
    """
    if content_type is None:
        return None
    return f"{API_PREFIX}/products/{product_id}/image"
