# /// script
# requires-python = ">=3.12"
# dependencies = ["pdfplumber>=0.11,<0.12", "numpy>=2,<3"]
# ///
"""PROTOTYPE: local OCR of one Moscow Polytech scanned curriculum layout.

No database writes. The layout profile is specific to the supplied full-time plan.
"""

import argparse
import csv
import hashlib
import html
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pdfplumber
from PIL import Image, ImageDraw, ImageFont, ImageOps

PROFILE = "mospolytech-2025-full-time"
# Grid reference measured on the sample rendered at 240 DPI (2807 x 3969).
REFERENCE_WIDTH = 2807
REFERENCE_HEIGHT = 3969
COLUMNS = [54, 160, 1278, 1360, 1442, 1522, 1604, 1687, 1728, 1811,
           1893, 1974, 2057, 2139, 2221, 2262, 2303, 2345, 2386, 2427,
           2468, 2510, 2550]
FIELDS = ["code", "subject", "exam_semesters", "credit_semesters",
          "graded_credit_semesters", "coursework_semesters", "course_project_semesters",
          "credit_units", "total_hours", "contact_hours", "lecture_hours",
          "practice_hours", "lab_hours", "independent_hours",
          *[f"semester_{n}_contact_hours" for n in range(1, 9)]]
SCHEMA_VERSION = 1


def groups(indices):
    result = []
    for index in indices:
        if not result or index > result[-1][-1] + 2:
            result.append([])
        result[-1].append(int(index))
    return [round(sum(group) / len(group)) for group in result]


def number(text, field, warnings):
    """Empty cells remain unknown, never silently become zero."""
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return None
    if not re.fullmatch(r"\d+", compact):
        warnings.append(f"{field}: не распознано число {text!r}")
        return None
    return int(compact)


def semesters(text, field, warnings):
    if not text.strip():
        return []
    parts = re.split(r"[,;\s]+", text.strip())
    if not all(part.isdigit() and 1 <= int(part) <= 8 for part in parts):
        warnings.append(f"{field}: проверить номера семестров {text!r}")
        return None
    return sorted({int(part) for part in parts})


def parse_row(raw, confidence, row, bbox):
    warnings = []
    code = raw["code"].replace(" ", "").translate(str.maketrans({"B": "Б", "b": "Б", "І": "1", "I": "1"}))
    # Frequent OCR confusion in the leading Cyrillic Б; record any normalization.
    if re.match(r"^[56]1\.", code):
        code = "Б" + code[1:]
    if code != raw["code"]:
        warnings.append("Код нормализован; сверить с исходной строкой")
    valid_code = bool(re.fullmatch(r"Б1\.(?:\d+\.)+(?:ЭД\.)?\d+(?:\.\d+)*", code))
    if not valid_code:
        warnings.append("Код дисциплины требует проверки")
    values = {field: number(raw[field], field, warnings) for field in FIELDS[7:]}
    controls = {field: semesters(raw[field], field, warnings) for field in FIELDS[2:7]}
    if any(text and confidence.get(field, 0) < .95 for field, text in raw.items()):
        warnings.append("Есть ячейки с низкой уверенностью OCR")
    contact = values["contact_hours"]
    types = [values[field] for field in ("lecture_hours", "practice_hours", "lab_hours")]
    if contact is not None and sum(value or 0 for value in types) != contact:
        warnings.append("Сумма распознанных часов по видам не совпадает с аудиторными")
    total, independent = values["total_hours"], values["independent_hours"]
    if total is not None and contact is not None and independent is not None:
        remainder = total - contact - independent
        if remainder:
            warnings.append(f"Разница общей нагрузки и аудиторной + СРС: {remainder} ч; сверить иные виды нагрузки/OCR")
    terms = [n for n in range(1, 9) if values[f"semester_{n}_contact_hours"]]
    if contact is not None and sum(values[f"semester_{n}_contact_hours"] or 0 for n in range(1, 9)) != contact:
        warnings.append("Сумма часов семестров не совпадает с аудиторными")
    if any(term not in terms for items in controls.values() if items is not None for term in items):
        warnings.append("Форма контроля указана в семестре без распознанных часов")
    if not terms:
        warnings.append("Не найдены семестры проведения")
    if len(terms) > 1:
        warnings.append("PDF не содержит разбивки видов занятий по семестрам; значения не вычислялись")
    return {
        "source_row": row, "source_bbox_pixels": bbox,
        "discipline_code": code if valid_code else None, "subject": raw["subject"],
        "totals": {key: value for key, value in values.items() if not key.startswith("semester_")},
        "controls": controls,
        "semester_loads": [{
            "semester_number": term,
            "contact_hours": values[f"semester_{term}_contact_hours"],
            "lecture_hours": values["lecture_hours"] if len(terms) == 1 else None,
            "practice_hours": values["practice_hours"] if len(terms) == 1 else None,
            "lab_hours": values["lab_hours"] if len(terms) == 1 else None,
        } for term in terms],
        "review_required": True, "is_elective_candidate": "ЭД" in raw["code"], "warnings": warnings,
        "raw_cells": raw, "ocr_confidence": confidence,
    }


