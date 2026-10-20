import unittest
from html import escape

from prototypes.activity_parser.pd import (
    SheetParser,
    dates_in,
    expand_table,
    inherit_missing_places,
    parse_pd,
    parse_schedule_cell,
)

LEGEND = {"АВ": "ул. Автозаводская", "Пр": "ул. Прянишникова", "Д": "ул. Дубровская",
          "default": "ул. Большая Семёновская"}


class ScheduleCellTests(unittest.TestCase):
    def parse(self, raw, day=0):
        return parse_schedule_cell(raw, day, 2026, LEGEND)

    def test_shared_room_is_attached_to_each_time(self):
        result = self.parse("понедельник\n09:00-10:30\n10:40-12:10\nД307")
        self.assertEqual([m["place_raw"] for m in result["meetings"]], ["Д307", "Д307"])
        self.assertEqual(result["warnings"], [])

    def test_each_pair_can_have_a_different_room(self):
        result = self.parse("среда\n16:10-17:40\nПр1211\n19:30-21:00\nПр1105", 2)
        self.assertEqual([m["room_candidates"][0]["room"] for m in result["meetings"]], ["1211", "1105"])

    def test_inline_room_and_original_nonstandard_time(self):
        result = self.parse("понедельник\n09:00-10:20 Н203")
        meeting = result["meetings"][0]
        self.assertEqual(meeting["end"], "10:20")
        self.assertIsNone(meeting["lesson_number"])
        self.assertIn("nonstandard_time", meeting["timing_notes"])
        self.assertEqual(meeting["blocked_lesson_numbers"], [1])
        self.assertEqual(meeting["place_raw"], "Н203")

    def test_dated_override_is_not_repeated_weekly(self):
        result = self.parse("понедельник\n14:30-16:00\nВ406\n12 октября\n13:00-16:00\nаудитория А100")
        regular, exception = result["meetings"]
        self.assertEqual((regular["recurrence"], regular["place_raw"]), ("weekly_candidate", "В406"))
        self.assertEqual((exception["recurrence"], exception["date"]), ("dated", "2026-10-12"))
        self.assertEqual(exception["place_raw"], "аудитория А100")

    def test_transfer_changes_day_and_keeps_review_warning(self):
        result = self.parse("перенос на среду 14 октября\n14:30-16:00 Н203")
        meeting = result["meetings"][0]
        self.assertEqual((meeting["day_of_week"], meeting["date"]), (2, "2026-10-14"))
        self.assertEqual(meeting["recurrence"], "dated")
        self.assertIn("date_or_change_in_source", result["warnings"])

    def test_two_transfer_dates_do_not_get_collapsed_into_one(self):
        result = self.parse("понедельник\n12:20-13:50\nПр2411\n28.09 перенос на 12.10")
        self.assertEqual(len(result["meetings"]), 1)
        self.assertIsNone(result["notes"][-1]["date"])
        self.assertEqual(len(result["notes"][-1]["date_mentions"]), 2)
        self.assertIn("reschedule_requires_review", result["warnings"])
        self.assertEqual((result["changes"][0]["from_date"], result["changes"][0]["to_date"]),
                         ("2026-09-28", "2026-10-12"))
        self.assertIsNone(result["changes"][0]["times"])

    def test_dated_note_without_times_never_inherits_regular_times(self):
        result = self.parse("понедельник\n09:00-10:30\nПК323\n12 октября занятие по адресу ул. Правды, д.24")
        self.assertEqual(len(result["meetings"]), 1)
        self.assertIn("ул. Правды", result["notes"][-1]["text"])
        self.assertEqual(result["notes"][-1]["date"], "2026-10-12")
        place = result["notes"][-1]["place_details"]
        self.assertEqual(place["address_raw"], "ул. Правды, д.24")
        self.assertEqual(place["venue"]["kind"], "additional")
        self.assertFalse(place["resource_policy"]["reserve_room"])
        self.assertFalse(place["resource_policy"]["allow_new_assignments"])

    def test_additional_venue_policy_is_preserved_in_dated_inheritance(self):
        schedule = self.parse("понедельник\n09:00-10:30\nНАМИ, ул. Автомоторная, д. 2, Б204\n12 октября\n10:40-12:10")
        inherit_missing_places([schedule])
        meeting = schedule["meetings"][1]
        self.assertTrue(meeting["place_inherited"])
        self.assertEqual(meeting["place_details"]["venue"]["kind"], "additional")
        self.assertFalse(meeting["place_details"]["resource_policy"]["reserve_room"])
        self.assertTrue(meeting["place_details"]["resource_policy"]["block_attendees"])
        self.assertTrue(meeting["place_details"]["resource_policy"]["block_teacher"])
        self.assertTrue(meeting["place_details"]["resource_policy"]["travel_time_required"])

    def test_multiple_rooms_remain_candidates(self):
        result = self.parse("понедельник\n09:00-10:30\nД501, Д410")
        self.assertEqual(len(result["meetings"][0]["room_candidates"]), 2)
        self.assertEqual(result["meetings"][0]["place_details"]["room_assignment_mode"],
                         "listed_without_student_assignment")

    def test_invalid_time_and_missing_place_are_visible(self):
        result = self.parse("понедельник\n25:00-26:30")
        self.assertIsNone(result["meetings"][0]["start"])
        self.assertIn("invalid_time", result["warnings"])
        self.assertIn("missing_place", result["warnings"])

    def test_dotted_times_are_not_dates(self):
        self.assertEqual(dates_in("09.00-10.30", 2026), [])
        self.assertEqual(self.parse("09.00-10.30\nД307")["warnings"], [])

    def test_unknown_year_and_invalid_calendar_date_are_not_invented(self):
        self.assertIsNone(dates_in("12 октября", None)[0]["date"])
        self.assertIsNone(dates_in("31.02", 2026)[0]["date"])

    def test_inline_dated_time_and_teacher_override_do_not_contaminate_room(self):
        result = self.parse("12.10 занятие в 19:30-21:00\nА13\nПетухов И.С.")
        meeting = result["meetings"][0]
        self.assertEqual(meeting["date"], "2026-10-12")
        self.assertEqual(meeting["place_raw"], "А13")
        self.assertEqual(meeting["room_candidates"][0]["label"], "А13")
        self.assertEqual(meeting["teacher_override_raw"], ["Петухов И.С."])
        self.assertIn("teacher_override_requires_review", result["warnings"])

    def test_time_introduction_does_not_become_a_place(self):
        result = self.parse("14.10\nзанятие в 17:50-19:20\nВ206")
        self.assertEqual(result["meetings"][0]["place_raw"], "В206")
        self.assertEqual(result["meetings"][0]["teacher_override_raw"], [])

    def test_ambiguous_multi_date_note_still_requires_review(self):
        result = self.parse("понедельник\n09:00-10:30\nН203\n12.10 или 19.10")
        self.assertIn("multiple_dates_require_review", result["warnings"])
        self.assertEqual(result["changes"], [])

    def test_dated_change_inherits_unique_base_place(self):
        schedule = self.parse("среда\n16:10-17:40\n17:50-19:20\nПК505\n7 октября занятие в\n17:50-19:20\n19:30-21:00", 2)
        raw = schedule["raw"]
        inherit_missing_places([schedule])
        self.assertEqual([m["place_raw"] for m in schedule["meetings"]], ["ПК505"] * 4)
        self.assertTrue(schedule["meetings"][2]["place_inherited"])
        self.assertNotIn("missing_place", schedule["warnings"])
        self.assertEqual(schedule["raw"], raw)

    def test_inheritance_never_overwrites_explicit_override(self):
        schedule = self.parse("понедельник\n09:00-10:30\nА13\n12 октября\n10:40-12:10\nВ206")
        inherit_missing_places([schedule])
        self.assertEqual(schedule["meetings"][1]["place_raw"], "В206")
        self.assertFalse(schedule["meetings"][1]["place_inherited"])

    def test_multiple_base_places_do_not_get_arbitrarily_inherited(self):
        schedule = self.parse("понедельник\n09:00-10:30\nА13\n10:40-12:10\nВ206\n12 октября\n14:30-16:00")
        inherit_missing_places([schedule])
        self.assertIsNone(schedule["meetings"][-1]["place_raw"])
        self.assertIn("ambiguous_inherited_place", schedule["warnings"])

    def test_unique_project_base_can_supply_other_day_override(self):
        monday = self.parse("понедельник\n09:00-10:30\nА13")
        wednesday = self.parse("14 октября\n14:30-16:00", 2)
        inherit_missing_places([monday, wednesday])
        self.assertEqual(wednesday["meetings"][0]["place_raw"], "А13")
        self.assertEqual(wednesday["meetings"][0]["place_source"]["schedule_index"], 0)


