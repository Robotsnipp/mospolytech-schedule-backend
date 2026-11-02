"""Время пар — единственное место правды для расписания rasp.dmami.ru."""

from datetime import time

from schedule_generator.bell_profiles import PROFILES

# Published Moscow daytime data uses this profile; other forms must choose theirs.
PAIR_TIMES: dict[int, tuple[time, time]] = {
    number: (time.fromisoformat(start), time.fromisoformat(end))
    for number, start, end in PROFILES["MOSCOW_DAY"]["times"]
}

# «Места», которые не являются физическими аудиториями.
VIRTUAL_LOCATIONS = ("Webinar", "ПД")

# Подстроки (в нижнем регистре) в названии/месте, по которым аудитория считается не физической.
VIRTUAL_MARKERS = ("online", "лайн", "webinar", "вебинар", "дистанц")
