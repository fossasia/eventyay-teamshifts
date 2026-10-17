from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("teamshifts", "0025_shift_action_emails"),
    ]

    operations = [
        migrations.AddField(
            model_name="teamshiftsemailqueue",
            name="is_draft",
            field=models.BooleanField(
                default=False,
                help_text="Drafts are kept out of the outbox and are never sent until they are moved there.",
                verbose_name="Draft",
            ),
        ),
    ]
