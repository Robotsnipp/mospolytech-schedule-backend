"""Разбор ответа rasp.dmami.ru/site/group (без сети и БД)."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser


class ScheduleUnavailable(Exception):
    """Ответ нельзя разобрать. ``empty=True`` — у группы просто нет расписания."""

    def __init__(self, reason: str, empty: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.empty = empty


@dataclass(frozen=True)
class ParsedRoom:
    name: str
    raw_title: str
    url: str | None


@dataclass(frozen=True)
class ParsedTeacher:
    id: int
    name: str
    is_vacancy: bool


@dataclass
class ParsedLesson:
    weekday: int
    pair: int
    subject: str
    type: str
    date_from: date | None
    date_to: date | None
    dts: str
    week: str
    e_link: str | None
    location: str
    teachers: list[ParsedTeacher] = field(default_factory=list)
    rooms: list[ParsedRoom] = field(default_factory=list)


@dataclass
class ParsedSchedule:
    group: dict
    is_session: bool
    lessons: list[ParsedLesson]


class _TitleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.url: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "a" and self.url is None:
            self.url = dict(attrs).get("href") or None

    def handle_data(self, data):
        self.parts.append(data)


def _strip_symbols(text: str) -> str:
    """Убирает эмодзи/символы и невидимые модификаторы, схлопывает пробелы."""
    kept = [ch for ch in text if unicodedata.category(ch) not in ("So", "Sk", "Cf", "Mn", "Cs")]
    return " ".join("".join(kept).split())


def parse_room_title(title: str) -> ParsedRoom:
    """``<a href=URL>📷 Webinar</a>`` -> имя «Webinar» + ссылка; простой текст -> имя."""
    parser = _TitleParser()
    parser.feed(title or "")
    parser.close()
    text = "".join(parser.parts)
    name = _strip_symbols(text) or " ".join(text.split())
    return ParsedRoom(name=name, raw_title=title, url=parser.url)


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _parse_teachers(raw) -> list[ParsedTeacher]:
    teachers: dict[int, ParsedTeacher] = {}
    for item in raw or []:
        try:
            teacher_id = int(item["id"])
        except (KeyError, TypeError, ValueError):
            continue
        name = " ".join(str(item.get("name", "")).split())
        teachers[teacher_id] = ParsedTeacher(teacher_id, name, name.startswith("Вакансия"))
    return list(teachers.values())


def _parse_rooms(lesson: dict) -> list[ParsedRoom]:
    rooms = [parse_room_title(a.get("title", "")) for a in lesson.get("auditories") or []]
    if not rooms:
        rooms = [ParsedRoom(r.strip(), r, None) for r in lesson.get("shortRooms") or [] if r.strip()]
    return [r for r in rooms if r.name]


def parse_response(payload) -> ParsedSchedule:
    """Разбирает JSON-ответ. Бросает ScheduleUnavailable, если расписания нет/ответ битый."""
    if not isinstance(payload, dict):
        raise ScheduleUnavailable("ответ не является JSON-объектом")
    if payload.get("status") != "ok":
        message = str(payload.get("message") or payload.get("status"))
        raise ScheduleUnavailable(message, empty="Не нашлось расписание" in message)
    grid = payload.get("grid")
    if not isinstance(grid, dict):
        raise ScheduleUnavailable("в ответе нет grid")

    lessons: list[ParsedLesson] = []
    for day_key, pairs in grid.items():
        for pair_key, cell in (pairs or {}).items():
            try:
                weekday, pair = int(day_key), int(pair_key)
            except ValueError:
                continue
            for item in cell or []:
                subject = " ".join(str(item.get("sbj") or "").split())
                if not subject:
                    continue
                lessons.append(ParsedLesson(
                    weekday=weekday, pair=pair, subject=subject,
                    type=str(item.get("type") or "").strip(),
                    date_from=_parse_date(item.get("df")), date_to=_parse_date(item.get("dt")),
                    dts=str(item.get("dts") or ""), week=str(item.get("week") or ""),
                    e_link=item.get("e_link") or None,
                    location=str(item.get("location") or "").strip(),
                    teachers=_parse_teachers(item.get("teachers")),
                    rooms=_parse_rooms(item),
                ))
    return ParsedSchedule(group=payload.get("group") or {},
                          is_session=bool(payload.get("isSession")), lessons=lessons)
