from datetime import datetime
import html
import re
from zoneinfo import ZoneInfo


def to_pakistan_time(utc_time_str: str) -> str:
    """
    Convert ISO utc time string (with Z) to Pakistan timezone formatted string.
    """
    if not utc_time_str:
        return ""
    dt_utc = datetime.fromisoformat(utc_time_str.replace("Z", "+00:00"))
    dt_pkt = dt_utc.astimezone(ZoneInfo("Asia/Karachi"))
    return dt_pkt.strftime("%Y-%m-%d %H:%M:%S")


def html_to_plain_text(html_content: str) -> str:
    """
    Convert small HTML snippets to plain text preserving paragraphs.
    - unescape entities
    - convert <p>, <br>, <li>, header tags to newlines
    - remove remaining tags
    - collapse whitespace and return tidy paragraphs
    """
    if not html_content:
        return ""
    text = html.unescape(html_content)
    # Convert block tags to newlines
    text = re.sub(r"(?i)</?(p|div|br|li|ul|ol|h[1-6])[^>]*>", "\n", text)
    # Remove remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Normalize line endings and collapse multiple blank lines
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Collapse consecutive spaces
    text = re.sub(r"[ \t]{2,}", " ", text)
    # Strip and keep non-empty lines
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n\n".join(lines)


def format_failure_message(
    env: str, candidate: str, title: str, html_message: str, traceback_text: str, max_traceback_chars: int = 2000
) -> str:
    """
    Build a clean textual message summarizing the failure for plain-text notifications.
    This returns markdown-like text with a code block for traceback.
    """
    plain_msg = html_to_plain_text(html_message)
    tb = (traceback_text or "").strip()

    # If traceback is large, keep head and tail with a truncated marker
    if len(tb) > max_traceback_chars:
        half = max_traceback_chars // 2
        tb = tb[:half] + "\n\n...[truncated]...\n\n" + tb[-half:]

    # Escape triple backticks in the traceback to avoid breaking code fences
    tb = tb.replace("```", "`\u200b``")  # insert zero-width char

    parts = []
    if title:
        parts.append(f"*Error:* {title}")
    if plain_msg:
        parts.append("\n*Details:*\n" + plain_msg)
    if tb:
        parts.append("\n*Traceback:*\n```\n" + tb + "\n```")

    return "\n\n".join(parts)

