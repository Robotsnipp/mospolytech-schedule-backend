"""Время пар — единственное место правды для расписания rasp.dmami.ru."""

from datetime import time

PAIR_TIMES: dict[int, tuple[time, time]] = {
    1: (time(9, 0), time(10, 30)),
    2: (time(10, 40), time(12, 10)),
    3: (time(12, 20), time(13, 50)),
    4: (time(14, 30), time(16, 0)),
    5: (time(16, 10), time(17, 40)),
    6: (time(17, 50), time(19, 20)),
    7: (time(19, 30), time(21, 0)),
}

# «Места», которые не являются физическими аудиториями.
VIRTUAL_LOCATIONS = ("Webinar", "ПД")
