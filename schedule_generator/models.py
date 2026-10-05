"""Модели предметной области генератора расписания.

Сущности:
- Semester / WeekPattern — календарь (недели, пары в день).
- Group / Student — учебные группы и студенты.
- Project / ProjectEnrollment — проектная деятельность (ПД): студент сам
  выбирает проект; «группа» по ПД = все выбравшие один проект.
- Teacher / Subject / Room — преподаватели, дисциплины, аудитории.
- LessonEvent — «что нужно запланировать»: занятие для группы/потока/проекта
  с требуемым числом пар и типом. Физкультура создаётся, но игнорируется
  генератором.
- ScheduledClass — результат генерации: конкретная пара в слот/аудиторию.
- GenerationRun / GenerationIssue — журнал запусков и проблем.
"""

from django.core.exceptions import ValidationError
from django.db import models

from .enums import Format, LessonType


class Semester(models.Model):
    name = models.CharField("Название", max_length=50, unique=True)  # например «2026/27 осень»
    start_date = models.DateField("Начало")
    weeks_count = models.PositiveSmallIntegerField("Недель", default=18)

    class Meta:
        verbose_name = "Семестр"
        verbose_name_plural = "Семестры"

    def __str__(self):
        return self.name


class WeekPattern(models.Model):
    """Шаблон недели: набор пар (слотов) в каждый день.

    Один семестр может иметь чётную/нечётную недели — тогда паттернов два.
    """

    semester = models.ForeignKey(Semester, on_delete=models.CASCADE, related_name="week_patterns")
    name = models.CharField("Название", max_length=50, default="Базовая неделя")
    parity = models.PositiveSmallIntegerField(
        "Чётность недели",
        choices=[(0, "Любая"), (1, "Нечётная"), (2, "Чётная")],
        default=0,
    )

    class Meta:
        verbose_name = "Шаблон недели"
        verbose_name_plural = "Шаблоны недель"
        unique_together = [("semester", "name")]

    def __str__(self):
        return f"{self.semester.name}: {self.name}"


class TimeSlot(models.Model):
    """Пара в сетке расписания: день недели + порядковый номер пары."""

    DAY_CHOICES = [
        (0, "Понедельник"), (1, "Вторник"), (2, "Среда"),
        (3, "Четверг"), (4, "Пятница"), (5, "Суббота"),
    ]

    week_pattern = models.ForeignKey(WeekPattern, on_delete=models.CASCADE, related_name="slots")
    day_of_week = models.PositiveSmallIntegerField("День недели", choices=DAY_CHOICES)
    lesson_number = models.PositiveSmallIntegerField("Номер пары")
    start_time = models.TimeField("Начало", null=True, blank=True)
    end_time = models.TimeField("Конец", null=True, blank=True)

    class Meta:
        verbose_name = "Слот (пара)"
        verbose_name_plural = "Слоты (пары)"
        unique_together = [("week_pattern", "day_of_week", "lesson_number")]
        ordering = ["day_of_week", "lesson_number"]

    def __str__(self):
        return f"{self.get_day_of_week_display()} {self.lesson_number}-я пара"


class Group(models.Model):
    """Учебная группа (для очных пар и лекционных потоков)."""

    name = models.CharField("Название", max_length=50, unique=True)
    semester = models.ForeignKey(
        Semester, on_delete=models.CASCADE, related_name="groups",
        verbose_name="Семестр", null=True, blank=True,
    )
    is_first_year_first_semester = models.BooleanField(
        "Перваки, первый семестр",
        default=False,
        help_text="Такие группы не участвуют в проектной деятельности.",
    )

    class Meta:
        verbose_name = "Группа"
        verbose_name_plural = "Группы"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Student(models.Model):
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="students",
                              verbose_name="Учебная группа")
    full_name = models.CharField("ФИО", max_length=150)

    class Meta:
        verbose_name = "Студент"
        verbose_name_plural = "Студенты"

    def __str__(self):
        return self.full_name


class Project(models.Model):
    """Проект по проектной деятельности (ПД)."""

    semester = models.ForeignKey(Semester, on_delete=models.CASCADE, related_name="projects",
                                 verbose_name="Семестр")
    title = models.CharField("Название проекта", max_length=200)
    supervisor = models.ForeignKey(
        "Teacher", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="projects", verbose_name="Руководитель",
    )
    max_students = models.PositiveSmallIntegerField("Макс. студентов", null=True, blank=True)

    class Meta:
        verbose_name = "Проект (ПД)"
        verbose_name_plural = "Проекты (ПД)"

    def __str__(self):
        return self.title


