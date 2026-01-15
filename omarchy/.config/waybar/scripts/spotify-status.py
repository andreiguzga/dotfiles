#!/usr/bin/env python3

import json
import subprocess
from typing import Optional


PLAYER = "spotify"
MAX_TITLE_CHARS = 50
BUCKET_STEP_PERCENT = 5


def run(cmd: list[str]) -> str:
    return subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True).strip()


def playerctl(*args: str) -> Optional[str]:
    try:
        return run(["playerctl", "-p", PLAYER, *args])
    except Exception:
        return None


def format_seconds(total_seconds: float) -> str:
    total_seconds_int = max(0, int(total_seconds))
    minutes, seconds = divmod(total_seconds_int, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def normalize(text: str) -> str:
    return " ".join(text.replace("\n", " ").split()).strip()


def truncate(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    if max_chars == 1:
        return "…"
    return text[: max_chars - 1].rstrip() + "…"


def main() -> None:
    status = playerctl("status")
    if status not in {"Playing", "Paused"}:
        print(json.dumps({"text": "", "tooltip": "", "class": "hidden"}))
        return

    artist = normalize(playerctl("metadata", "artist") or "")
    title = normalize(playerctl("metadata", "title") or "")
    album = normalize(playerctl("metadata", "album") or "")

    position_raw = playerctl("position") or "0"
    try:
        position_seconds = float(position_raw)
    except ValueError:
        position_seconds = 0.0

    length_us_raw = playerctl("metadata", "mpris:length") or "0"
    try:
        length_us = int(length_us_raw)
    except ValueError:
        length_us = 0

    length_seconds = length_us / 1_000_000 if length_us > 0 else 0.0

    progress_percent = 0
    if length_seconds > 0:
        progress_percent = int(round((position_seconds / length_seconds) * 100))
        progress_percent = min(100, max(0, progress_percent))

    bucket = 0
    if length_seconds > 0 and BUCKET_STEP_PERCENT > 0:
        bucket = int(round(progress_percent / BUCKET_STEP_PERCENT) * BUCKET_STEP_PERCENT)
        bucket = min(100, max(0, bucket))

    main_text = " - ".join(part for part in [artist, title] if part)
    if not main_text:
        main_text = "Spotify"

    main_text = truncate(main_text, MAX_TITLE_CHARS)

    text = main_text

    tooltip_lines: list[str] = []
    if album:
        tooltip_lines.append(album)
    tooltip_lines.append(main_text)

    if length_seconds > 0:
        tooltip_lines.append(
            f"{format_seconds(position_seconds)} / {format_seconds(length_seconds)}"
        )
    else:
        tooltip_lines.append(format_seconds(position_seconds))

    classes = ["spotify", status.lower(), f"p{bucket}"]

    print(
        json.dumps(
            {
                "text": text,
                "tooltip": "\n".join(tooltip_lines),
                "class": classes,
            }
        )
    )


if __name__ == "__main__":
    main()
