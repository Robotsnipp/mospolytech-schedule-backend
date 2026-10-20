"""API, которым пользуется фронтенд: health, права, физкультура."""

from datetime import date, time

from django.test import TestCase

from .models import Group, PEDirection, PESlot, Semester, Student


class FrontendApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.semester = Semester.objects.create(name="тест", start_date=date(2026, 9, 1))
        cls.group = Group.objects.create(name="241-334", semester=cls.semester,
                                         admission_year=2024)
        cls.student = Student.objects.create(group=cls.group, full_name="Тест Т. Т.")
        direction = PEDirection.objects.create(code="yoga", name="Йога")
        cls.slot1 = PESlot.objects.create(direction=direction, day_of_week=0,
                                          start_time=time(9), end_time=time(10, 30))
        cls.slot2 = PESlot.objects.create(direction=direction, day_of_week=0,
                                          start_time=time(10, 40), end_time=time(12))
        cls.slot3 = PESlot.objects.create(direction=direction, day_of_week=1,
                                          start_time=time(9), end_time=time(10, 30))

    def test_health(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_reads_are_public_writes_are_not(self):
        self.assertEqual(self.client.get("/api/schedule/students/").status_code, 200)
        response = self.client.post("/api/schedule/students/", {"full_name": "x"})
        self.assertEqual(response.status_code, 403)

    def test_pe_enroll_rules(self):
        url = "/api/schedule/pe/enrollments/"
        ok = self.client.post(url, {"student": self.student.id, "slot": self.slot1.id})
        self.assertEqual(ok.status_code, 201)
        same_day = self.client.post(url, {"student": self.student.id, "slot": self.slot2.id})
        self.assertEqual(same_day.status_code, 400)
        other_day = self.client.post(url, {"student": self.student.id, "slot": self.slot3.id})
        self.assertEqual(other_day.status_code, 201)
        listed = self.client.get(url, {"student": self.student.id}).json()
        self.assertEqual(listed["count"], 2)
        self.assertEqual(self.client.delete(f"{url}{ok.json()['id']}/").status_code, 204)

    def test_pe_lists(self):
        self.assertEqual(self.client.get("/api/schedule/pe/directions/").json()["count"], 1)
        slots = self.client.get("/api/schedule/pe/slots/", {"direction": 1}).json()
        self.assertEqual(slots["count"], 3)
