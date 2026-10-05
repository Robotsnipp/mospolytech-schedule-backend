"""Ресурсы для занятий: дисциплины, аудитории, лекционные потоки."""

from django.db import models


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
