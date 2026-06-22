from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("teamshifts", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="teammemberapplication",
            name="phone",
            field=models.CharField(
                blank=True,
                help_text="Optional contact number for shift coordination.",
                max_length=50,
                verbose_name="Phone / Mobile",
            ),
        ),
        migrations.AddField(
            model_name="teammemberapplication",
            name="attendance_confirmed",
            field=models.BooleanField(
                blank=True,
                null=True,
                verbose_name="Attendance confirmed",
                help_text=("Whether the team member has confirmed they will attend. None = not yet responded, True = confirmed, False = declined."),
            ),
        ),
    ]
