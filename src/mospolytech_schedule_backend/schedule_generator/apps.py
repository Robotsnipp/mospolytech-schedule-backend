from django.apps import AppConfig


class ScheduleGeneratorConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'mospolytech_schedule_backend.schedule_generator'
    label = 'schedule_generator'
    verbose_name = 'Генератор расписания'
