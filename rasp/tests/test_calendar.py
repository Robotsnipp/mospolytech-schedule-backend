"""Экспорт расписания в iCalendar: /api/rasp/.../calendar/."""

import json
import re
from datetime import UTC, date, datetime, time
from pathlib import Path

from django.db.models import Q
from django.test import TestCase

from rasp import calendar
from rasp.importer import store_group
from rasp.models import Group, Lesson, PDEnrollment, PDProject, PDSlot, Subject, Teacher
from rasp.parser import parse_response
from schedule_generator.models import PEDirection, PEEnrollment, PESlot

FIX = Path(__file__).parent / "fixtures"
VEVENT = re.compile(r"BEGIN:VEVENT\r\n(.*?)\r\nEND:VEVENT", re.DOTALL)
BYDAY = {"MO": 1, "TU": 2, "WE": 3, "TH": 4, "FR": 5, "SA": 6, "SU": 7}


def parse_ics(text: str) -> list[dict[str, str]]:
    """Разворачивает фолдинг (RFC 5545 §3.1) и собирает VEVENT в словари."""
    events = []
    for match in VEVENT.findall(text):
        props = {}
        for line in match.replace("\r\n ", "").split("\r\n"):
            key, _, value = line.partition(":")
            for quoted, plain in (("\\n", "\n"), ("\\,", ","), ("\\;", ";"), ("\\\\", "\\")):
                value = value.replace(quoted, plain)
            props[key.split(";")[0]] = value
        events.append(props)
    return events


class CalendarUnitTests(TestCase):
    def test_escape_and_fold(self):
        long_subject = "Очень длинное название предмета, " + "которое не влезает в строку " * 6
        text = calendar.build_calendar(
            "Тест", [calendar.CalendarEvent(
                uid="x@t", summary=f"{long_subject}; с ; символами,\nперенос",
                weekday=1, start=time(9, 0), end=time(10, 30),
                date_from=datetime(2026, 9, 1).date(), date_to=datetime(2026, 12, 30).date())],
            now=datetime(2026, 11, 1, tzinfo=UTC))
        physical = text.split("\r\n")
        for line in physical:
            self.assertLessEqual(len(line.encode()), 75, line)
        # строки-продолжения обязаны начинаться с пробела (RFC 5545 §3.1)
        self.assertTrue(any(line.startswith(" ") for line in physical))
        unfolded = text.replace("\r\n ", "")
        self.assertIn(calendar.escape_text(f"{long_subject}; с ; символами,\nперенос"), unfolded)

    def test_dtstart_is_first_weekday_in_utc(self):
        event = calendar.CalendarEvent(
            uid="x@t", summary="Пара", weekday=3, start=time(14, 30), end=time(16, 0),
            date_from=datetime(2026, 11, 1).date(), date_to=datetime(2026, 11, 30).date())
        text = calendar.build_calendar("Т", [event], now=datetime(2026, 11, 1, tzinfo=UTC))
        (props,) = parse_ics(text)
        self.assertEqual(props["DTSTART"], "20261104T113000Z")  # ср 14:30 MSK = 11:30 UTC
        self.assertEqual(props["DTEND"], "20261104T130000Z")
        self.assertEqual(props["RRULE"], "FREQ=WEEKLY;BYDAY=WE;UNTIL=20261130T205959Z")

    def test_expired_event_is_skipped(self):
        event = calendar.CalendarEvent(
            uid="x@t", summary="Пара", weekday=1, start=time(9, 0), end=time(10, 30),
            date_from=datetime(2026, 9, 1).date(), date_to=datetime(2026, 10, 1).date())
        text = calendar.build_calendar("Т", [event], now=datetime(2026, 11, 1, tzinfo=UTC))
        self.assertNotIn("BEGIN:VEVENT", text)


class GroupCalendarApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        payload = json.loads((FIX / "231-333_session0.json").read_text(encoding="utf-8"))
        store_group("231-333", True, parse_response(payload), 0)

    def calendar_text(self, query: str = "") -> str:
        response = self.client.get(f"/api/rasp/groups/231-333/calendar/{query}")
        self.assertEqual(response.status_code, 200)
        return response.content.decode("utf-8")

    def test_calendar_headers_and_structure(self):
        response = self.client.get("/api/rasp/groups/231-333/calendar/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/calendar; charset=utf-8")
        self.assertEqual(response["Content-Disposition"],
                         'attachment; filename="schedule-231-333.ics"')
        text = response.content.decode("utf-8")
        self.assertTrue(text.startswith("BEGIN:VCALENDAR\r\n"))
        self.assertIn("X-WR-CALNAME:Расписание 231-333", text)
        active = (Lesson.objects.filter(is_session=False)
                  .filter(Q(date_to__isnull=True) | Q(date_to__gte=date.today())).count())
        self.assertEqual(len(parse_ics(text)), active)

    def test_event_fields_match_lesson(self):
        lesson = Lesson.objects.filter(is_session=False, date_from__isnull=False).first()
        props = next(p for p in parse_ics(self.calendar_text())
                     if p["UID"] == f"lesson-{lesson.id}@mospolytech-schedule")
        self.assertIn(props["UID"], self.calendar_text())  # стабильные UID — без дублей при реимпорте
        weekday_code = re.search(r"BYDAY=(\w\w)", props["RRULE"]).group(1)
        self.assertEqual(BYDAY[weekday_code], lesson.weekday)
        dtstart = datetime.strptime(props["DTSTART"], "%Y%m%dT%H%M%SZ")
        self.assertEqual(dtstart.isoweekday(), lesson.weekday)
        self.assertIn(lesson.subject.name, props["SUMMARY"])

    def test_unknown_group_and_teacher(self):
        self.assertEqual(self.client.get("/api/rasp/groups/нет/calendar/").status_code, 404)
        self.assertEqual(self.client.get("/api/rasp/teachers/999999/calendar/").status_code, 404)

    def test_teacher_calendar_contains_group(self):
        teacher = Teacher.objects.filter(lessons__isnull=False).first()
        response = self.client.get(f"/api/rasp/teachers/{teacher.pk}/calendar/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Disposition"],
                         f'attachment; filename="schedule-teacher-{teacher.pk}.ics"')
        self.assertIn("Группа: 231-333", response.content.decode("utf-8"))

    def test_owner_calendar_merges_pe_and_replaces_pd_placeholder(self):
        group = Group.objects.get(title="231-333")
        direction = PEDirection.objects.create(code="games", name="Спортивные игры")
        slot = PESlot.objects.create(direction=direction, day_of_week=0,
                                     start_time=time(18, 0), end_time=time(19, 30),
                                     place="Зал №1, Авангард")
        PEEnrollment.objects.create(owner="owner-1234", group_title="231-333", slot=slot)
        project = PDProject.objects.create(number=1, direction="IT", title="Мобильное приложение",
                                           teacher="Иванов И.И.")
        PDSlot.objects.create(project=project, day_of_week=1,
                              start_time=time(16, 10), end_time=time(17, 40), room="2103")
        PDEnrollment.objects.create(owner="owner-1234", group_title="231-333", project=project)
        placeholder, _ = Subject.objects.get_or_create(name="Проектная деятельность")
        placeholder_lesson = Lesson.objects.create(
            group=group, subject=placeholder, weekday=1, pair=1, location="ПД")

        events = parse_ics(self.calendar_text("?owner=owner-1234"))
        pe = next(e for e in events if e["SUMMARY"] == "Физическая культура")
        self.assertEqual(pe["LOCATION"], "Зал №1, Авангард")
        self.assertTrue(pe["DTSTART"].endswith("T150000Z"))  # 18:00 MSK = 15:00 UTC
        self.assertEqual(datetime.strptime(pe["DTSTART"], "%Y%m%dT%H%M%SZ").isoweekday(), 1)
        self.assertTrue(any(e["SUMMARY"] == "Мобильное приложение" for e in events))
        placeholder_uid = f"lesson-{placeholder_lesson.pk}@mospolytech-schedule"
        self.assertFalse(any(e["UID"] == placeholder_uid for e in events))

    def test_foreign_owner_does_not_leak_into_calendar(self):
        direction = PEDirection.objects.create(code="games", name="Спортивные игры")
        slot = PESlot.objects.create(direction=direction, day_of_week=0,
                                     start_time=time(18, 0), end_time=time(19, 30))
        PEEnrollment.objects.create(owner="owner-1234", group_title="221-111", slot=slot)
        self.assertNotIn("Физическая культура", self.calendar_text("?owner=owner-1234"))
