from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("schedule_generator", "0015_bell_schedule_and_protected_slots")]
    operations = [
        migrations.AddField(model_name="generationrun", name="solver_diagnostics",
            field=models.JSONField(default=dict, blank=True, verbose_name="Качество поиска решателя")),
        migrations.AlterField(model_name="generationrun", name="objective_value",
                              field=models.BigIntegerField(blank=True, null=True,
                                                           verbose_name="Значение целевой функции")),
        migrations.AlterField(model_name="generationrun", name="status",
            field=models.CharField(default="PENDING", max_length=20, verbose_name="Статус",
                choices=[("PENDING", "Ожидание"), ("SOLVED", "Решено"), ("PARTIAL", "Частично"),
                         ("INFEASIBLE", "Неразрешимо"), ("UNKNOWN", "Решение не найдено за время поиска"),
                         ("ERROR", "Ошибка")])),
    ]
