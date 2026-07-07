import django.db.models.deletion
import i18nfield.fields
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("base", "0033_alter_event_private_testmode_default"),
        ("teamshifts", "0005_remove_teamapplicationquestion_position"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="TeamShiftsEmailQueue",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("subject", i18nfield.fields.I18nTextField()),
                ("message", i18nfield.fields.I18nTextField()),
                ("reply_to", models.CharField(blank=True, default="", max_length=200)),
                ("bcc", models.TextField(blank=True, default="")),
                ("locale", models.CharField(blank=True, default="", max_length=16)),
                (
                    "status_filter",
                    models.CharField(
                        blank=True,
                        choices=[("pending", "Pending"), ("accepted", "Accepted"), ("rejected", "Rejected")],
                        default="",
                        max_length=20,
                    ),
                ),
                ("send_after", models.DateTimeField(blank=True, null=True)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("updated", models.DateTimeField(auto_now=True)),
                (
                    "event",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="teamshifts_email_queue",
                        to="base.event",
                    ),
                ),
                (
                    "role_filter",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="teamshifts.teamrole",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Queued email",
                "verbose_name_plural": "Queued emails",
                "ordering": ["-created"],
            },
        ),
        migrations.CreateModel(
            name="TeamShiftsEmailQueueRecipient",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("email", models.EmailField(max_length=254)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("error", models.TextField(blank=True, default="")),
                (
                    "queue",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="recipients",
                        to="teamshifts.teamshiftsemailqueue",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Email recipient",
                "verbose_name_plural": "Email recipients",
                "unique_together": {("queue", "email")},
            },
        ),
    ]
