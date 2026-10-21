import json
from pathlib import Path

from django.test import TestCase

from rasp.importer import store_group
from rasp.models import Lesson, Room, Teacher
from rasp.parser import parse_response

FIX = Path(__file__).parent / "fixtures"


class ImportAndApiTests(TestCase):
    def setUp(self):
        payload = json.loads((FIX / "231-333_session0.json").read_text(encoding="utf-8"))
        self.schedule = parse_response(payload)
        store_group("231-333", True, self.schedule, 0)

    def test_import_is_idempotent(self):
        counts = (Lesson.objects.count(), Teacher.objects.count(), Room.objects.count())
        store_group("231-333", True, self.schedule, 0)
        self.assertEqual(counts, (Lesson.objects.count(), Teacher.objects.count(),
                                  Room.objects.count()))
        self.assertEqual(Lesson.objects.count(), 35)

    def test_empty_session_clears_only_its_part(self):
        store_group("231-333", True, None, 1)
        self.assertEqual(Lesson.objects.filter(is_session=False).count(), 35)

    def test_group_lessons_api(self):
        data = self.client.get("/api/rasp/groups/231-333/lessons/").json()
        self.assertEqual(len(data["lessons"]), 35)
        self.assertEqual(data["group"]["title"], "231-333")
        self.assertTrue(data["group"]["flag"] is True)
        self.assertEqual(self.client.get("/api/rasp/groups/нет/lessons/").status_code, 404)
        self.assertEqual(
            self.client.get("/api/rasp/groups/231-333/lessons/?session=1").json()["lessons"], [])

    def test_teachers_and_free_rooms_api(self):
        teachers = self.client.get("/api/rasp/teachers/").json()
        self.assertTrue(teachers and not any(t["is_vacancy"] for t in teachers))
        lessons = self.client.get(f"/api/rasp/teachers/{teachers[0]['id']}/lessons/").json()
        self.assertTrue(lessons["lessons"])
        self.assertEqual(self.client.get("/api/rasp/rooms/free/").status_code, 400)
        free = self.client.get("/api/rasp/rooms/free/?weekday=7&pair=1")
        self.assertEqual(free.status_code, 200)
        self.assertGreater(len(free.json()), 0)
        self.assertEqual(self.client.get("/api/rasp/pair-times/").json()[0]["start"], "09:00")

    def test_rooms_status_shows_who_occupies_room(self):
        from datetime import timedelta

        lesson = (Lesson.objects.filter(rooms__url__isnull=True, date_from__isnull=False)
                  .exclude(rooms__location__in=("ПД", "Webinar")).distinct().first())
        room = lesson.rooms.filter(url__isnull=True).first()
        day = lesson.date_from
        while day.isoweekday() != lesson.weekday:
            day += timedelta(days=1)
        url = f"/api/rasp/rooms/status/?weekday={lesson.weekday}&pair={lesson.pair}&date={day}"
        data = self.client.get(url).json()
        row = next(r for r in data["rooms"] if r["id"] == room.id)
        self.assertTrue(row["busy"])
        self.assertIn("231-333", row["lessons"][0]["groups"])
        self.assertTrue(row["lessons"][0]["subject"])
        self.assertIn(room.location, data["locations"])
        self.assertFalse(any("лайн" in r["name"].lower() for r in data["rooms"]))
        # другое время: та же аудитория свободна
        other = self.client.get(
            f"/api/rasp/rooms/status/?weekday={lesson.weekday}&pair={lesson.pair}&date=2000-01-01").json()
        self.assertFalse(next(r for r in other["rooms"] if r["id"] == room.id)["busy"])
        self.assertEqual(self.client.get("/api/rasp/rooms/status/").status_code, 400)