class SheetTests(unittest.TestCase):
    def test_merged_cells_and_sheet_coordinates(self):
        html = ('<table><tr><th></th><th>A</th><th>B</th><th>C</th><th>D</th><th>E</th><th>F</th><th>G</th></tr>'
                '<tr><th>1</th><td colspan="7">2026/2027<br>«Д» = ул. Дубровская, остальное = ул. Большая Семёновская</td></tr>')
        header = ["№", "проектное направление", "название проекта", "ФИО преподавателя",
                  "контактная информация", "понедельник", "среда"]
        html += '<tr><th>2</th>' + ''.join(f'<td>{escape(s)}</td>' for s in header) + '</tr>'
        html += ('<tr><th>3</th><td>1</td><td rowspan="2">IT</td><td>Первый</td><td>Преподаватель</td><td></td>'
                 '<td>понедельник<br>09:00-10:30<br>Д307</td><td></td></tr>'
                 '<tr><th>4</th><td>2</td><td>Второй</td><td>Преподаватель</td><td></td><td></td>'
                 '<td>среда<br>14:30-16:00<br>Д307</td></tr></table>')
        result = parse_pd(html)
        first, second = result["projects"]
        self.assertEqual(first["source_row"], 3)
        self.assertEqual(first["schedules"][0]["source_cell"]["a1"], "F3")
        self.assertEqual(second["schedules"][0]["source_cell"]["a1"], "G4")
        self.assertEqual(second["direction"], "IT")

    def test_wrapper_or_wrong_layout_fails_explicitly(self):
        with self.assertRaisesRegex(ValueError, "Таблица ПД не найдена"):
            parse_pd('<html><script>not a table</script></html>')

    def test_merged_cell_text_keeps_line_breaks_and_entities(self):
        parser = SheetParser()
        parser.feed('<table><tr><td rowspan="2">A &amp; B<br>C</td><td>X</td></tr><tr><td>Y</td></tr></table>')
        grid = expand_table(parser.tables[0])
        self.assertEqual(grid[1][0]["text"], "A & B\nC")
        self.assertEqual(grid[1][0]["anchor"], [1, 1])
