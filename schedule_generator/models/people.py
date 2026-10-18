"""Участники учебного процесса: группы, студенты, преподаватели."""

from django.db import models


class Group(models.Model):
    """Учебная группа (для очных пар и лекционных потоков).

    Отдельный вид групп — группы ДПО (дополнительное профессиональное
    образование): для каждой программы ДПО создаётся своя группа со
    слушателями; пары ДПО планируются на такую группу как на обычного
    носителя занятия (см. LessonType.DPO).
    """

    name = models.CharField("Название", max_length=50, unique=True)
    semester = models.ForeignKey(
        "schedule_generator.Semester", on_delete=models.CASCADE, related_name="groups",
        verbose_name="Семестр", null=True, blank=True,
    )
    curriculum = models.ForeignKey("schedule_generator.Curriculum", on_delete=models.PROTECT, null=True, blank=True, related_name="groups", verbose_name="Учебный план")
    is_first_year_first_semester = models.BooleanField(
        "Перваки, первый семестр",
        default=False,
        help_text="Такие группы не участвуют в проектной деятельности.",
    )
    is_dpo = models.BooleanField(
        "Группа ДПО",
        default=False,
        help_text="Группа дополнительного профессионального образования: "
                  "создаётся отдельно для каждого ДПО.",
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


class Teacher(models.Model):
    """Преподаватель. Лимиты пар используются генератором
    (жёстко: одна пара в слот; мягко: перегрузка по max_pairs_per_day)."""

    full_name = models.CharField("ФИО", max_length=150)
    max_pairs_per_day = models.PositiveSmallIntegerField("Макс. пар в день", default=4)
    max_pairs_per_week = models.PositiveSmallIntegerField("Макс. пар в неделю", default=20)

    class Meta:
        verbose_name = "Преподаватель"
        verbose_name_plural = "Преподаватели"

    def __str__(self):
        return self.full_name
