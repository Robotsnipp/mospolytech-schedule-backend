from datetime import date

from django.test import TestCase
from rest_framework.test import APIClient

from rasp.models import Group, Lesson, Subject

from .free_periods import FreePeriodsError, find_free_periods, resolve_groups

MONDAY = date(2026, 11, 2)
SUNDAY = date(2026, 11, 8)
URL = "/api/events/free-periods/"


def add_lesson(group, weekday, pair, date_from=None, date_to=None):
    subject, _ = Subject.objects.get_or_create(name="Предмет")
    return Lesson.objects.create(group=group, subject=subject, weekday=weekday, pair=pair,
                                 date_from=date_from, date_to=date_to)


def spans(periods):
    return [(p.day, p.pair_from, p.pair_to, p.free_count) for p in periods]


class FindFreePeriodsTests(TestCase):
    def setUp(self):
        self.a = Group.objects.create(title="231-333")
        self.b = Group.objects.create(title="231-334")

    def find(self, groups, date_from=MONDAY, date_to=MONDAY, **kwargs):
        return find_free_periods(groups, date_from, date_to, limit=200, **kwargs)

    def test_busy_pairs_split_day_into_periods(self):
        add_lesson(self.a, 1, 3)
        add_lesson(self.a, 1, 4)
        self.assertEqual(spans(self.find([self.a])), [
            (MONDAY, 5, 7, 1),  # длинный период раньше короткого
            (MONDAY, 1, 2, 1),
        ])

    def test_more_free_groups_first(self):
        add_lesson(self.a, 1, 1)
        add_lesson(self.b, 1, 1)
        add_lesson(self.b, 1, 2)
        result = self.find([self.a, self.b])
        self.assertEqual(spans(result), [(MONDAY, 3, 7, 2), (MONDAY, 2, 2, 1)])
        self.assertEqual(result[1].as_dict()["free_groups"], ["231-333"])
        self.assertEqual(result[1].as_dict()["busy_groups"], ["231-334"])
        self.assertEqual(result[0].as_dict()["start"], "12:20")
        self.assertEqual(result[0].as_dict()["end"], "21:00")

    def test_lesson_outside_its_dates_is_not_busy(self):
        add_lesson(self.a, 1, 1, date_to=date(2026, 11, 1))   # уже закончилась
        add_lesson(self.a, 1, 2, date_from=date(2026, 11, 3))  # ещё не началась
        add_lesson(self.a, 1, 7, date_from=MONDAY, date_to=MONDAY)  # идёт ровно в этот день
        self.assertEqual(spans(self.find([self.a])), [(MONDAY, 1, 6, 1)])

    def test_lesson_dates_checked_per_day_in_long_range(self):
        # Занятие идёт со 2-го понедельника: в 1-й понедельник группа свободна.
        add_lesson(self.a, 1, 1, date_from=date(2026, 11, 9))
        next_monday = date(2026, 11, 9)
        result = self.find([self.a], MONDAY, next_monday)
        self.assertIn((MONDAY, 1, 7, 1), spans(result))
        self.assertIn((next_monday, 2, 7, 1), spans(result))
        self.assertNotIn((next_monday, 1, 7, 1), spans(result))

    def test_free_count_beats_length(self):
        # Пара 1: свободны обе группы (короткий период).
        # Пары 2-7: свободна только 231-333 (длинный период).
        for pair in range(2, 8):
            add_lesson(self.b, 1, pair)
        self.assertEqual(spans(self.find([self.a, self.b])),
                         [(MONDAY, 1, 1, 2), (MONDAY, 2, 7, 1)])

    def test_other_weekdays_do_not_interfere(self):
        add_lesson(self.a, 2, 1)
        self.assertEqual(spans(self.find([self.a])), [(MONDAY, 1, 7, 1)])

    def test_sunday_skipped_by_default(self):
        self.assertEqual(len(self.find([self.a], MONDAY, SUNDAY)), 6)
        self.assertEqual(len(self.find([self.a], MONDAY, SUNDAY, include_sunday=True)), 7)

    def test_min_free_and_limit(self):
        add_lesson(self.b, 1, 4)
        self.assertEqual(spans(self.find([self.a, self.b], min_free=2)),
                         [(MONDAY, 1, 3, 2), (MONDAY, 5, 7, 2)])
        self.assertEqual(len(find_free_periods([self.a], MONDAY, SUNDAY, limit=2)), 2)

    def test_fully_busy_period_is_never_returned(self):
        for pair in range(1, 8):
            add_lesson(self.a, 1, pair)
        self.assertEqual(self.find([self.a]), [])

    def test_errors(self):
        with self.assertRaises(FreePeriodsError):
            resolve_groups([" ", ""])
        with self.assertRaises(FreePeriodsError):
            resolve_groups(["231-333", "нет-такой"])
        with self.assertRaises(FreePeriodsError):
            self.find([self.a], SUNDAY, MONDAY)
        with self.assertRaises(FreePeriodsError):
            self.find([self.a], MONDAY, date(2027, 1, 30))
        self.assertEqual([g.title for g in resolve_groups(["231-334", "231-333", "231-334"])],
                         ["231-334", "231-333"])


class FreePeriodsApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        a = Group.objects.create(title="231-333")
        Group.objects.create(title="24А-111")
        add_lesson(a, 1, 1)

    def get(self, **params):
        base = {"groups": "231-333,24А-111", "date_from": "2026-11-02", "date_to": "2026-11-02"}
        return self.client.get(URL, {**base, **params})

    def test_public_and_sorted(self):
        response = self.get()
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["groups"], ["231-333", "24А-111"])
        first, second = data["periods"]
        self.assertEqual((first["pair_from"], first["pair_to"], first["free_count"]), (2, 7, 2))
        self.assertEqual((second["pair_from"], second["busy_groups"]), (1, ["231-333"]))
        self.assertEqual(first["weekday"], 1)

    def test_bad_input_gives_400(self):
        self.assertEqual(self.get(groups="").status_code, 400)
        self.assertEqual(self.get(groups="нет").status_code, 400)
        self.assertEqual(self.get(date_from="02.11.2026").status_code, 400)
        self.assertEqual(self.client.get(URL, {"groups": "231-333"}).status_code, 400)
        self.assertEqual(self.get(date_to="2027-03-01").status_code, 400)
        self.assertEqual(self.get(limit="abc").status_code, 400)
        self.assertEqual(self.get(limit="0").status_code, 400)

    def test_route_does_not_clash_with_event_detail(self):
        self.assertEqual(self.client.get("/api/events/1/").status_code, 404)
