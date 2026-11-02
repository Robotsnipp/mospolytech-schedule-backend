import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("schedule_generator", "0014_pe_real_slots")]

    operations = [
        migrations.AddField(
            model_name="weekpattern", name="bell_schedule",
            field=models.JSONField(blank=True, default=dict, verbose_name="Источник сетки звонков")),
        migrations.AlterField(
            model_name="scheduledclass", name="slot",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                    related_name="classes", to="schedule_generator.timeslot",
                                    verbose_name="Слот")),
    ]
