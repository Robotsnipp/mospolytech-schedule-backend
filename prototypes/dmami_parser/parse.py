# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Download every published group schedule into reviewable JSON/CSV, without DB writes."""

import argparse
import base64
import hashlib
import json
import re
import sys
import time
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from prototypes.activity_parser.common import write_csv

BASE_URL = "https://rasp.dmami.ru"
MAX_BYTES = 100_000_000
CSV_FIELDS = ["group", "day_of_week", "lesson_number", "start", "end", "subject",
              "lesson_type", "teachers", "teacher_raw", "date_from", "date_to",
              "week_raw", "location_raw", "rooms", "places", "links", "warnings",
              "source_path", "review_required"]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def stamp():
    return datetime.now(UTC).isoformat()


def save_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def save_json(path, value):
    save_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def save_csv(path, rows):
    temporary = path.with_name(path.name + ".tmp")
    write_csv(temporary, rows, CSV_FIELDS)
    temporary.replace(path)


class SourceHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.scripts = []
        self.script = None
        self.links = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and "src" not in attrs:
            self.script = []
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])

    def handle_data(self, data):
        if self.script is not None:
            self.script.append(data)
        else:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.script is not None:
            self.scripts.append("".join(self.script))
            self.script = None


def parse_catalog(html):
    """Decode JSON inside an inline assignment; never execute source JavaScript."""
    document = SourceHTML()
    document.feed(html)
    candidates = []
    for script in document.scripts:
        match = re.search(r"\bvar\s+globalListGroups\s*=\s*", script)
        if match:
            value, _ = json.JSONDecoder().raw_decode(script[match.end():].lstrip())
            candidates.append(value)
    if len(candidates) != 1:
        raise ValueError("Expected one globalListGroups catalog in homepage")
    catalog = candidates[0]
    groups = catalog.get("groups") if isinstance(catalog, dict) else None
    if not isinstance(groups, dict) or not groups or any(
        not isinstance(name, str) or not name.strip() for name in groups
    ):
        raise ValueError("Invalid or empty group catalog")
    return catalog


def parse_bells(javascript):
    """Read the published static times object; fail rather than guess its layout."""
    match = re.search(r"\bvar\s+times\s*=\s*\{(.*?)\n\s*\};", javascript, re.S)
    if not match:
        raise ValueError("Published bell times object not found in main.js")
    result = {}
    for mode, block in re.findall(r"['\"](\d+)['\"]\s*:\s*\{([^{}]*)\}", match[1]):
        slots = {}
        for slot, start, end in re.findall(
            r"(\d+)\s*:\s*['\"](\d{1,2}:\d{2})-(\d{1,2}:\d{2})['\"]", block
        ):
            start, end = start.zfill(5), end.zfill(5)
            for value in (start, end):
                hours, minutes = map(int, value.split(":"))
                if hours > 23 or minutes > 59:
                    raise ValueError("Invalid published bell time")
            if start >= end:
                raise ValueError("Invalid published bell interval")
            slots[slot] = {"start": start, "end": end}
        if set(slots) != {str(i) for i in range(1, 8)}:
            raise ValueError(f"Incomplete published bell table: {mode}")
        result[mode] = slots
    if set(result) != {"0", "1", "2"}:
        raise ValueError("Expected three published bell tables")
    return result


