"""Разбор таблицы «Проектная деятельность» (Google Sheets, экспорт CSV) без сети и БД.

Колонки: №, направление, название проекта, ФИО преподавателя, контакты, понедельник, среда.
Ячейка дня: первая строка — день, далее строки времени ``ЧЧ:ММ-ЧЧ:ММ``, затем аудитория и
пометки (переносы, разовые занятия). Что не удалось отнести к расписанию, сохраняется в ``note``.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import time

TIME_RE = re.compile(r"(\d{1,2})[:.](\d{2})\s*[-–—]\s*(\d{1,2})[:.](\d{2})")
DAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота")
# колонки с расписанием по индексу -> день недели 0..5
DAY_COLUMNS = {5: 0, 6: 2}
NOTE_RE = re.compile(
    r"(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря|"
    r"перенос|занятие|\d{1,2}\.\d{2}\b)", re.IGNORECASE)


class PDParseError(ValueError):
    """Таблица не похожа на ожидаемую."""


@dataclass
class PDSlotData:
    day_of_week: int
    start: time
    end: time
    room: str


@dataclass
class PDProjectData:
    number: int
    direction: str
    title: str
    teacher: str
    contact: str
    note: str = ""
    irregular: bool = False
    slots: list[PDSlotData] = field(default_factory=list)


def _clean(text: str) -> str:
    return " ".join(text.split())


def _times(text: str) -> list[tuple[time, time]]:
    return [(time(int(a), int(b)), time(int(c), int(d))) for a, b, c, d in TIME_RE.findall(text)]


def _is_time_line(line: str) -> bool:
    return bool(TIME_RE.search(line)) and not TIME_RE.sub("", line).strip()


def parse_day_cell(cell: str, default_day: int) -> tuple[list[PDSlotData], str, bool]:
    """Возвращает (слоты, пометки, нерегулярно). Нерегулярно — ячейка не в стандартном формате.

    Стандартно: строка с днём, затем строки времени, затем аудитория, остальное — пометки.
    Ячейка без дня, но с датой в первой строке («12 октября», времена ниже), даёт слоты
    колонки; ячейка-перенос и разовые занятия без отдельной строки времени остаются только
    пометкой.
    """
    lines = [_clean(line) for line in cell.split("\n")]
    lines = [line for line in lines if line]
    if not lines:
        return [], "", False

    day_at = next((i for i, line in enumerate(lines)
                   if any(line.lower().startswith(d) for d in DAYS)), None)
    if day_at is not None:
        day_word = next(d for d in DAYS if lines[day_at].lower().startswith(d))
        day, start, irregular = DAYS.index(day_word), day_at + 1, day_at > 0
        notes = lines[:day_at]
        remark = lines[day_at][len(day_word):].strip()
        if remark:
            notes.append(remark)
    elif lines[0].lower().startswith("перенос"):
        return [], _clean(" / ".join(lines)), True
    else:
        day, start, irregular, notes = default_day, 1, True, [lines[0]]

    i = start
    times: list[tuple[time, time]] = []
    while i < len(lines) and _is_time_line(lines[i]):
        times.extend(_times(lines[i]))
        i += 1
    if not times:
        return [], _clean(" / ".join(lines)), True
    room_lines = []
    while i < len(lines) and not NOTE_RE.search(lines[i]):
        room_lines.append(lines[i])
        i += 1
    notes.extend(lines[i:])
    room = _clean(" ".join(room_lines))
    return ([PDSlotData(day, s, e, room) for s, e in times],
            _clean(" / ".join(notes)), irregular or bool(lines[i:]))


def parse_csv(text: str) -> list[PDProjectData]:
    rows = list(csv.reader(io.StringIO(text)))
    header_at = next((i for i, r in enumerate(rows)
                      if r and r[0].strip() == "№" and len(r) >= 7), None)
    if header_at is None:
        raise PDParseError("не найдена строка заголовка таблицы")
    projects: list[PDProjectData] = []
    for row in rows[header_at + 1:]:
        if len(row) < 7 or not row[0].strip().isdigit():
            continue
        project = PDProjectData(
            number=int(row[0]), direction=_clean(row[1]), title=_clean(row[2]),
            teacher=_clean(row[3]), contact=_clean(row[4]))
        notes = []
        for col, day in DAY_COLUMNS.items():
            slots, note, irregular = parse_day_cell(row[col], day)
            project.slots.extend(slots)
            project.irregular = project.irregular or irregular
            if note:
                notes.append(note)
        project.note = " / ".join(notes)
        if project.title:
            projects.append(project)
    if not projects:
        raise PDParseError("в таблице нет проектов")
    return projects
