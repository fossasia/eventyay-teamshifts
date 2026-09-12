from django.db import migrations


def migrate_question_options(apps, schema_editor):
    TeamApplicationQuestion = apps.get_model(
        "teamshifts",
        "TeamApplicationQuestion",
    )
    TeamApplicationQuestionOption = apps.get_model(
        "teamshifts",
        "TeamApplicationQuestionOption",
    )

    for question in TeamApplicationQuestion.objects.all():
        if not question.options:
            continue

        for position, answer in enumerate(line.strip() for line in question.options.splitlines()):
            if answer:
                TeamApplicationQuestionOption.objects.create(
                    question=question,
                    answer=answer,
                    position=position,
                )


class Migration(migrations.Migration):
    dependencies = [
        ("teamshifts", "0021_teamapplicationquestionoption"),
    ]

    operations = [
        migrations.RunPython(migrate_question_options, migrations.RunPython.noop),
    ]