class ProjectEnrollment(models.Model):
    """Выбор проекта студентом. Группа ПД = все enrollment одного проекта."""

    student = models.OneToOneField(Student, on_delete=models.CASCADE,
                                   related_name="project_choice", verbose_name="Студент")
    project = models.ForeignKey(Project, on_delete=models.CASCADE,
                                related_name="enrollments", verbose_name="Проект")

    class Meta:
        verbose_name = "Выбор проекта"
        verbose_name_plural = "Выборы проектов"

    def clean(self):
        super().clean()
        if (self.student_id and self.project_id
                and self.student.group.is_first_year_first_semester
                and self.project.semester_id == self.student.group.semester_id):
            raise ValidationError(
                "Первокурсники в первом семестре не выбирают проект по ПД."
            )

    def __str__(self):
        return f"{self.student} -> {self.project}"


class Teacher(models.Model):
    full_name = models.CharField("ФИО", max_length=150)
    max_pairs_per_day = models.PositiveSmallIntegerField("Макс. пар в день", default=4)
    max_pairs_per_week = models.PositiveSmallIntegerField("Макс. пар в неделю", default=20)

    class Meta:
        verbose_name = "Преподаватель"
        verbose_name_plural = "Преподаватели"

    def __str__(self):
        return self.full_name


class Subject(models.Model):
    name = models.CharField("Название", max_length=200)

    class Meta:
        verbose_name = "Дисциплина"
        verbose_name_plural = "Дисциплины"

    def __str__(self):
        return self.name


class Room(models.Model):
    """Аудитория. capacity ограничивает только очные занятия;
    онлайн-занятия (лекции, часть внеучебки) проходят без аудитории."""

    name = models.CharField("Название", max_length=50, unique=True)
    capacity = models.PositiveSmallIntegerField("Вместимость", default=30)
    allows_lecture = models.BooleanField("Подходит для лекций", default=True)

    class Meta:
        verbose_name = "Аудитория"
        verbose_name_plural = "Аудитории"

    def __str__(self):
        return self.name


class LectureStream(models.Model):
    """Поток лекций: несколько групп смотрят одну и ту же онлайн-лекцию."""

    semester = models.ForeignKey(Semester, on_delete=models.CASCADE, related_name="streams",
                                 verbose_name="Семестр")
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="streams",
                                verbose_name="Дисциплина")
    teacher = models.ForeignKey(Teacher, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name="streams", verbose_name="Преподаватель")
    groups = models.ManyToManyField(Group, related_name="lecture_streams",
                                    verbose_name="Группы")

    class Meta:
        verbose_name = "Лекционный поток"
        verbose_name_plural = "Лекционные потоки"

    def __str__(self):
        return f"{self.subject} ({', '.join(g.name for g in self.groups.all())})"


