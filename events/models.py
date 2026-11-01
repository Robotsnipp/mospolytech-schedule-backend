"""Каталог мероприятий, которые студент может свободно посетить."""

from django.db import models


class Event(models.Model):
    class Scope(models.TextChoices):
        UNIVERSITY = "university", "Университет"
        CITY = "city", "Город"

    class Access(models.TextChoices):
        FREE = "free", "Свободный вход"
        REGISTRATION = "registration", "Нужна регистрация"
        ONLINE = "online", "Онлайн"
        STUDENTS_ONLY = "students_only", "Только для студентов"
        UNKNOWN = "unknown", "Неизвестно"

    scope = models.CharField("масштаб", max_length=20, choices=Scope.choices)
    title = models.CharField("название", max_length=300)
    date_start = models.DateField("дата начала", null=True, blank=True)
    date_end = models.DateField("дата окончания", null=True, blank=True)
    announced_at = models.DateField(
        "дата анонса", null=True, blank=True,
        help_text="Дата поста-анонса, когда точная дата события неизвестна.",
    )
    date_text = models.CharField("уточнение даты", max_length=200, blank=True, default="")
    access = models.CharField(
        "доступ", max_length=20, choices=Access.choices, default=Access.UNKNOWN,
    )
    source = models.CharField("источник", max_length=200, blank=True, default="")
    url = models.URLField("ссылка", max_length=500)
    note = models.TextField("примечание", blank=True, default="")
    is_published = models.BooleanField("опубликовано", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "мероприятие"
        verbose_name_plural = "мероприятия"
        ordering = ["date_start", "announced_at", "id"]
        constraints = [
            # date_start входит в ключ: один источник может описывать одно и то же
            # мероприятие в разные дни (например, дни открытых дверей).
            # Записи без date_start БД не считает дублями (NULL != NULL),
            # поэтому повторный импорт ищет их по date_start IS NULL сам.
            models.UniqueConstraint(
                fields=["url", "title", "date_start"],
                name="event_unique_url_title_date",
            ),
        ]
        indexes = [
            models.Index(fields=["date_start"]),
            models.Index(fields=["announced_at"]),
        ]

    def __str__(self):
        return self.title
