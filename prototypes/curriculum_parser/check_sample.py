"""Validate OCR against manually verified cells; never used to alter extraction."""
import argparse
import json
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('result', type=Path)
    parser.add_argument('--fixture', type=Path, default=Path(__file__).with_name('fixtures') / 'verified_cells.json')
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_text())
    result = json.loads(args.result.read_text())
    failures = check(result, fixture)
    for failure in failures:
        print(f"row {failure['source_row']} {failure['path']}: {failure['actual']!r} != {failure['expected']!r}")
    print(f"Verified cells: {len(fixture['checks']) - len(failures)}/{len(fixture['checks'])}")
    raise SystemExit(bool(failures))


if __name__ == '__main__':
    main()