def prepare(pdf, output):
    with pdfplumber.open(pdf) as document:
        if len(document.pages) != 1:
            raise ValueError("Профиль поддерживает только одностраничный план")
        page = document.pages[0]
        image = page.to_image(resolution=240).original.convert("RGB")
    output.mkdir(parents=True, exist_ok=True)
    image.save(output / "source.png")
    array = np.asarray(image.convert("L"))
    height, width = array.shape
    sx, sy = width / REFERENCE_WIDTH, height / REFERENCE_HEIGHT
    # Horizontal lines in the whitespace at the right of the name column.
    scores = (array[:, round(1000*sx):round(1200*sx)] < 170).mean(axis=1)
    boundaries = [y for y in groups(np.where(scores > .7)[0]) if 1100*sy <= y <= 3077*sy]
    if not 65 <= len(boundaries) <= 90:
        raise ValueError(f"Сетка не соответствует профилю: {len(boundaries)} границ строк")
    cells, rows = [], []
    font_path = Path("/System/Library/Fonts/Supplemental/Arial.ttf")
    for row, (top, bottom) in enumerate(zip(boundaries, boundaries[1:], strict=False)):
        # The source scan has a small horizontal drift down the page.
        shift = ((top + bottom) / 2 / sy - 1450) * .005
        columns = [round((x + shift) * sx) for x in COLUMNS]
        bbox = [columns[0], top, columns[-1], bottom]
        crop = image.crop(bbox)
        crop.resize((crop.width * 2, crop.height * 3)).save(output / f"row-{row:03}.png")
        rows.append({"row": row, "bbox": bbox})
        for column, (left, right) in enumerate(zip(columns, columns[1:], strict=False)):
            cell = image.crop((left + 3, top + 3, right - 3, bottom - 2))
            if np.count_nonzero(np.asarray(cell.convert("L")) < 150) < 3:
                continue
            cell = ImageOps.expand(cell.resize((cell.width * 3, cell.height * 3)), 12, "white")
            if column >= 2:
                # A printed anchor improves Vision's detection of isolated digits.
                wide = Image.new("RGB", (cell.width + 140, cell.height), "white")
                wide.paste(cell, (140, 0))
                wide_font = ImageFont.truetype(str(font_path), 40)
                ImageDraw.Draw(wide).text((10, 12), "Num", font=wide_font, fill="black")
                path = output / f"cell-{row:03}-{column:02}.png"
                wide.save(path)
                cells.append({"id": f"{row}:{column}", "path": str(path.resolve())})
                mask = cell.convert("L").point(lambda value: 255 if value < 150 else 0)
                ink = cell.crop(mask.getbbox())
                font = ImageFont.truetype(str(font_path), ink.height)
                bounds = font.getbbox("Num")
                anchor_width, anchor_height = bounds[2], bounds[3] - bounds[1]
                anchored = Image.new("RGB", (anchor_width + ink.width + 40,
                                             max(ink.height, anchor_height) + 30), "white")
                ImageDraw.Draw(anchored).text((10, 10 - bounds[1]), "Num", font=font, fill="black")
                anchored.paste(ink, (anchor_width + 20, 10))
                cell = anchored
            suffix = "-tight" if column >= 2 else ""
            path = output / f"cell-{row:03}-{column:02}{suffix}.png"
            cell.save(path)
            key = f"tight:{row}:{column}" if column >= 2 else f"{row}:{column}"
            cells.append({"id": key, "path": str(path.resolve())})
    header = image.crop((round(width*.35), round(height*.064), round(width*.73), round(height*.108)))
    header = header.resize((header.width*3, header.height*3))
    header.save(output / "header.png")
    cells.append({"id": "header", "path": str((output / "header.png").resolve())})
    (output / "cells.json").write_text(json.dumps(cells), encoding="utf-8")
    (output / "rows.json").write_text(json.dumps(rows), encoding="utf-8")
    return rows


