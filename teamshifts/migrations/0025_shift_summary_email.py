from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("teamshifts", "0024_shift_notification_queue_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="teamshiftsemailqueue",
            name="is_shift_summary",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="teamshiftsemailtemplate",
            name="role",
            field=models.CharField(
                choices=[
                    ("teamshifts.application.received", "Application received"),
                    ("teamshifts.application.accepted", "Application accepted"),
                    ("teamshifts.application.rejected", "Application rejected"),
                    ("teamshifts.member.added_by_organizer", "Added as volunteer by organizer"),
                    ("teamshifts.shift.assigned_by_organizer", "Shift assigned by organizer"),
                    ("teamshifts.shift.claimed_by_volunteer", "Shift sign-up confirmation"),
                    ("teamshifts.shift.summary", "Shift summary"),
                    ("teamshifts.voucher.sent", "Voucher sent to volunteer"),
                    ("teamshifts.certificate.generated", "Certificate of participation generated"),
                ],
                max_length=40,
            ),
        ),
    ]
