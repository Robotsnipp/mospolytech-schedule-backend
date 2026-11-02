"""A persisted command coordinating genuine, independent module solver runs."""
from django.db import models


class SemesterGeneration(models.Model):
    semester = models.ForeignKey('schedule_generator.Semester', on_delete=models.PROTECT, related_name='semester_generations')
    group = models.ForeignKey('schedule_generator.Group', on_delete=models.PROTECT, related_name='semester_generations')
    week_pattern = models.ForeignKey('schedule_generator.WeekPattern', on_delete=models.PROTECT, related_name='semester_generations')
    resource_profile = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, default='PENDING')
    settings_snapshot = models.JSONField(default=dict)
    input_snapshot = models.JSONField(default=dict)
    module_results = models.JSONField(default=list)
    solve_time_seconds = models.FloatField(default=0)
    elapsed_seconds = models.FloatField(default=0)
    message = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f'Semester generation #{self.pk} ({self.status})'


class SemesterGenerationModule(models.Model):
    batch = models.ForeignKey(SemesterGeneration, on_delete=models.CASCADE, related_name='module_runs')
    module = models.ForeignKey('schedule_generator.Module', on_delete=models.PROTECT, related_name='semester_generations')
    run = models.ForeignKey('schedule_generator.GenerationRun', on_delete=models.PROTECT, null=True, blank=True, related_name='semester_generations')
    budget_seconds = models.PositiveSmallIntegerField()
    result = models.JSONField(default=dict)

    class Meta:
        ordering = ['id']
        constraints = [models.UniqueConstraint(fields=['batch', 'module'], name='unique_semester_generation_module')]

    def __str__(self):
        return f'Semester generation #{self.batch_id}: module #{self.module_id}'
