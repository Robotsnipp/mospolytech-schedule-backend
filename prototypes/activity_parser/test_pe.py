import csv
import tempfile
import unittest
from pathlib import Path

from prototypes.activity_parser.common import write_csv
from prototypes.activity_parser.parse import sheet_url
from prototypes.activity_parser.pe import classify, parse_place


class PhysicalEducationTests(unittest.TestCase):
    def test_combined_label_and_smg_are_preserved(self):
        self.assertEqual(classify("пауэрлифтинг, кроссфит"), (["пауэрлифтинг", "кроссфит"], None))
        self.assertEqual(classify("СМГ настольный теннис")[1], "special_medical_group")

    def test_venue_does_not_replace_literal_address(self):
        text = "Спортзал №5\nм.ВДНХ\nул. Павла\nКорчагина д.22\nауд.102"
        place = parse_place(text)
        self.assertEqual(place["venue"], "Спортзал №5")
        self.assertEqual(place["address_raw"], "ул. Павла Корчагина д.22 ауд.102")
        self.assertEqual(place["room_label"], "102")
        self.assertEqual(place["raw"], text)

    def test_unknown_venue_is_not_guessed(self):
        self.assertIsNone(parse_place("неразборчивое место")["venue"])


class ExportTests(unittest.TestCase):
    def test_published_url_selects_sheet_and_rejects_other_hosts(self):
        self.assertEqual(sheet_url("https://docs.google.com/spreadsheets/d/e/TEST/pubhtml?gid=123&single=true"),
                         "https://docs.google.com/spreadsheets/d/e/TEST/pubhtml/sheet?headers=false&gid=123")
        for url in ("https://example.com/pubhtml?gid=1", "https://docs.google.com/spreadsheets/d/e/TEST/pubhtml"):
            with self.assertRaises(ValueError):
                sheet_url(url)

    def test_csv_does_not_execute_source_formulas(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "out.csv"
            write_csv(target, [{"text": "=1+1"}], ["text"])
            with target.open(encoding="utf-8-sig", newline="") as stream:
                self.assertEqual(next(csv.DictReader(stream))["text"], "'=1+1")
