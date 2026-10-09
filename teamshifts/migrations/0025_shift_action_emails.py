from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("teamshifts", "0024_shift_notification_queue_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="teammemberapplication",
            name="shift_action_emails",
            field=models.BooleanField(
                default=True,
                help_text="Whether the member gets an email when they sign up for or drop a shift on the shift schedule.",
                verbose_name="Email on shift sign-up and drop",
            ),
        ),
        migrations.AlterField(
            model_name="teamshiftsemailtemplate",
            name="role",
            field=models.CharField(
                choices=[
                    ("teamshifts.application.received", "Application received"),
                    ("teamshifts.application.accepted", "Application accepted"),
                    ("teamshifts.application.rejected", "Application rejected"),
                    ("teamshifts.application.organizer", "New application (organizer notification)"),
                    ("teamshifts.member.added_by_organizer", "Added as volunteer by organizer"),
                    ("teamshifts.shift.assigned_by_organizer", "Shift assigned by organizer"),
                    ("teamshifts.shift.claimed_by_volunteer", "Shift sign-up confirmation"),
                    ("teamshifts.shift.dropped_by_volunteer", "Shift drop confirmation"),
                    ("teamshifts.shift.dropped_organizer", "Shift dropped (organizer notification)"),
                    ("teamshifts.voucher.sent", "Voucher sent to volunteer"),
                    ("teamshifts.certificate.generated", "Certificate of participation generated"),
                ],
                max_length=40,
            ),
        ),
    ]
