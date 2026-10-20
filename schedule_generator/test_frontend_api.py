"""API, которым пользуется фронтенд: health, права, физкультура."""

from datetime import date, time

from django.test import TestCase

from rasp.models import Group, Lesson, Subject

from .models import PEDirection, PESlot

OWNER = "browser-client-1"


class FrontendApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        direction = PEDirection.objects.create(code="yoga", name="Йога")
        cls.slot1 = PESlot.objects.create(direction=direction, day_of_week=0,
                                          start_time=time(9), end_time=time(10, 30))
        cls.slot2 = PESlot.objects.create(direction=direction, day_of_week=0,
                                          start_time=time(10, 40), end_time=time(12))
        cls.slot3 = PESlot.objects.create(direction=direction, day_of_week=1,
                                          start_time=time(9), end_time=time(10, 30))
        group = Group.objects.create(title="231-333")
        subject = Subject.objects.create(name="Матан")
        # Вторник (weekday=2), 1 пара — пересекается со slot3.
        Lesson.objects.create(group=group, subject=subject, weekday=2, pair=1,
                              date_to=date(2099, 1, 1))

    def test_health(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_writes_to_reference_data_need_admin(self):
        response = self.client.post("/api/schedule/students/", {"full_name": "x"})
        self.assertEqual(response.status_code, 403)

    def test_pe_enroll_rules(self):
        url = "/api/schedule/pe/enrollments/"
        body = {"owner": OWNER, "group_title": "231-333"}
        ok = self.client.post(url, {**body, "slot": self.slot1.id})
        self.assertEqual(ok.status_code, 201)
        same_day = self.client.post(url, {**body, "slot": self.slot2.id})
        self.assertEqual(same_day.status_code, 400)
        conflict = self.client.post(url, {**body, "slot": self.slot3.id})
        self.assertEqual(conflict.status_code, 400)
        self.assertIn("Матан", str(conflict.json()))
        free_group = self.client.post(url, {"owner": OWNER, "slot": self.slot3.id})
        self.assertEqual(free_group.status_code, 201)
        listed = self.client.get(url, {"owner": OWNER}).json()
        self.assertEqual(listed["count"], 2)
        self.assertEqual(self.client.get(url).json()["count"], 0)

    def test_pe_delete_only_by_owner(self):
        url = "/api/schedule/pe/enrollments/"
        created = self.client.post(url, {"owner": OWNER, "slot": self.slot1.id}).json()
        self.assertEqual(self.client.delete(f"{url}{created['id']}/?owner=other").status_code, 403)
        self.assertEqual(self.client.delete(f"{url}{created['id']}/?owner={OWNER}").status_code, 204)

    def test_pe_lists(self):
        self.assertEqual(self.client.get("/api/schedule/pe/directions/").json()["count"], 1)
        slots = self.client.get("/api/schedule/pe/slots/", {"direction": 1}).json()
        self.assertEqual(slots["count"], 3)
