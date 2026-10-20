import unittest

from prototypes.activity_parser.common import TIME_RE, time_range


class SlotCoverageTests(unittest.TestCase):
    def timing(self, text):
        return time_range(TIME_RE.fullmatch(text))

    def test_long_interval_blocks_two_overlapping_slots(self):
        interval = self.timing("13:00-16:00")
        self.assertEqual(interval["blocked_lesson_numbers"], [3, 4])
        self.assertEqual((interval["start_minute"], interval["end_minute"]), (780, 960))

    def test_nonstandard_start_does_not_get_rounded(self):
        interval = self.timing("14:40-16:00")
        self.assertEqual(interval["start"], "14:40")
        self.assertEqual(interval["blocked_lesson_numbers"], [4])

    def test_adjacent_boundary_does_not_block_next_slot(self):
        self.assertEqual(self.timing("16:10-17:50")["blocked_lesson_numbers"], [5])

    def test_invalid_interval_has_no_projected_slots(self):
        interval = self.timing("25:00-26:30")
        self.assertEqual(interval["blocked_lesson_numbers"], [])
        self.assertIsNone(interval["start_minute"])
