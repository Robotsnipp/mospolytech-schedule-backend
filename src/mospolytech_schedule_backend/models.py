"""
Модели БД для генерации расписания пар университета.

Ключевые сущности:
  - Subject       — дисциплина (учебный предмет).
  - ClassType     — вид занятия (лекция/лаба/практика/семинар/физра/ПД/внеучебка)
                    с флагами формата (онлайн/офлайн) и участия в генерации.
  - Group         — учебная группа (академическая).
  - ProjectGroup  — группа, сформированная по выбранному проекту (ПД).
  - Teacher       — преподаватель.
  - Room          — помещение (для офлайна) / онлайн-ссылка.
  - EventSlot     — конкретная пара в сетке расписания (то, что порождает генератор).

Принципы:
  * Лекции планируются сразу на несколько групп -> у EventSlot связь с группами many-to-many.
  * Очные лабы/практики планируются на одну группу (просто одна запись в m2m).
  * Физкультура не участвует в генерации (ClassType.generate=False): студент приходит
    в любое время в любое доступное место — такие пары заводятся без дня/времени/места.
  * ПД: каждый студент (кроме первокурсников на первом семестре) выбирает проект сам;
    ПД-группа формируется из выбравших один проект; пары по ПД всегда очные.
  * Внеучебка может быть онлайн/офлайн и участвует в генерации.
"""

from django.db import models


class BaseModel(models.Model):
    """Абстрактная база: id + временные метки."""

    created_at = models.DateTimeField("создано", auto_now_add=True)
    updated_at = models.DateTimeField("обновлено", auto_now=True)

    class Meta:
        abstract = True
        ordering = ("id",)


# --------------------------------------------------------------------------- #
#  Справочники / формат занятия
# --------------------------------------------------------------------------- #
class Semester(BaseModel):
    """Учебный семестр (например: 2026/2027-1)."""

    name = models.CharField("название", max_length=50, unique=True)
    year = models.PositiveSmallIntegerField("год начала")
    number = models.PositiveSmallIntegerField(
        "номер семестра",
        help_text="1 — первый (осенний), 2 — второй (весенний)",
    )
    start_date = models.DateField("начало", null=True, blank=True)
    end_date = models.DateField("конец", null=True, blank=True)

    class Meta(BaseModel.Meta):
        verbose_name = "семестр"
        verbose_name_plural = "семестры"

    def __str__(self) -> str:
        return self.name


class Subject(BaseModel):
    """Дисциплина (учебный предмет)."""

    code = models.CharField("код", max_length=50, blank=True)
    name = models.CharField("название", max_length=255)

    class Meta(BaseModel.Meta):
        verbose_name = "дисциплина"
        verbose_name_plural = "дисциплины"

    def __str__(self) -> str:
        return self.name


class ClassType(BaseModel):
    """
    Вид занятия. Определяет правила участия в генерации и допустимый формат.

    Примеры заполнения:
      Лекция       — ONLINE,  multi_group=True
      Лабораторная — OFFLINE, multi_group=False (на одну группу)
      Практика     — OFFLINE
      Физкультура  — ANY,     generate=False (не планируется)
      ПД           — OFFLINE, project_based=True
      Внеучебка    — ANY,     generate=True
    """

    class Format(models.TextChoices):
        OFFLINE = "OFFLINE", "Очно"
        ONLINE = "ONLINE", "Онлайн"
        ANY = "ANY", "Любой"

    name = models.CharField("название", max_length=100, unique=True)
    format = models.CharField(
        "формат", max_length=10, choices=Format.choices, default=Format.OFFLINE
    )
    multi_group = models.BooleanField(
        "несколько групп",
        default=False,
        help_text="True — занятие ведётся сразу для набора групп (лекции).",
    )
    generate = models.BooleanField(
        "участвует в генерации",
        default=True,
        help_text="False — расписание не строится (физкультура).",
    )
    project_based = models.BooleanField(
        "группа по проекту (ПД)",
        default=False,
        help_text="True — участники определяются выбором проекта, а не академической группой.",
    )

    class Meta(BaseModel.Meta):
        verbose_name = "вид занятия"
        verbose_name_plural = "виды занятий"

    def __str__(self) -> str:
        return self.name


# --------------------------------------------------------------------------- #
#  Участники / ресурсы
# --------------------------------------------------------------------------- #
class Department(BaseModel):
    """Кафедра / подразделение."""

    name = models.CharField("название", max_length=255)

    class Meta(BaseModel.Meta):
        verbose_name = "кафедра"
        verbose_name_plural = "кафедры"

    def __str__(self) -> str:
        return self.name


