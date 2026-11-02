"""Official bell profiles, explicitly scoped by location and study form."""

SOURCE_URL = "https://mospolytech.ru/obuchauschimsya/raspisaniya/"
PROFILES = {
    "MOSCOW_DAY": {
        "scope": "Московский Политех: очная и заочная формы, дневные пары",
        "study_forms": ["FULL_TIME", "EXTRAMURAL"],
        "times": [(1, "09:00", "10:30"), (2, "10:40", "12:10"),
                  (3, "12:20", "13:50"), (4, "14:30", "16:00"),
                  (5, "16:10", "17:40"), (6, "17:50", "19:20"),
                  (7, "19:30", "21:00")],
    },
    "MOSCOW_EVENING": {
        "scope": "Московский Политех: очно-заочная форма, вечерние пары",
        "study_forms": ["PART_TIME"],
        "times": [(6, "18:20", "19:40"), (7, "19:50", "21:10")],
    },
}
