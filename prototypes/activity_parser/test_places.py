import unittest

from prototypes.activity_parser.places import parse_place

LEGEND = {"АВ": "ул. Автозаводская", "Пр": "ул. Прянишникова",
          "default": "ул. Большая Семёновская"}


class PlaceTests(unittest.TestCase):
    def parse(self, *lines):
        return parse_place(list(lines), LEGEND)

    def test_descriptive_parentheses_do_not_break_room_reference(self):
        place = self.parse("А12", "(«Космос»)")
        self.assertEqual(place["room_candidates"][0]["label"], "А12")
        self.assertEqual(place["annotations"], ["«Космос»"])
        self.assertEqual(place["warnings"], [])
        self.assertEqual(place["raw"], "А12 («Космос»)")

    def test_confirmed_numeric_annotation_is_a_subgroup_not_a_new_room(self):
        place = self.parse("АВ4108 (1)")
        self.assertEqual(place["room_candidates"][0]["room"], "4108")
        self.assertEqual(place["annotations"], ["1"])
        self.assertEqual(place["subgroup_number"], 1)
        self.assertNotIn("room_subdivision_requires_review", place["warnings"])

    def test_other_numeric_annotations_are_not_assumed_to_be_subgroups(self):
        self.assertIn("room_subdivision_requires_review", self.parse("АВ4109 (1)")["warnings"])

    def test_parenthetical_alternative_room_is_not_dropped(self):
        place = self.parse("АВ2509", "(АВ2102 лаборатория)")
        self.assertEqual([r["label"] for r in place["room_candidates"]], ["АВ2509", "АВ2102"])
        self.assertEqual(place["room_assignment_mode"], "listed_without_student_assignment")

    def test_slash_separated_rooms_remain_ambiguous(self):
        place = self.parse("Н206/Н205")
        self.assertEqual([r["label"] for r in place["room_candidates"]], ["Н206", "Н205"])
        self.assertEqual(place["room_assignment_mode"], "listed_without_student_assignment")

    def test_comma_room_at_external_address_never_uses_university_default(self):
        place = self.parse("НАМИ, ул. Автомоторная, д. 2, Б204")
        self.assertEqual(place["room_candidates"], [])
        self.assertEqual(place["external_room_labels"], ["Б204"])
        self.assertEqual(place["kind"], "additional_address")
        self.assertEqual(place["venue"]["kind"], "additional")
        self.assertFalse(place["resource_policy"]["allow_new_assignments"])
        self.assertEqual(place["warnings"], [])

    def test_separate_room_line_at_external_address_is_also_external(self):
        place = self.parse("НАМИ, ул. Автомоторная, д. 2", "Б204")
        self.assertEqual(place["room_candidates"], [])
        self.assertEqual(place["external_room_labels"], ["Б204"])

    def test_street_without_abbreviation_dot_is_an_address(self):
        place = self.parse("Электрозаводская ул, д. 12, с. 1, Малый зал")
        self.assertEqual(place["kind"], "additional_address")
        self.assertEqual(place["unparsed"], [])

    def test_address_and_matching_university_room_are_both_retained(self):
        place = self.parse("ул. Прянишникова, 2А", "Пр2506")
        self.assertEqual(place["kind"], "address_with_rooms")
        self.assertEqual(place["address_raw"], "ул. Прянишникова, 2А")
        self.assertEqual(place["room_candidates"][0]["label"], "Пр2506")

    def test_unknown_text_is_not_resolved_by_a_room_like_fragment(self):
        place = self.parse("непонятное место Б204, согласовать")
        self.assertEqual(place["room_candidates"], [])
        self.assertIn("unmapped_place", place["warnings"])

    def test_confirmed_technopark_does_not_reserve_room_but_blocks_people(self):
        place = self.parse("ул. Зорге, д. 9а, стр. 2, этаж 4, каб. 475")
        self.assertFalse(place["resource_policy"]["reserve_room"])
        self.assertTrue(place["resource_policy"]["block_attendees"])
        self.assertTrue(place["resource_policy"]["block_teacher"])
        self.assertTrue(place["resource_policy"]["travel_time_required"])
        self.assertEqual(place["address_raw"], "ул. Зорге, д. 9а, стр. 2, этаж 4, каб. 475")

    def test_other_addresses_are_additional_without_inventing_partner(self):
        place = self.parse("ул. Зорге, д. 9а, стр. 3")
        self.assertFalse(place["resource_policy"]["reserve_room"])
        self.assertEqual(place["venue"]["kind"], "additional")
        self.assertIsNone(place["venue"]["organization"])
        self.assertIsNone(place["venue"]["coordinates"])

    def test_confirmed_campus_is_not_classified_as_partner(self):
        place = self.parse("ул. Прянишникова, 2А", "Пр2506")
        self.assertEqual(place["venue"]["kind"], "university")
        self.assertEqual(place["resource_policy"]["scope"], "university_venue_reference")
        self.assertIsNone(place["resource_policy"]["reserve_room"])
        self.assertIn("university_place_reference_required", place["warnings"])

    def test_confirmed_garage_is_not_automatically_available_for_new_lessons(self):
        place = self.parse("ул. Автозаводская, д.16 стр. 13, гараж")
        self.assertEqual(place["kind"], "university_address")
        self.assertEqual(place["venue"]["kind"], "university")
        self.assertIsNone(place["resource_policy"]["reserve_room"])
        self.assertFalse(place["resource_policy"]["allow_new_assignments"])

    def test_nearby_addresses_do_not_match_confirmed_university_buildings(self):
        for address in ("ул. Прянишникова, 20А", "ул. Автозаводская, 16 стр. 130",
                        "ул. Автозаводская, 16к2с13", "Автозаводская ул., 23А, корп. 4"):
            with self.subTest(address=address):
                self.assertEqual(self.parse(address)["venue"]["kind"], "additional")

    def test_additional_address_overrides_university_street_legend(self):
        place = self.parse("ул. Автозаводская, 23А", "АВ4108")
        self.assertEqual(place["room_candidates"], [])
        self.assertEqual(place["external_room_labels"], ["АВ4108"])

    def test_room_without_address_keeps_university_legend(self):
        place = self.parse("АВ4108")
        self.assertEqual(place["resource_policy"]["scope"], "university_room_reference")
        self.assertEqual(place["room_candidates"][0]["room"], "4108")
        self.assertIsNone(place["resource_policy"]["allow_new_assignments"])

    def test_external_office_number_keeps_slash(self):
        place = self.parse("Мичуринский проспект, 13к1, оф. 1/12")
        self.assertEqual(place["external_room_labels"], ["1/12"])

    def test_external_letter_room_is_preserved_without_treating_house_as_room(self):
        self.assertEqual(self.parse("Ленинский проспект, 6с3, Г-158")["external_room_labels"], ["Г-158"])
        self.assertEqual(self.parse("ул. Прянишникова, 2А")["external_room_labels"], [])
