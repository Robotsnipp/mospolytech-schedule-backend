"""Parse explicit room labels and addresses without guessing a directory ID."""

import re

from .common import clean

ROOM_LABEL = r"[А-Яа-яЁё]{1,3}\d+[А-Яа-яЁё]?(?:-\d+)?"
ROOM_RE = re.compile(r"(?:аудитория\s+|ауд\.?\s*)?(" + ROOM_LABEL + r")", re.I)
ROOM_TOKEN_RE = re.compile(r"(?<![\w-])(" + ROOM_LABEL + r")(?![\w-])")
ADDRESS_RE = re.compile(r"\b(?:ул(?:\.|(?=[\s,]))|улица\b|проспект\b|пр-т\b|ш\.|шоссе\b|переулок\b|набережная\b)", re.I)
EXTERNAL_ROOM_RE = re.compile(
    r"(?:аудитория|ауд\.?|каб\.?|оф\.?|переговорная)\s*([А-Яа-яA-Za-z]?-?\d+(?:[-/]\d+)*[А-Яа-яA-Za-z]?)", re.I)
EXTERNAL_BARE_ROOM_RE = re.compile(r"[А-Яа-яЁё]{1,3}-?\d+(?:[-/]\d+)*[А-Яа-яЁё]?")


def address_venue(address: str) -> dict:
    """Classify reviewed university sites; keep all other addresses additional."""
    # Match the house/structure too: sharing a street never identifies a campus.
    if re.search(r"\bПрянишникова\s*,?\s*(?:д\.?\s*)?2\s*а\b", address, re.I):
        return {"kind": "university", "name": "Политех — Прянишникова, 2А",
                "organization": "Московский Политех", "coordinates": None,
                "basis": "official_campus_list",
                "source_url": "https://mospolytech.ru/ob-universitete/adresa-i-kontakty/"}
    if re.search(r"\bАвтозаводская\s*,?\s*(?:д\.?\s*)?16\s*,?\s*стр\.?\s*13\b", address, re.I):
        return {"kind": "university", "name": "Политех — Автозаводская, 16с13, гараж",
                "organization": "Московский Политех", "coordinates": None,
                "basis": "official_property_report_2024_01_01",
                "source_url": "https://mospolytech.ru/sveden/files/eid/ORD_SVOD_2023.pdf"}
    # Additional is a scheduling category, not a claim about ownership/partner.
    return {"kind": "additional", "name": None, "organization": None,
            "coordinates": None, "basis": "user_clarification", "source_url": None}


def note_place(text: str, legend: dict) -> dict | None:
    """A date-only venue change stays a note, without inventing meeting times."""
    if not ADDRESS_RE.search(text):
        return None
    address = re.sub(r"^.*?\bпо\s+адресу\s+", "", text, flags=re.I)
    return parse_place([address], legend)


def candidate(label: str, legend: dict) -> dict:
    letters = re.match(r"[А-Яа-яЁё]+", label)[0]
    prefix = next((p for p in sorted(legend, key=len, reverse=True)
                   if p != "default" and letters.casefold().startswith(p.casefold())), None)
    return {"label": label, "location": legend.get(prefix) if prefix else legend.get("default"),
            "room": label[len(prefix):] if prefix else label}


