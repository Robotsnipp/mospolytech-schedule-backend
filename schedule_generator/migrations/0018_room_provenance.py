from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("schedule_generator", "0017_generation_settings_snapshots")]
    operations = [
        migrations.AlterField(model_name="timeslot", name="day_of_week", field=models.PositiveSmallIntegerField(choices=[(0,"Понедельник"),(1,"Вторник"),(2,"Среда"),(3,"Четверг"),(4,"Пятница"),(5,"Суббота"),(6,"Воскресенье")], verbose_name="День недели")),
        migrations.AddField(model_name="room", name="confirmed_capacity", field=models.PositiveSmallIntegerField(blank=True, null=True, verbose_name="Подтверждённая вместимость")),
        migrations.AddField(model_name="room", name="provenance", field=models.JSONField(blank=True, default=dict, verbose_name="Источник характеристик")),
        migrations.AddField(model_name="room", name="profile_only", field=models.BooleanField(default=False, verbose_name="Только в отдельном ресурсном профиле")),
        migrations.CreateModel(name="ResourceProfile", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("key", models.SlugField(max_length=100, unique=True)),
            ("name", models.CharField(max_length=150)),
            ("notice", models.TextField(blank=True)),
            ("rooms", models.JSONField(default=list)),
            ("provenance", models.JSONField(blank=True, default=dict)),
            ("group", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="schedule_generator.group")),
            ("semester", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="schedule_generator.semester")),
        ]),
    ]
