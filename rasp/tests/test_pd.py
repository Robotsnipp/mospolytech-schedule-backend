import csv
import io
from datetime import time

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from rasp.models import Group, Lesson, PDEnrollment, PDProject, Subject
from rasp.pd_parser import PDParseError, parse_csv
from rasp.pd_service import store_projects

HEADER = ["№", "проектное направление", "название проекта", "ФИО преподавателя",
          "контактная информация", "понедельник", "среда"]


def table(*rows):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["ЦЕНТР ПРОЕКТНОЙ ДЕЯТЕЛЬНОСТИ"])
    w.writerow(HEADER)
    w.writerows(rows)
    return out.getvalue()


class PDParserTests(SimpleTestCase):
    def test_regular_cell_and_room(self):
        p = parse_csv(table(["1", "Дизайн", "ArtProfit [1]", "Иванова И.И.", "", "",
                             "среда\n14:30-16:00\n16:10-17:40\nД307"]))[0]
        self.assertEqual([(s.day_of_week, s.start, s.room) for s in p.slots],
                         [(2, time(14, 30), "Д307"), (2, time(16, 10), "Д307")])
        self.assertFalse(p.irregular)

    def test_both_days_and_multiline_room(self):
        p = parse_csv(table(["2", "IT", "Робот", "Петров П.П.", "a@b.ru",
                             "понедельник\n09:00-10:30\nРобостанция. ВДНХ\n(лаборатория)",
                             "среда\n10:40-12:10 12:20-13:50\nА12"]))[0]
        self.assertEqual(len(p.slots), 3)
        self.assertEqual(p.slots[0].room, "Робостанция. ВДНХ (лаборатория)")
        self.assertEqual({s.day_of_week for s in p.slots}, {0, 2})

    def test_notes_do_not_become_slots(self):
        p = parse_csv(table(["3", "IT", "Бизнес", "Сидоров С.С.", "",
                             "понедельник\n10:40-12:10\nВ311\n12.10 занятие в\n17:50-19:20\nА13", ""]))[0]
        self.assertEqual([s.start for s in p.slots], [time(10, 40)])
        self.assertIn("17:50-19:20", p.note)
        self.assertTrue(p.irregular)

    def test_transfer_only_cell_is_note(self):
        p = parse_csv(table(["4", "IT", "Гонки", "Кто-то", "",
                             "перенос на среду 14 октября\n14:30-16:00 Н203", "среда\n14:30-16:00\nПК312"]))[0]
        self.assertEqual([(s.day_of_week, s.room) for s in p.slots], [(2, "ПК312")])
        self.assertIn("перенос", p.note)

    def test_date_prefixed_cell_keeps_slots_of_the_column_day(self):
        p = parse_csv(table(["5", "IT", "Проект", "Кто-то", "", "12 октября\n16:10-17:40\nАВ4211А-1", ""]))[0]
        self.assertEqual([(s.day_of_week, s.start) for s in p.slots], [(0, time(16, 10))])

    def test_bad_table(self):
        with self.assertRaises(PDParseError):
            parse_csv("a,b\n1,2\n")


class PDApiTests(TestCase):
    OWNER = "browser-owner-1"

    def setUp(self):
        store_projects(table(
            ["1", "IT", "Проект А", "Иванов И.И.", "", "понедельник\n09:00-10:30\nА1", ""],
            ["2", "IT", "Проект Б", "Петров П.П.", "", "", "среда\n10:40-12:10\nБ2"],
            ["3", "Дизайн", "Проект В", "Сидоров С.С.", "", "понедельник\n14:30-16:00\nВ3", ""]))

    def test_store_is_idempotent_and_replaces(self):
        again = store_projects(table(["1", "IT", "Проект А", "Иванов И.И.", "", "понедельник\n09:00-10:30\nА1", ""]))
        self.assertEqual(again["removed"], 2)
        self.assertEqual(PDProject.objects.count(), 1)
        store_projects(table(["1", "IT", "Проект А", "Иванов И.И.", "", "понедельник\n09:00-10:30\nА1", ""]))
        self.assertEqual(PDProject.objects.count(), 1)

    def test_directions_and_projects(self):
        names = {d["name"]: d["count"] for d in self.client.get("/api/rasp/pd/directions/").json()}
        self.assertEqual(names, {"IT": 2, "Дизайн": 1})
        data = self.client.get("/api/rasp/pd/projects/?direction=IT").json()
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["slots"][0]["pair"], 1)
        self.assertEqual(len(self.client.get("/api/rasp/pd/projects/?q=Сидоров").json()), 1)

    def test_enroll_replace_and_delete(self):
        url = "/api/rasp/pd/enrollments/"
        a, b = PDProject.objects.get(number=1), PDProject.objects.get(number=2)
        first = self.client.post(url, {"owner": self.OWNER, "project": a.id, "group_title": "231-333"})
        self.assertEqual(first.status_code, 201)
        second = self.client.post(url, {"owner": self.OWNER, "project": b.id})
        self.assertEqual(second.status_code, 201)
        listed = self.client.get(url, {"owner": self.OWNER}).json()
        self.assertEqual([e["project"]["number"] for e in listed], [2])
        self.assertEqual(self.client.get(url).json(), [])
        eid = listed[0]["id"]
        self.assertEqual(self.client.delete(f"{url}{eid}/?owner=other").status_code, 403)
        self.assertEqual(self.client.delete(f"{url}{eid}/?owner={self.OWNER}").status_code, 204)
        self.assertEqual(PDEnrollment.objects.count(), 0)
        self.assertEqual(self.client.post(url, {"owner": "x", "project": a.id}).status_code, 400)
        self.assertEqual(self.client.post(url, {"owner": self.OWNER, "project": 9999}).status_code, 404)

    def test_conflict_with_group_lessons_but_not_with_pd_placeholder(self):
        group = Group.objects.create(title="231-333")
        subject = Subject.objects.create(name="Матан")
        # пара 1 в понедельник пересекается с проектом А (09:00–10:30)
        Lesson.objects.create(group=group, subject=subject, weekday=1, pair=1, location="Д")
        # заглушка ПД в расписании группы (место «ПД») конфликтом не считается
        Lesson.objects.create(group=group, subject=subject, weekday=1, pair=4, location="ПД")
        url = "/api/rasp/pd/enrollments/"
        a, c = PDProject.objects.get(number=1), PDProject.objects.get(number=3)
        bad = self.client.post(url, {"owner": self.OWNER, "project": a.id, "group_title": "231-333"})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("Матан", bad.json()["detail"])
        ok = self.client.post(url, {"owner": self.OWNER, "project": c.id, "group_title": "231-333"})
        self.assertEqual(ok.status_code, 201)

    def test_import_command_uses_cached_csv(self):
        call_command("import_pd", verbosity=0)
        self.assertEqual(PDProject.objects.count(), 323)

    def test_pe_enrollment_conflicts_with_chosen_project(self):
        from schedule_generator.models import PEDirection, PESlot

        project = PDProject.objects.get(number=1)  # понедельник 09:00–10:30
        self.client.post("/api/rasp/pd/enrollments/", {"owner": self.OWNER, "project": project.id})
        direction = PEDirection.objects.create(code="x", name="X")
        clash = PESlot.objects.create(direction=direction, day_of_week=0,
                                      start_time=time(9, 0), end_time=time(10, 30))
        response = self.client.post("/api/schedule/pe/enrollments/", {"owner": self.OWNER, "slot": clash.id})
        self.assertEqual(response.status_code, 400)
        self.assertIn("проектной деятельностью", str(response.json()))