class Teacher(BaseModel):
    """Преподаватель."""

    first_name = models.CharField("имя", max_length=100)
    last_name = models.CharField("фамилия", max_length=100)
    middle_name = models.CharField("отчество", max_length=100, blank=True)
    department = models.ForeignKey(
        Department,
        verbose_name="кафедра",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="teachers",
    )

    class Meta(BaseModel.Meta):
        verbose_name = "преподаватель"
        verbose_name_plural = "преподаватели"

    def __str__(self) -> str:
        return f"{self.last_name} {self.first_name}".strip()

    @property
    def full_name(self) -> str:
        return " ".join(filter(None, (self.last_name, self.first_name, self.middle_name)))


class Building(BaseModel):
    """Корпус."""

    name = models.CharField("название", max_length=100)
    address = models.CharField("адрес", max_length=255, blank=True)

    class Meta(BaseModel.Meta):
        verbose_name = "корпус"
        verbose_name_plural = "корпуса"

    def __str__(self) -> str:
        return self.name


class Room(BaseModel):
    """Помещение (для очных занятий). Онлайн-занятия могут не иметь комнаты."""

    building = models.ForeignKey(
        Building,
        verbose_name="корпус",
        on_delete=models.CASCADE,
        related_name="rooms",
    )
    number = models.CharField("номер", max_length=20)
    capacity = models.PositiveSmallIntegerField("вместимость", default=0)
    online_url = models.URLField(
        "онлайн-ссылка",
        blank=True,
        help_text="Заполняется для онлайн-занятий вместо физической комнаты.",
    )

    class Meta(BaseModel.Meta):
        verbose_name = "помещение"
        verbose_name_plural = "помещения"
        constraints = [
            models.UniqueConstraint(
                fields=("building", "number"), name="uniq_room_in_building"
            )
        ]

    def __str__(self) -> str:
        if self.online_url:
            return f"онлайн ({self.number})"
        return f"{self.building.name}-{self.number}"


class Group(BaseModel):
    """Академическая учебная группа."""

    name = models.CharField("название", max_length=50, unique=True)
    course = models.PositiveSmallIntegerField(
        "курс",
        help_text="Номер курса. 1 — первокурсник (важно для правил ПД).",
    )
    department = models.ForeignKey(
        Department,
        verbose_name="кафедра",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="groups",
    )

    class Meta(BaseModel.Meta):
        verbose_name = "группа"
        verbose_name_plural = "группы"

    def __str__(self) -> str:
        return self.name

    @property
    def is_first_year(self) -> bool:
        return self.course == 1


class Student(BaseModel):
    """Студент. Привязан к академической группе."""

    group = models.ForeignKey(
        Group,
        verbose_name="учебная группа",
        on_delete=models.PROTECT,
        related_name="students",
    )
    first_name = models.CharField("имя", max_length=100)
    last_name = models.CharField("фамилия", max_length=100)
    middle_name = models.CharField("отчество", max_length=100, blank=True)
    email = models.EmailField("email", blank=True)

    class Meta(BaseModel.Meta):
        verbose_name = "студент"
        verbose_name_plural = "студенты"

    def __str__(self) -> str:
        return f"{self.last_name} {self.first_name}".strip()


# --------------------------------------------------------------------------- #
#  Проектная деятельность (ПД)
# --------------------------------------------------------------------------- #
class Project(BaseModel):
    """Проект для проектной деятельности. Студент выбирает один проект."""

    name = models.CharField("название", max_length=255)
    description = models.TextField("описание", blank=True)
    supervisor = models.ForeignKey(
        Teacher,
        verbose_name="руководитель",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="projects",
    )
    capacity = models.PositiveSmallIntegerField(
        "максимум участников",
        default=0,
        help_text="0 — без ограничения.",
    )

    class Meta(BaseModel.Meta):
        verbose_name = "проект (ПД)"
        verbose_name_plural = "проекты (ПД)"

    def __str__(self) -> str:
        return self.name


