"""Bounded in-memory NovelAI ZIP/JSON output decoding; never extracts paths."""

import io
import json
import re
import zipfile
from typing import Any
from uuid import uuid4

from .base import ArtifactDescriptor, ImmediateResult, ProviderFailure
from .common import (
    MAX_IMAGE_BYTES,
    MAX_PIXELS,
    MAX_RESPONSE_BYTES,
    decode_image,
    validate_image_bytes,
)

MAX_MEMBERS = 64
MAX_EXPANDED_BYTES = MAX_RESPONSE_BYTES
MAX_COMPRESSION_RATIO = 200
_SAFE_NAME = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*\.(?:png|jpg|jpeg|webp)", re.I)


def decode_output(content: bytes, content_type: str, *, requested_count: int) -> ImmediateResult:
    """Preserve actual count; failures reveal no remote payload or archive names."""
    try:
        if not content or len(content) > MAX_RESPONSE_BYTES:
            raise ValueError
        media = content_type.split(";", 1)[0].strip().lower()
        rows: list[tuple[bytes, str, int, int | None]] = []
        expanded = 0
        if media in {"application/zip", "application/x-zip-compressed"}:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                members = archive.infolist()
                if not members or len(members) > MAX_MEMBERS:
                    raise ValueError
                names: set[str] = set()
                for index, member in enumerate(members):
                    if (
                        not _SAFE_NAME.fullmatch(member.filename)
                        or member.filename in names
                        or member.flag_bits & 1
                        or member.file_size > MAX_IMAGE_BYTES
                        or member.file_size > max(member.compress_size, 1) * MAX_COMPRESSION_RATIO
                    ):
                        raise ValueError
                    names.add(member.filename)
                    expanded += member.file_size
                    if expanded > MAX_EXPANDED_BYTES:
                        raise ValueError
                    with archive.open(member) as stream:
                        image = stream.read(MAX_IMAGE_BYTES + 1)
                    raw, mime = validate_image_bytes(
                        image, max_bytes=MAX_IMAGE_BYTES, max_pixels=MAX_PIXELS
                    )
                    rows.append((raw, mime, index, None))
        elif media == "application/json":
            body = json.loads(content)
            images = body["images"]
            if not isinstance(images, list) or not images or len(images) > MAX_MEMBERS:
                raise ValueError
            indices = set()
            for position, row in enumerate(images):
                raw, mime = decode_image(
                    row["image"], max_bytes=MAX_IMAGE_BYTES, max_pixels=MAX_PIXELS
                )
                expanded += len(raw)
                index, seed = row.get("index", position), row.get("seed")
                if (
                    type(index) is not int
                    or index < 0
                    or index in indices
                    or (seed is not None and (type(seed) is not int or seed < 0))
                    or expanded > MAX_EXPANDED_BYTES
                ):
                    raise ValueError
                indices.add(index)
                rows.append((raw, mime, index, seed))
        else:
            raise ValueError
        artifacts = tuple(
            ArtifactDescriptor(f"art_{uuid4().hex}", index, raw, mime)
            for raw, mime, index, _ in rows
        )
        metadata: dict[str, Any] = {"seeds": [seed for _, _, _, seed in rows]}
        return ImmediateResult(
            requested_count, len(artifacts), artifacts, private_response=metadata
        )
    except Exception:
        raise ProviderFailure("provider_error", accepted_maybe=True) from None
