from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("teamshifts", "0003_callforteammembers_show_on_menu"),
    ]

    operations = [
        migrations.AddField(
            model_name="callforteammembers",
            name="ask_full_name",
            field=models.CharField(
                choices=[("do_not_ask", "Do not ask"), ("optional", "Optional"), ("required", "Required")],
                default="optional",
                max_length=20,
                verbose_name="Full name",
            ),
        ),
        migrations.AddField(
            model_name="callforteammembers",
            name="ask_email",
            field=models.CharField(
                choices=[("do_not_ask", "Do not ask"), ("optional", "Optional"), ("required", "Required")],
                default="required",
                max_length=20,
                verbose_name="Email address",
            ),
        ),
        migrations.AddField(
            model_name="callforteammembers",
            name="ask_phone",
            field=models.CharField(
                choices=[("do_not_ask", "Do not ask"), ("optional", "Optional"), ("required", "Required")],
                default="optional",
                max_length=20,
                verbose_name="Phone / Mobile",
            ),
        ),
        migrations.AddField(
            model_name="callforteammembers",
            name="ask_role",
            field=models.CharField(
                choices=[("do_not_ask", "Do not ask"), ("optional", "Optional"), ("required", "Required")],
                default="required",
                max_length=20,
                verbose_name="Role",
            ),
        ),
        migrations.AddField(
            model_name="callforteammembers",
            name="ask_availability",
            field=models.CharField(
                choices=[("do_not_ask", "Do not ask"), ("optional", "Optional"), ("required", "Required")],
                default="optional",
                max_length=20,
                verbose_name="Availability notes",
            ),
        ),
        migrations.AddField(
            model_name="callforteammembers",
            name="field_order",
            field=models.JSONField(default=list, verbose_name="Field order"),
        ),
    ]
