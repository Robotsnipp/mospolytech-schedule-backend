"""Validate OCR against manually verified cells; never used to alter extraction."""
import argparse
import html
import json
import os
from pathlib import Path


def value_at(entry, path):
    parts = path.split('.')
    value = entry
    if parts[0] == 'semester_loads':
        value = next((load for load in value['semester_loads'] if load['semester_number'] == int(parts[1])), {})
        parts = parts[2:]
    for part in parts:
        value = value.get(part) if isinstance(value, dict) else None
    return value


def check(result, fixture):
    if result['source']['sha256'] != fixture['source_sha256']:
        raise ValueError('The fixture belongs to another PDF')
    entries = {row['source_row']: row for row in result['entries']}
    failures = []
    for item in fixture['checks']:
        value = value_at(entries[item['source_row']], item['path'])
        if value != item['expected']:
            failures.append({**item, 'actual': value})
    return failures


def render_comparison(result, fixture, baseline, destination, image_dir):
    """A report of checked cells; expected values are never fed to the parser."""
    entries = {entry['source_row']: entry for entry in result['entries']}
    before = {entry['source_row']: entry for entry in baseline['entries']} if baseline else {}
    labels = {'credit_semesters': 'Семестр зачёта', 'exam_semesters': 'Семестр экзамена',
              'contact_hours': 'Аудиторные часы', 'independent_hours': 'Часы СРС',
              'credit_units': 'Зачётные единицы', 'discipline_code': 'Код дисциплины'}
    def display(value):
        if value is None:
            return 'неизвестно'
        if isinstance(value, list):
            return ', '.join(map(str, value)) if value else 'пусто'
        return html.escape(str(value))
    rows = []
    for item in fixture['checks']:
        entry = entries[item['source_row']]
        actual = value_at(entry, item['path'])
        previous = value_at(before[item['source_row']], item['path']) if before else None
        field = labels.get(item['path'].split('.')[-1], item['path'])
        if item['path'].startswith('semester_loads.'):
            field += f" — семестр {item['path'].split('.')[1]}"
        image = os.path.relpath(image_dir / f"row-{item['source_row']:03}.png", destination.parent)
        state = 'Верно' if actual == item['expected'] else 'Проверить'
        rows.append(f'<tr><td>{html.escape(entry["subject"])}</td><td>{html.escape(field)}</td>'
                    f'<td>{display(previous)}</td><td>{display(actual)}</td><td>{display(item["expected"])}</td><td>{state}</td></tr>'
                    f'<tr><td colspan="6"><details><summary>Исходная строка</summary><img src="{html.escape(image)}"></details></td></tr>')
    passed = len(fixture['checks']) - len(check(result, fixture))
    old_passed = len(fixture['checks']) - len(check(baseline, fixture)) if baseline else None
    destination.write_text('<!doctype html><html lang="ru"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1"><title>OCR: до и после</title>'
        '<style>body{font:16px/1.5 system-ui;max-width:1500px;margin:32px auto;padding:20px;color:#222}'
        'table{border-collapse:collapse;width:100%}td,th{padding:12px;border-bottom:1px solid #ddd;text-align:left}'
        'th{background:#edf2ed}details{overflow:auto}img{min-width:1600px;width:100%}</style>'
        f'<h1>Распознавание: {old_passed if old_passed is not None else "—"}/{len(fixture["checks"])} → {passed}/{len(fixture["checks"])}</h1>'
        '<p>Сравнение с вручную проверенными ячейками исходного PDF. Это контрольная выборка, а не гарантия точности всего документа.</p>'
        '<table><thead><tr><th>Дисциплина</th><th>Поле</th><th>Было</th><th>Стало</th><th>В PDF</th><th>Проверка</th></tr></thead><tbody>'
        + ''.join(rows) + '</tbody></table></html>', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('result', type=Path)
    parser.add_argument('--fixture', type=Path, default=Path(__file__).with_name('fixtures') / 'verified_cells.json')
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--html', type=Path)
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_text())
    result = json.loads(args.result.read_text())
    failures = check(result, fixture)
    for failure in failures:
        print(f"row {failure['source_row']} {failure['path']}: {failure['actual']!r} != {failure['expected']!r}")
    print(f"Verified cells: {len(fixture['checks']) - len(failures)}/{len(fixture['checks'])}")
    if args.html:
        baseline = json.loads(args.baseline.read_text()) if args.baseline else None
        render_comparison(result, fixture, baseline, args.html, args.result.parent)
    raise SystemExit(bool(failures))


if __name__ == '__main__':
    main()
