from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("teamshifts", "0035_shift_summary_email"),
    ]

    operations = [
        migrations.AddField(
            model_name="callforteammembers",
            name="self_signup_closes_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text=(
                    "After this time, team members can no longer sign up for shifts themselves. "
                    "Organizers can still assign people. Leave empty to keep sign-up open."
                ),
                verbose_name="Self sign-up closes at",
            ),
        ),
    ]