def exports(output, rows, ocr, source):
    entries, excluded = [], []
    for row in rows:
        index = row["row"]
        observations = [ocr.get(f"{index}:{column}", {"text": "", "confidence": 0}) for column in range(22)]
        raw = {field: item["text"] for field, item in zip(FIELDS, observations, strict=True)}
        ambiguous = []
        for column, field in enumerate(FIELDS[2:], start=2):
            raw[field] = raw[field].replace("Num", "").strip()
            alternative = ocr.get(f"tight:{index}:{column}", {}).get("text", "").replace("Num", "").strip()
            decode = semesters if column < 7 else number
            primary_value = decode(raw[field], field, [])
            secondary_value = decode(alternative, field, [])
            if primary_value in (None, []) and secondary_value not in (None, []):
                raw[field] = alternative
            elif primary_value not in (None, []) and secondary_value not in (None, []) and primary_value != secondary_value:
                ambiguous.append(field)
        name = raw["subject"]
        code = raw["code"].replace(" ", "")
        reason = None
        if not name:
            reason = "Нет названия"
        elif name.casefold().startswith(("модуль", "элективные дисциплины", "блок", "часть,", "для обязательного")):
            reason = "Итоговая строка/группа дисциплин"
        elif re.match(r"^[БB56][23]", code):
            reason = "Практики/аттестация вне профиля прототипа"
        if reason:
            excluded.append({"source_row": index, "reason": reason, "raw_cells": raw})
        else:
            confidence = {field: item["confidence"] for field, item in zip(FIELDS, observations, strict=True)}
            decoded_raw = dict(raw)
            for field in ambiguous:
                decoded_raw[field] = ""
            entry = parse_row(decoded_raw, confidence, index, row["bbox"])
            entry["raw_cells"] = raw
            entry["ambiguous_fields"] = ambiguous
            for field in ambiguous:
                entry["warnings"].append(f"{field}: варианты OCR расходятся; значение оставлено неизвестным")
            for column, field in enumerate(FIELDS):
                if f"{index}:{column}" in ocr and not raw[field]:
                    entry["warnings"].append(f"{field}: ячейка содержит следы печати, но OCR не прочитал значение")
            entries.append(entry)
    header = ocr.get("header", {}).get("text", "")
    program = re.search(r"\b\d{2}\.\d{2}\.\d{2}\b", header)
    admission = re.search(r"(?:набора|набора:)\s*:?\s*(20\d{2})", header, re.IGNORECASE)
    profile_name = re.search(r"Профиль\s*:\s*(.+?)\s*Форма обучения", header, re.IGNORECASE)
    form = re.search(r"Форма обучения\s*:\s*(очно-заочная|очная|заочная)", header, re.IGNORECASE)
    result = {
        "schema_version": SCHEMA_VERSION, "source": source,
        "metadata": {"program_code": program[0] if program else None,
                     "admission_year": int(admission[1]) if admission else None,
                     "study_form_raw": form[1] if form else None,
                     "profile_name": profile_name[1] if profile_name else None, "header_raw": header},
        "warnings": ["Непроверенная OCR-выгрузка: каждая строка требует сверки с PDF.",
                     "Разбивка часов по типам доступна в PDF суммарно, не по каждому семестру.",
                     "Элективные альтернативы не означают обязательное посещение всех дисциплин."],
        "entries": entries, "excluded_rows": excluded,
    }
    (output / "curriculum.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output / "curriculum.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["source_row", *[f"raw_{field}" for field in FIELDS], "warnings"])
        for entry in entries:
            writer.writerow([entry["source_row"], *[entry["raw_cells"][field] for field in FIELDS], "; ".join(entry["warnings"])])
    cards = []
    for entry in entries:
        warnings = "<br>".join(html.escape(w) for w in entry["warnings"]) or "Автоматические сверки пройдены; проверить визуально"
        cells = "".join(f"<tr><th>{field}</th><td>{html.escape(value)}</td></tr>" for field, value in entry["raw_cells"].items())
        cards.append(f'<section><h2>{html.escape(entry["subject"])}</h2><p>{warnings}</p>'
                     f'<img src="row-{entry["source_row"]:03}.png" alt="Исходная строка">'
                     f'<details><summary>Ячейки OCR</summary><table>{cells}</table></details></section>')
    (output / "review.html").write_text('<!doctype html><html lang="ru"><meta charset="utf-8">'
        '<title>Проверка OCR учебного плана</title><style>body{font:16px system-ui;max-width:1400px;margin:32px auto;padding:16px}'
        'section{border-top:1px solid #ccc;padding:16px 0}img{width:100%}th,td{text-align:left;padding:4px 12px}p{color:#704000}</style>'
        f'<h1>Прототип: {len(entries)} строк для проверки</h1><p>Не импортировать без сверки. '
        'Ни одна строка автоматически не считается проверенной.</p>'+''.join(cards)+'</html>', encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reuse-ocr", action="store_true", help="Повторная выгрузка из OCR-кэша того же файла")
    args = parser.parse_args()
    output = args.output.resolve()
    source = {"filename": args.pdf.name, "sha256": hashlib.sha256(args.pdf.read_bytes()).hexdigest(),
              "profile": PROFILE, "dpi": 240, "schema_version": SCHEMA_VERSION, "pipeline_revision": 3}
    if args.reuse_ocr:
        if json.loads((output / "source.json").read_text()) != source:
            parser.error("Кэш относится к другому документу/профилю")
        rows = json.loads((output / "rows.json").read_text())
    else:
        if sys.platform != "darwin":
            parser.error("Первичное OCR этого прототипа требует macOS Vision и Swift")
        rows = prepare(args.pdf, output)
        subprocess.run(["swift", str(Path(__file__).with_name("vision_cells.swift")),
                        str(output / "cells.json"), str(output / "ocr.json")], check=True)
        (output / "source.json").write_text(json.dumps(source), encoding="utf-8")
    result = exports(output, rows, json.loads((output / "ocr.json").read_text()), source)
    print(json.dumps({"entries": len(result["entries"]), "excluded_rows": len(result["excluded_rows"]),
                      "rows_with_warnings": sum(bool(e["warnings"]) for e in result["entries"]),
                      "review": str(output / "review.html")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
