"""Inventory every full address for user classification, without merging sites."""

from html import escape
from pathlib import Path

from .common import write_csv, write_json
from .places import note_place


def write_address_review(output: Path, pd: dict):
    groups = {}

    def include_place(project, schedule, place, note=None):
        address = place.get("address_raw")
        if not address:
            return
        key = (address, tuple(place["external_room_labels"]),
               tuple(r["label"] for r in place["room_candidates"]))
        entry = groups.setdefault(key, {"address_raw": address,
            "external_room_labels": place["external_room_labels"],
            "university_room_labels": [r["label"] for r in place["room_candidates"]],
            "venue": place["venue"], "resource_policy": place["resource_policy"], "projects": {}})
        ref = entry["projects"].setdefault(project["source_number"],
            {"number": project["source_number"], "name": project["name"], "cells": [], "dated_notes": []})
        cell = schedule["source_cell"]["a1"]
        if cell not in ref["cells"]:
            ref["cells"].append(cell)
        if note and note not in ref["dated_notes"]:
            ref["dated_notes"].append(note)

    for project in pd["projects"]:
        for schedule in project["schedules"]:
            for meeting in schedule["meetings"]:
                include_place(project, schedule, meeting.get("place_details", {}))
            for note in schedule["notes"]:
                place = note.get("place_details") or note_place(note["text"], pd["location_legend"])
                if place:
                    include_place(project, schedule, place, note)
    entries = []
    rows = []
    for i, entry in enumerate(groups.values(), 1):
        entry["projects"] = list(entry["projects"].values())
        entries.append(entry)
        venue = entry["venue"]
        status = ("Университетская площадка. Нужно сопоставить помещение со справочником; автоматическое назначение новых пар запрещено."
                  if venue["kind"] == "university" else
                  "Дополнительная площадка / место партнёра. Без резерва помещения и назначения новых пар; время участников, преподавателя и дорогу учитывать.")
        name = venue["name"] or "Дополнительная площадка"
        source = (f'<br><a href="{escape(venue["source_url"], quote=True)}">Подтверждение площадки</a>'
                  if venue["source_url"] else "")
        rooms = entry["external_room_labels"] + entry["university_room_labels"]
        links = ""
        for project in entry["projects"]:
            date_text = "; ".join(n["date"] or n["text"] for n in project["dated_notes"])
            links += (f'<li><a href="review.html#pd-{project["number"]}">№{project["number"]}. {escape(project["name"])}</a> '
                      f'({escape(", ".join(project["cells"]))})'
                      + (f'<br>Разовое изменение: {escape(date_text)}' if date_text else "") + '</li>')
        rows.append(f'<tr><td>{i}</td><td>{escape(name)}<br>{escape(entry["address_raw"])}{source}</td>'
                    f'<td>{escape(", ".join(rooms)) or "Не указан отдельный номер"}</td>'
                    f'<td>{escape(status)}</td><td><ul>{links}</ul></td></tr>')
    data = {"source_sha256": pd["source"]["sha256"], "parser_revision": pd["parser_revision"], "addresses": entries}
    write_json(output / "addresses.json", data)
    write_csv(output / "addresses.csv", entries, ["address_raw", "external_room_labels",
              "university_room_labels", "venue", "resource_policy", "projects"])
    count = len({p["number"] for e in entries for p in e["projects"]})
    html = f'''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Все адреса ПД</title><style>body{{font:16px/1.5 system-ui;max-width:1400px;margin:30px auto;padding:0 20px;color:#182332}}table{{border-collapse:collapse;width:100%}}th,td{{text-align:left;padding:12px;border:1px solid #dce2ea;vertical-align:top}}th{{background:#edf2fa}}ul{{padding-left:18px;margin:0}}a{{color:#1858b3}}</style>
<h1>Все полные адресные подписи ПД</h1><p><a href="warnings.html">Предупреждения</a> · <a href="review.html">Занятия</a> · <a href="addresses.csv">CSV</a> · <a href="addresses.json">JSON</a></p>
<p>{len(entries)} вариантов подписей в {count} проектах, включая адреса в датированных примечаниях. Полный адрес не означает, что объект сторонний.
Варианты написания и разные помещения по одному адресу сохранены отдельно для сверки.</p>
<p>Прянишникова, 2А и гараж на Автозаводской, 16с13 подтверждены как университетские места.
Остальные адреса пока сохранены как дополнительные площадки: готовые занятия занимают время людей, новые пары туда не назначаются.
Организация и координаты могут оставаться неуточнёнными. Проверенные интервалы подключаются через import_pd_schedule;
дорога учитывается заданным запасом минут, маршруты по карте пока не рассчитываются.</p>
<table><tr><th>№</th><th>Адрес из источника</th><th>Помещение</th><th>Правило</th><th>Проекты и ячейки</th></tr>'''
    (output / "addresses.html").write_text(html + "".join(rows) + '</table></html>', encoding="utf-8")
