"""Ресурсы для занятий: дисциплины, корпуса/локации, аудитории, лекционные потоки."""

from django.db import models


class Subject(models.Model):
    name = models.CharField("Название", max_length=200)

    class Meta:
        verbose_name = "Дисциплина"
        verbose_name_plural = "Дисциплины"

    def __str__(self):
        return self.name


class Location(models.Model):
    """Учебная локация — адрес, где стоит один или несколько корпусов.

    Кампусов и площадок у нас нет: есть корпуса на разных локациях, между
    каждым час езды (поэтому и действует правило «один участник — одна
    локация в слоте»). Примеры локаций: Михалковская, Прянишникова,
    Большая Семёновская, Павла Корчагина, Дубровская, Автозаводская.
    """

    name = models.CharField("Локация", max_length=100, unique=True)
    address = models.CharField("Адрес", max_length=250, blank=True)

    class Meta:
        verbose_name = "Локация"
        verbose_name_plural = "Локации"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Building(models.Model):
    """Учебный корпус на конкретной локации.

    Внутри корпуса аудитория может иметь своё деление (например, буквенные
    блоки «Г» и цифровые «251») — это отражено в названии Room, отдельной
    сущности для блоков нет.
    """

    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="buildings")
    name = models.CharField("Корпус", max_length=100)

    class Meta:
        verbose_name = "Корпус"
        verbose_name_plural = "Корпуса"
        constraints = [models.UniqueConstraint(fields=["location", "name"], name="uniq_location_building")]

    def __str__(self):
        return f"{self.location.name}: {self.name}"


class Room(models.Model):
    """Аудитория внутри корпуса. capacity ограничивает только очные занятия;
    онлайн-занятия (лекции, часть внеучебки) проходят без аудитории.

    Название — то, что написано на двери: «Г-251», «251», «Л-201».
    """

    building = models.ForeignKey(Building, on_delete=models.PROTECT, null=True, blank=True,
                                 related_name="rooms",
                                 help_text="Корпус, в котором находится аудитория.")
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
    def location(self) -> Location | None:
        """Локация, к которой относится аудитория (через корпус)."""
        if self.building_id:
            return self.building.location
        return None

    @property
    def location_label(self) -> str:
        """Человекочитаемое место: «Локация · корпус X · Аудитория» (без пустых уровней)."""
        parts = []
        if (loc := self.location) is not None:
            parts.append(loc.name)
        if self.building_id:
            parts.append(f"к. {self.building.name}")
        parts.append(self.name)
        return " · ".join(parts)

    def location_name_for_ui(self) -> str:
        """Название локации для интерфейса; если локация не указана — пустая строка."""
        loc = self.location
        return loc.name if loc is not None else ""


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
