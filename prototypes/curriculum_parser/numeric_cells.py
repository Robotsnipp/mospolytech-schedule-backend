"""Image preparation for numeric cells, independent of curriculum values."""
from collections import deque

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def remove_rules(image):
    """Remove long edge rules, preserving central strokes such as the digit 1."""
    pixels = np.array(image.convert('L'))
    dark = pixels < 170
    height, width = dark.shape
    remaining = set(np.flatnonzero(dark).tolist())
    while remaining:
        start = remaining.pop()
        queue = deque([start])
        component = [start]
        while queue:
            current = queue.popleft()
            y, x = divmod(current, width)
            for dy in [-1, 0, 1]:
                for dx in [-1, 0, 1]:
                    if 0 <= y + dy < height and 0 <= x + dx < width:
                        neighbor = (y + dy) * width + x + dx
                        if neighbor in remaining:
                            remaining.remove(neighbor)
                            queue.append(neighbor)
                            component.append(neighbor)
        ys, xs = np.divmod(component, width)
        span_x, span_y = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
        horizontal = span_x > width * .6 and span_y < max(12, height * .2)
        vertical = span_y > height * .5 and span_x < width * .12 and (
            xs.max() < width * .2 or xs.min() > width * .8)
        if horizontal or vertical or len(component) < 4:
            pixels[ys, xs] = 255
    return Image.fromarray(pixels).convert('RGB')


def numeric_lines(image):
    """Return individual text lines rather than scaling context to a whole list."""
    cleaned = remove_rules(image)
    mask = np.array(cleaned.convert('L')) < 170
    occupied = np.where(mask.any(axis=1))[0]
    if not len(occupied):
        return []
    runs = [[occupied[0]]]
    for y in occupied[1:]:
        if y > runs[-1][-1] + 12:
            runs.append([])
        runs[-1].append(y)
    lines = []
    for run in runs:
        top, bottom = int(run[0]), int(run[-1]) + 1
        xs = np.where(mask[top:bottom].any(axis=0))[0]
        lines.append(cleaned.crop((int(xs[0]), top, int(xs[-1]) + 1, bottom)))
    return lines


def numeric_context(lines, font_path):
    """A small prefix on each line anchors orientation and thin isolated digits."""
    images = []
    for ink in lines:
        font = ImageFont.truetype(str(font_path), max(12, round(ink.height * 1.35)))
        bounds = font.getbbox('Value:')
        anchor_width, anchor_height = bounds[2], bounds[3] - bounds[1]
        anchored = Image.new('RGB', (anchor_width + ink.width + 50,
                                    max(ink.height, anchor_height) + 40), 'white')
        ImageDraw.Draw(anchored).text((10, 20 - bounds[1]), 'Value:', font=font, fill='black')
        anchored.paste(ink, (anchor_width + 20, 20))
        images.append(anchored)
    canvas = Image.new('RGB', (max(image.width for image in images),
                              sum(image.height for image in images)), 'white')
    offset = 0
    for image in images:
        canvas.paste(image, (0, offset))
        offset += image.height
    return canvas
