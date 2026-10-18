"""Checks for data ambiguity; OCR quality is verified against source crops."""
import tempfile
import unittest
from pathlib import Path

from parse import FIELDS, clean_ocr, exports, number, parse_row, semesters


class DecodeTests(unittest.TestCase):
    def row(self, **changes):
        raw = dict.fromkeys(FIELDS, "")
        raw.update(code="Б1.1.1", subject="История России", total_hours="144",
                   contact_hours="118", lecture_hours="68", practice_hours="50",
                   independent_hours="26", semester_6_contact_hours="64",
                   semester_7_contact_hours="54", exam_semesters="7", credit_semesters="6")
        raw.update(changes)
        return parse_row(raw, dict.fromkeys(FIELDS, 1), 1, [0, 0, 1, 1])

    def test_multisemester_totals_are_not_invented_per_semester(self):
        row = self.row()
        self.assertEqual(row["totals"]["lecture_hours"], 68)
        self.assertEqual([s["contact_hours"] for s in row["semester_loads"]], [64, 54])
        self.assertTrue(all(s["lecture_hours"] is None for s in row["semester_loads"]))
        self.assertTrue(row["review_required"])

    def test_single_semester_copies_only_available_breakdown(self):
        row = self.row(contact_hours="36", lecture_hours="18", practice_hours="18",
                       semester_6_contact_hours="", semester_7_contact_hours="36", credit_semesters="")
        self.assertEqual(row["semester_loads"][0]["lecture_hours"], 18)
        self.assertIsNone(row["semester_loads"][0]["lab_hours"])

    def test_wrong_hours_trigger_reconciliation_warning(self):
        row = self.row(lecture_hours="88")
        self.assertTrue(any("Сумма распознанных" in warning for warning in row["warnings"]))

    def test_ocr_nine_is_not_silently_changed_to_six(self):
        warnings = []
        self.assertIsNone(semesters("9", "exam", warnings))
        self.assertTrue(warnings)

    def test_blank_and_unreadable_numbers_remain_unknown(self):
        warnings = []
        self.assertIsNone(number("", "hours", warnings))
        self.assertIsNone(number("l8", "hours", warnings))
        self.assertEqual(number("1 132", "hours", warnings), 1132)
        self.assertEqual(len(warnings), 1)

    def test_elective_code_and_controls(self):
        row = self.row(code="Б1.2.ЭД.1.2", graded_credit_semesters="2, 4")
        self.assertEqual(row["discipline_code"], "Б1.2.ЭД.1.2")
        self.assertTrue(row["is_elective_candidate"])
        self.assertEqual(row["controls"]["graded_credit_semesters"], [2, 4])


    def test_disagreeing_ocr_variants_do_not_become_importable_numbers(self):
        raw = dict.fromkeys(FIELDS, "")
        raw.update(code="Б1.1.1", subject="Test", total_hours="144", contact_hours="54",
                   lecture_hours="18", practice_hours="18", lab_hours="18", independent_hours="06",
                   semester_3_contact_hours="54")
        ocr = {f"0:{index}": {"text": value, "confidence": 1} for index, value in enumerate(raw.values())}
        ocr["tight:0:13"] = {"text": "Num 90", "confidence": 1}
        with tempfile.TemporaryDirectory() as folder:
            result = exports(Path(folder), [{"row": 0, "bbox": [0, 0, 1, 1]}], ocr, {})
        entry = result["entries"][0]
        self.assertIsNone(entry["totals"]["independent_hours"])
        self.assertIn("independent_hours", entry["ambiguous_fields"])
        self.assertEqual(entry["raw_cells"]["independent_hours"], "06")

    def test_total_workload_difference_requires_review(self):
        row = self.row(independent_hours="6")
        self.assertTrue(any("Разница общей нагрузки" in warning for warning in row["warnings"]))


    def test_synthetic_context_is_removed_without_rewriting_digits(self):
        self.assertEqual(clean_ocr("Value:36"), "36")
        self.assertEqual(clean_ocr("Value: 2, 3, 4 Value: 5, 6, 7"), "2, 3, 4 5, 6, 7")
        self.assertEqual(clean_ocr("Value: 9"), "9")


    def test_missing_elective_code_separator_is_not_guessed(self):
        row = self.row(code="Б1.2.ЭД.11")
        self.assertIsNone(row["discipline_code"])
        self.assertIn("Код дисциплины требует проверки", row["warnings"])


if __name__ == "__main__":
    unittest.main()
