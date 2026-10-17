from django.db import migrations, models


def switch_existing_events_to_manual(apps, schema_editor):
    CertificateSettings = apps.get_model("teamshifts", "CertificateSettings")
    CertificateSettings.objects.filter(trigger="auto").update(trigger="manual")


class Migration(migrations.Migration):
    dependencies = [
        ("teamshifts", "0025_shift_action_emails"),
    ]

    operations = [
        migrations.AddField(
            model_name="certificatesettings",
            name="require_after_event_end",
            field=models.BooleanField(default=False, verbose_name="Only after the event has ended"),
        ),
        migrations.AlterField(
            model_name="certificatesettings",
            name="trigger",
            field=models.CharField(
                choices=[("auto", "Automatically"), ("manual", "Manually")],
                default="manual",
                max_length=8,
                verbose_name="Certificate generation trigger",
            ),
        ),
        migrations.RunPython(switch_existing_events_to_manual, migrations.RunPython.noop),
    ]
