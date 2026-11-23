"""Uploaded files and their rendered pages on local disk, addressed by sha256.

Real client documents live here, so the folder is gitignored and never logged (design
rule 12). A filename from the upload never becomes a path: only the sha256 does.
"""

import re
from pathlib import Path

from app.files import write_atomically
from app.llm.client import ImageInput

SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
PAGE_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg"}
ORIGINAL_NAME = "original"


class StorageError(ValueError):
    """A key that isn't a sha256, or a page that doesn't exist."""


class DocumentStorage:
    def __init__(self, root: Path) -> None:
        self._root = root

    def _folder(self, sha256: str) -> Path:
        if not SHA256_HEX.match(sha256):
            raise StorageError("document ids are sha256 hex digests")
        return self._root / sha256

    def save_original(self, sha256: str, data: bytes) -> None:
        write_atomically(self._folder(sha256) / ORIGINAL_NAME, data)

    def read_original(self, sha256: str) -> bytes:
        return (self._folder(sha256) / ORIGINAL_NAME).read_bytes()

    def save_pages(self, sha256: str, pages: list[ImageInput]) -> None:
        for number, page in enumerate(pages, start=1):
            extension = PAGE_EXTENSIONS[page.media_type]
            write_atomically(self._folder(sha256) / f"page-{number}.{extension}", page.data)

    def page_path(self, sha256: str, number: int) -> Path:
        """The rendered page image (1-based), whichever format preprocessing produced."""
        for extension in PAGE_EXTENSIONS.values():
            path = self._folder(sha256) / f"page-{number}.{extension}"
            if path.is_file():
                return path
        raise StorageError(f"page {number} not rendered")
