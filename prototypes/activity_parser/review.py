"""Static escaped review report; source HTML is never embedded or executed."""

import json
from collections import Counter
from html import escape
from pathlib import Path

from .address_review import write_address_review
from .common import DAYS

WARNINGS = {
    "date_or_change_in_source": "Есть дата или перенос: проверить отдельно от недельного расписания",
    "dated_change_requires_review": "Занятие на конкретную дату требует проверки",
    "source_note_requires_review": "У занятия есть примечание источника",
    "nonstandard_time": "Время отличается от стандартной сетки пар",
    "invalid_time": "Некорректный интервал времени",
    "missing_place": "Не найдено место проведения",
    "unmapped_place": "Не удалось разобрать подпись места",
    "external_place_reference_required": "Полный адрес: нужно определить тип места и правило учёта помещения",
    "university_place_reference_required": "Университетская площадка: нужно сопоставить помещение со справочником",
    "ambiguous_inherited_place": "Для наследования места есть несколько основных аудиторий",
    "room_subdivision_requires_review": "Число в скобках у аудитории: нужно уточнить его смысл",
    "teacher_override_requires_review": "В ячейке указан преподаватель отдельно от основного поля",
    "reschedule_requires_review": "Даты переноса выделены; применение к календарю требует проверки",
    "multiple_rooms_unspecified": "Несколько аудиторий: не указан способ распределения",
    "weekday_differs_from_column": "День в ячейке отличается от заголовка столбца",
    "date_weekday_mismatch": "Указанные дата и день недели не совпадают",
    "unresolved_date": "Не удалось определить полную дату",
    "multiple_dates_require_review": "В строке несколько дат: связь требует проверки",
    "no_meetings_extracted": "Не удалось выделить занятия",
    "missing_schedule": "Расписание не указано",
    "combined_activity_label": "Общее название нескольких активностей сохранено без разделения",
    "special_medical_group_eligibility_required": "СМГ: требуется учитывать допуск студента",
    "unrecognized_place": "Не полностью распознано место проведения",
}

WARNING_HELP = {
    "external_place_reference_required": "Предупреждение предыдущей версии: теперь остальные адреса сохраняются как дополнительные площадки без резерва помещения и автоматического назначения новых пар.",
    "university_place_reference_required": "Площадка подтверждена официальными источниками, но помещение ещё не связано со справочником приложения. Университетский адрес сам по себе не разрешает назначать туда другие занятия.",
    "date_or_change_in_source": "В источнике есть исключение из недельного шаблона. Нужен календарный слой, который применяет переносы и замены только к нужной дате.",
    "dated_change_requires_review": "Дата извлечена. Нужно подтвердить, заменяет ли запись обычное занятие или дополняет его.",
    "source_note_requires_review": "Сохранено исходное примечание к датированному занятию. Оно может менять аудиторию, время или преподавателя.",
    "nonstandard_time": "По согласованному правилу блокируются все пересекающиеся стандартные слоты. Точное время сохранено отдельно для расчёта перемещений.",
    "multiple_rooms_unspecified": "Согласовано показывать все перечисленные аудитории. Персональная аудитория появится при наличии связи студента с подгруппой; помещение не выбирается автоматически.",
    "teacher_override_requires_review": "ФИО с инициалами отделено от аудитории. Нужно сопоставить его с преподавателем и подтвердить замену на указанную дату.",
    "room_subdivision_requires_review": "Для АВ4108 (1)/(2) пользователь подтвердил подгруппы. Другие числовые подписи требуют отдельного уточнения.",
    "missing_place": "Согласовано наследование неизменённого места. Если основной записи нет, место нельзя определить автоматически.",
    "ambiguous_inherited_place": "Основных аудиторий несколько. Нужна привязка изменения к определённой подгруппе или занятию.",
    "reschedule_requires_review": "Выделены дата исходного занятия и дата назначения. Время намеренно не унаследовано и отмена основной пары автоматически не применена.",
    "special_medical_group_eligibility_required": "Это ограничение допуска, а не ошибка чтения PDF. Нужны сведения о медицинской группе студента.",
    "combined_activity_label": "Пользователь подтвердил: обе активности доступны, выбор делается на месте. Запись не разделяется на два обязательных занятия.",
    "unmapped_place": "Текст сохранён без угадывания; нужен дополнительный шаблон разбора либо ручная привязка.",
}


