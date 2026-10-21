"""Разбор PDF «Расписание элективных дисциплин по физкультуре» в data/pe/pe_schedule.json.

Нужен pdfplumber (в зависимости проекта не входит; результат разбора лежит в репозитории):
    python -m venv /tmp/pe && /tmp/pe/bin/pip install pdfplumber
    /tmp/pe/bin/python scripts/parse_pe_pdf.py data/pe/<файл>.pdf data/pe/pe_schedule.json

Раскладка PDF: страница = одна пара (время в первой ячейке), строки = направления
(цветная ячейка с названием), столбцы = дни Пн–Сб; справа от названия — зал(ы).
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import pdfplumber

DAY_X0 = [89.3, 209.0, 328.9, 448.0, 567.0, 686.0]  # левые границы столбцов дней, pt
DAY_WIDTH = 119.5
LABEL_WIDTH = 61  # ширина цветной ячейки с названием внутри дня
VENUE_START = re.compile(r"^(СК\b|Спортзал\b)")
METRO = re.compile(r"^(м\.|МЦК)")
TIME = re.compile(r"(\d{1,2}):(\d{2})")


def lines_of(words):
    rows = defaultdict(list)
    for w in words:
        rows[round(w["top"] / 2)].append(w)
    return [" ".join(w["text"] for w in sorted(r, key=lambda w: w["x0"]))
            for _, r in sorted(rows.items())]


def parse_venue(lines: list[str]) -> list[dict]:
    """Разбивает строки ячейки на залы: название, метро, адрес."""
    groups: list[list[str]] = []
    for line in lines:
        if VENUE_START.match(line) and (not groups or any(METRO.match(x) for x in groups[-1])):
            groups.append([line])
        elif groups:
            groups[-1].append(line)
    venues = []
    for g in groups:
        metro_at = next((i for i, x in enumerate(g) if METRO.match(x)), None)
        if metro_at is None:
            name, metro, address = " ".join(g), "", ""
        else:
            name = " ".join(g[:metro_at])
            metro = g[metro_at]
            address = " ".join(g[metro_at + 1:])
        venues.append({"name": name, "metro": metro, "address": address})
    return venues


def parse_page(page):
    tm = TIME.findall(page.crop((29, 76, 89, page.height)).extract_text() or "")
    if len(tm) < 2:
        raise ValueError("не найдено время пары")
    start, end = f"{int(tm[0][0]):02d}:{tm[0][1]}", f"{int(tm[1][0]):02d}:{tm[1][1]}"

    # Границы строк-направлений: тонкие горизонтальные прямоугольники над зонами названий.
    borders = sorted({round(r["top"], 1) for r in page.rects
                      if r["height"] <= 1.6 and 55 < r["width"] < 65
                      and any(abs(r["x0"] - d0) < 2 for d0 in DAY_X0)})
    bands = list(zip(borders, borders[1:], strict=False))

    out = []
    for day, d0 in enumerate(DAY_X0):
        for top, bottom in bands:
            # Текст верхнего соседа иногда «вылезает» за границу ячейки: отсекаем символы,
            # начинающиеся выше границы (+допуск), иначе они склеиваются с первой строкой.
            def own(obj, top=top):
                return obj["object_type"] != "char" or obj["top"] >= top + 1.0

            label_box = page.crop((d0, top, d0 + LABEL_WIDTH, bottom)).filter(own)
            label = " ".join((label_box.extract_text() or "").split())
            if not label:
                continue
            venue_box = page.crop((d0 + LABEL_WIDTH, top, min(d0 + DAY_WIDTH, page.width), bottom)).filter(own)
            venue_lines = [ln.strip() for ln in (venue_box.extract_text() or "").split("\n") if ln.strip()]
            out.append({"day": day, "start": start, "end": end, "label": label,
                        "venues": parse_venue(venue_lines)})
    return out


def main(pdf_path: str, out_path: str):
    slots = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            slots.extend(parse_page(page))
    Path(out_path).write_text(json.dumps(
        {"source": Path(pdf_path).name, "slots": slots}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(slots)} направлений-ячеек, {sum(len(s['venues']) for s in slots)} залов")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
