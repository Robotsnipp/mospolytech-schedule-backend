from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework.test import APIClient

from .management.commands.seed_generation_settings_demo import seed_settings_demo
from .models import (
    Building,
    Group,
    LessonEvent,
    Location,
    ResourceProfile,
    Room,
    Semester,
    Student,
    Subject,
    Teacher,
)
from .services.resource_profiles import (
    parse_pr_room,
    resolve_resource_profile,
    room_nearness_key,
)


class ResourceProfilesTests(TestCase):
    def setUp(self):
        self.semester = Semester.objects.create(name="Исходный период", start_date=date(2026, 9, 1))
        self.group = Group.objects.create(name="251-3210 · демо без ПД", semester=self.semester)
        self.location = Location.objects.create(name="Прянишникова")
        self.building = Building.objects.create(location=self.location, name="2")
        self.room = Room.objects.create(name="Пр2609", building=self.building, capacity=99)
        self.profile = ResourceProfile.objects.create(
            key="test-demo", name="Допущение", semester=self.semester, group=self.group,
            rooms=[{"room_id": self.room.pk, "capacity_assumption": 24}])

    def test_assumption_is_effective_without_mutating_room_fact(self):
        rooms, snapshot = resolve_resource_profile("test-demo", self.semester, self.group)
        self.assertEqual(rooms[0].capacity, 24)
        self.assertEqual(snapshot[0]["capacity_source"], "Допущение демо")
        self.room.refresh_from_db()
        self.assertEqual(self.room.capacity, 99)
        self.assertIsNone(self.room.confirmed_capacity)
        self.room.confirmed_capacity = 20
        self.room.save()
        _, snapshot = resolve_resource_profile("test-demo", self.semester, self.group)
        self.assertEqual(snapshot[0]["effective_capacity"], 20)

    def test_unknown_capacity_inactive_or_foreign_pool_cannot_generate(self):
        self.profile.rooms = [{"room_id": self.room.pk}]
        self.profile.save()
        with self.assertRaises(ValidationError):
            resolve_resource_profile("test-demo", self.semester, self.group)
        with self.assertRaises(ValidationError):
            resolve_resource_profile("unknown", self.semester, self.group)

    def test_numbering_heuristic_preserves_suffix_and_does_not_guess_other_schemes(self):
        self.assertEqual(parse_pr_room("Пр2815 б"), {"building": 2, "floor": 8, "number": 15, "suffix": "б"})
        self.assertIsNone(parse_pr_room("4-ПР2815"))
        self.assertIsNone(parse_pr_room("ПК409"))
        reference = {"name": "Пр2816", "building_id": 2}
        nearby = {"name": "Пр2815 а", "building_id": 2}
        another_floor = {"name": "Пр2716", "building_id": 2}
        self.assertLess(room_nearness_key(reference, nearby), room_nearness_key(reference, another_floor))

    def test_seed_is_separate_and_keeps_original_events_and_default(self):
        # Remove the deliberately conflicting test catalog label before fresh import.
        self.profile.delete()
        self.room.delete()
        Student.objects.bulk_create([Student(group=self.group, full_name=f"Синтетический {n}") for n in range(30)])
        event = LessonEvent.objects.create(
            semester=self.semester, group=self.group, subject=Subject.objects.create(name="Дисциплина"),
            teacher=Teacher.objects.create(full_name="Преподаватель демо"), lesson_type="PRACTICE", audience_size=30)
        profile = seed_settings_demo(source_group_id=self.group.pk)
        self.assertNotEqual(profile.semester_id, self.semester.pk)
        self.assertEqual(seed_settings_demo().pk, profile.pk)
        self.assertEqual(profile.group.students.count(), 30)
        self.assertEqual(profile.semester.week_patterns.get().slots.count(), 49)
        self.assertEqual(profile.semester.events.count(), 1)
        event.refresh_from_db()
        self.assertEqual(event.group_id, self.group.pk)
        rooms, snapshots = resolve_resource_profile(profile.key, profile.semester, profile.group)
        self.assertEqual(len(rooms), 6)
        self.assertTrue(all(item["confirmed_capacity"] is None for item in snapshots))
        self.assertEqual(sorted(item["effective_capacity"] for item in snapshots), [20, 24, 30, 30, 30, 60])
        self.assertTrue(all(room.profile_only for room in rooms))
        response = APIClient().get("/api/schedule/generation-options/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["resource_profiles"]), 1)
