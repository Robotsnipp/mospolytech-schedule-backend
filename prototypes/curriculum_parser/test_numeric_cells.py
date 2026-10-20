"""Regressions for isolated digits, grid rules and multiline semester lists."""
import unittest

import numpy as np
from numeric_cells import numeric_lines, remove_rules
from PIL import Image, ImageDraw


class NumericImageTests(unittest.TestCase):
    def test_grid_line_is_removed_but_central_one_is_kept(self):
        image = Image.new('RGB', (120, 90), 'white')
        draw = ImageDraw.Draw(image)
        draw.rectangle((116, 10, 119, 80), fill='black')
        draw.rectangle((58, 25, 62, 65), fill='black')
        result = np.array(remove_rules(image).convert('L'))
        self.assertTrue((result[:, 116:] == 255).all())
        self.assertTrue((result[25:66, 58:63] == 0).all())

    def test_grid_only_cell_is_empty(self):
        image = Image.new('RGB', (120, 90), 'white')
        ImageDraw.Draw(image).rectangle((116, 10, 119, 80), fill='black')
        self.assertEqual(numeric_lines(image), [])

    def test_multiline_cell_is_split_into_two_real_lines(self):
        image = Image.new('RGB', (120, 140), 'white')
        draw = ImageDraw.Draw(image)
        draw.rectangle((30, 15, 60, 45), fill='black')
        draw.rectangle((30, 85, 60, 115), fill='black')
        self.assertEqual(len(numeric_lines(image)), 2)


    def test_broken_stroke_of_a_digit_stays_on_one_line(self):
        image = Image.new('RGB', (120, 100), 'white')
        draw = ImageDraw.Draw(image)
        draw.rectangle((50, 20, 65, 40), fill='black')
        draw.rectangle((45, 48, 70, 51), fill='black')
        self.assertEqual(len(numeric_lines(image)), 1)


if __name__ == '__main__':
    unittest.main()