def parse_place(lines: list[str], legend: dict) -> dict:
    """Keep explanatory parentheses, room alternatives and address text distinct."""
    raw = clean(" ".join(lines)) or None
    result = {"kind": "missing", "raw": raw, "room_candidates": [],
              "annotations": [], "address_raw": None, "external_room_labels": [],
              "unparsed": [], "warnings": [], "subgroup_number": None,
              "room_assignment_mode": "single_or_unspecified",
              "venue": None,
              "resource_policy": {"reserve_room": None, "block_attendees": True,
                                  "block_teacher": True, "travel_time_required": True,
                                  "allow_new_assignments": None, "scope": "unclassified"}}
    if raw is None:
        result["warnings"].append("missing_place")
        return result

    has_address = bool(ADDRESS_RE.search(raw))
    # Parentheses alone are descriptive, except when they contain another
    # explicit room or a numeric subdivision that changes the room identity.
    parentheses = re.findall(r"\(([^()]*)\)", raw)
    body = re.sub(r"\([^()]*\)", "", raw)
    labels = []
    for annotation in parentheses:
        extra_labels = ROOM_TOKEN_RE.findall(annotation)
        if extra_labels:
            labels.extend(extra_labels)
        result["annotations"].append(annotation)
        if annotation.strip().isdigit():
            result["warnings"].append("room_subdivision_requires_review")

    def room_list(value):
        parts = re.split(r"[,/;]", clean(value))
        matches = [ROOM_RE.fullmatch(part.strip()) for part in parts]
        return [m[1] for m in matches] if matches and all(matches) else []

    if has_address:
        # Only standalone label lines are university room references. A room
        # such as Б-435 inside an external address must not use our legend.
        address_lines = []
        possible_labels = list(labels)
        for line in lines:
            line_labels = room_list(line)
            if line_labels:
                possible_labels.extend(line_labels)
            else:
                address_lines.append(line)
        result["address_raw"] = clean(" ".join(address_lines))
        result["external_room_labels"] = EXTERNAL_ROOM_RE.findall(result["address_raw"])
        labels = []
        address_key = re.sub(r"\W", "", result["address_raw"].casefold())
        for label in possible_labels:
            location = candidate(label, legend)["location"]
            location_key = re.sub(r"\W", "", (location or "").casefold())
            if location_key and location_key in address_key:
                labels.append(label)
            else:
                result["external_room_labels"].append(label)
        # Bare room labels after commas belong to this external address too.
        for part in body.split(","):
            match = ROOM_RE.fullmatch(part.strip())
            if match:
                result["external_room_labels"].append(match[1])
            elif EXTERNAL_BARE_ROOM_RE.fullmatch(part.strip()):
                result["external_room_labels"].append(part.strip())
        result["external_room_labels"] = list(dict.fromkeys(result["external_room_labels"]))
        venue = address_venue(result["address_raw"])
        result["venue"] = venue
        result["resource_policy"]["allow_new_assignments"] = False
        if venue["kind"] == "university":
            result["kind"] = "address_with_rooms" if labels else "university_address"
            result["resource_policy"]["scope"] = "university_venue_reference"
            result["warnings"].append("university_place_reference_required")
        else:
            # A shared street in the source legend does not make this a campus
            # room (e.g. АВ at Автозаводская 23А instead of university house 16).
            result["external_room_labels"] = list(dict.fromkeys(result["external_room_labels"] + labels))
            labels = []
            result["kind"] = "additional_address"
            result["resource_policy"].update(reserve_room=False, scope="additional_venue",
                                              basis="user_clarification")
    else:
        body_labels = room_list(body)
        if body_labels:
            labels = body_labels + labels
            result["kind"] = "rooms"
            result["resource_policy"]["scope"] = "university_room_reference"
        else:
            result["kind"] = "unresolved"
            result["unparsed"].append(body.strip())
            result["warnings"].append("unmapped_place")
    labels = list(dict.fromkeys(labels))
    result["room_candidates"] = [candidate(label, legend) for label in labels]
    if labels == ["АВ4108"] and len(parentheses) == 1 and parentheses[0].strip() in ("1", "2"):
        # User clarified these two annotations as subgroups, not room partitions.
        result["subgroup_number"] = int(parentheses[0].strip())
        result["room_assignment_mode"] = "subgroup_label"
        result["warnings"].remove("room_subdivision_requires_review")
    if len(labels) > 1:
        # Display all listed rooms until per-student subgroup membership arrives.
        # This does not authorize selecting one or reserving all of them.
        result["room_assignment_mode"] = "listed_without_student_assignment"
    return result
