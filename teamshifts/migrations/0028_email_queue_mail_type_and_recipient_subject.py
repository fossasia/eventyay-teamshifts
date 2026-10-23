from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("teamshifts", "0027_teamshiftsemailqueue_is_draft"),
    ]

    operations = [
        migrations.AddField(
            model_name="teamshiftsemailqueue",
            name="mail_type",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AddField(
            model_name="teamshiftsemailqueuerecipient",
            name="subject",
            field=models.TextField(blank=True, default=""),
        ),
    ]