class LessonEvent(models.Model):
    """Единица планирования: сколько пар какого типа нужно провести.

    Носитель занятия — одно из:
      - group   (практика/лаборатория/внеучебка на группу),
      - stream  (лекция на поток групп),
      - project (ПД на выбранную группу студентов).
    Физкультура (PE) тоже может храниться здесь, но генератор её пропускает.
    """

    semester = models.ForeignKey(Semester, on_delete=models.CASCADE, related_name="events",
                                 verbose_name="Семестр")
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="events",
                                verbose_name="Дисциплина")
    lesson_type = models.CharField("Тип занятия", max_length=20, choices=LessonType.choices)
    format = models.CharField(
        "Формат", max_length=10, choices=Format.choices, null=True, blank=True,
        help_text="Заполняется только для внеучебки; остальные типы имеют фиксированный формат.",
    )
    group = models.ForeignKey(Group, on_delete=models.CASCADE, null=True, blank=True,
                              related_name="events", verbose_name="Группа")
    stream = models.ForeignKey(LectureStream, on_delete=models.CASCADE, null=True, blank=True,
                               related_name="events", verbose_name="Лекционный поток")
    project = models.ForeignKey(Project, on_delete=models.CASCADE, null=True, blank=True,
                                related_name="events", verbose_name="Проект (ПД)")
    teacher = models.ForeignKey(Teacher, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name="events", verbose_name="Преподаватель")
    pairs_per_week = models.PositiveSmallIntegerField("Пар в неделю", default=1)
    audience_size = models.PositiveSmallIntegerField("Слушателей", default=25)

    class Meta:
        verbose_name = "Занятие (план)"
        verbose_name_plural = "Занятия (план)"

    def clean(self):
        from .enums import LessonType

        super().clean()
        errors = {}
        holders = [self.group_id, self.stream_id, self.project_id]
        if sum(1 for h in holders if h) != 1:
            errors["holder"] = (
                "У занятия должен быть ровно один носитель: группа, "
                "лекционный поток или проект (ПД)."
            )
        if self.lesson_type == LessonType.LECTURE and not self.stream_id:
            errors["stream"] = "Лекция должна быть привязана к лекционному потоку."
        if self.lesson_type == LessonType.PD and not self.project_id:
            errors["project"] = "Пара ПД должна быть привязана к проекту."
        if self.lesson_type in (LessonType.PRACTICE, LessonType.LAB) and not self.group_id:
            errors["group"] = "Практика/лаборатория планируются на учебную группу."
        # Формат у жёстких типов фиксирован — не даём задать противоречивое значение.
        from .enums import DEFAULT_FORMAT_BY_TYPE, Format

        if self.lesson_type in DEFAULT_FORMAT_BY_TYPE and self.format:
            expected = DEFAULT_FORMAT_BY_TYPE[self.lesson_type]
            if self.format != expected:
                errors["format"] = (
                    f"Тип «{self.get_lesson_type_display()}» всегда проходит в формате "
                    f"«{expected.label}»; поле формата можно задавать только для внеучебки."
                )
        if self.lesson_type not in DEFAULT_FORMAT_BY_TYPE and self.format == Format.ONLINE \
                and not self.group_id and not self.project_id:
            errors["format"] = "Онлайн-внеучебка должна быть привязана к группе."
        if self.pairs_per_week is not None and self.pairs_per_week < 0:
            errors["pairs_per_week"] = "Не может быть отрицательным."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        holder = self.group or self.stream or self.project
        return f"{self.subject} [{self.get_lesson_type_display()}] — {holder}"

    @property
    def effective_format(self) -> str:
        from .enums import effective_format as _ef
        return _ef(self.lesson_type, self.format)


class ScheduledClass(models.Model):
    """Результат генерации: конкретная пара в конкретный слот."""

    event = models.ForeignKey(LessonEvent, on_delete=models.CASCADE, related_name="classes",
                              verbose_name="Занятие")
    slot = models.ForeignKey(TimeSlot, on_delete=models.CASCADE, related_name="classes",
                             verbose_name="Слот")
    room = models.ForeignKey(Room, on_delete=models.SET_NULL, null=True, blank=True,
                             related_name="classes", verbose_name="Аудитория")
    run = models.ForeignKey("GenerationRun", on_delete=models.CASCADE,
                            related_name="scheduled_classes", verbose_name="Запуск")

    class Meta:
        verbose_name = "Пара в расписании"
        verbose_name_plural = "Пары в расписании"
        constraints = [
            models.UniqueConstraint(fields=["event", "slot"], name="uniq_event_slot"),
        ]

    def __str__(self):
        return f"{self.event.subject} @ {self.slot}"


class GenerationRun(models.Model):
    STATUS_CHOICES = [
        ("PENDING", "Ожидание"),
        ("SOLVED", "Решено"),
        ("PARTIAL", "Частично"),
        ("INFEASIBLE", "Неразрешимо"),
        ("ERROR", "Ошибка"),
    ]

    semester = models.ForeignKey(Semester, on_delete=models.CASCADE, related_name="runs",
                                 verbose_name="Семестр")
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    status = models.CharField("Статус", max_length=20, choices=STATUS_CHOICES, default="PENDING")
    objective_value = models.IntegerField("Значение целевой функции", null=True, blank=True)
    solve_time_seconds = models.FloatField("Время решения (сек)", null=True, blank=True)
    message = models.TextField("Сообщение", blank=True)

    class Meta:
        verbose_name = "Запуск генерации"
        verbose_name_plural = "Запуски генерации"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Run #{self.pk} ({self.semester}, {self.status})"


class GenerationIssue(models.Model):
    """Проблема/предупреждение процесса генерации (например, unscheduled event)."""

    run = models.ForeignKey(GenerationRun, on_delete=models.CASCADE, related_name="issues")
    event = models.ForeignKey(LessonEvent, on_delete=models.SET_NULL, null=True, blank=True)
    level = models.CharField("Уровень", max_length=10, choices=[
        ("WARNING", "Предупреждение"), ("ERROR", "Ошибка"),
    ], default="WARNING")
    text = models.TextField("Текст")

    class Meta:
        verbose_name = "Проблема генерации"
        verbose_name_plural = "Проблемы генерации"

    def __str__(self):
        return f"[{self.level}] {self.text[:80]}"
