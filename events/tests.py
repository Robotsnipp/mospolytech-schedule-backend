import json
from datetime import date
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from .importer import import_events
from .models import Event

DATA_FILE = Path(settings.BASE_DIR) / "data" / "events" / "events_2025.json"


def load_data():
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


class ImportEventsTests(TestCase):
    def test_command_imports_all_events(self):
        out = StringIO()
        call_command("import_events", str(DATA_FILE), stdout=out)
        self.assertEqual(Event.objects.count(), 56)
        self.assertIn("Создано: 56", out.getvalue())

    def test_repeated_import_does_not_duplicate(self):
        import_events(load_data())
        result = import_events(load_data())
        self.assertEqual(result, {"created": 0, "updated": 56, "skipped": 0})
        self.assertEqual(Event.objects.count(), 56)

    def test_repeated_import_updates_fields(self):
        data = load_data()
        import_events(data)
        data["events"][0]["note"] = "новое примечание"
        import_events(data)
        event = Event.objects.get(url=data["events"][0]["url"], title=data["events"][0]["title"])
        self.assertEqual(event.note, "новое примечание")

    def test_bad_records_are_skipped(self):
        data = {"schema_version": 1, "events": [
            {"scope": "planet", "title": "x", "url": "https://example.com"},
            {"scope": "city", "title": "", "url": "https://example.com"},
            {"scope": "city", "title": "ok", "url": "https://example.com", "date_start": "bad"},
        ]}
        self.assertEqual(import_events(data), {"created": 0, "updated": 0, "skipped": 3})


class EventApiTests(TestCase):
    def setUp(self):
        import_events(load_data())
        self.client = APIClient()

    def get_list(self, **params):
        response = self.client.get("/api/events/", {"page_size": 1000, **params})
        self.assertEqual(response.status_code, 200)
        return response.json()["results"]

    def test_list_is_public_and_sorted(self):
        results = self.get_list()
        self.assertEqual(len(results), 56)
        keys = [r["date_start"] or r["announced_at"] for r in results]
        dated = [k for k in keys if k]
        self.assertEqual(dated, sorted(dated))
        # мероприятия совсем без даты стоят в конце
        self.assertEqual(keys[len(dated):], [None] * (len(keys) - len(dated)))

    def test_filters(self):
        city = self.get_list(scope="city")
        self.assertEqual(len(city), 6)
        self.assertTrue(all(r["scope"] == "city" for r in city))
        free = self.get_list(access="free")
        self.assertEqual(len(free), 11)
        self.assertEqual(len(self.get_list(year=2025)), 54)  # 2 записи без дат
        self.assertEqual(self.get_list(year=2024), [])
        self.assertEqual(len(self.get_list(scope="city", access="free", year=2025)), 5)
        may = self.get_list(date_from="2025-05-01", date_to="2025-05-31")
        self.assertTrue(all("2025-05" in (r["date_start"] or r["announced_at"]) for r in may))
        self.assertTrue(may)
        night = [r["title"] for r in self.get_list(q="ночь")]
        self.assertEqual(sorted(night), ["Ночь в музее (тема «Герои», 170 площадок)", "Ночь искусств"])

    def test_bad_filter_value_gives_400(self):
        self.assertEqual(self.client.get("/api/events/", {"year": "abc"}).status_code, 400)
        self.assertEqual(self.client.get("/api/events/", {"date_from": "1.05"}).status_code, 400)

    def test_unpublished_hidden(self):
        event = Event.objects.get(title="Ночь искусств")
        event.is_published = False
        event.save()
        self.assertNotIn(event.id, [r["id"] for r in self.get_list()])
        self.assertEqual(self.client.get(f"/api/events/{event.id}/").status_code, 404)

    def test_retrieve(self):
        event = Event.objects.get(title="Ночь искусств")
        response = self.client.get(f"/api/events/{event.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["date_start"], "2025-11-03")


class EventWriteTests(TestCase):
    payload = {"scope": "city", "title": "Тестовое событие", "url": "https://example.com/e",
               "date_start": "2025-12-01", "access": "free"}

    def setUp(self):
        self.client = APIClient()
        self.user = get_user_model().objects.create_user("student", password="x")
        self.admin = get_user_model().objects.create_user("admin", password="x", is_staff=True)

    def test_post_without_admin_rights_rejected(self):
        self.assertIn(self.client.post("/api/events/", self.payload, format="json").status_code,
                      (401, 403))
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.post("/api/events/", self.payload, format="json").status_code, 403)
        self.assertEqual(self.client.post("/api/events/import/", load_data(),
                                          format="json").status_code, 403)
        self.assertEqual(Event.objects.count(), 0)

    def test_admin_can_create(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post("/api/events/", self.payload, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Event.objects.get().date_start, date(2025, 12, 1))

    def test_admin_can_create_without_date(self):
        self.client.force_authenticate(self.admin)
        payload = {k: v for k, v in self.payload.items() if k != "date_start"}
        response = self.client.post("/api/events/", payload, format="json")
        self.assertEqual(response.status_code, 201, response.content)

    def test_admin_import_over_http(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post("/api/events/import/", load_data(), format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["created"], 56)
        bad = self.client.post("/api/events/import/", {"schema_version": 99, "events": []},
                               format="json")
        self.assertEqual(bad.status_code, 400)
