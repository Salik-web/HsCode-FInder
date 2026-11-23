"""Review UI (Streamlit): upload, review fields next to the page, choose HS codes, export.

uv run streamlit run ui/app.py

Talks to the API only (API_URL, default http://127.0.0.1:8000); it holds no logic of its
own. Every edit goes back as a correction, which the API validates against the schema.
"""

from typing import Any

import httpx
import pandas as pd
import streamlit as st

from app.config import Settings

TIMEOUT_SECONDS = 30
UNKNOWN_VISITOR = "unknown"  # the API's marker for "the proxy didn't say"
STATUS_ICONS = {"ok": "✅", "needs_review": "🟡", "failed_check": "🔴"}
DOCUMENT_ICONS = {
    "queued": "⏳",
    "processing": "⚙️",
    "hs_queued": "🔁",
    "needs_review": "🟡",
    "ready": "✅",
    "approved": "📦",
    "failed": "🔴",
}


@st.cache_resource
def settings() -> Settings:
    return Settings()


@st.cache_resource
def api() -> httpx.Client:
    return httpx.Client(base_url=settings().api_url, timeout=TIMEOUT_SECONDS)


def visitor_ip() -> str:
    """The visitor's IP as the Space's proxy reports it; "unknown" when it doesn't.

    The first X-Forwarded-For entry is the client the proxy saw. The API stores only a
    salted hash, and without one only the demo's global daily cap applies.
    """
    headers = st.context.headers
    forwarded = (headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return forwarded or (headers.get("X-Real-Ip") or "").strip() or UNKNOWN_VISITOR


def demo_banner() -> None:
    limit = settings().demo_session_uploads
    st.info(
        "**Public demo.** Upload **synthetic documents only**, never a real invoice. "
        f"Uploads are deleted within {settings().demo_retention_minutes} minutes. "
        f"Up to {limit} uploads per session; the sample invoices are free to explore. "
        "The first load after a quiet spell may take a minute while the Space wakes up."
    )


def show_error(response: httpx.Response) -> None:
    try:
        detail = response.json().get("detail", response.text)
    except ValueError:
        detail = response.text
    st.error(f"{response.status_code}: {detail}")


def sidebar() -> str | None:
    st.sidebar.header("Upload")
    used = st.session_state.get("uploads", 0)
    capped = settings().demo_mode and used >= settings().demo_session_uploads
    uploaded = st.sidebar.file_uploader("Invoice (PDF, JPG or PNG, up to 10 MB)", disabled=capped)
    if capped:
        st.sidebar.caption("You've used this session's demo uploads.")
    if uploaded is not None and not capped and st.sidebar.button("Upload"):
        response = api().post(
            "/documents",
            files={"file": (uploaded.name, uploaded.getvalue(), uploaded.type)},
            headers={"X-Visitor": visitor_ip()},
        )
        if response.status_code == 202:
            st.session_state["selected"] = response.json()["id"]
            st.session_state["uploads"] = used + 1
        else:
            show_error(response)
    st.sidebar.header("Documents")
    st.sidebar.button("Refresh")
    page = api().get("/documents", params={"limit": 50}).json()
    if not page["items"]:
        st.sidebar.caption("Nothing uploaded yet.")
        return None
    labels = {
        item[
            "id"
        ]: f"{DOCUMENT_ICONS.get(item['status'], '')} {item['filename']} ({item['status']})"
        for item in page["items"]
    }
    ids = list(labels)
    current = st.session_state.get("selected")
    index = ids.index(current) if current in ids else 0
    selected = st.sidebar.radio("Pick one", ids, index=index, format_func=labels.__getitem__)
    st.session_state["selected"] = selected
    return selected


def document_view(document_id: str) -> None:
    document = api().get(f"/documents/{document_id}").json()
    st.subheader(f"{DOCUMENT_ICONS.get(document['status'], '')} {document['filename']}")
    st.caption(f"Status: {document['status']} · id {document_id[:12]}…")
    if document["status"] in ("queued", "processing", "hs_queued"):
        st.info("Waiting for the worker (`python -m app.worker`). Press Refresh.")
        return
    if document["status"] == "failed":
        st.error(f"Processing failed: {document['error']}")
        if document["retryable"] and not settings().demo_mode and st.button("Retry"):
            api().post(f"/documents/{document_id}/retry")
            st.rerun()
        return
    response = api().get(f"/documents/{document_id}/review")
    if response.status_code != 200:
        show_error(response)
        return
    review = response.json()
    left, right = st.columns([1, 1])
    with left:
        for number in range(1, (document["page_count"] or 0) + 1):
            image = api().get(f"/documents/{document_id}/pages/{number}")
            if image.status_code == 200:
                st.image(image.content, caption=f"Page {number}", use_container_width=True)
    with right:
        fields_editor(document_id, document["status"], review)
    hs_section(document_id, document["status"], review)
    finish_section(document_id, document["status"])


def fields_editor(document_id: str, status: str, review: dict[str, Any]) -> None:
    st.markdown("#### Fields")
    st.caption("🟡 needs review · 🔴 failed a check · ✅ ok. Edit a value, then save.")
    if not review["fields"]:
        st.warning("No invoice was extracted; nothing to correct.")
        return
    table = pd.DataFrame(
        [
            {
                "": STATUS_ICONS.get(field["status"], ""),
                "field": field["path"],
                "value": "" if field["value"] is None else str(field["value"]),
                "confidence": round(field["confidence"], 2),
                "why": "; ".join(field["reasons"]),
                "corrected": field["corrected"],
            }
            for field in review["fields"]
        ]
    )
    editable = status in ("needs_review", "ready")
    edited = st.data_editor(
        table,
        disabled=[column for column in table.columns if column != "value" or not editable],
        hide_index=True,
        use_container_width=True,
        key=f"fields-{document_id}-{len(review['corrections'])}",
    )
    changes = [
        {"field_path": row["field"], "new_value": row["value"] or None}
        for (_, row), (_, before) in zip(edited.iterrows(), table.iterrows(), strict=True)
        if row["value"] != before["value"]
    ]
    if editable and st.button(f"Save {len(changes)} correction(s)", disabled=not changes):
        response = api().post(f"/documents/{document_id}/corrections", json=changes)
        if response.status_code == 200:
            st.success("Saved.")
            st.rerun()
        else:
            show_error(response)


def hs_section(document_id: str, status: str, review: dict[str, Any]) -> None:
    if not review["hs_lines"]:
        return
    st.markdown("#### HS code suggestions")
    st.caption("Suggestions only: a licensed person makes the final classification.")
    reviewable = status in ("needs_review", "ready")
    unfinished = [h for h in review["hs_lines"] if h["result"]["hs"]["stage"]["status"] != "ok"]
    can_rerun = unfinished and reviewable and not settings().demo_mode
    if can_rerun and st.button(f"Re-run HS for {len(unfinished)} line(s)"):
        response = api().post(f"/documents/{document_id}/hs/rerun")
        if response.status_code == 202:
            st.rerun()
        show_error(response)
    lines = (review["invoice"] or {}).get("line_items", [])
    skipped = len([line for line in lines if line["description"]]) - len(review["hs_lines"])
    if skipped > 0:
        st.caption(f"HS skipped on {skipped} more line(s) (demo limit).")
    for hs_line in review["hs_lines"]:
        index = hs_line["line_index"]
        description = lines[index]["description"] if index < len(lines) else ""
        with st.expander(f"Line {index + 1}: {description}", expanded=False):
            hs_line_view(document_id, reviewable, hs_line)


def hs_line_view(document_id: str, reviewable: bool, hs_line: dict[str, Any]) -> None:
    index = hs_line["line_index"]
    result = hs_line["result"]["hs"]
    if result["stage"]["status"] != "ok":
        st.warning(f"No suggestion ({result['stage']['status']}): {result['stage']['error']}")
    options = [s["code"] for s in result["suggestions"]]
    for suggestion in result["suggestions"]:
        st.write(f"**{suggestion['code']}**: {suggestion['reason']}")
    st.caption(f"Chosen: {hs_line['chosen_code'] or 'none'}")
    if not reviewable:
        return
    choices = options + [c["code"] for c in result["candidates"] if c["code"] not in options]
    current = hs_line["chosen_code"]
    picked = None
    if choices:
        picked = st.selectbox(
            "Pick a suggested or retrieved code",
            choices,
            index=choices.index(current) if current in choices else 0,
            key=f"hs-{document_id}-{index}",
        )
    typed = st.text_input(
        "Or type any PCT code (NNNN.NNNN)", key=f"hs-typed-{document_id}-{index}"
    ).strip()
    code = typed or picked
    if code and st.button("Save code", key=f"hs-save-{document_id}-{index}"):
        url = f"/documents/{document_id}/lines/{index}/hs_code"
        response = api().put(url, json={"code": code})
        if response.status_code == 200:
            st.rerun()
        show_error(response)


def finish_section(document_id: str, status: str) -> None:
    st.markdown("#### Approve and export")
    if status in ("needs_review", "ready") and st.button("Approve"):
        response = api().post(f"/documents/{document_id}/approve")
        if response.status_code == 200:
            st.rerun()
        show_error(response)
    if status == "approved":
        for export_format, mime in (("json", "application/json"), ("csv", "text/csv")):
            exported = api().get(
                f"/documents/{document_id}/export", params={"format": export_format}
            )
            st.download_button(
                f"Download {export_format.upper()}",
                exported.content,
                file_name=f"{document_id[:12]}.{export_format}",
                mime=mime,
            )


def main() -> None:
    st.set_page_config(page_title="TradeDoc AI review", layout="wide")
    st.title("TradeDoc AI: invoice review")
    if settings().demo_mode:
        demo_banner()
    selected = sidebar()
    if selected is not None:
        document_view(selected)


main()
