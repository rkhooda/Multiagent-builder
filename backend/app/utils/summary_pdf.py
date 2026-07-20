"""One-to-two page project summary PDF.

This is handover documentation, not a designed artifact: readable structure,
built-in fonts, no images or embedded assets. Every section is driven off a
partial project — a cancelled run with no code, no QA report and no metrics must
still export a sensible page rather than raise on a missing field, so every
accessor defaults and every section is skipped when it has nothing to say.
"""
import io
import json
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

INK = colors.HexColor("#111827")
MUTED = colors.HexColor("#6b7280")
RULE = colors.HexColor("#d1d5db")
BAND = colors.HexColor("#f3f4f6")

_base = getSampleStyleSheet()
STYLES = {
    "title": ParagraphStyle("title", parent=_base["Title"], fontSize=18, leading=22,
                            alignment=TA_LEFT, textColor=INK, spaceAfter=2),
    "subtitle": ParagraphStyle("subtitle", parent=_base["Normal"], fontSize=9,
                               textColor=MUTED, spaceAfter=10),
    "h2": ParagraphStyle("h2", parent=_base["Heading2"], fontSize=11, leading=14,
                         textColor=INK, spaceBefore=12, spaceAfter=4),
    "body": ParagraphStyle("body", parent=_base["Normal"], fontSize=9, leading=12.5,
                           textColor=INK),
    "small": ParagraphStyle("small", parent=_base["Normal"], fontSize=8, leading=10.5,
                            textColor=MUTED),
    "mono": ParagraphStyle("mono", parent=_base["Normal"], fontName="Courier",
                           fontSize=7.5, leading=10, textColor=INK),
}

TABLE_STYLE = TableStyle([
    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ("FONTSIZE", (0, 0), (-1, -1), 8),
    ("TEXTCOLOR", (0, 0), (-1, -1), INK),
    ("BACKGROUND", (0, 0), (-1, 0), BAND),
    ("LINEBELOW", (0, 0), (-1, 0), 0.6, RULE),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("TOPPADDING", (0, 0), (-1, -1), 3),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ("LEFTPADDING", (0, 0), (-1, -1), 5),
])


