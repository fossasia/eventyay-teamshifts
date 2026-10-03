import django.db.models.deletion
from django.db import migrations, models

import teamshifts.models


class Migration(migrations.Migration):
    dependencies = [
        ("base", "0001_initial"),
        ("teamshifts", "0023_shiftlocation_linked_room"),
    ]

    operations = [
        migrations.CreateModel(
            name="ShiftCalendarToken",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "token",
                    models.CharField(
                        default=teamshifts.models.generate_calendar_token,
                        max_length=64,
                        unique=True,
                        verbose_name="Calendar token",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("regenerated_at", models.DateTimeField(blank=True, null=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="shift_calendar_token",
                        to="base.user",
                    ),
                ),
            ],
            options={
                "verbose_name": "Shift calendar token",
                "verbose_name_plural": "Shift calendar tokens",
            },
        ),
    ]
