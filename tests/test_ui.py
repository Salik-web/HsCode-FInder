"""The review UI's entry point: it must start the way Streamlit starts it."""

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

UI_DIR = Path(__file__).parents[1] / "ui"
ENTRY_POINT = UI_DIR / "streamlit_app.py"
TIMEOUT_SECONDS = 120


class NoDocuments(BaseHTTPRequestHandler):
    """A stand-in API with an empty document list."""

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"items": [], "total": 0}).encode())


@pytest.fixture
def api_url() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), NoDocuments)
    Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def test_no_file_under_ui_is_named_app_py() -> None:
    """Regression: `streamlit run` puts ui/ first on the import path, so ui/app.py shadowed
    the app package ("No module named 'app.config'; 'app' is not a package")."""
    assert list(UI_DIR.rglob("app.py")) == []


def test_the_page_runs_with_only_its_own_folder_on_the_import_path(
    api_url: str, tmp_path: Path
) -> None:
    """As under `streamlit run`: the repo root is neither on the path nor the working
    directory. A health check can't see a failure here, because Streamlit answers it
    without running the script."""
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    finished = subprocess.run(
        [sys.executable, str(ENTRY_POINT)],
        cwd=tmp_path,
        env=environment | {"API_URL": api_url},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=TIMEOUT_SECONDS,
        check=False,
    )
    assert finished.returncode == 0, finished.stderr[-2000:]