def _escape(text) -> str:
    """Paragraph() parses a mini-HTML dialect, so any model-generated text has to
    be escaped or a stray '<' in a brief aborts the whole render."""
    return (str(text or "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _fmt_bytes(n: int) -> str:
    n = n or 0
    return f"{n/1024:.1f} KB" if n >= 1024 else f"{n} B"


def _fmt_duration(seconds) -> str:
    if not seconds:
        return "—"
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}m {secs}s" if minutes else f"{secs}s"


def _fmt_timestamp(iso: str) -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return str(iso)[:16]


def _table(rows, widths):
    table = Table(rows, colWidths=widths, hAlign="LEFT", repeatRows=1)
    table.setStyle(TABLE_STYLE)
    return table


def build_summary_pdf(state: dict, files: list, metrics: dict, project_id: str) -> io.BytesIO:
    """Render the summary. `state` is serialize_project_state output; `files` the
    file-list payload; `metrics` the run_summary rollup. All may be sparse."""
    state = state or {}
    files = files or []
    metrics = metrics or {}

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=LETTER,
        leftMargin=0.7 * inch, rightMargin=0.7 * inch,
        topMargin=0.6 * inch, bottomMargin=0.6 * inch,
        title=f"{state.get('project_name') or project_id} — Project Summary",
        author="Multi-Agent Product Builder",
    )
    width = doc.width
    story = []

    # ── Header ───────────────────────────────────────────────────────────────
    story.append(Paragraph(_escape(state.get("project_name") or "Untitled Project"), STYLES["title"]))
    story.append(Paragraph(
        f"Project Summary &middot; {_escape(project_id)} &middot; "
        f"exported {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        STYLES["subtitle"]))

    status = state.get("status") or "unknown"
    stage = state.get("current_stage") or "—"
    qa_issues = state.get("qa_issues_count", 0)
    story.append(_table([
        ["Status", "Stage", "Files", "QA Issues", "Duration", "Tokens"],
        [
            status.replace("_", " "),
            stage.replace("_", " "),
            str(len(files)),
            "not reviewed" if qa_issues == -1 else str(qa_issues or 0),
            _fmt_duration(state.get("generation_seconds")),
            f"{metrics.get('total_tokens', 0):,}" if metrics.get("has_metrics") else "—",
        ],
    ], [width * 0.19, width * 0.21, width * 0.11, width * 0.16, width * 0.16, width * 0.17]))

    # ── Brief ────────────────────────────────────────────────────────────────
    if state.get("brief"):
        story.append(Paragraph("Brief", STYLES["h2"]))
        story.append(Paragraph(_escape(state["brief"]), STYLES["body"]))

    # ── Tech stack ───────────────────────────────────────────────────────────
    stack = (state.get("tech_stack") or "").strip()
    if stack:
        story.append(Paragraph("Tech Stack", STYLES["h2"]))
        # tech_stack is sometimes a JSON blob, sometimes prose — render either.
        try:
            parsed = json.loads(stack)
            rendered = ", ".join(
                f"{k}: {', '.join(v) if isinstance(v, list) else v}"
                for k, v in parsed.items()) if isinstance(parsed, dict) else str(parsed)
        except (json.JSONDecodeError, TypeError):
            rendered = stack
        story.append(Paragraph(_escape(rendered[:1500]), STYLES["body"]))

    # ── Metrics ──────────────────────────────────────────────────────────────
    if metrics.get("has_metrics"):
        story.append(Paragraph("Generation Metrics", STYLES["h2"]))
        rows = [["Agent", "Calls", "Avg in", "Avg out", "Total tokens"]]
        for row in metrics.get("by_agent", []):
            rows.append([
                str(row.get("agent", "—")),
                str(row.get("calls", 0)),
                f"{row.get('avg_prompt_tokens') or 0:,.0f}",
                f"{row.get('avg_completion_tokens') or 0:,.0f}",
                f"{(row.get('total_prompt_tokens') or 0) + (row.get('total_completion_tokens') or 0):,}",
            ])
        rows.append([
            "TOTAL", str(metrics.get("ok_attempts", 0)), "", "",
            f"{metrics.get('total_tokens', 0):,}",
        ])
        story.append(_table(rows, [width * 0.32, width * 0.12, width * 0.16, width * 0.16, width * 0.24]))
        story.append(Paragraph(
            f"{metrics.get('attempts', 0)} attempts, {metrics.get('failed_attempts', 0)} failed "
            f"(retries and provider fallbacks). Free-tier models — tokens, not dollars, are the budget.",
            STYLES["small"]))

    # ── Files ────────────────────────────────────────────────────────────────
    if files:
        story.append(Paragraph(f"Generated Files ({len(files)})", STYLES["h2"]))
        rows = [["Path", "Lines", "Size"]]
        # Cap the listing so a large project cannot balloon a handover summary
        # into 20 pages; the count above still reports the true total.
        shown = files[:60]
        for f in shown:
            rows.append([
                Paragraph(_escape(f.get("path", "")), STYLES["mono"]),
                str(f.get("line_count") or "—"),
                _fmt_bytes(f.get("size_bytes")),
            ])
        story.append(_table(rows, [width * 0.66, width * 0.14, width * 0.20]))
        if len(files) > len(shown):
            story.append(Paragraph(
                f"… and {len(files) - len(shown)} more files. Download the ZIP for the full tree.",
                STYLES["small"]))

    # ── QA ───────────────────────────────────────────────────────────────────
    qa_report = (state.get("qa_report") or "").strip()
    if qa_report:
        story.append(Paragraph("QA Summary", STYLES["h2"]))
        if qa_issues == -1:
            story.append(Paragraph("QA was skipped — the generated code was not reviewed.", STYLES["body"]))
        else:
            story.append(Paragraph(f"{qa_issues or 0} issues recorded.", STYLES["body"]))
        # An excerpt, not the whole report — the full document lives in the app.
        excerpt = "<br/>".join(_escape(line) for line in qa_report.splitlines()[:25] if line.strip())
        story.append(Paragraph(excerpt[:3000], STYLES["small"]))

    validation = state.get("validation_report") or {}
    if validation:
        story.append(Paragraph(
            f"Automated checks: {validation.get('files_checked', 0)} files, "
            f"{validation.get('syntax_errors', 0)} unresolved syntax errors, "
            f"{validation.get('auto_repaired', 0)} auto-repaired, "
            f"{validation.get('import_warnings', 0)} import warnings.",
            STYLES["small"]))

    # ── Stage history ────────────────────────────────────────────────────────
    history = state.get("stage_history") or []
    if history:
        story.append(Paragraph("Stage History", STYLES["h2"]))
        rows = [["Stage", "Attempt", "Trigger", "Completed"]]
        for entry in history:
            trigger = entry.get("trigger", "initial")
            if entry.get("from_stage"):
                trigger = f"{trigger} ({entry['from_stage']})"
            rows.append([
                str(entry.get("stage", "—")).replace("_", " "),
                str(entry.get("attempt", 1)),
                trigger,
                _fmt_timestamp(entry.get("timestamp")),
            ])
        story.append(_table(rows, [width * 0.30, width * 0.14, width * 0.28, width * 0.28]))

    if len(story) <= 3:
        story.append(Paragraph(
            "This project has not produced any documented output yet.", STYLES["body"]))

    doc.build(story)
    buffer.seek(0)
    return buffer