def e(value):
    return escape(str(value if value is not None else "—"), quote=True)


def warnings_html(warnings):
    return "".join(f"<li>{e(WARNINGS.get(w, w))}</li>" for w in warnings)


def pd_card(project):
    meetings = []
    for schedule in project["schedules"]:
        for m in schedule["meetings"]:
            period = m["date"] or ("дата не определена" if m["recurrence"] == "dated" else "недельный кандидат")
            teacher = "; ".join(m.get("teacher_override_raw", [])) or project["teacher_raw"]
            slots = ", ".join(map(str, m.get("blocked_lesson_numbers", []))) or "нет пересечений"
            place_note = " (унаследовано из основной записи)" if m.get("place_inherited") else ""
            subgroup = m.get("place_details", {}).get("subgroup_number")
            if subgroup is not None:
                place_note += f" · подгруппа {subgroup}"
            venue = m.get("place_details", {}).get("venue")
            if venue:
                place_note += (" · университетская площадка" if venue["kind"] == "university"
                               else " · дополнительная площадка, без назначения новых пар")
            meetings.append(f"<tr><td>{e(DAYS[m['day_of_week']])}</td><td>{e(m['start'])}–{e(m['end'])}<br>Блокирует пары: {e(slots)}</td>"
                            f"<td>{e(period)}</td><td>{e(m['place_raw'])}{e(place_note)}</td><td>{e(teacher)}</td></tr>")
    raw = "".join(f"<h4>{e(k)}</h4><pre>{e(v)}</pre>" for k, v in project["raw_cells"].items())
    return (f'<article id="pd-{project["source_number"]}" data-kind="pd" '
            f'data-codes="{e(" ".join(project["warnings"]))}" data-warning="{int(bool(project["warnings"]))}">'
            f'<h3>ПД №{project["source_number"]}. {e(project["name"])}</h3>'
            f'<p>{e(project["direction"])} · {e(project["teacher_raw"])} · строка {project["source_row"]}</p>'
            '<table><thead><tr><th>День</th><th>Время</th><th>Повторение / дата</th><th>Место из источника</th><th>Преподаватель</th>'
            '</tr></thead><tbody>' + "".join(meetings) + '</tbody></table>'
            f'<ul class="issues">{warnings_html(project["warnings"])}</ul>'
            f'<details><summary>Исходные ячейки и примечания</summary>{raw}</details></article>')


def pe_card(session):
    places = "".join(f"<li><pre>{e(p['raw'])}</pre></li>" for p in session["places"])
    image = session["source_image"]
    source = f'<a href="pe/{e(image)}"><img loading="lazy" src="pe/{e(image)}" alt="Ячейка источника"></a>' if image else ""
    choice_note = (f'<p>Активность выбирается на месте: {e(" или ".join(session["activity_labels"]))}.</p>'
                   if session.get("activity_choice_mode") == "on_arrival" else "")
    return (f'<article data-kind="pe" data-codes="{e(" ".join(session["warnings"]))}" '
            f'data-warning="{int(bool(session["warnings"]))}">'
            f'<h3>{e(session["activity"])} · {e(DAYS[session["day_of_week"]])} '
            f'{e(session["start"])}–{e(session["end"])}</h3>'
            f'<p>Страница {session["page"]}. Доступные места: {len(session["places"])}. '
            'Правила выбора места в документе не уточнены.</p>'
            f'{choice_note}<ul>{places}</ul><ul class="issues">{warnings_html(session["warnings"])}</ul>'
            f'<details><summary>Сверить с PDF</summary>{source}</details></article>')


