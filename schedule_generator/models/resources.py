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
    name = models.CharField("Название площадки", max_length=100, unique=True)
    address = models.CharField("Адрес", max_length=250, blank=True)

    def __str__(self):
        return self.name


class Building(models.Model):
    """Учебный корпус.

    Корпуса (Михалковская, Прянишникова, Большая семёновская и т.д.)
    находятся далеко друг от друга (~час езды), поэтому каждый корпус —
    отдельная площадка (кампус). Объединять корпуса в общие площадки не
    нужно: генератор запрещает участнику быть на двух площадках в одном
    слоте (services/constraints.add_campus_consistency).
    """

    campus = models.ForeignKey(Campus, on_delete=models.PROTECT, related_name="buildings")
    name = models.CharField("Корпус", max_length=100)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["campus", "name"], name="uniq_campus_building")]

    def __str__(self):
        return self.name


class Room(models.Model):
    """Аудитория. capacity ограничивает только очные занятия;
    онлайн-занятия (лекции, часть внеучебки) проходят без аудитории."""

    name = models.CharField("Название", max_length=50, unique=True)
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
    def building_display(self) -> str:
        """Корпус аудитории для интерфейса; у аудиторий без корпуса — пустая строка."""
        if self.building_id is None:
            return ""
        return str(self.building)


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