def parse_group(payload, name, bells):
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        message = payload.get("message", "Invalid API response") if isinstance(payload, dict) else "Invalid API response"
        raise ValueError(str(message))
    group, grid = payload.get("group"), payload.get("grid")
    if not isinstance(group, dict) or group.get("title") != name:
        raise ValueError("Response group does not match requested group")
    if payload.get("isSession") is not False:
        raise ValueError("Only regular schedules (session=0) are supported")
    # An empty PHP array may serialize as [], not {}.
    if grid != [] and not isinstance(grid, dict):
        raise ValueError("Invalid schedule grid")
    timings = bells.get(str(group.get("evening")), {})
    warnings = [] if timings else ["unknown_bell_mode"]
    lessons = []
    for day, slots in (grid.items() if isinstance(grid, dict) else []):
        if day not in {str(i) for i in range(1, 8)} or not isinstance(slots, dict):
            raise ValueError(f"Invalid weekday/slots: {day}")
        for slot, entries in slots.items():
            if not slot.isdigit() or not isinstance(entries, list):
                raise ValueError(f"Invalid slot/entries: {slot}")
            for index, raw in enumerate(entries):
                if not isinstance(raw, dict) or not isinstance(raw.get("sbj"), str):
                    raise ValueError("Invalid lesson object")
                issues = []
                timing = timings.get(slot, {})
                if not timing:
                    issues.append("unknown_bell_slot")
                if raw.get("week"):
                    issues.append("uninterpreted_week_pattern")
                for field in ("df", "dt"):
                    try:
                        date.fromisoformat(raw[field])
                    except (KeyError, TypeError, ValueError):
                        issues.append(f"invalid_{field}")
                if not any(x.startswith("invalid_") for x in issues) and raw["df"] > raw["dt"]:
                    issues.append("reversed_date_range")
                places, links = [], []
                auditories = raw.get("auditories", [])
                if not isinstance(auditories, list):
                    raise ValueError("Invalid auditories list")
                for place in auditories:
                    if not isinstance(place, dict) or not isinstance(place.get("title"), str):
                        raise ValueError("Invalid auditory object")
                    html = SourceHTML()
                    html.feed(place["title"])
                    places.append({"text": " ".join(" ".join(html.text).split()), "raw": place})
                    links.extend(html.links)
                if raw.get("e_link"):
                    links.append(str(raw["e_link"]))
                lessons.append({
                    "group": name, "day_of_week": int(day), "lesson_number": int(slot),
                    **{"start": timing.get("start"), "end": timing.get("end")},
                    "subject": raw["sbj"], "lesson_type": raw.get("type"),
                    "teachers": raw.get("teachers", []), "teacher_raw": raw.get("teacher"),
                    "date_from": raw.get("df"), "date_to": raw.get("dt"),
                    "week_raw": raw.get("week"), "location_raw": raw.get("location"),
                    "rooms": raw.get("shortRooms", []), "places": places,
                    "links": list(dict.fromkeys(links)), "warnings": issues,
                    "source_path": f"grid.{day}.{slot}[{index}]", "raw": raw,
                    "review_required": True,
                })
    return {"group": group, "lessons": lessons, "warnings": warnings,
            "review_required": True}


class Network:
    def __init__(self, timeout, retries, delay):
        self.timeout, self.retries, self.delay = timeout, retries, delay
        self.last_request = 0

    def get(self, path):
        for attempt in range(self.retries + 1):
            time.sleep(max(0, self.last_request + self.delay - time.monotonic()))
            self.last_request = time.monotonic()
            try:
                request = Request(BASE_URL + path, headers={
                    "User-Agent": "MospolytechScheduleParserPrototype/1.0",
                    "Accept": "application/json,text/html,application/javascript",
                })
                with urlopen(request, timeout=self.timeout) as response:
                    body = response.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    raise ValueError("Source exceeds 100 MB")
                return body, stamp()
            except (URLError, TimeoutError, OSError) as exc:
                if isinstance(exc, HTTPError) and exc.code not in (429, 500, 502, 503, 504):
                    raise
                if isinstance(exc, HTTPError) and "use /semester.json" in str(exc):
                    raise ValueError("Group API disabled: run without --group-api to use /semester.json") from exc
                if attempt == self.retries:
                    raise
                time.sleep(min(2 ** attempt, 8))
        raise RuntimeError("Unreachable")