def write_review(output: Path, pd: dict | None, pe: dict | None):
    projects = pd["projects"] if pd else []
    sessions = pe["sessions"] if pe else []
    total_meetings = sum(len(s["meetings"]) for p in projects for s in p["schedules"])
    issues = sum(bool(p["warnings"]) for p in projects) + sum(bool(s["warnings"]) for s in sessions)
    warning_counts = Counter(w for r in projects + sessions for w in set(r["warnings"]))
    categories = "".join(f'<option value="{e(code)}">{e(WARNINGS.get(code, code))} ({count})</option>'
                         for code, count in warning_counts.most_common())
    cards = "".join(pd_card(p) for p in projects) + "".join(pe_card(s) for s in sessions)
    diagnostics = ""
    if pd and pd["excluded_rows"]:
        diagnostics += f'<p class="issues">Неразобранных строк ПД: {len(pd["excluded_rows"])} — см. pd.json.</p>'
    if pe and pe["orphan_place_cells"]:
        diagnostics += f'<p class="issues">Мест без связанной активности: {len(pe["orphan_place_cells"])} — см. pe.json.</p>'
    source_link = f'<a href="{e(pd["source"]["url"])}" target="_blank" rel="noreferrer">Таблица ПД</a>' if pd and pd["source"].get("url") else ""
    exports = []
    if pd:
        exports.append('<a href="pd.json">ПД JSON</a> · <a href="pd.csv">ПД CSV</a>')
        exports.append('<a href="addresses.html">Все полные адреса ПД</a>')
    if pe:
        source_link += ' <a href="pe/source.pdf">Исходный PDF физры</a>'
        exports.append('<a href="pe.json">Физра JSON</a> · <a href="pe.csv">Физра CSV</a>')
    document = '''<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Разбор ПД и физкультуры</title>
<style>body{font:16px/1.5 system-ui;margin:24px auto;padding:0 20px;max-width:1100px;color:#182332;background:#f5f7fa}
h1{font-size:28px}h3{margin:0 0 8px;font-size:19px}h4{margin:10px 0 0}a{color:#1858b3}
.note{background:#e8effb;padding:16px;border-radius:8px}nav{position:sticky;top:0;background:#f5f7fa;padding:14px 0;display:flex;gap:16px;flex-wrap:wrap;z-index:1}
input[type=search]{flex:1;min-width:180px;padding:9px}select{padding:8px;max-width:100%}#category{width:min(100%,540px)}article{background:white;border:1px solid #dbe1e8;padding:20px;margin:14px 0;border-radius:10px}
article[hidden]{display:none}table{border-collapse:collapse;width:100%;font-size:14px}td,th{text-align:left;padding:8px;border-bottom:1px solid #e1e7ef;vertical-align:top}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.5 system-ui;margin:4px 0}.issues{color:#8a4500}summary{cursor:pointer;color:#1858b3}img{max-width:100%;margin-top:12px;border:1px solid #ddd}
</style><h1>ПД и физкультура: результат разбора</h1>'''
    document += (f'<p>ПД: {len(projects)} проектов, {total_meetings} интервалов. '
                 f'Физра: {len(sessions)} предложений занятий. Предупреждения: {issues} записей.</p>'
                 '<p class="note">Все записи требуют сверки. ПД — кандидаты фиксированных занятий; '
                 'конкретные даты и переносы выделены отдельно. Физра — доступные варианты посещения, '
                 'включая СМГ. Привязок к студентам в источниках нет. Этап разбора не пишет в БД; импорт ПД выполняется отдельно.</p>'
                 f'<p>{source_link} · {" · ".join(exports)}</p>'
                 '<p><a href="warnings.html">Разбор предупреждений: что исправлено и что требует уточнения</a></p>'
                 + diagnostics + '<nav><select id="kind"><option value="">Все записи</option>'
                 '<option value="pd">ПД</option><option value="pe">Физра</option></select>'
                 '<select id="category" aria-label="Тип предупреждения"><option value="">Все типы предупреждений</option>' + categories + '</select>'
                 '<input id="search" type="search" placeholder="Проект, секция, место, преподаватель">'
                 '<label><input id="warnings" type="checkbox"> Только с предупреждениями</label>'
                 '<span id="count"></span></nav><main>' + cards + '</main>')
    document += '''<script>
const cards=[...document.querySelectorAll('article')],kind=document.querySelector('#kind'),query=document.querySelector('#search'),warnings=document.querySelector('#warnings'),category=document.querySelector('#category');
function filter(){let n=0;for(const c of cards){c.hidden=!!((kind.value&&c.dataset.kind!==kind.value)||(category.value&&!c.dataset.codes.split(' ').includes(category.value))||(warnings.checked&&c.dataset.warning!=='1')||(query.value&&!c.textContent.toLocaleLowerCase('ru').includes(query.value.toLocaleLowerCase('ru'))));if(!c.hidden)n++}document.querySelector('#count').textContent='Показано: '+n}
category.value=new URLSearchParams(location.search).get('warning')||'';
for(const control of [kind,query,warnings,category])control.addEventListener('input',filter);filter();
</script></html>'''
    (output / "review.html").write_text(document, encoding="utf-8")
    write_warning_report(output, pd, pe)
    if pd:
        write_address_review(output, pd)


