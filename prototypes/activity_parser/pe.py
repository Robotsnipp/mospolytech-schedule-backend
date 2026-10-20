"""Extract optional PE sessions from the supplied vector PDF grid."""

import re
from pathlib import Path

import pdfplumber

from .common import DAYS, TIME_RE, clean, time_range


def grid_tables(page):
    # In this PDF borders are filled ~0.48pt rectangles. Underlines are
    # ~0.24pt; feeding all rectangle edges to TableFinder splits text cells.
    vertical = [r for r in page.rects if .35 < r["width"] < .8 and r["height"] > 1]
    horizontal = [r for r in page.rects if .35 < r["height"] < .8 and r["width"] > 20]
    if not vertical or not horizontal:
        raise ValueError(f"Страница {page.page_number}: не найдена сетка профиля PE")
    return page.find_tables({
        "vertical_strategy": "explicit", "horizontal_strategy": "explicit",
        "explicit_vertical_lines": vertical, "explicit_horizontal_lines": horizontal,
        "snap_tolerance": 1, "join_tolerance": 1,
    })


def cell_text(page, box):
    x0, top, x1, bottom = box
    return page.crop((x0 + .35, top + .35, x1 - .35, bottom - .35)).extract_text(
        x_tolerance=1, y_tolerance=2) or ""


def parse_place(text: str) -> dict:
    flat = clean(text)
    venue = re.search(r"Спортзал\s*№\s*\d+|СК на Автозаводской|СК Михалковская", flat)
    address = re.search(r"ул\.\s*.+", flat)
    metro = re.search(r"(?:МЦК|м\.)\s*([^\n]+)", text)
    room = re.search(r"ауд\.?\s*([\w-]+)", flat)
    return {"venue": venue.group() if venue else None,
            "address_raw": address.group() if address else None,
            "metro_raw": clean(metro[1]) if metro else None,
            "room_label": room[1] if room else None, "raw": text}


def classify(sport: str) -> tuple[list[str], str | None]:
    # User clarified slash/comma combinations as activities chosen on arrival.
    health = "special_medical_group" if re.search(r"\bСМГ\b", sport) else None
    labels = [clean(p) for p in re.split(r"[,/]", sport) if clean(p)]
    return labels, health


def parse_pe(path: Path, output: Path | None = None) -> dict:
    sessions, pages, empty_cells, orphan_cells = [], [], [], []
    source_heading = ""
    if output:
        output.mkdir(parents=True, exist_ok=True)
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            if not page.chars:
                raise ValueError(f"Страница {page.page_number}: нет текста; требуется отдельный OCR-профиль")
            tables = grid_tables(page)
            if len(tables) != 1:
                raise ValueError(f"Страница {page.page_number}: ожидалась одна таблица, найдено {len(tables)}")
            table = tables[0]
            boxes = table.cells
            texts = {box: cell_text(page, box) for box in boxes}
            headers = []
            for day, label in enumerate(DAYS[:6]):
                matches = [b for b, text in texts.items() if clean(text).casefold() == label]
                if len(matches) != 1:
                    raise ValueError(f"Страница {page.page_number}: неоднозначный заголовок {label}")
                headers.append((day, matches[0]))
            first_day_x = headers[0][1][0]
            time_boxes = [b for b in boxes if b[2] <= first_day_x + 1]
            raw_time = "\n".join(texts[b] for b in time_boxes)
            matches = list(TIME_RE.finditer(raw_time))
            if len(matches) != 1 or not time_range(matches[0])["valid"]:
                raise ValueError(f"Страница {page.page_number}: не удалось определить единый интервал времени")
            timing = time_range(matches[0])
            heading = page.crop((0, 0, page.width, table.bbox[1])).extract_text() or ""
            if page.page_number == 1:
                source_heading = heading
            rendered = page.to_image(resolution=240).original if output else None
            page_name = f"page-{page.page_number}.png"
            if rendered:
                rendered.save(output / page_name)
            page_info = {"page": page.page_number, "time_raw": raw_time, **timing,
                         "table_bbox": list(table.bbox), "source_image": page_name if output else None}
            pages.append(page_info)
            used_places = set()
            for day, header in headers:
                left, header_top, right, header_bottom = header
                sport_boxes = [b for b in boxes if abs(b[0] - left) < 1
                               and b[1] >= header_bottom - .5
                               and 15 < b[2] - b[0] < (right - left) * .75]
                for sport_box in sorted(sport_boxes, key=lambda b: b[1]):
                    sport = clean(texts[sport_box])
                    if not sport:
                        empty_cells.append({"page": page.page_number, "day_of_week": day,
                                            "bbox": list(sport_box)})
                        continue
                    place_boxes = [b for b in boxes if abs(b[0] - sport_box[2]) < 1
                                   and abs(b[2] - right) < 1
                                   and b[1] >= sport_box[1] - .5
                                   and b[3] <= sport_box[3] + .5]
                    places = []
                    warnings = []
                    for box in sorted(place_boxes, key=lambda b: b[1]):
                        text = texts[box]
                        if text.strip():
                            used_places.add(box)
                            place = parse_place(text)
                            place["bbox"] = list(box)
                            if not place["venue"] or not place["address_raw"]:
                                warnings.append("unrecognized_place")
                            places.append(place)
                    if not places:
                        warnings.append("missing_place")
                    labels, health_group = classify(sport)
                    if health_group:
                        warnings.append("special_medical_group_eligibility_required")
                    crop_box = (sport_box[0], sport_box[1], right, sport_box[3])
                    image_name = f"pe-{page.page_number}-{day}-{len(sessions) + 1}.png"
                    if rendered:
                        sx, sy = rendered.width / page.width, rendered.height / page.height
                        rendered.crop(tuple(round(v * (sx if i % 2 == 0 else sy))
                                            for i, v in enumerate(crop_box))).save(output / image_name)
                    sessions.append({"id": f"pe-p{page.page_number}-d{day}-y{sport_box[1]:.2f}",
                                     "page": page.page_number, "day_of_week": day, **timing,
                                     "activity_raw": texts[sport_box], "activity": sport,
                                     "activity_labels": labels, "health_group": health_group,
                                     "activity_choice_mode": "on_arrival" if len(labels) > 1 else "single",
                                     "places": places, "choice_mode": "available_options_unspecified",
                                     "recurrence": "weekly_candidate", "bbox": list(crop_box),
                                     "source_image": image_name if output else None,
                                     "warnings": sorted(set(warnings)), "review_required": True,
                                     "teacher": None, "capacity": None})
            # A location with no paired activity must remain visible as an
            # extraction failure rather than disappearing from the report.
            for box, text in texts.items():
                if box not in used_places and re.search(r"Спортзал|СК Михалковская|СК на", text):
                    orphan_cells.append({"page": page.page_number, "bbox": list(box), "raw": text})
    years = re.search(r"20\d{2}/20\d{2}", source_heading)
    return {"kind": "pe", "schema_version": 1, "parser_revision": 2, "profile": "mospolytech-pe-autumn-2026",
            "heading_raw": source_heading, "academic_year": years.group() if years else None,
            "sessions": sessions, "pages": pages, "empty_activity_cells": empty_cells,
            "orphan_place_cells": orphan_cells,
            "warnings": ["period_dates_not_specified", "eligibility_and_attendance_rules_not_in_source",
                         "teacher_and_capacity_not_in_source", "all_rows_require_review"]
            + (["unpaired_place_cells"] if orphan_cells else [])}
