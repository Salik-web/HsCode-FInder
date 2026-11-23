"""File helpers shared by the LLM cache and document storage."""

import os
import tempfile
from pathlib import Path


def write_atomically(path: Path, data: bytes) -> None:
    """Concurrent workers and interrupted runs must never leave a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(data)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
