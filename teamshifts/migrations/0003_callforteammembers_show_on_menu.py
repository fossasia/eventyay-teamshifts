from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("teamshifts", "0002_teammemberapplication_phone_attendance"),
    ]

    operations = [
        migrations.AddField(
            model_name="callforteammembers",
            name="show_on_menu",
            field=models.BooleanField(
                default=True,
                verbose_name="Show in public navigation",
                help_text=("Show a link to the application form in the public event navigation. Only visible when Active is enabled."),
            ),
        ),
    ]