class Semester:
    """One source download, followed by local extraction of individual groups."""
    def __init__(self, network, path=None, javascript=None, snapshot=None):
        self.network = network
        self.javascript = javascript
        self.body, self.captured = ((path.read_bytes(), None) if path else network.get("/semester.json"))
        if snapshot:
            save_bytes(snapshot, self.body)
        self.source_url = path.resolve().as_uri() if path else BASE_URL + "/semester.json"
        document = json.loads(self.body.decode("utf-8-sig"))
        if not isinstance(document, dict):
            raise ValueError("semester.json must contain an object of group schedules")
        self.prefix = ""
        self.entries = document
        for field in ("contents", "groups", "schedules", "data"):
            if isinstance(document.get(field), dict):
                self.entries = document[field]
                self.prefix = "/" + field
                break
        self.metadata = {key: document[key] for key in ("date", "time", "v", "copyright")
                         if key in document}
        # Metadata is allowed alongside a top-level map of schedules.
        self.entries = {name: value for name, value in self.entries.items()
                        if isinstance(value, dict) and "grid" in value}
        if not self.entries:
            raise ValueError("Unsupported semester.json structure: no group objects containing grid; "
                             "provide the source file to inspect its schema")
        if self.prefix:
            original = document[self.prefix[1:]]
            if set(original) != set(self.entries):
                raise ValueError("semester.json contains group entries without grid; export aborted")
        else:
            unexpected = set(document) - set(self.entries) - {"date", "time", "v", "copyright"}
            if unexpected:
                raise ValueError("Unsupported entries in semester.json; export aborted")

    def pointer(self, name):
        return self.prefix + "/" + name.replace("~", "~0").replace("/", "~1")

    def get(self, path):
        if path == "/js/main.js":
            return ((self.javascript.read_bytes(), None) if self.javascript else self.network.get(path))
        name = parse_qs(urlsplit(path).query).get("group", [None])[0]
        if name not in self.entries:
            raise ValueError("Group absent from semester.json")
        entry = self.entries[name]
        group = entry.get("group")
        if group is None:
            group = {key: value for key, value in entry.items()
                     if key not in ("grid", "status", "isSession")}
            group.setdefault("title", name)
        if not isinstance(group, dict):
            raise ValueError("Unsupported group metadata in semester.json")
        payload = {**entry, "group": group, "status": entry.get("status", "ok"),
                   "isSession": entry.get("isSession", False)}
        return json.dumps(payload, ensure_ascii=False).encode(), self.captured


class HAR:
    """Replay only public source bodies; do not copy headers/cookies/analytics."""
    def __init__(self, path):
        self.bodies = {}
        self.groups = set()
        entries = json.loads(path.read_text(encoding="utf-8-sig"))["log"]["entries"]
        for entry in entries:
            request, response = entry["request"], entry["response"]
            url = urlsplit(request["url"])
            if url.hostname != "rasp.dmami.ru" or request["method"] != "GET" or response["status"] != 200:
                continue
            query = parse_qs(url.query)
            name = query.get("group", [None])[0]
            if url.path == "/site/group" and name and query.get("session") == ["0"]:
                key = "/site/group?" + urlencode({"group": name, "session": 0})
            elif url.path in ("/", "/js/main.js"):
                key = url.path
            else:
                continue
            content = response.get("content", {})
            if "text" not in content:
                continue
            body = (base64.b64decode(content["text"]) if content.get("encoding") == "base64"
                    else content["text"].encode("utf-8"))
            self.bodies[key] = body, entry.get("startedDateTime")
            if url.path == "/site/group":
                self.groups.add(name)

    def get(self, path):
        if path not in self.bodies:
            raise ValueError("Source response is not present in HAR")
        return self.bodies[path]


