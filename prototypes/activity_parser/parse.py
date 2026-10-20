# /// script
# requires-python = ">=3.12"
# dependencies = ["pdfplumber>=0.11,<0.12"]
# ///
"""Extract reviewable PD/PE candidates, without writing to application DB."""

import argparse
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

# Allow both `python -m prototypes.activity_parser.parse` and PEP 723/uv runs.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from prototypes.activity_parser.common import digest, write_csv, write_json
from prototypes.activity_parser.pd import parse_pd
from prototypes.activity_parser.pe import parse_pe


def sheet_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "docs.google.com" or not re.fullmatch(
            r"/spreadsheets/d/e/[\w-]+/pubhtml(?:/sheet)?", parsed.path):
        raise ValueError("Нужна HTTPS-ссылка опубликованной Google-таблицы (/d/e/.../pubhtml)")
    gid = parse_qs(parsed.query).get("gid", [None])[0]
    if not gid or not gid.isdigit():
        raise ValueError("В ссылке нужен числовой gid конкретного листа")
    base = parsed.path.removesuffix("/sheet")
    return f"https://docs.google.com{base}/sheet?{urlencode({'headers': 'false', 'gid': gid})}"


def download(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "MospolytechScheduleParserPrototype/1.0"})
    with urlopen(request, timeout=45) as response:
        content = response.read(20_000_001)
    if len(content) > 20_000_000:
        raise ValueError("Источник больше 20 МБ")
    return content


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pd-url", help="Опубликованный лист Google Sheets")
    parser.add_argument("--pd-html", type=Path, help="Сохранённый HTML листа для повторного офлайн-разбора")
    parser.add_argument("--pe-pdf", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not (args.pd_url or args.pd_html or args.pe_pdf):
        parser.error("Нужен хотя бы один источник: --pd-url, --pd-html или --pe-pdf")
    args.output.mkdir(parents=True, exist_ok=True)
    pd = pe = None
    timestamp = datetime.now(UTC).isoformat()
    if args.pd_url or args.pd_html:
        url = sheet_url(args.pd_url) if args.pd_url else None
        data = args.pd_html.read_bytes() if args.pd_html else download(url)
        pd = parse_pd(data.decode("utf-8-sig"))
        pd["source"] = {"url": args.pd_url, "sheet_url": url, "sha256": digest(data),
                        "captured_at": timestamp, "snapshot": "pd-source.html"}
        (args.output / "pd-source.html").write_bytes(data)
        write_json(args.output / "pd.json", pd)
        rows = []
        for project in pd["projects"]:
            meetings = [m for s in project["schedules"] for m in s["meetings"]]
            for meeting in meetings or [{}]:
                rows.append({"source_row": project["source_row"], "source_number": project["source_number"],
                             "project": project["name"], "teacher_raw": project["teacher_raw"],
                             **meeting, "warnings": project["warnings"],
                             "raw_monday": project["raw_cells"]["понедельник"],
                             "raw_wednesday": project["raw_cells"]["среда"]})
        write_csv(args.output / "pd.csv", rows, ["source_row", "source_number", "project", "teacher_raw",
                  "day_of_week", "start", "end", "lesson_number", "blocked_lesson_numbers",
                  "start_minute", "end_minute", "recurrence", "date", "place_raw", "place_inherited",
                  "room_candidates", "place_details", "teacher_override_raw", "warnings", "raw_monday", "raw_wednesday"])
        print(f"ПД: проектов {len(pd['projects'])}, интервалов {sum(len(s['meetings']) for p in pd['projects'] for s in p['schedules'])}, "
              f"с предупреждениями {sum(bool(p['warnings']) for p in pd['projects'])}, исключено строк {len(pd['excluded_rows'])}")
    if args.pe_pdf:
        pe = parse_pe(args.pe_pdf)
        (args.output / "pe").mkdir(parents=True, exist_ok=True)
        pe["source"] = {"filename": args.pe_pdf.name, "sha256": digest(args.pe_pdf.read_bytes()),
                        "captured_at": timestamp, "snapshot": "pe/source.pdf"}
        destination = args.output / "pe" / "source.pdf"
        if args.pe_pdf.resolve() != destination.resolve():
            shutil.copyfile(args.pe_pdf, destination)
        write_json(args.output / "pe.json", pe)
        write_csv(args.output / "pe.csv", pe["sessions"], ["id", "page", "day_of_week", "start", "end",
                  "lesson_number", "activity", "activity_labels", "activity_choice_mode", "health_group", "places", "warnings", "activity_raw"])
        print(f"Физра: страниц {len(pe['pages'])}, предложений {len(pe['sessions'])}, "
              f"мест без активности {len(pe['orphan_place_cells'])}, "
              f"с предупреждениями {sum(bool(s['warnings']) for s in pe['sessions'])}")
    print(args.output)


if __name__ == "__main__":
    main()
