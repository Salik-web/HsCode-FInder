"""The review UI's entry point: it must start the way Streamlit starts it."""

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

UI_DIR = Path(__file__).parents[1] / "ui"
ENTRY_POINT = UI_DIR / "streamlit_app.py"
TIMEOUT_SECONDS = 120


DOCUMENT_ID = "a" * 64
WARNING = "Only the first 3 of 4 pages were read."
PLAIN_ERROR = "The AI service didn't respond."


@contextmanager
def stub_api(responses: dict[str, Any]) -> Iterator[str]:
    """A stand-in API: JSON by path, 404 for anything else."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = responses.get(self.path.split("?")[0])
            self.send_response(404 if body is None else 200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body or {"detail": "not found"}).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def show(document: dict[str, Any], monkeypatch: pytest.MonkeyPatch, **more: Any) -> AppTest:
    """The page with one document selected."""
    item = {"id": DOCUMENT_ID, "filename": "invoice.pdf", "page_count": 4, **document}
    responses = {"/documents": {"items": [item]}, f"/documents/{DOCUMENT_ID}": item, **more}
    with stub_api(responses) as url:
        monkeypatch.setenv("API_URL", url)
        st.cache_resource.clear()  # the page caches its settings and API client
        page = AppTest.from_file(str(ENTRY_POINT), default_timeout=TIMEOUT_SECONDS).run()
        st.cache_resource.clear()
    assert not page.exception
    return page


def test_no_file_under_ui_is_named_app_py() -> None:
    """Regression: `streamlit run` puts ui/ first on the import path, so ui/app.py shadowed
    the app package ("No module named 'app.config'; 'app' is not a package")."""
    assert list(UI_DIR.rglob("app.py")) == []


def test_the_page_runs_with_only_its_own_folder_on_the_import_path(tmp_path: Path) -> None:
    """As under `streamlit run`: the repo root is neither on the path nor the working
    directory. A health check can't see a failure here, because Streamlit answers it
    without running the script."""
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    with stub_api({"/documents": {"items": []}}) as url:
        finished = subprocess.run(
            [sys.executable, str(ENTRY_POINT)],
            cwd=tmp_path,
            env=environment | {"API_URL": url},
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    assert finished.returncode == 0, finished.stderr[-2000:]


def test_a_failed_document_shows_the_plain_message_and_a_retry_button(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed = {"status": "failed", "error": PLAIN_ERROR, "retryable": True}
    page = show(failed, monkeypatch)
    assert [error.value for error in page.error] == [PLAIN_ERROR]
    assert "Retry" in [button.label for button in page.button]


def test_a_partly_read_document_shows_its_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    reviewed = {"status": "needs_review", "error": None, "retryable": False}
    review = {
        "result": {"warnings": [WARNING], "pages_read": 3},
        "invoice": None,
        "fields": [],
        "hs_lines": [],
        "corrections": [],
    }
    page = show(reviewed, monkeypatch, **{f"/documents/{DOCUMENT_ID}/review": review})
    assert WARNING in [warning.value for warning in page.warning]


def test_a_failed_hs_line_shows_the_plain_message(monkeypatch: pytest.MonkeyPatch) -> None:
    reviewed = {"status": "needs_review", "error": None, "retryable": False}
    stage = {"status": "failed", "error": "RateLimitedError: groq 429", "message": PLAIN_ERROR}
    review = {
        "result": {"warnings": [], "pages_read": 4},
        "invoice": {"line_items": [{"description": "Shirts"}]},
        "fields": [],
        "hs_lines": [
            {
                "line_index": 0,
                "chosen_code": None,
                "result": {"hs": {"stage": stage, "suggestions": [], "candidates": []}},
            }
        ],
        "corrections": [],
    }
    page = show(reviewed, monkeypatch, **{f"/documents/{DOCUMENT_ID}/review": review})
    shown = [warning.value for warning in page.warning]
    assert f"No suggestion (failed): {PLAIN_ERROR}" in shown
    assert not any("groq" in text for text in shown)


def test_a_printed_code_mismatch_shows_both_codes_and_reasons_are_marked_unverified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reviewed = {"status": "needs_review", "error": None, "retryable": False}
    ok = {"status": "ok", "error": None, "message": None}
    suggestion = {"code": "6109.1000", "reason": "T-shirts of cotton"}
    review = {
        "result": {"warnings": [], "pages_read": 1},
        "invoice": {"line_items": [{"description": "Cotton shirts"}]},
        "fields": [],
        "hs_lines": [
            {
                "line_index": 0,
                "chosen_code": None,
                "result": {
                    "hs": {"stage": ok, "suggestions": [suggestion], "candidates": []},
                    "mismatch": {
                        "printed": "6105.10.4000.8",
                        "suggested": "6109.1000",
                        "digits": 6,
                    },
                },
            }
        ],
        "corrections": [],
    }
    page = show(reviewed, monkeypatch, **{f"/documents/{DOCUMENT_ID}/review": review})
    [warning] = [w.value for w in page.warning if "6105.10.4000.8" in w.value]
    assert "6109.1000" in warning
    captions = [caption.value for caption in page.caption]
    assert "Model explanation, not verified: T-shirts of cotton" in captions
    assert page.expander[0].label.startswith("🟡")