def export(source, output, requested=None):
    started = stamp()
    if isinstance(source, Semester):
        catalog = {**source.metadata, "groups": dict.fromkeys(source.entries)}
        print(f"Source: {source.source_url}; published date: {source.metadata.get('date', 'unknown')}; "
              f"groups: {len(source.entries)}", flush=True)
        catalog_source = {"url": source.source_url, "sha256": digest(source.body),
                          "captured_at": source.captured, "snapshot": "source/semester.json"}
        save_bytes(output / "source/semester.json", source.body)
    else:
        homepage, home_time = source.get("/")
        catalog = parse_catalog(homepage.decode("utf-8-sig"))
        catalog_source = {"url": BASE_URL + "/", "sha256": digest(homepage), "captured_at": home_time}
        save_bytes(output / "source/home.html", homepage)
    javascript, js_time = source.get("/js/main.js")
    bells = parse_bells(javascript.decode("utf-8-sig"))
    names = sorted(set(requested) if requested else
                   source.groups if isinstance(source, HAR) else catalog["groups"])
    if not names or any(name not in catalog["groups"] for name in names):
        raise ValueError("Requested groups are empty or absent from catalog")
    save_bytes(output / "source/main.js", javascript)
    save_json(output / "catalog.json", {"catalog": catalog, "bell_times": bells,
              "source": catalog_source,
              "bell_source": {"url": BASE_URL + "/js/main.js", "sha256": digest(javascript), "captured_at": js_time}})
    groups, errors, rows = [], [], []
    for index, name in enumerate(names, 1):
        path = "/site/group?" + urlencode({"group": name, "session": 0})
        # Never use untrusted group names as filesystem paths.
        key = digest(name.encode("utf-8"))
        try:
            body, captured = source.get(path)
            raw_path = f"source/groups/{key}.json"
            save_bytes(output / raw_path, body)
            parsed = parse_group(json.loads(body.decode("utf-8-sig")), name, bells)
            parsed["source"] = {"url": BASE_URL + path, "sha256": digest(body),
                                "captured_at": captured, "snapshot": raw_path}
            if isinstance(source, Semester):
                parsed["source"].update({"url": source.source_url,
                    "snapshot_kind": "extracted_group", "document": catalog_source,
                    "json_pointer": source.pointer(name)})
            save_json(output / f"groups/{key}.json", parsed)
            save_csv(output / f"groups/{key}.csv", parsed["lessons"])
            groups.append({"name": name, "file": f"groups/{key}.json", **parsed})
            rows.extend(parsed["lessons"])
            print(f"[{index}/{len(names)}] {name}: {len(parsed['lessons'])} lesson rules", flush=True)
        except (ValueError, URLError, TimeoutError, OSError) as exc:
            errors.append({"group": name, "url": source.source_url if isinstance(source, Semester)
                           else BASE_URL + path, "error": str(exc)})
            print(f"[{index}/{len(names)}] {name}: ERROR: {exc}", file=sys.stderr, flush=True)
    document = {"kind": "dmami_schedule", "schema_version": 1, "parser_revision": 3,
                "started_at": started, "finished_at": stamp(),
                "source": catalog_source,
                "source_metadata": {key: catalog[key] for key in ("date", "time", "v", "copyright")
                                    if key in catalog},
                "mode": "har" if isinstance(source, HAR) else "semester_json" if isinstance(source, Semester)
                else "network", "session": 0,
                "catalog_count": len(catalog["groups"]), "requested_count": len(names),
                "exported_count": len(groups), "complete": not errors,
                "covers_entire_catalog": set(names) == set(catalog["groups"]) and not errors,
                "requested_groups": names, "groups": groups, "errors": errors,
                "review_required": True}
    save_json(output / "schedule.json", document)
    save_csv(output / "schedule.csv", rows)
    print(f"Exported {len(groups)}/{len(names)} groups; {len(errors)} errors. Output: {output}")
    return document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--group", action="append", help="Export only this group; repeatable")
    parser.add_argument("--har", type=Path, help="Offline: export group responses captured in a HAR")
    parser.add_argument("--semester-json", type=Path, help="Read a saved semester.json instead of downloading")
    parser.add_argument("--main-js", type=Path, help="Read saved bell times JavaScript (with --semester-json)")
    parser.add_argument("--group-api", action="store_true", help="Legacy: request each /site/group instead of semester.json")
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--delay", type=float, default=0.5, help="Minimum seconds between request starts")
    args = parser.parse_args()
    if args.timeout <= 0 or args.delay < 0 or args.retries < 0:
        parser.error("timeout must be positive; delay and retries must be nonnegative")
    if sum(bool(value) for value in (args.har, args.semester_json, args.group_api)) > 1:
        parser.error("--har, --semester-json and --group-api are mutually exclusive")
    if args.main_js and not args.semester_json:
        parser.error("--main-js requires --semester-json")
    try:
        network = Network(args.timeout, args.retries, args.delay)
        source = (HAR(args.har) if args.har else network if args.group_api
                  else Semester(network, args.semester_json, args.main_js,
                                args.output / "source/semester.json"))
        result = export(source, args.output, args.group)
    except (ValueError, URLError, TimeoutError, OSError) as exc:
        print(f"Export failed: {exc}", file=sys.stderr)
        return 1
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
