"""Экспорт расписания в формат iCalendar (.ics).

Пара в rasp — недельный шаблон (день недели + номер пары + период действия),
поэтому каждое занятие выгружается одним VEVENT с RRULE:FREQ=WEEKLY и
границей UNTIL по дате окончания: календарь сам развернёт повторы.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING

from .models import Lesson, PDEnrollment
from .pair_times import PAIR_TIMES, VIRTUAL_LOCATIONS

if TYPE_CHECKING:  # импорт ленивый: schedule_generator сам зависит от rasp
    from schedule_generator.models import PEEnrollment

# Москва не переходит на летнее время с 2014 года (UTC+3 круглый год),
# поэтому времена пар переводятся в UTC фиксированным смещением.
MSK_UTC_OFFSET = timedelta(hours=3)

WEEKDAY_CODES = {1: "MO", 2: "TU", 3: "WE", 4: "TH", 5: "FR", 6: "SA", 7: "SU"}

# Занятия без даты окончания повторяются не бесконечно, а до горизонта выгрузки.
DEFAULT_HORIZON = timedelta(days=180)

PRODID = "-//mospolytech-schedule//rasp//RU"

FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(part: str) -> str:
    return FILENAME_SAFE.sub("-", part).strip("-") or "schedule"


def escape_text(value: str) -> str:
    """Экранирование текстовых значений по RFC 5545 (§3.3.11)."""
    return (value.replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\n", "\\n"))


def fold_line(line: str) -> Iterator[str]:
    """Разбивает строку длиннее 75 октетов (RFC 5545 §3.1), не рвя UTF-8
    символы; строки-продолжения возвращаются уже с ведущим пробелом."""
    limit, current, width = 75, "", 0
    for char in line:
        size = len(char.encode("utf-8"))
        if current and width + size > limit:
            yield current
            current, width, limit = " " + char, 1 + size, 75  # пробел входит в 75 октетов
        else:
            current, width = current + char, width + size
    yield current


@dataclass(frozen=True)
class CalendarEvent:
    uid: str
    summary: str
    weekday: int  # 1 = пн … 7 = вс
    start: time
    end: time
    date_from: date | None = None
    date_to: date | None = None
    location: str = ""
    description: str = ""


def _utc(day: date, at: time) -> datetime:
    return datetime.combine(day, at) - MSK_UTC_OFFSET


def _fmt(moment: datetime) -> str:
    return moment.strftime("%Y%m%dT%H%M%SZ")


def first_weekday(on_or_after: date, weekday: int) -> date:
    return on_or_after + timedelta(days=(weekday - on_or_after.isoweekday()) % 7)


def _event_lines(event: CalendarEvent, default_from: date, default_to: date,
                 dtstamp: str, now: datetime) -> list[str] | None:
    """Один VEVENT; None, если серия уже закончилась или не начнётся."""
    start_day = first_weekday(event.date_from or default_from, event.weekday)
    date_to = event.date_to or default_to
    if start_day > date_to or date_to < now.date():
        return None
    until = _fmt(_utc(date_to, time(23, 59, 59)))
    lines = [
        "BEGIN:VEVENT",
        f"UID:{event.uid}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART:{_fmt(_utc(start_day, event.start))}",
        f"DTEND:{_fmt(_utc(start_day, event.end))}",
        f"RRULE:FREQ=WEEKLY;BYDAY={WEEKDAY_CODES.get(event.weekday, 'MO')};UNTIL={until}",
        f"SUMMARY:{escape_text(event.summary)}",
    ]
    if event.location:
        lines.append(f"LOCATION:{escape_text(event.location)}")
    if event.description:
        lines.append(f"DESCRIPTION:{escape_text(event.description)}")
    lines.append("END:VEVENT")
    return lines


def build_calendar(name: str, events: Iterable[CalendarEvent], *,
                   default_from: date | None = None,
                   default_to: date | None = None,
                   now: datetime | None = None) -> str:
    """Собирает календарь; события без своих дат живут в переданном диапазоне."""
    now = now or datetime.now(UTC)
    dtstamp = _fmt(now)
    default_from = default_from or now.date()
    default_to = default_to or (now + DEFAULT_HORIZON).date()

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape_text(name)}",
        f"X-WR-CALDESC:{escape_text(f'Экспорт расписания от {now:%d.%m.%Y}')}",
    ]
    for event in events:
        event_lines = _event_lines(event, default_from, default_to, dtstamp, now)
        if event_lines is not None:
            lines.extend(event_lines)
    lines.append("END:VCALENDAR")
    return "\r\n".join(line for raw in lines for line in fold_line(raw)) + "\r\n"


def _lesson_place(lesson: Lesson) -> str:
    rooms = ", ".join(room.name for room in lesson.rooms.all())
    if rooms and lesson.location and lesson.location not in VIRTUAL_LOCATIONS:
        return f"{rooms} · {lesson.location}"
    return rooms or lesson.location


def events_from_lessons(lessons: Iterable[Lesson], *,
                        with_group: bool = False) -> list[CalendarEvent]:
    events = []
    for lesson in lessons:
        start, end = PAIR_TIMES.get(lesson.pair, (None, None))
        if start is None:
            continue
        description = []
        if with_group:
            description.append(f"Группа: {lesson.group.title}")
        teachers = ", ".join(t.name for t in lesson.teachers.all() if not t.is_vacancy)
        if teachers:
            description.append(f"Преподаватель: {teachers}")
        if lesson.e_link:
            description.append(f"Ссылка: {lesson.e_link}")
        events.append(CalendarEvent(
            uid=f"lesson-{lesson.id}@mospolytech-schedule",
            summary=f"{lesson.subject.name} ({lesson.type})" if lesson.type else lesson.subject.name,
            weekday=lesson.weekday, start=start, end=end,
            date_from=lesson.date_from, date_to=lesson.date_to,
            location=_lesson_place(lesson), description="\n".join(description),
        ))
    return events


def events_from_pe(enrollments: Iterable[PEEnrollment]) -> list[CalendarEvent]:
    return [CalendarEvent(
        uid=f"pe-{enrollment.id}@mospolytech-schedule",
        summary="Физическая культура",
        weekday=enrollment.slot.day_of_week + 1,  # 0 = пн … 5 = сб → 1 = пн … 6 = сб
        start=enrollment.slot.start_time, end=enrollment.slot.end_time,
        location=enrollment.slot.place,
        description=f"Направление: {enrollment.slot.direction.name}",
    ) for enrollment in enrollments]


def events_from_pd(enrollment: PDEnrollment) -> list[CalendarEvent]:
    project = enrollment.project
    return [CalendarEvent(
        uid=f"pd-{enrollment.id}-{index}@mospolytech-schedule",
        summary=project.title,
        weekday=slot.day_of_week + 1, start=slot.start_time, end=slot.end_time,
        location=slot.room,
        description=f"Проектная деятельность · {project.teacher}".rstrip(" ·"),
    ) for index, slot in enumerate(project.slots.all())]