class ProjectChoice(BaseModel):
    """
    Выбор проекта студентом в конкретном семестре.
    На основе выборов формируется ProjectGroup.
    Правило: первокурсники на первом семестре выбор не делают (контролируется на уровне логики/API).
    """

    student = models.ForeignKey(
        Student,
        verbose_name="студент",
        on_delete=models.CASCADE,
        related_name="project_choices",
    )
    project = models.ForeignKey(
        Project,
        verbose_name="проект",
        on_delete=models.CASCADE,
        related_name="choices",
    )
    semester = models.ForeignKey(
        Semester,
        verbose_name="семестр",
        on_delete=models.CASCADE,
        related_name="project_choices",
    )

    class Meta(BaseModel.Meta):
        verbose_name = "выбор проекта"
        verbose_name_plural = "выборы проектов"
        constraints = [
            # Один студент — один проект в семестре.
            models.UniqueConstraint(
                fields=("student", "semester"), name="uniq_project_choice_per_semester"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.student} → {self.project} ({self.semester})"


class ProjectGroup(BaseModel):
    """
    ПД-группа — формируется из студентов, выбравших один проект в семестре.
    Используется как «группа» для очных пар по ПД.
    """

    project = models.ForeignKey(
        Project,
        verbose_name="проект",
        on_delete=models.CASCADE,
        related_name="groups",
    )
    semester = models.ForeignKey(
        Semester,
        verbose_name="семестр",
        on_delete=models.CASCADE,
        related_name="project_groups",
    )
    members = models.ManyToManyField(
        Student,
        verbose_name="участники",
        related_name="project_groups",
        blank=True,
    )

    class Meta(BaseModel.Meta):
        verbose_name = "ПД-группа"
        verbose_name_plural = "ПД-группы"
        constraints = [
            models.UniqueConstraint(
                fields=("project", "semester"), name="uniq_project_group_per_semester"
            ),
        ]

    def __str__(self) -> str:
        return f"ПД: {self.project} ({self.semester})"


# --------------------------------------------------------------------------- #
#  Сетка расписания
# --------------------------------------------------------------------------- #
class WeekPattern(BaseModel):
    """Шаблон недель (нечётные/чётные или произвольные недели семестра)."""

    name = models.CharField("название", max_length=50, unique=True)

    class Meta(BaseModel.Meta):
        verbose_name = "шаблон недель"
        verbose_name_plural = "шаблоны недель"

    def __str__(self) -> str:
        return self.name


class TimeSlot(BaseModel):
    """Окно/пара в сетке дня (например, 1-я пара 09:00–10:35)."""

    number = models.PositiveSmallIntegerField("номер пары")
    start_time = models.TimeField("начало")
    end_time = models.TimeField("конец")

    class Meta(BaseModel.Meta):
        verbose_name = "временной слот"
        verbose_name_plural = "временные слоты"
        ordering = ("start_time",)
        constraints = [
            models.UniqueConstraint(fields=("number",), name="uniq_timeslot_number")
        ]

    def __str__(self) -> str:
        return f"{self.number} пара {self.start_time:%H:%M}-{self.end_time:%H:%M}"


class EventSlot(BaseModel):
    """
    Конкретная пара в расписании — единица, которую порождает генератор.

    * subject + class_type описывают ЧТО это за занятие.
    * groups (m2m) — для кого: одна запись для очной лабы (одна группа),
      много записей для общей онлайн-лекции (несколько групп).
    * project_group — заполняется только для пар по ПД (вместо академических групп);
      пары по ПД всегда очные (class_type.format == OFFLINE).
    * room / online_url — где (зависит от формата).
    * Физкультура (class_type.generate=False): weekday/time_slot/room не назначаются,
      студент приходит в любое время в любое доступное место.
    """

    semester = models.ForeignKey(
        Semester,
        verbose_name="семестр",
        on_delete=models.PROTECT,
        related_name="events",
    )
    subject = models.ForeignKey(
        Subject,
        verbose_name="дисциплина",
        on_delete=models.PROTECT,
        related_name="events",
    )
    class_type = models.ForeignKey(
        ClassType,
        verbose_name="вид занятия",
        on_delete=models.PROTECT,
        related_name="events",
    )
    teacher = models.ForeignKey(
        Teacher,
        verbose_name="преподаватель",
        on_delete=models.PROTECT,
        related_name="events",
    )

    # Для кого проводится занятие (академические группы).
    # Один элемент — очная лаба/практика; много — общая онлайн-лекция.
    groups = models.ManyToManyField(
        Group,
        verbose_name="учебные группы",
        related_name="events",
        blank=True,
    )
    # Только для ПД: занятие привязано к ПД-группе, сформированной по проекту.
    project_group = models.ForeignKey(
        ProjectGroup,
        verbose_name="ПД-группа",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
    )

    week_pattern = models.ForeignKey(
        WeekPattern,
        verbose_name="шаблон недель",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
    )
    weekday = models.PositiveSmallIntegerField(
        "день недели",
        null=True,
        blank=True,
        help_text="0=понедельник … 6=воскресенье. None — время не закреплено (физра).",
    )
    time_slot = models.ForeignKey(
        TimeSlot,
        verbose_name="временной слот",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
    )

    # Место проведения.
    room = models.ForeignKey(
        Room,
        verbose_name="помещение",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
    )
    online_url = models.URLField(
        "онлайн-ссылка",
        blank=True,
        help_text="Для занятий в формате ONLINE.",
    )

    # Служебное поле генератора.
    is_generated = models.BooleanField(
        "сгенерировано",
        default=False,
        help_text="False — слот-заготовка либо занятие вне генерации (физра).",
    )

    class Meta(BaseModel.Meta):
        verbose_name = "пара в расписании"
        verbose_name_plural = "расписание (пары)"
        ordering = ("weekday", "time_slot__start_time", "id")

    def __str__(self) -> str:
        place = str(self.room) if self.room_id else (self.online_url or "—")
        return f"{self.subject} / {self.class_type} @ {place}"

    @property
    def is_online(self) -> bool:
        return self.class_type.format == ClassType.Format.ONLINE or bool(self.online_url)

    @property
    def participates_in_generation(self) -> bool:
        return self.class_type.generate
