from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('schedule_generator', '0016_preference_scores_and_unknown_status')]
    operations = [
        migrations.AddField(model_name='generationrun', name='settings_snapshot', field=models.JSONField(blank=True, default=dict, verbose_name='Настройки запуска')),
        migrations.AddField(model_name='generationrun', name='input_snapshot', field=models.JSONField(blank=True, default=dict, verbose_name='Входы планирования')),
        migrations.AddField(model_name='generationrun', name='validation_snapshot', field=models.JSONField(blank=True, default=dict, verbose_name='Проверка результата')),
    ]
