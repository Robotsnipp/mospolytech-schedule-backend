import json
import urllib.parse
from pathlib import Path

from django.test import SimpleTestCase

from rasp.client import URL
from rasp.parser import ScheduleUnavailable, parse_response, parse_room_title

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


class ParserTests(SimpleTestCase):
    def test_html_room_title(self):
        room = parse_room_title(
            '<a href="https://my.mts-link.ru/j/1/session/2" target="_blank">📷 Webinar</a>')
        self.assertEqual(room.name, "Webinar")
        self.assertEqual(room.url, "https://my.mts-link.ru/j/1/session/2")
        self.assertIn("<a ", room.raw_title)

    def test_plain_room_title_has_no_url(self):
        room = parse_room_title("Д108")
        self.assertEqual((room.name, room.url), ("Д108", None))

    def test_real_response_counts(self):
        schedule = parse_response(load("231-333_session0.json"))
        self.assertEqual(len(schedule.lessons), 35)
        self.assertEqual(schedule.group["title"], "231-333")
        self.assertFalse(schedule.is_session)
        webinars = [r for item in schedule.lessons for r in item.rooms if r.url]
        self.assertEqual(len(webinars), 10)
        self.assertTrue(all("📷" not in r.name and "🚀" not in r.name for r in webinars))

    def test_several_lessons_in_one_cell_are_kept(self):
        schedule = parse_response(load("231-333_session0.json"))
        cell = [item for item in schedule.lessons if (item.weekday, item.pair) == (2, 4)]
        self.assertEqual(len(cell), 2)

    def test_vacancy_and_trimmed_names(self):
        teachers = {t.id: t for item in parse_response(load("231-333_session0.json")).lessons
                    for t in item.teachers}
        self.assertTrue(teachers[3210].is_vacancy)
        self.assertEqual(teachers[4117].name, "Рысьев Матвей Сергеевич")
        self.assertFalse(teachers[4117].is_vacancy)

    def test_cyrillic_group_is_url_encoded(self):
        schedule = parse_response(load("24A-111_session0.json"))
        self.assertEqual(schedule.group["title"], "24А-111")
        url = URL.format(group=urllib.parse.quote("24А-111"), session=0)
        self.assertIn("%D0%90", url)

    def test_empty_cells_are_not_errors(self):
        payload = {"status": "ok", "group": {}, "isSession": False,
                   "grid": {"1": {"1": [], "2": []}}}
        self.assertEqual(parse_response(payload).lessons, [])

    def test_empty_session_is_flagged_empty(self):
        with self.assertRaises(ScheduleUnavailable) as ctx:
            parse_response(load("231-333_session1.json"))
        self.assertTrue(ctx.exception.empty)

    def test_other_errors_are_not_empty(self):
        with self.assertRaises(ScheduleUnavailable) as ctx:
            parse_response({"status": "error", "message": "boom"})
        self.assertFalse(ctx.exception.empty)
        with self.assertRaises(ScheduleUnavailable):
            parse_response("<html>not json</html>")
