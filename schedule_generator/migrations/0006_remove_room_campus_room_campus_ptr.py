"""Переименование поля Room.campus -> Room.campus_ptr (та же колонка campus_id).

`RenameField` меняет только имя столбца, которого здесь и так нет: поле
изначально объявлено с db_column='campus_id', поэтому на уровне схемы БД
миграция не выполняет ничего, а в состоянии миграций атрибут модели
переименовывается. Атрибут `campus` теперь — свойство модели, возвращающее
кампус аудитории: через корпус (building.campus) либо прямую привязку
(campus_ptr), если деления на корпуса нет.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('schedule_generator', '0005_alter_group_admission_year_alter_group_name'),
    ]

    operations = [
        migrations.RenameField(
            model_name='room',
            old_name='campus',
            new_name='campus_ptr',
        ),
        migrations.AlterField(
            model_name='room',
            name='campus_ptr',
            field=models.ForeignKey(
                blank=True,
                db_column='campus_id',
                help_text='Указывается напрямую, если у кампуса нет деления на корпуса. '
                          'Свойство Room.campus возвращает кампус (через корпус или напрямую).',
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='rooms',
                to='schedule_generator.campus',
                verbose_name='Кампус',
            ),
        ),
    ]
