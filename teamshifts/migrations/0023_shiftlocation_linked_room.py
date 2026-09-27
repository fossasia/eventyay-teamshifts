import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("base", "0001_initial"),
        ("teamshifts", "0022_membercertificate_notified_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="shiftlocation",
            name="linked_room",
            field=models.OneToOneField(
                blank=True,
                help_text="If set, this location mirrors a room from the Talks schedule.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="shift_location",
                to="base.room",
                verbose_name="Linked Talks Room",
            ),
        ),
    ]
