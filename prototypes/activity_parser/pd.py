"""Extract projects and candidate fixed meetings from a published Sheets HTML."""

import re
from copy import deepcopy
from datetime import date
from html.parser import HTMLParser

from .common import DAYS, TIME_RE, clean, time_range
from .places import note_place, parse_place

MONTHS = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5,
          "июня": 6, "июля": 7, "августа": 8, "сентября": 9, "октября": 10,
          "ноября": 11, "декабря": 12}
DATE_RE = re.compile(
    r"(?<!\d)(\d{1,2})\s+(" + "|".join(MONTHS) + r")\b|"
    r"(?<![\d.])(\d{1,2})\.(\d{2})(?:\.(\d{4}))?(?![\d.])", re.I)
DAY_RE = re.compile(r"понедельник|вторник|сред[ау]|четверг|пятниц[ау]|суббот[ау]|воскресенье", re.I)
TEACHER_NOTE_RE = re.compile(r"[А-ЯЁ][а-яё-]+\s+[А-ЯЁ]\.\s*[А-ЯЁ]\.")


class SheetParser(HTMLParser):
    """Read table cell text and spans without executing source HTML/scripts."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self.table = None
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.table = []
            self.tables.append(self.table)
        elif tag == "tr" and self.table is not None:
            self.row = []
            self.table.append(self.row)
        elif tag in ("td", "th") and self.row is not None:
            self.cell = {"attrs": dict(attrs), "tag": tag, "text": ""}
            self.row.append(self.cell)
        elif tag == "br" and self.cell is not None:
            self.cell["text"] += "\n"

    def handle_data(self, data):
        if self.cell is not None:
            self.cell["text"] += data

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self.cell = None
        elif tag == "tr":
            self.row = None
        elif tag == "table":
            self.table = None


def expand_table(rows: list[list[dict]]) -> list[list[dict | None]]:
    """Expand merged cells, retaining the anchor coordinate for provenance."""
    grid = {}
    for r, row in enumerate(rows):
        c = 0
        for cell in row:
            while (r, c) in grid:
                c += 1
            value = {"text": cell["text"].strip(), "anchor": [r + 1, c + 1],
                     "tag": cell["tag"]}
            rs = int(cell["attrs"].get("rowspan", 1))
            cs = int(cell["attrs"].get("colspan", 1))
            if not (1 <= rs <= 1000 and 1 <= cs <= 100):
                raise ValueError("Неподдерживаемый размер объединённой ячейки")
            for rr in range(r, r + rs):
                for cc in range(c, c + cs):
                    if (rr, cc) in grid:
                        raise ValueError("Пересекающиеся объединённые ячейки")
                    grid[rr, cc] = value
            c += cs
    width = max((c for _, c in grid), default=-1) + 1
    height = max((r for r, _ in grid), default=-1) + 1
    return [[grid.get((r, c)) for c in range(width)] for r in range(height)]


def dates_in(text: str, academic_start: int | None) -> list[dict]:
    found = []
    time_spans = [m.span() for m in TIME_RE.finditer(text)]
    for match in DATE_RE.finditer(text):
        if any(start <= match.start() < end for start, end in time_spans):
            continue
        if match[1]:
            day, month, year = int(match[1]), MONTHS[match[2].lower()], None
        else:
            day, month = int(match[3]), int(match[4])
            year = int(match[5]) if match[5] else None
        if year is None and academic_start is not None:
            year = academic_start if month >= 9 else academic_start + 1
        try:
            iso = date(year, month, day).isoformat() if year else None
        except ValueError:
            iso = None
        found.append({"raw": match.group(), "date": iso})
    return found


def weekday(text: str) -> int | None:
    match = DAY_RE.search(text)
    if match is None:
        return None
    value = match.group().lower()
    if value == "среду":
        return 2
    if value == "пятницу":
        return 4
    if value == "субботу":
        return 5
    return DAYS.index(value)


def parse_schedule_cell(raw: str, column_day: int, academic_start: int | None,
                        legend: dict) -> dict:
    """Preserve dated changes separately; never make their times weekly."""
    meetings, notes, warnings, changes = [], [], [], []
    pending_times, place_lines = [], []
    teacher_lines = []
    context_date = None
    context_day = column_day
    context_dated = False
    context_notes = []
    context_dates = []

    def flush():
        nonlocal pending_times, place_lines, teacher_lines
        place = parse_place(place_lines, legend)
        for timing in pending_times:
            issues = list(place["warnings"])
            if not timing["valid"]:
                issues.append("invalid_time")
            if teacher_lines:
                issues.append("teacher_override_requires_review")
            if context_dated:
                issues.append("dated_change_requires_review")
            if context_notes:
                issues.append("source_note_requires_review")
            meetings.append({**timing, "day_of_week": context_day,
                             "recurrence": "dated" if context_dated else "weekly_candidate",
                             "date": context_date, "date_mentions": list(context_dates),
                             "place_raw": place["raw"], "room_candidates": place["room_candidates"],
                             "place_details": place, "teacher_override_raw": list(teacher_lines),
                             "place_inherited": False,
                             "notes": list(context_notes), "warnings": issues,
                             "review_required": True})
        if place_lines and not pending_times:
            notes.append({"date": context_date, "date_mentions": list(context_dates),
                          "text": place["raw"], "warnings": ["note_without_time"]})
        pending_times, place_lines, teacher_lines = [], [], []

    for source_line in raw.splitlines():
        line = clean(source_line)
        if not line:
            continue
        dates = dates_in(line, academic_start)
        is_change = bool(re.search(r"перенос|отмен|по соглас|уточня", line, re.I))
        if dates or is_change:
            flush()
            context_dated = True
            context_date = dates[0]["date"] if len(dates) == 1 else None
            context_dates = dates
            context_notes = [line]
            warnings.append("date_or_change_in_source")
            if any(d["date"] is None for d in dates):
                warnings.append("unresolved_date")
            explicit_move = len(dates) == 2 and all(d["date"] for d in dates) and re.fullmatch(
                re.escape(dates[0]["raw"]) + r"\s+перенос\s+на\s+" + re.escape(dates[1]["raw"]), line, re.I)
            if explicit_move:
                changes.append({"kind": "reschedule", "from_date": dates[0]["date"],
                                "to_date": dates[1]["date"], "raw": line,
                                "times": None, "review_required": True})
                warnings.append("reschedule_requires_review")
            elif len(dates) > 1:
                warnings.append("multiple_dates_require_review")
            explicit_day = weekday(line)
            context_day = explicit_day if explicit_day is not None else column_day
            if context_date:
                date_day = date.fromisoformat(context_date).weekday()
                if explicit_day is not None and explicit_day != date_day:
                    warnings.append("date_weekday_mismatch")
                else:
                    context_day = date_day
            if not TIME_RE.search(line):
                notes.append({"date": context_date, "date_mentions": dates, "text": line,
                              "place_details": note_place(line, legend),
                              "warnings": ["source_change_requires_review"]})
                continue
        explicit_day = weekday(line)
        if explicit_day is not None and not dates and not is_change:
            context_day = explicit_day
            if context_day != column_day:
                warnings.append("weekday_differs_from_column")
        matches = list(TIME_RE.finditer(line))
        if matches:
            if place_lines:
                flush()
            pending_times.extend(time_range(m) for m in matches)
            remainder = TIME_RE.sub("", line)
            remainder = DAY_RE.sub("", remainder).strip(" ,;-/")
            remainder = DATE_RE.sub("", remainder)
            remainder = re.sub(r"^\s*занятие\s+в\b", "", remainder, flags=re.I).strip()
            if remainder:
                place_lines.append(remainder)
        elif TEACHER_NOTE_RE.fullmatch(line):
            teacher_lines.append(line)
        elif DAY_RE.fullmatch(line):
            continue
        else:
            place_lines.append(line)
    flush()
    if raw.strip() and not meetings:
        warnings.append("no_meetings_extracted")
    for meeting in meetings:
        warnings.extend(meeting["warnings"])
    return {"raw": raw, "column_day": column_day, "meetings": meetings, "changes": changes,
            "notes": notes, "warnings": sorted(set(warnings))}


def inherit_missing_places(schedules: list[dict]):
    """Dated changes inherit a unique base place, preserving source provenance."""
    bases = [(si, mi, m) for si, s in enumerate(schedules) for mi, m in enumerate(s["meetings"])
             if m["recurrence"] == "weekly_candidate" and m["place_raw"]]
    for si, schedule in enumerate(schedules):
        local = [item for item in bases if item[0] == si]
        candidates = local or bases
        unique = {item[2]["place_raw"] for item in candidates}
        for meeting in schedule["meetings"]:
            if meeting["recurrence"] != "dated" or meeting["place_raw"] is not None:
                continue
            if len(unique) == 1:
                source_si, source_mi, source = candidates[0]
                meeting["place_raw"] = source["place_raw"]
                meeting["room_candidates"] = deepcopy(source["room_candidates"])
                meeting["place_details"] = deepcopy(source["place_details"])
                meeting["place_inherited"] = True
                meeting["place_source"] = {"schedule_index": source_si, "meeting_index": source_mi,
                                            "rule": "unchanged_place_for_dated_override"}
                meeting["warnings"] = [w for w in meeting["warnings"] if w != "missing_place"]
                meeting["warnings"] = sorted(set(meeting["warnings"] + source["place_details"]["warnings"]))
            elif len(unique) > 1:
                meeting["warnings"].append("ambiguous_inherited_place")
        schedule["warnings"] = sorted((set(schedule["warnings"]) - {"missing_place"})
                                      | {w for m in schedule["meetings"] for w in m["warnings"]})


def parse_pd(html: str) -> dict:
    parser = SheetParser()
    parser.feed(html)
    chosen = None
    for table in parser.tables:
        grid = expand_table(table)
        for i, row in enumerate(grid):
            text = [clean(c["text"]).lower() if c else "" for c in row]
            if "название проекта" in text and "понедельник" in text and "среда" in text:
                if chosen is not None:
                    raise ValueError("Найдено несколько таблиц ПД; требуется выбрать лист")
                chosen = grid, i, text
    if chosen is None:
        raise ValueError("Таблица ПД не найдена. Нужен HTML самого опубликованного листа (/pubhtml/sheet).")
    grid, header_row, header = chosen
    metadata_text = "\n".join(c["text"] for row in grid[:header_row] for c in row if c)
    years = re.search(r"(20\d{2})\s*/\s*(20\d{2})", metadata_text)
    academic_start = int(years[1]) if years else None
    legend = dict(re.findall(r"«([^»]+)»\s*=\s*(ул\.[^,«\n]+)", metadata_text))
    fallback = re.search(r"остальное\s*=\s*([^\n]+)", metadata_text)
    if fallback:
        legend["default"] = clean(fallback[1])
    if not legend:
        raise ValueError("Не найдена легенда локаций: нельзя безопасно привязать аудитории")
    columns = {label: header.index(label) for label in
               ("№", "проектное направление", "название проекта", "фио преподавателя",
                "контактная информация", "понедельник", "среда")}

    def source_coordinate(anchor):
        r, c = anchor
        row_header = grid[r - 1][0]
        if row_header and row_header["tag"] == "th" and row_header["text"].isdigit():
            # Published Sheets adds a letters row and a row-number column.
            # Coordinates in output refer to the actual sheet, not that HTML grid.
            r, c = int(row_header["text"]), c - 1
        letter = ""
        while c:
            c, digit = divmod(c - 1, 26)
            letter = chr(65 + digit) + letter
        return {"row": r, "column": letter, "a1": f"{letter}{r}"}

    projects, excluded = [], []
    for i, row in enumerate(grid[header_row + 1:], header_row + 2):
        def cell(label, source_row=row):
            c = source_row[columns[label]]
            return c["text"] if c else ""

        if not any(c and c["text"] for c in row[columns["№"]:]):
            continue
        number, name = cell("№"), cell("название проекта")
        if not number.isdigit() or not name:
            excluded.append({"source_row": i, "raw": row, "reason": "unrecognized_project_row"})
            continue
        schedules = []
        for label, day in (("понедельник", 0), ("среда", 2)):
            raw = cell(label)
            if raw.strip():
                schedule = parse_schedule_cell(raw, day, academic_start, legend)
                schedule["source_cell"] = source_coordinate(row[columns[label]]["anchor"])
                schedules.append(schedule)
        inherit_missing_places(schedules)
        warnings = sorted({w for s in schedules for w in s["warnings"]})
        if not schedules:
            warnings.append("missing_schedule")
        source_row = source_coordinate(row[columns["№"]]["anchor"])["row"]
        projects.append({"source_number": int(number), "source_row": source_row,
                         "name": name, "direction": cell("проектное направление"),
                         "teacher_raw": cell("фио преподавателя"),
                         "contact_raw": cell("контактная информация"),
                         "schedules": schedules, "warnings": warnings,
                         "review_required": True, "project_id": None,
                         "raw_cells": {k: cell(k) for k in columns}})
    numbers = [p["source_number"] for p in projects]
    duplicate_numbers = sorted({n for n in numbers if numbers.count(n) > 1})
    return {"kind": "pd", "schema_version": 1, "parser_revision": 4,
            "academic_year": years.group() if years else None,
            "location_legend": legend, "projects": projects, "excluded_rows": excluded,
            "warnings": ["student_project_membership_not_in_source",
                         "period_dates_not_specified", "all_rows_require_review"]
            + (["duplicate_project_numbers"] if duplicate_numbers else []),
            "duplicate_project_numbers": duplicate_numbers}
