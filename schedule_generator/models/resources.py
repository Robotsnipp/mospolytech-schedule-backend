"""Ресурсы для занятий: дисциплины, аудитории, лекционные потоки."""

from django.db import models


class Subject(models.Model):
    name = models.CharField("Название", max_length=200)

    class Meta:
        verbose_name = "Дисциплина"
        verbose_name_plural = "Дисциплины"

    def __str__(self):
        return self.name


class Campus(models.Model):
    """Кампус университета (площадка).

    Примеры кампусов: Михалковская, Прянишникова, Большая Семёновская,
    Павла Корчагина, Дубравная (Дубровский), Автозаводская.
    Внутри кампуса могут быть корпуса (Building), а может и не быть —
    тогда аудитории относятся к кампусу напрямую.
    """

    name = models.CharField("Название кампуса", max_length=100, unique=True)
    address = models.CharField("Адрес", max_length=250, blank=True)

    class Meta:
        verbose_name = "Кампус"
        verbose_name_plural = "Кампусы"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Building(models.Model):
    """Корпус внутри кампуса (необязательный уровень: у кампуса может не быть корпусов)."""

    campus = models.ForeignKey(Campus, on_delete=models.PROTECT, related_name="buildings")
    name = models.CharField("Корпус", max_length=100)

    class Meta:
        verbose_name = "Корпус"
        verbose_name_plural = "Корпуса"
        constraints = [models.UniqueConstraint(fields=["campus", "name"], name="uniq_campus_building")]

    def __str__(self):
        return f"{self.campus}: {self.name}"


class Room(models.Model):
    """Аудитория. capacity ограничивает только очные занятия;
    онлайн-занятия (лекции, часть внеучебки) проходят без аудитории.

    Привязка к месту: building (если у кампуса есть корпуса) либо campus
    напрямую (если деления на корпуса нет).
    """

    name = models.CharField("Название", max_length=50, unique=True)
    campus_ptr = models.ForeignKey(
        Campus, on_delete=models.PROTECT, null=True, blank=True, related_name="rooms",
        db_column="campus_id", verbose_name="Кампус",
        help_text="Указывается напрямую, если у кампуса нет деления на корпуса. "
                  "Свойство Room.campus возвращает кампус (через корпус или напрямую).",
    )
    building = models.ForeignKey(Building, on_delete=models.PROTECT, null=True, blank=True, related_name="rooms")
    is_active = models.BooleanField("Доступна для генерации", default=True)
    room_type = models.CharField("Тип помещения", max_length=50, blank=True)
    equipment = models.TextField("Оборудование", blank=True)
    capacity = models.PositiveSmallIntegerField("Вместимость", default=30)
    allows_lecture = models.BooleanField("Подходит для лекций", default=True)

    class Meta:
        verbose_name = "Аудитория"
        verbose_name_plural = "Аудитории"

    def __str__(self):
        return self.name

    @property
    def campus(self) -> Campus | None:
        """Кампус, к которому относится аудитория (через корпус или напрямую)."""
        if self.building_id:
            return self.building.campus
        return self.campus_ptr

    @property
    def location_label(self) -> str:
        """Человекочитаемое место: «Кампус · Корпус · Аудитория» (без пустых уровней)."""
        parts = []
        c = self.campus
        if c is not None:
            parts.append(c.name)
        if self.building_id:
            parts.append(f"к. {self.building.name}")
        parts.append(self.name)
        return " · ".join(parts)

    def campus_label_for_ui(self) -> str:
        """Название кампуса для интерфейса; если кампус не указан — пустая строка."""
        c = self.campus
        return c.name if c is not None else ""


class LectureStream(models.Model):
    """Поток лекций: несколько групп смотрят одну и ту же онлайн-лекцию."""

    semester = models.ForeignKey("schedule_generator.Semester", on_delete=models.CASCADE,
                                 related_name="streams", verbose_name="Семестр")
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="streams",
                                verbose_name="Дисциплина")
    teacher = models.ForeignKey("schedule_generator.Teacher", on_delete=models.SET_NULL,
                                null=True, blank=True,
                                related_name="streams", verbose_name="Преподаватель")
    groups = models.ManyToManyField("schedule_generator.Group", related_name="lecture_streams",
                                    verbose_name="Группы")

    class Meta:
        verbose_name = "Лекционный поток"
        verbose_name_plural = "Лекционные потоки"

    def __str__(self):
        return f"{self.subject} ({', '.join(g.name for g in self.groups.all())})"
