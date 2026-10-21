"""Нормализованное расписание rasp.dmami.ru (отдельно от генератора расписания)."""

from django.db import models


class Group(models.Model):
    title = models.CharField(max_length=50, unique=True)
    course = models.PositiveSmallIntegerField(null=True, blank=True)
    date_from = models.DateField(null=True, blank=True)
    date_to = models.DateField(null=True, blank=True)
    evening = models.BooleanField(default=False)
    comment = models.TextField(blank=True)
    # Значение из groups.json; смысл уточняется, на логику не влияет.
    flag = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "rasp_groups"
        ordering = ["title"]

    def __str__(self):
        return self.title


class Teacher(models.Model):
    id = models.PositiveIntegerField(primary_key=True)  # внешний id rasp.dmami.ru
    name = models.CharField(max_length=200)
    is_vacancy = models.BooleanField(default=False)

    class Meta:
        db_table = "rasp_teachers"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Subject(models.Model):
    name = models.CharField(max_length=300, unique=True)

    class Meta:
        db_table = "rasp_subjects"

    def __str__(self):
        return self.name


class Room(models.Model):
    name = models.CharField(max_length=200)
    raw_title = models.TextField(blank=True, help_text="Исходный title (возможно HTML).")
    location = models.CharField(max_length=200, blank=True)
    url = models.URLField(max_length=1000, null=True, blank=True)  # noqa: DJ001 — NULL = нет ссылки

    class Meta:
        db_table = "rasp_rooms"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["name", "location", "url"], name="uniq_rasp_room"),
        ]

    def __str__(self):
        return self.name


class Lesson(models.Model):
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="lessons")
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="lessons")
    weekday = models.PositiveSmallIntegerField(help_text="1 = пн … 7 = вс")
    pair = models.PositiveSmallIntegerField(help_text="Номер пары 1–7")
    type = models.CharField(max_length=50, blank=True)
    date_from = models.DateField(null=True, blank=True)
    date_to = models.DateField(null=True, blank=True)
    dts = models.CharField(max_length=100, blank=True)
    is_session = models.BooleanField(default=False)
    week = models.CharField(max_length=20, blank=True)
    e_link = models.URLField(max_length=1000, null=True, blank=True)  # noqa: DJ001
    location = models.CharField(max_length=200, blank=True)
    teachers = models.ManyToManyField(Teacher, related_name="lessons",
                                      db_table="rasp_lesson_teachers")
    rooms = models.ManyToManyField(Room, related_name="lessons", db_table="rasp_lesson_rooms")

    class Meta:
        db_table = "rasp_lessons"
        indexes = [models.Index(fields=["group", "weekday", "pair"], name="rasp_lesson_gwp")]
        ordering = ["weekday", "pair", "date_from", "id"]

    def __str__(self):
        return f"{self.group_id} {self.weekday}/{self.pair} {self.subject_id}"


class PDProject(models.Model):
    """Проект по проектной деятельности (таблица Центра проектной деятельности)."""

    number = models.PositiveIntegerField(unique=True, help_text="№ в таблице")
    direction = models.CharField(max_length=100)
    title = models.CharField(max_length=300)
    teacher = models.CharField(max_length=300, blank=True)
    contact = models.CharField(max_length=300, blank=True)
    note = models.TextField(blank=True, help_text="Переносы и разовые занятия из таблицы")
    irregular = models.BooleanField(default=False)

    class Meta:
        db_table = "rasp_pd_projects"
        ordering = ["direction", "number"]

    def __str__(self):
        return f"{self.number}. {self.title}"


class PDSlot(models.Model):
    project = models.ForeignKey(PDProject, on_delete=models.CASCADE, related_name="slots")
    day_of_week = models.PositiveSmallIntegerField(help_text="0 = пн … 5 = сб")
    start_time = models.TimeField()
    end_time = models.TimeField()
    room = models.CharField(max_length=300, blank=True)

    class Meta:
        db_table = "rasp_pd_slots"
        ordering = ["day_of_week", "start_time"]

    def __str__(self):
        return f"{self.project_id} {self.day_of_week} {self.start_time:%H:%M}"


class PDEnrollment(models.Model):
    """Выбор проекта. Входа в системе нет: владелец — идентификатор браузера."""

    owner = models.CharField(max_length=64, unique=True)
    group_title = models.CharField(max_length=50, blank=True)
    project = models.ForeignKey(PDProject, on_delete=models.CASCADE, related_name="enrollments")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "rasp_pd_enrollments"

    def __str__(self):
        return f"{self.owner} -> {self.project_id}"
