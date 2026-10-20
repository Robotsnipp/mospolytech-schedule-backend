"""Independent checks against a reviewed sample; extractors never read fixtures."""

import argparse
import json
from pathlib import Path


def resolve(value, path):
    for key in path.split(".") if path else []:
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def check(directory: Path, fixture: dict) -> list[str]:
    documents = {kind: json.loads((directory / f"{kind}.json").read_text()) for kind in ("pd", "pe")}
    failures = []
    for kind, expected in fixture["source_sha256"].items():
        if documents[kind]["source"]["sha256"] != expected:
            failures.append(f"{kind}: источник изменился; выборку нужно проверить заново")
    for item in fixture["checks"]:
        document = documents[item["kind"]]
        rows = document["projects" if item["kind"] == "pd" else "sessions"]
        rows = [row for row in rows if all(row.get(k) == v for k, v in item.get("where", {}).items())]
        try:
            if "count" in item:
                actual = len(rows)
                expected = item["count"]
            else:
                if len(rows) != 1:
                    raise ValueError(f"ожидалась одна запись, найдено {len(rows)}")
                actual = resolve(rows[0], item.get("path", ""))
                if "length" in item:
                    actual = len(actual)
                    expected = item["length"]
                else:
                    expected = item["expected"]
            if actual != expected:
                failures.append(f"{item['label']}: ожидалось {expected!r}, получено {actual!r}")
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            failures.append(f"{item['label']}: {exc}")
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--fixture", type=Path, default=Path(__file__).parent / "fixtures/verified_sample.json")
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_text())
    failures = check(args.directory, fixture)
    for failure in failures:
        print("FAIL:", failure)
    print(f"Контрольных проверок: {len(fixture['checks'])}; ошибок: {len(failures)}")
    raise SystemExit(bool(failures))


if __name__ == "__main__":
    main()
