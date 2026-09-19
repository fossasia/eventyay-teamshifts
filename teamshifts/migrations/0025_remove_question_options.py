from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("teamshifts", "0024_migrate_question_options"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="teamapplicationquestion",
            name="options",
        ),
    ]
