from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("teamshifts", "0006_teamshifts_email_template"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="teamapplicationquestion",
            options={
                "ordering": ["pk"],
                "verbose_name": "Application Question",
                "verbose_name_plural": "Application Questions",
            },
        ),
        migrations.RemoveField(
            model_name="teamapplicationquestion",
            name="position",
        ),
    ]