def write_warning_report(output: Path, pd: dict | None, pe: dict | None):
    current, previous = {}, {}
    for kind, document, field in (("pd", pd, "projects"), ("pe", pe, "sessions")):
        if document is None:
            continue
        current[kind] = document[field]
        path = output / f"{kind}-before-warning-fixes.json"
        if path.exists():
            baseline = json.loads(path.read_text())
            if baseline["source"]["sha256"] == document["source"]["sha256"]:
                previous[kind] = baseline[field]
    sections = []
    for kind, records in current.items():
        old = previous.get(kind)
        counts = Counter(w for r in records for w in set(r["warnings"]))
        before = Counter(w for r in old or [] for w in set(r["warnings"]))
        title = "ПД" if kind == "pd" else "Физкультура"
        was = str(sum(bool(r["warnings"]) for r in old)) if old is not None else "—"
        sections.append(f'<h2>{title}</h2><p>Записей с предупреждениями: было {was}, '
                        f'стало {sum(bool(r["warnings"]) for r in records)}.</p>')
        sections.append('<table><tr><th>Предупреждение</th><th>Было</th><th>Стало</th><th>Что делать</th></tr>')
        for code in sorted(counts.keys() | before.keys(), key=lambda c: (-counts[c], c)):
            label = e(WARNINGS.get(code, code))
            if counts[code]:
                label = f'<a href="review.html?warning={e(code)}">{label}</a>'
            sections.append(f'<tr><td>{label}</td>'
                            f'<td>{before[code] if old is not None else "—"}</td><td>{counts[code]}</td>'
                            f'<td>{e(WARNING_HELP.get(code, "Сверить с исходной ячейкой."))}</td></tr>')
        sections.append('</table>')
        if kind == "pd" and old is not None:
            old_by_row = {r["source_row"]: r for r in old}
            fixed = [r for r in records if old_by_row[r["source_row"]]["warnings"] and not r["warnings"]]
            newly_found = [r for r in records if r["warnings"] and not old_by_row[r["source_row"]]["warnings"]]
            for label, rows in (("Предупреждения полностью ушли", fixed),
                                ("Дополнительно найдены внешние места, ранее ошибочно привязанные по умолчанию", newly_found)):
                sections.append(f'<h3>{label}: {len(rows)}</h3><ul>')
                sections.extend(f'<li><a href="review.html#pd-{r["source_number"]}">№{r["source_number"]}. {e(r["name"])}</a></li>' for r in rows)
                sections.append('</ul>')
    html = '''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Разбор предупреждений</title><style>body{font:16px/1.5 system-ui;max-width:1200px;margin:30px auto;padding:0 20px;color:#182332}table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:12px;border:1px solid #dce2ea;vertical-align:top}th{background:#edf2fa}a{color:#1858b3}h2{margin-top:36px}</style>
<h1>Предупреждения: исправления и оставшиеся вопросы</h1><p><a href="review.html">Вернуться к записям</a> · <a href="addresses.html">Все полные адреса ПД</a></p>
<p>Числа относятся к записям, а не к количеству ошибок. Категории пересекаются: один проект может содержать несколько предупреждений.
Одна и та же выгрузка сравнивается до и после исправлений. Время, число интервалов и исходные ячейки сохранены.</p>
<p>Исправлен разбор подписей аудиторий, альтернатив через дробь, дат со временем в одной строке и ФИО в примечании.
Адреса разделены на подтверждённые университетские места и дополнительные площадки.
Для дополнительных площадок согласован режим без резерва помещения и назначения новых пар;
проверенные интервалы подключаются к генератору командой import_pd_schedule с явными ID и запасом минут на дорогу.</p>'''
    (output / "warnings.html").write_text(html + "".join(sections) + '</html>', encoding="utf-8")
