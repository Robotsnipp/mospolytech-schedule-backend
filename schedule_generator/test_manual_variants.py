"""Manual alternatives validate the entire future state and preserve the source."""

from datetime import date, time

from django.test import TestCase
from rest_framework.test import APIClient

from schedule_generator.models import (
    Building,
    GenerationRun,
    Group,
    LessonEvent,
    Location,
    ManualScheduleVariant,
    Room,
    ScheduledClass,
    Semester,
    Student,
    Subject,
    Teacher,
    TimeSlot,
    WeekPattern,
)
from schedule_generator.services.manual_variants import (
    ManualVariantError,
    preview_variant,
    read_variant,
    save_variant,
    suggest_rooms,
)
from schedule_generator.services.placement_validation import validate_placements


class ManualVariantTests(TestCase):
    def setUp(self):
        self.semester = Semester.objects.create(name="Manual test", start_date=date(2026, 9, 1))
        self.pattern = WeekPattern.objects.create(semester=self.semester)
        self.group = Group.objects.create(name="Manual demo")
        self.student = Student.objects.create(full_name="Студент 01", group=self.group)
        self.teacher = Teacher.objects.create(full_name="Преподаватель 01")
        location = Location.objects.create(name="Тестовая локация")
        self.building = Building.objects.create(location=location, name="2")
        self.rooms = [Room.objects.create(name=f"Пр260{i}", capacity=30, building=self.building) for i in range(3)]
        self.slots = [TimeSlot.objects.create(week_pattern=self.pattern, day_of_week=day,
                      lesson_number=number, start_time=time(9 + (number - 1) * 2),
                      end_time=time(10 + (number - 1) * 2, 30))
                      for day, number in [(0, 1), (0, 2), (1, 1), (1, 2), (5, 1)]]
        subject = Subject.objects.create(name="Предмет снимка")
        self.events = [LessonEvent.objects.create(semester=self.semester, group=self.group,
                       subject=subject, teacher=self.teacher, lesson_type="PRACTICE",
                       audience_size=30) for _ in range(2)]
        self.snapshot = {
            "schema_version": 1, "group_id": self.group.pk, "group_name": self.group.name,
            "semester_id": self.semester.pk, "module_id": None, "week_pattern_id": self.pattern.pk,
            "travel_buffer_slots": 1, "settings": {"schema_version": 1, "rules": {}},
            "students": [{"id": self.student.pk, "group_id": self.group.pk}],
            "events": [{"id": event.pk, "event_id": event.pk, "subject_id": subject.pk,
                        "subject_name": subject.name, "name": subject.name, "lesson_type": "PRACTICE",
                        "format": "ONSITE", "pairs_per_week": 1, "audience_size": 30,
                        "student_ids": [self.student.pk], "blocking_group_ids": [self.group.pk],
                        "teacher_id": self.teacher.pk, "teacher_name": self.teacher.full_name,
                        "group_id": self.group.pk, "group_name": self.group.name,
                        "allowed_days": list(range(7))} for event in self.events],
            "slots": [{"id": slot.pk, "day_of_week": slot.day_of_week,
                       "lesson_number": slot.lesson_number,
                       "start_minute": slot.start_time.hour * 60,
                       "end_minute": slot.end_time.hour * 60 + 30,
                       "start_time": slot.start_time.strftime("%H:%M"),
                       "end_time": slot.end_time.strftime("%H:%M")} for slot in self.slots],
            "rooms": [{"id": room.pk, "name": room.name, "building_id": self.building.pk,
                       "building_name": "2", "location_id": location.pk, "location_name": location.name,
                       "effective_capacity": 30, "capacity_source": "Допущение демо"} for room in self.rooms],
            "fixed_pd": [],
        }
        self.run = GenerationRun.objects.create(semester=self.semester, status="SOLVED",
                   settings_snapshot=self.snapshot["settings"], input_snapshot=self.snapshot)
        self.classes = [ScheduledClass.objects.create(run=self.run, event=event,
                        slot=self.slots[index], room=self.rooms[0]) for index, event in enumerate(self.events)]
        self.before = list(self.run.scheduled_classes.values("id", "event_id", "slot_id", "room_id"))
        self.client = APIClient()

    def changes(self, **fields):
        return [{"class_id": self.classes[0].pk, **fields}]

    def test_move_preserves_all_other_placements_and_source(self):
        result = save_variant(run_id=self.run.pk, changes=self.changes(slot_id=self.slots[2].pk), name="После переноса")
        self.assertEqual(result["variant"]["name"], "После переноса")
        self.assertEqual(result["placements"][1]["slot_id"], self.slots[1].pk)
        self.assertEqual(list(self.run.scheduled_classes.values("id", "event_id", "slot_id", "room_id")), self.before)
        self.assertEqual(GenerationRun.objects.count(), 1)

    def test_swap_is_atomic_not_sequential(self):
        changes = [{"class_id": self.classes[0].pk, "slot_id": self.slots[1].pk},
                   {"class_id": self.classes[1].pk, "slot_id": self.slots[0].pk}]
        result = save_variant(run_id=self.run.pk, changes=changes)
        self.assertTrue(result["validation"]["valid"])
        self.assertEqual([p["slot_id"] for p in result["placements"]], [self.slots[1].pk, self.slots[0].pk])

    def test_room_clear_persists_as_incomplete_after_reopening(self):
        result = save_variant(run_id=self.run.pk, changes=self.changes(room_id=None))
        reopened = read_variant(result["variant"]["id"])
        self.assertTrue(reopened["validation"]["valid"])
        self.assertFalse(reopened["validation"]["rooms_complete"])
        self.assertIsNone(reopened["scheduled_classes"][0]["room"])
        self.assertEqual(reopened["scheduled_classes"][0]["room_status"], "Нужно назначить аудиторию")

    def test_known_overlap_preview_and_save_share_blockers(self):
        changes = self.changes(slot_id=self.slots[1].pk)
        preview = preview_variant(run_id=self.run.pk, changes=changes)
        self.assertFalse(preview["validation"]["valid"])
        with self.assertRaises(ManualVariantError) as error:
            save_variant(run_id=self.run.pk, changes=changes)
        self.assertEqual(error.exception.validation, preview["validation"])
        self.assertFalse(ManualScheduleVariant.objects.exists())

    def test_clear_room_does_not_waive_people_conflict(self):
        result = preview_variant(run_id=self.run.pk, changes=self.changes(slot_id=self.slots[1].pk, room_id=None))
        codes = {item["code"] for item in result["validation"]["blocking"]}
        self.assertIn("attendee_overlap", codes)
        self.assertIn("teacher_overlap", codes)

    def test_unknown_source_and_foreign_class_rejected(self):
        response = self.client.post("/api/schedule/manual-variants/preview/", {"run_id": 999999, "changes": []}, format="json")
        self.assertEqual(response.status_code, 404)
        response = self.client.post("/api/schedule/manual-variants/preview/", {"run_id": self.run.pk,
                                   "changes": [{"class_id": 999999, "room_id": None}]}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_unknown_operation_fields_rejected(self):
        response = self.client.post("/api/schedule/manual-variants/preview/", {"run_id": self.run.pk,
                                   "changes": [{"class_id": self.classes[0].pk, "duration": 20}]}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("duration", str(response.data))

    def test_historical_runs_require_new_snapshot(self):
        self.run.input_snapshot = {}
        self.run.save()
        with self.assertRaisesRegex(ManualVariantError, "исторического"):
            preview_variant(run_id=self.run.pk)

    def test_effective_capacity_and_unknown_capacity(self):
        self.snapshot["rooms"][0]["effective_capacity"] = 24
        placements = [{"class_id": row.pk, "event_id": row.event_id, "slot_id": row.slot_id, "room_id": row.room_id} for row in self.classes]
        result = validate_placements(self.snapshot, placements)
        self.assertIn("room_capacity", {item["code"] for item in result["blocking"]})
        self.snapshot["rooms"][0]["effective_capacity"] = None
        result = validate_placements(self.snapshot, placements)
        self.assertTrue(result["valid"])
        self.assertIn("capacity_unknown", {item["code"] for item in result["incomplete"]})

    def test_fixed_pd_exact_overlap_and_buffer(self):
        self.snapshot["fixed_pd"] = [{"session_id": 1, "project_id": 1, "day_of_week": 1,
            "start_minute": 11 * 60, "end_minute": 12 * 60, "student_ids": [self.student.pk],
            "teacher_id": 999, "room_id": None, "place_raw": "Партнёр", "travel_buffer_minutes": 45}]
        self.run.input_snapshot = self.snapshot
        self.run.save()
        result = preview_variant(run_id=self.run.pk, changes=self.changes(slot_id=self.slots[2].pk))
        # 09:00–10:30 touches no PD time but 30min is short of the 45min buffer.
        self.assertIn("pd_travel_buffer", {item["code"] for item in result["validation"]["blocking"]})
        result = preview_variant(run_id=self.run.pk, changes=self.changes(slot_id=self.slots[3].pk))
        self.assertIn("attendee_overlap", {item["code"] for item in result["validation"]["blocking"]})

    def test_room_suggestions_use_future_state_and_capacity(self):
        # Give the second class a different audience/teacher so only its room blocks.
        self.snapshot["events"][1]["student_ids"] = []
        self.snapshot["events"][1]["blocking_group_ids"] = []
        self.snapshot["events"][1]["teacher_id"] = None
        self.snapshot["rooms"][2]["effective_capacity"] = 20
        self.run.input_snapshot = self.snapshot
        self.run.save()
        result = suggest_rooms(run_id=self.run.pk, class_id=self.classes[0].pk,
                              changes=self.changes(slot_id=self.slots[1].pk))
        self.assertEqual([room["id"] for room in result["rooms"]], [self.rooms[1].pk])

    def test_parent_chain_and_immutable_labels(self):
        original = save_variant(run_id=self.run.pk, changes=self.changes(room_id=None))
        variant_id = original["variant"]["id"]
        self.events[0].subject.name = "Изменённое имя справочника"
        self.events[0].subject.save()
        second = save_variant(run_id=self.run.pk, variant_id=variant_id, changes=self.changes(slot_id=self.slots[2].pk))
        self.assertEqual(second["variant"]["parent_variant_id"], variant_id)
        self.assertEqual(second["scheduled_classes"][0]["subject_name"], "Предмет снимка")
        self.assertIsNone(read_variant(variant_id)["scheduled_classes"][0]["room"])

    def test_http_save_list_and_retrieve(self):
        response = self.client.post("/api/schedule/manual-variants/", {"run_id": self.run.pk,
                                   "name": "Мой вариант", "changes": self.changes(room_id=None)}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        variant_id = response.data["variant"]["id"]
        listed = self.client.get(f"/api/schedule/manual-variants/?run_id={self.run.pk}")
        self.assertEqual(listed.data["results"][0]["id"], variant_id)
        loaded = self.client.get(f"/api/schedule/manual-variants/{variant_id}/")
        self.assertEqual(loaded.status_code, 200)
        self.assertFalse(loaded.data["validation"]["rooms_complete"])

    def test_required_rule_blocks_and_preferred_deviation_saves(self):
        self.snapshot["settings"]["rules"] = {"free_days": [{"day": "sat", "mode": "required"}]}
        self.run.input_snapshot = self.snapshot
        self.run.settings_snapshot = self.snapshot["settings"]
        self.run.save()
        changes = self.changes(slot_id=self.slots[4].pk)
        with self.assertRaises(ManualVariantError) as error:
            save_variant(run_id=self.run.pk, changes=changes)
        self.assertIn("free_days", {row["code"] for row in error.exception.validation["blocking"]})
        self.snapshot["settings"]["rules"]["free_days"][0]["mode"] = "preferred"
        self.run.input_snapshot = self.snapshot
        self.run.save()
        result = save_variant(run_id=self.run.pk, changes=changes)
        self.assertTrue(result["validation"]["valid"])
        self.assertIn("free_days", {row["code"] for row in result["validation"]["warnings"]})

    def test_required_building_becomes_incomplete_when_room_cleared(self):
        self.snapshot["settings"]["rules"] = {"preferred_building": {"value": self.building.pk, "mode": "required"}}
        self.run.input_snapshot = self.snapshot
        self.run.save()
        result = save_variant(run_id=self.run.pk, changes=self.changes(room_id=None))
        self.assertTrue(result["validation"]["valid"])
        self.assertIn("preferred_building", {row["code"] for row in result["validation"]["incomplete"]})

    def test_different_duration_and_duplicate_changes_rejected(self):
        self.snapshot["slots"][2]["end_minute"] += 10
        self.run.input_snapshot = self.snapshot
        self.run.save()
        with self.assertRaisesRegex(ManualVariantError, "продолжительность"):
            preview_variant(run_id=self.run.pk, changes=self.changes(slot_id=self.slots[2].pk))
        with self.assertRaisesRegex(ManualVariantError, "несколько"):
            preview_variant(run_id=self.run.pk, changes=self.changes(room_id=None) * 2)

    def test_wrong_variant_cannot_fall_back_to_source(self):
        response = self.client.post("/api/schedule/manual-variants/rooms/", {
            "run_id": self.run.pk, "variant_id": 999999, "class_id": self.classes[0].pk}, format="json")
        self.assertEqual(response.status_code, 404)

    def test_online_needs_no_room_and_cannot_assign_one(self):
        self.snapshot["events"][0]["format"] = "ONLINE"
        self.run.input_snapshot = self.snapshot
        self.run.save()
        result = save_variant(run_id=self.run.pk, changes=self.changes(room_id=None))
        self.assertTrue(result["validation"]["rooms_complete"])
        self.assertFalse(result["scheduled_classes"][0]["needs_room"])
        result = preview_variant(run_id=self.run.pk)
        self.assertIn("online_room", {row["code"] for row in result["validation"]["blocking"]})

    def test_known_location_travel_is_blocked_and_unknown_room_incomplete(self):
        self.snapshot["rooms"][1]["location_id"] = 999
        self.run.input_snapshot = self.snapshot
        self.run.save()
        result = preview_variant(run_id=self.run.pk, changes=self.changes(room_id=self.rooms[1].pk))
        self.assertIn("ordinary_travel_buffer", {row["code"] for row in result["validation"]["blocking"]})
        result = preview_variant(run_id=self.run.pk, changes=self.changes(room_id=None))
        self.assertTrue(result["validation"]["valid"])
        self.assertFalse(result["validation"]["rooms_complete"])

    def test_pd_travel_buffer_crosses_sunday_monday_boundary(self):
        self.snapshot["slots"][0]["start_minute"] = 10
        self.snapshot["slots"][0]["end_minute"] = 100
        self.snapshot["fixed_pd"] = [{"session_id": 1, "project_id": 1, "day_of_week": 6,
            "start_minute": 1410, "end_minute": 1439, "student_ids": [self.student.pk],
            "teacher_id": 999, "room_id": None, "place_raw": "Партнёр", "travel_buffer_minutes": 45}]
        placements = [{"class_id": row.pk, "event_id": row.event_id,
                       "slot_id": row.slot_id, "room_id": row.room_id} for row in self.classes]
        result = validate_placements(self.snapshot, placements)
        self.assertIn("pd_travel_buffer", {row["code"] for row in result["blocking"]})
        self.snapshot["fixed_pd"][0]["travel_buffer_minutes"] = 10
        result = validate_placements(self.snapshot, placements)
        self.assertNotIn("pd_travel_buffer", {row["code"] for row in result["blocking"]})
