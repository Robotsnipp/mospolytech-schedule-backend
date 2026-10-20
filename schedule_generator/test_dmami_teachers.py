"""Teacher identity, backfill and published-schedule API regression tests."""

import copy
import json

from django.test import TestCase

from schedule_generator.models import ImportedLesson, ScheduleImport, Teacher
from schedule_generator.services.dmami_import import import_dmami_schedule


def sample(raw, *, structured=None, suffix=""):
    lesson = {"group": "241-3210", "source_path": "grid.2.1[0]", "day_of_week": 2,
              "lesson_number": 1, "subject": "Math" + suffix, "teacher_raw": raw,
              "teachers": structured or [], "date_from": "2025-02-03", "date_to": "2025-06-08",
              "start": "09:00", "end": "10:30", "raw": {"teacher": raw}}
    return {"kind": "dmami_schedule", "schema_version": 1, "complete": True,
            "exported_count": 1, "errors": [], "source_metadata": {"date": "2025-04-30"},
            "groups": [{"name": "241-3210", "group": {"title": "241-3210"}, "lessons": [lesson]}]}


def load(document, **kwargs):
    return import_dmami_schedule(json.dumps(document, ensure_ascii=False).encode(), **kwargs)


class DMAMITeacherTests(TestCase):
    def test_multiple_teachers_and_normalized_names_share_stable_ids(self):
        first = load(sample("Иванов Иван Иванович, Петров Пётр Петрович, Вакансия ХТОПП"))
        ids = set(ImportedLesson.objects.get().teachers.values_list("pk", flat=True))
        self.assertEqual(len(ids), 2)
        self.assertEqual(first["teachers_created"], 2)
        self.assertEqual(first["vacancy_labels"], 1)
        second = load(sample("  иванов   иван\u00a0иванович, Петров Пётр Петрович  ", suffix="2"))
        self.assertEqual(second["teachers_created"], 0)
        self.assertEqual(set(ImportedLesson.objects.latest("pk").teachers.values_list("pk", flat=True)), ids)
        self.assertEqual(Teacher.objects.count(), 2)

    def test_initials_and_different_external_ids_are_not_fuzzy_merged(self):
        load(sample("Иванов И.И., Иванов Иван Иванович"))
        load(sample("", structured=[{"id": 10, "name": "Иванов Иван Иванович"},
                                    {"id": 20, "name": "Иванов Иван Иванович"}]))
        self.assertEqual(Teacher.objects.count(), 4)
        self.assertEqual(ImportedLesson.objects.latest("pk").teachers.count(), 2)

    def test_structured_ids_have_priority_over_raw_and_no_duplicates(self):
        document = sample("Other Person", structured=[{"id": 123, "name": "Source Teacher"}])
        load(document)
        teacher = Teacher.objects.get()
        self.assertEqual(teacher.dmami_identity, "dmami:id:123")
        self.assertEqual(teacher.full_name, "Source Teacher")
        load(sample("Other", structured=[{"id": "123", "name": "Source Teacher"}], suffix="2"))
        self.assertEqual(Teacher.objects.count(), 1)
        self.assertEqual(teacher.imported_lessons.count(), 2)

    def test_repeat_backfills_existing_import_without_new_batch_or_teachers(self):
        document = sample("Teacher One")
        first = load(document)
        lesson = ImportedLesson.objects.get()
        ids = list(lesson.teachers.values_list("pk", flat=True))
        lesson.teachers.clear()
        repeated = load(document)
        self.assertEqual(repeated["import_id"], first["import_id"])
        self.assertFalse(repeated["created"])
        self.assertEqual(list(lesson.teachers.values_list("pk", flat=True)), ids)
        self.assertEqual(ScheduleImport.objects.count(), 1)
        self.assertEqual(repeated["teachers_created"], 0)

    def test_dry_run_and_invalid_structured_teacher_roll_back_all_links(self):
        load(sample("Teacher One"), dry_run=True)
        self.assertEqual(Teacher.objects.count(), 0)
        document = sample("", structured=[{"name": "Valid", "id": 1}])
        other = copy.deepcopy(document["groups"][0]["lessons"][0])
        other["source_path"] = "grid.2.1[1]"
        other["teachers"] = [{"id": "invalid", "name": "Bad"}]
        document["groups"][0]["lessons"].append(other)
        with self.assertRaises(ValueError):
            load(document)
        self.assertEqual(Teacher.objects.count(), 0)
        self.assertEqual(ImportedLesson.objects.count(), 0)

    def test_empty_or_vacant_labels_do_not_invent_teachers(self):
        load(sample("Вакансия-2, ,"))
        self.assertEqual(Teacher.objects.count(), 0)
        self.assertEqual(ImportedLesson.objects.get().teacher_warnings[0]["code"], "vacancy")


class DMAMITeacherAPITests(TestCase):
    def setUp(self):
        self.first = load(sample("Иванов Иван Иванович"))
        self.teacher = Teacher.objects.get()
        self.second = load(sample("Иванов Олег Олегович", suffix="2"))

    def test_search_cyrillic_and_list_import_filter(self):
        response = self.client.get("/api/schedule/imported/teachers/", {"search": "  иВАНОВ   иВАН "})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)
        self.assertEqual(response.json()["results"][0]["id"], self.teacher.pk)
        response = self.client.get("/api/schedule/imported/teachers/", {"import_id": self.first["import_id"]})
        self.assertEqual(response.json()["count"], 1)

    def test_schedule_filters_by_exact_id_and_requested_import(self):
        url = f"/api/schedule/imported/teachers/{self.teacher.pk}/schedule/"
        response = self.client.get(url, {"import_id": self.first["import_id"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)
        result = response.json()["results"][0]
        self.assertEqual(result["teachers"][0]["id"], self.teacher.pk)
        self.assertEqual(result["group_name"], "241-3210")
        self.assertEqual(result["date_from"], "2025-02-03")
        self.assertEqual(response.json()["schedule_import"]["source_metadata"]["date"], "2025-04-30")
        latest = self.client.get(url)
        self.assertEqual(latest.json()["schedule_import"]["id"], self.second["import_id"])
        self.assertEqual(latest.json()["count"], 0)

    def test_invalid_parameters_and_unknown_ids(self):
        url = f"/api/schedule/imported/teachers/{self.teacher.pk}/schedule/"
        self.assertEqual(self.client.get(url, {"import_id": "bad"}).status_code, 400)
        self.assertEqual(self.client.get(url, {"import_id": 999999}).status_code, 404)
        self.assertEqual(self.client.get("/api/schedule/imported/teachers/999999/schedule/").status_code, 404)
        self.assertEqual(self.client.post(url).status_code, 405)

    def test_schedule_is_paginated_without_duplicating_shared_teacher(self):
        data = sample("Shared One, Shared Two", suffix="paged")
        data["groups"][0]["lessons"] = [
            {**data["groups"][0]["lessons"][0], "source_path": f"grid.2.1[{i}]"} for i in range(11)
        ]
        imported = load(data)
        teacher = Teacher.objects.get(full_name="Shared One")
        response = self.client.get(f"/api/schedule/imported/teachers/{teacher.pk}/schedule/",
                                   {"import_id": imported["import_id"]})
        self.assertEqual(response.json()["count"], 11)
        self.assertEqual(len(response.json()["results"]), 10)
        self.assertIsNotNone(response.json()["next"])
