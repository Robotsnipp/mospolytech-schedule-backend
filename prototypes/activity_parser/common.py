"""Small source-preserving helpers shared by the two extractors."""

import csv
import hashlib
import json
import re
from pathlib import Path

DAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
TIME_RE = re.compile(r"(?<!\d)(\d{1,2})[:.](\d{2})\s*[-–—]\s*(\d{1,2})[:.](\d{2})(?!\d)")
STANDARD_TIMES = [
    ("09:00", "10:30"), ("10:40", "12:10"), ("12:20", "13:50"),
    ("14:30", "16:00"), ("16:10", "17:40"), ("17:50", "19:20"),
    ("19:30", "21:00"),
]


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def time_range(match) -> dict:
    sh, sm, eh, em = map(int, match.groups())
    start, end = f"{sh:02}:{sm:02}", f"{eh:02}:{em:02}"
    valid = sh < 24 and eh < 24 and sm < 60 and em < 60 and (sh, sm) < (eh, em)
    pair = STANDARD_TIMES.index((start, end)) + 1 if (start, end) in STANDARD_TIMES else None
    start_minute, end_minute = sh * 60 + sm, eh * 60 + em
    blocked = []
    if valid:
        for number, (slot_start, slot_end) in enumerate(STANDARD_TIMES, 1):
            a, b = (int(value[:2]) * 60 + int(value[3:]) for value in (slot_start, slot_end))
            if start_minute < b and a < end_minute:
                blocked.append(number)
    return {"start": start if valid else None, "end": end if valid else None,
            "lesson_number": pair, "raw": match.group(), "valid": valid,
            "start_minute": start_minute if valid else None,
            "end_minute": end_minute if valid else None,
            "blocked_lesson_numbers": blocked,
            "timing_notes": ["nonstandard_time"] if valid and pair is None else []}


def write_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict], fields: list[str]):
    # This is an inspection export, not a workbook. Protect source text from
    # being interpreted as formulas when the CSV is opened in Excel/Sheets.
    def value(v):
        if isinstance(v, (list, dict)):
            v = json.dumps(v, ensure_ascii=False)
        if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")):
            v = "'" + v
        return v

    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({k: value(row.get(k)) for k in fields} for row in rows)
