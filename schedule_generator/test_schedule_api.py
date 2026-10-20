from datetime import date, time

from django.contrib.auth import get_user_model
from django.test import TestCase
from drf_spectacular.generators import SchemaGenerator
from rest_framework.test import APIClient

from schedule_generator.enums import Format, LessonType
from schedule_generator.models import (
    Building,
    FixedProjectSession,
    GenerationIssue,
    GenerationRun,
    Group,
    LectureStream,
    LessonEvent,
    Location,
    Module,
    Project,
    ProjectEnrollment,
    Room,
    ScheduledClass,
    Semester,
    Student,
    Subject,
    Teacher,
    TimeSlot,
    WeekPattern,
)


class ScheduleAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.semester = Semester.objects.create(name="Осень", start_date=date(2026, 9, 1))
        self.module = Module.objects.create(semester=self.semester, number=1,
            start_date=date(2026, 9, 1), end_date=date(2026, 10, 31), teaching_weeks=9)
        self.other_module = Module.objects.create(semester=self.semester, number=2,
            start_date=date(2026, 11, 1), end_date=date(2026, 12, 31), teaching_weeks=9)
        self.pattern = WeekPattern.objects.create(semester=self.semester)
        self.slot = TimeSlot.objects.create(week_pattern=self.pattern, day_of_week=1,
            lesson_number=1, start_time=time(9), end_time=time(10, 30))
        self.other_slot = TimeSlot.objects.create(week_pattern=self.pattern, day_of_week=3,
            lesson_number=2, start_time=time(10, 45), end_time=time(12, 15))
        self.group = Group.objects.create(name="251-3210", semester=self.semester)
        self.other_group = Group.objects.create(name="251-3211", semester=self.semester)
        self.student = Student.objects.create(group=self.group, full_name="Тестовый студент 1")
        self.peer = Student.objects.create(group=self.group, full_name="Тестовый студент 2")
        self.outsider = Student.objects.create(group=self.other_group, full_name="Тестовый студент 3")
        self.teacher = Teacher.objects.create(full_name="Тестовый преподаватель")
        self.other_teacher = Teacher.objects.create(full_name="Другой преподаватель")
        self.subject = Subject.objects.create(name="Математика")
        location = Location.objects.create(name="Тестовая локация", address="Тестовый адрес")
        building = Building.objects.create(location=location, name="1")
        self.room = Room.objects.create(name="101", capacity=30, building=building)
        self.project = Project.objects.create(semester=self.semester, title="ПД 1", supervisor=self.teacher)
        self.other_project = Project.objects.create(semester=self.semester, title="ПД 2",
                                                   supervisor=self.other_teacher)
        ProjectEnrollment.objects.create(student=self.student, project=self.project)
        ProjectEnrollment.objects.create(student=self.outsider, project=self.project)
        self.stream = LectureStream.objects.create(semester=self.semester, subject=self.subject)
        self.stream.groups.set([self.group, self.other_group])
        self.run = GenerationRun.objects.create(semester=self.semester, module=self.module, status="SOLVED",
                                               fixed_pd_snapshot=[self.snapshot(), self.snapshot(other=True)])
        self.practice = self.placed(group=self.group)
        self.lecture = self.placed(stream=self.stream, lesson_type=LessonType.LECTURE,
                                   teacher=self.other_teacher, slot=self.other_slot)
        self.unrelated = self.placed(group=self.other_group, teacher=self.other_teacher)

    def snapshot(self, other=False):
        project = self.other_project if other else self.project
        students = [self.peer] if other else [self.student, self.outsider]
        return {"session_id": 2 if other else 1, "project_id": project.pk, "project_title": project.title,
                "day_of_week": 0, "start": "13:00", "end": "16:00", "date": None,
                "occurrence_dates": ["2026-09-07", "2026-09-14"],
                "teacher_id": project.supervisor_id, "room_id": None,
                "student_ids": [s.pk for s in students],
                "student_group_ids": {str(s.pk): s.group_id for s in students},
                "participant_group_ids": sorted({s.group_id for s in students}),
                "travel_buffer_minutes": 30, "place_details": {"raw": "Тестовый адрес партнёра"},
                "source": {"url": "https://example.org/pd"}}

    def placed(self, *, slot=None, run=None, **overrides):
        values = dict(semester=self.semester, subject=self.subject, teacher=self.teacher,
                      lesson_type=LessonType.PRACTICE)
        values.update(overrides)
        event = LessonEvent.objects.create(**values)
        return ScheduledClass.objects.create(run=run or self.run, event=event, slot=slot or self.slot,
            room=None if event.effective_format == Format.ONLINE else self.room)

    def get_schedule(self, kind="students", holder=None, params=None):
        holder = holder or self.student
        return self.client.get(f"/api/schedule/schedule/{kind}/{holder.pk}/",
                               params if params is not None else {"run_id": self.run.pk})

    def test_student_gets_common_pairs_and_only_own_fixed_pd(self):
        response = self.get_schedule()
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(set(data), {"run", "scheduled_classes", "fixed_pd_snapshot", "issues"})
        self.assertEqual(data["run"]["id"], self.run.pk)
        self.assertEqual([c["id"] for c in data["scheduled_classes"]], [self.practice.pk, self.lecture.pk])
        self.assertEqual([p["project_id"] for p in data["fixed_pd_snapshot"]], [self.project.pk])
        pd = data["fixed_pd_snapshot"][0]
        self.assertEqual(pd["student_ids"], [self.student.pk])
        self.assertEqual((pd["run"], pd["teacher_name"], pd["room_id"], pd["start"], pd["end"]),
                         (self.run.pk, self.teacher.full_name, None, "13:00", "16:00"))
        self.assertEqual(pd["place_details"]["raw"], "Тестовый адрес партнёра")
        self.assertEqual(pd["occurrence_dates"], ["2026-09-07", "2026-09-14"])
        self.assertEqual(pd["travel_buffer_minutes"], 30)
        self.assertEqual(pd["format"], "ONSITE")

    def test_student_without_project_choice_gets_common_pairs_and_snapshot_membership(self):
        # Нет reverse OneToOne; фиксированная ПД определяется сохранённым составом.
        response = self.get_schedule(holder=self.peer)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["scheduled_classes"]), 2)
        self.assertEqual(response.data["fixed_pd_snapshot"][0]["project_id"], self.other_project.pk)
        self.run.fixed_pd_snapshot = []
        self.run.save()
        self.assertEqual(self.get_schedule(holder=self.peer).data["fixed_pd_snapshot"], [])

    def test_snapshot_is_preserved_when_project_choice_changes(self):
        enrollment = self.student.project_choice
        enrollment.project = self.other_project
        enrollment.save()
        data = self.get_schedule().data
        self.assertEqual([p["project_id"] for p in data["fixed_pd_snapshot"]], [self.project.pk])
        self.run.refresh_from_db()
        self.assertEqual(len(self.run.fixed_pd_snapshot[0]["student_ids"]), 2)

    def test_group_includes_pd_only_for_its_own_participants(self):
        data = self.get_schedule("groups", self.group).data
        self.assertEqual(len(data["scheduled_classes"]), 2)
        self.assertEqual([p["student_ids"] for p in data["fixed_pd_snapshot"]],
                         [[self.student.pk], [self.peer.pk]])
        self.assertTrue(all(p["participant_group_ids"] == [self.group.pk] for p in data["fixed_pd_snapshot"]))

    def test_group_snapshot_membership_survives_student_transfer(self):
        self.student.group = self.other_group
        self.student.save()
        data = self.get_schedule("groups", self.group).data
        self.assertEqual(data["fixed_pd_snapshot"][0]["student_ids"], [self.student.pk])

    def test_old_snapshot_without_labels_or_group_map_is_supported(self):
        del self.run.fixed_pd_snapshot[0]["student_group_ids"]
        self.run.save()
        data = self.get_schedule("groups", self.group).data
        self.assertEqual(data["fixed_pd_snapshot"][0]["student_ids"], [self.student.pk])
        self.assertEqual(data["fixed_pd_snapshot"][0]["teacher_name"], self.teacher.full_name)

    def test_teacher_gets_own_pairs_and_fixed_pd_from_same_run(self):
        data = self.get_schedule("teachers", self.teacher).data
        self.assertEqual([c["id"] for c in data["scheduled_classes"]], [self.practice.pk])
        self.assertEqual([p["project_id"] for p in data["fixed_pd_snapshot"]], [self.project.pk])
        self.assertEqual(data["fixed_pd_snapshot"][0]["student_ids"], [self.student.pk, self.outsider.pk])
        empty = Teacher.objects.create(full_name="Без занятий")
        data = self.get_schedule("teachers", empty).data
        self.assertEqual(data["run"]["id"], self.run.pk)
        self.assertEqual((data["scheduled_classes"], data["fixed_pd_snapshot"]), ([], []))

    def test_stream_has_only_its_lectures_and_no_pd(self):
        data = self.get_schedule("streams", self.stream).data
        self.assertEqual([c["id"] for c in data["scheduled_classes"]], [self.lecture.pk])
        self.assertEqual(data["fixed_pd_snapshot"], [])
        lecture = data["scheduled_classes"][0]
        self.assertEqual(lecture["format"], "ONLINE")
        self.assertEqual(lecture["stream_id"], self.stream.pk)
        self.assertEqual(lecture["group_ids"], [self.group.pk, self.other_group.pk])
        self.assertIsNone(lecture["room"])
        self.assertEqual(lecture["teacher_id"], self.other_teacher.pk)
        self.assertEqual(lecture["subject_id"], self.subject.pk)

    def test_generated_pd_is_included_and_duplicate_joins_do_not_duplicate_pairs(self):
        ProjectEnrollment.objects.create(student=self.peer, project=self.project)
        pd = self.placed(project=self.project, lesson_type=LessonType.PD)
        data = self.get_schedule("groups", self.group).data
        classes = [c for c in data["scheduled_classes"] if c["id"] == pd.pk]
        self.assertEqual(len(classes), 1)
        self.assertEqual(classes[0]["student_ids"], [self.student.pk, self.peer.pk])
        data = self.get_schedule().data
        self.assertEqual([c["student_ids"] for c in data["scheduled_classes"] if c["id"] == pd.pk],
                         [[self.student.pk]])

    def test_generated_pd_uses_supervisor_when_event_teacher_is_empty(self):
        pd = self.placed(project=self.project, lesson_type=LessonType.PD, teacher=None)
        data = self.get_schedule("teachers", self.teacher).data
        placed = next(c for c in data["scheduled_classes"] if c["id"] == pd.pk)
        self.assertEqual((placed["teacher_id"], placed["teacher_name"]),
                         (self.teacher.pk, self.teacher.full_name))

    def test_global_and_relevant_issues_are_included_for_partial_run(self):
        self.run.status = "PARTIAL"
        self.run.save()
        general = GenerationIssue.objects.create(run=self.run, text="Общее предупреждение")
        own = GenerationIssue.objects.create(run=self.run, event=self.practice.event, text="Для группы")
        GenerationIssue.objects.create(run=self.run, event=self.unrelated.event, text="Для другой группы")
        data = self.get_schedule().data
        self.assertEqual(data["run"]["status"], "PARTIAL")
        self.assertEqual([issue["id"] for issue in data["issues"]], [general.pk, own.pk])

    def test_all_schedule_returns_both_collections_without_project_duplication(self):
        response = self.client.get("/api/schedule/schedule/groups/", {"run_id": self.run.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["scheduled_classes"]), 3)
        self.assertEqual(len(response.data["fixed_pd_snapshot"]), 2)

    def test_explicit_run_does_not_mix_versions_and_legacy_alias_works(self):
        second = GenerationRun.objects.create(semester=self.semester, module=self.module,
                                              status="PARTIAL", fixed_pd_snapshot=[])
        self.placed(group=self.group, run=second)
        first = self.get_schedule(params={"run": self.run.pk}).data
        self.assertEqual(len(first["scheduled_classes"]), 2)
        self.assertEqual(len(first["fixed_pd_snapshot"]), 1)
        latest = self.get_schedule(params={"semester": self.semester.pk, "module": self.module.pk}).data
        self.assertEqual(latest["run"]["id"], second.pk)
        self.assertEqual(len(latest["scheduled_classes"]), 1)
        self.assertEqual(latest["fixed_pd_snapshot"], [])

    def test_default_run_skips_error_pending_and_other_modules(self):
        for status in ("ERROR", "PENDING", "INFEASIBLE"):
            GenerationRun.objects.create(semester=self.semester, module=self.module, status=status)
        GenerationRun.objects.create(semester=self.semester, module=self.other_module, status="SOLVED")
        response = self.get_schedule(params={"semester": self.semester.pk, "module": self.module.pk})
        self.assertEqual(response.data["run"]["id"], self.run.pk)

    def test_fixed_only_run_does_not_require_scheduled_classes(self):
        self.run.scheduled_classes.all().delete()
        data = self.get_schedule().data
        self.assertEqual(data["run"]["id"], self.run.pk)
        self.assertEqual(data["scheduled_classes"], [])
        self.assertEqual(len(data["fixed_pd_snapshot"]), 1)

    def test_empty_period_returns_explicit_empty_response(self):
        response = self.get_schedule(params={"semester": self.semester.pk, "module": self.other_module.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"run": None, "scheduled_classes": [], "fixed_pd_snapshot": [], "issues": []})

    def test_legacy_semester_without_module_still_resolves(self):
        legacy = GenerationRun.objects.create(semester=self.semester, status="SOLVED")
        response = self.get_schedule(params={"semester": self.semester.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["run"]["id"], legacy.pk)

    def test_invalid_query_and_period_mismatches_return_400(self):
        foreign = Semester.objects.create(name="Другой", start_date=date(2027, 9, 1))
        foreign_pattern = WeekPattern.objects.create(semester=foreign)
        queries = [{}, {"run_id": "bad"}, {"run_id": 0}, {"run_id": -1},
                   {"run_id": self.run.pk, "run": self.run.pk + 1},
                   {"run_id": self.run.pk, "semester": foreign.pk},
                   {"run_id": self.run.pk, "module": self.other_module.pk},
                   {"run_id": self.run.pk, "week_pattern": foreign_pattern.pk},
                   {"semester": foreign.pk, "module": self.module.pk},
                   {"semester": self.semester.pk},
                   {"semester": foreign.pk, "week_pattern": self.pattern.pk}]
        for query in queries:
            with self.subTest(query=query):
                self.assertEqual(self.get_schedule(params=query).status_code, 400)

    def test_nonexistent_run_and_holders_return_404(self):
        self.assertEqual(self.get_schedule(params={"run_id": 99999}).status_code, 404)
        for kind in ("students", "teachers", "groups", "streams"):
            response = self.client.get(f"/api/schedule/schedule/{kind}/99999/", {"run_id": self.run.pk})
            self.assertEqual(response.status_code, 404)
            self.assertEqual(self.client.get(f"/api/schedule/schedule/{kind}/abc/").status_code, 404)

    def test_week_pattern_filters_saved_pd_dates_and_common_classes(self):
        odd = WeekPattern.objects.create(semester=self.semester, name="Нечётная", parity=1)
        data = self.get_schedule(params={"run_id": self.run.pk, "week_pattern": odd.pk}).data
        self.assertEqual(data["run"]["id"], self.run.pk)
        self.assertEqual(data["scheduled_classes"], [])
        # 1 сентября — первая (нечётная) неделя, 7 сентября — вторая.
        self.assertEqual(data["fixed_pd_snapshot"][0]["occurrence_dates"], ["2026-09-14"])
        self.run.refresh_from_db()
        self.assertEqual(len(self.run.fixed_pd_snapshot[0]["occurrence_dates"]), 2)

    def test_room_and_teacher_labels_from_snapshot_are_retained(self):
        self.run.fixed_pd_snapshot[0].update(room_id=self.room.pk, room_name="Сохранённая комната",
            location_name="Сохранённая локация", teacher_name="Сохранённый преподаватель")
        self.run.save()
        pd = self.get_schedule().data["fixed_pd_snapshot"][0]
        self.assertEqual(pd["room_name"], "Сохранённая комната")
        self.assertEqual(pd["teacher_name"], "Сохранённый преподаватель")
        regular = self.get_schedule().data["scheduled_classes"][0]
        self.assertEqual((regular["room_name"], regular["location_name"], regular["format"]),
                         ("101", "Тестовая локация", "ONSITE"))

    def test_read_queries_do_not_grow_per_class(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as first:
            self.get_schedule("groups", self.group)
        for number in range(3, 13):
            slot = TimeSlot.objects.create(week_pattern=self.pattern, day_of_week=3,
                                          lesson_number=number)
            self.placed(stream=self.stream, lesson_type=LessonType.LECTURE, slot=slot)
        with CaptureQueriesContext(connection) as second:
            response = self.get_schedule("groups", self.group)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(first), len(second))

    def test_real_generate_then_read_student_group_and_teacher(self):
        # Минимальный полный HTTP-проход с настоящим CP-SAT и сохранённой ПД.
        self.run.scheduled_classes.all().delete()
        LessonEvent.objects.all().delete()
        LessonEvent.objects.create(semester=self.semester, module=self.module, subject=self.subject,
            group=self.group, teacher=self.teacher, lesson_type=LessonType.PRACTICE)
        FixedProjectSession.objects.create(project=self.project, module=self.module, day_of_week=0,
            start_time=time(13), end_time=time(16), travel_buffer_minutes=30,
            place_details={"raw": "Тестовый партнёр"}, source_key="manual:api-test")
        generated = self.client.post("/api/schedule/generate/", {"semester": self.semester.pk,
            "module": self.module.pk, "clear_previous": False, "time_limit_seconds": 2}, format="json")
        self.assertEqual(generated.status_code, 201, generated.data)
        run_id = generated.data["id"]
        self.assertEqual(generated.data["status"], "SOLVED")
        for kind, holder in (("students", self.student), ("groups", self.group), ("teachers", self.teacher)):
            response = self.get_schedule(kind, holder, {"run_id": run_id})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["run"]["id"], run_id)
            self.assertEqual(len(response.data["scheduled_classes"]), 1)
            self.assertEqual(len(response.data["fixed_pd_snapshot"]), 1)
            pd = response.data["fixed_pd_snapshot"][0]
            self.assertEqual(pd["teacher_name"], self.teacher.full_name)
        stored = GenerationRun.objects.get(pk=run_id).fixed_pd_snapshot[0]
        self.assertEqual(stored["student_group_ids"][str(self.student.pk)], self.group.pk)

    def test_run_history_filters_module_and_counts_distinct_classes_and_issues(self):
        user = get_user_model().objects.create_user(username="api-test", is_staff=True)
        self.client.force_authenticate(user)
        for text in ("Первое", "Второе"):
            GenerationIssue.objects.create(run=self.run, text=text)
        GenerationRun.objects.create(semester=self.semester, module=self.other_module, status="SOLVED")
        response = self.client.get("/api/schedule/runs/", {"module": self.module.pk})
        rows = response.data.get("results", response.data) if isinstance(response.data, dict) else response.data
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["classes_count"], rows[0]["issues_count"]), (3, 2))

    def test_openapi_describes_envelope_and_query_fields(self):
        schema = SchemaGenerator().get_schema(public=True)
        operation = schema["paths"]["/api/schedule/schedule/students/{id}/"]["get"]
        self.assertEqual(operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"],
                         "#/components/schemas/ScheduleResponse")
        parameters = {p["name"]: p for p in operation["parameters"]}
        self.assertIn("run_id", parameters)
        self.assertFalse(parameters["semester"].get("required", False))
        self.assertIn("fixed_pd_snapshot", schema["components"]["schemas"]["ScheduleResponse"]["properties"])
