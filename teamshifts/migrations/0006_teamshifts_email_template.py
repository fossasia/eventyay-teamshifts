import django.db.models.deletion
import i18nfield.fields
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("base", "0033_alter_event_private_testmode_default"),
        ("teamshifts", "0005_teamshifts_email_queue"),
    ]

    operations = [
        migrations.CreateModel(
            name="TeamShiftsEmailTemplate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "role",
                    models.CharField(
                        choices=[
                            ("application.received", "Application received"),
                            ("application.accepted", "Application accepted"),
                            ("application.rejected", "Application rejected"),
                        ],
                        max_length=40,
                    ),
                ),
                ("subject", i18nfield.fields.I18nTextField()),
                ("body", i18nfield.fields.I18nTextField()),
                ("updated", models.DateTimeField(auto_now=True)),
                (
                    "event",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="teamshifts_email_templates",
                        to="base.event",
                    ),
                ),
            ],
            options={
                "verbose_name": "Email template",
                "verbose_name_plural": "Email templates",
                "ordering": ["role"],
                "unique_together": {("event", "role")},
            },
        ),
    ]
