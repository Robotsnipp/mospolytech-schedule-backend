"""Проектная деятельность (ПД): проекты и выбор проекта студентом.

Ключевая особенность домена: «группа» по ПД формируется НЕ из учебной
группы, а из всех студентов, выбравших один проект (ProjectEnrollment).
Поэтому при генерации ПД блокируются конкретные студенты, а не целиком
их учебные группы.
"""

from django.core.exceptions import ValidationError
from django.db import models


class Project(models.Model):
    """Проект по проектной деятельности (ПД)."""

    semester = models.ForeignKey("schedule_generator.Semester", on_delete=models.CASCADE,
                                 related_name="projects", verbose_name="Семестр")
    title = models.CharField("Название проекта", max_length=200)
    supervisor = models.ForeignKey(
        "schedule_generator.Teacher", on_delete=models.SET_NULL, null=True, blank=True,
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

    student = models.OneToOneField("schedule_generator.Student", on_delete=models.CASCADE,
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
