from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("base", "0001_initial"),
        ("teamshifts", "0019_member_added_by_organizer"),
    ]

    operations = [
        migrations.CreateModel(
            name="VolunteerVoucherSettings",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "enabled",
                    models.BooleanField(
                        default=False,
                        help_text="Allow sending ticket vouchers to accepted team members.",
                        verbose_name="Enable volunteer vouchers",
                    ),
                ),
                (
                    "voucher_tag",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="The tag that identifies the voucher batch in Tickets → Vouchers.",
                        max_length=255,
                        verbose_name="Voucher batch (tag)",
                    ),
                ),
                (
                    "event",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="volunteer_voucher_settings",
                        to="base.event",
                    ),
                ),
            ],
            options={
                "verbose_name": "Volunteer voucher settings",
                "verbose_name_plural": "Volunteer voucher settings",
            },
        ),
        migrations.CreateModel(
            name="MemberVoucher",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("not_sent", "Not sent"),
                            ("sent", "Sent — not claimed"),
                            ("claimed", "Claimed"),
                        ],
                        default="not_sent",
                        max_length=20,
                        verbose_name="Voucher status",
                    ),
                ),
                (
                    "sent_at",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                        verbose_name="Sent at",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True),
                ),
                (
                    "application",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="voucher_assignment",
                        to="teamshifts.teammemberapplication",
                    ),
                ),
                (
                    "voucher",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="teamshifts_member_link",
                        to="base.voucher",
                    ),
                ),
            ],
            options={
                "verbose_name": "Member voucher",
                "verbose_name_plural": "Member vouchers",
            },
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
                    ("teamshifts.voucher.sent", "Voucher sent to volunteer"),
                ],
                max_length=40,
            ),
        ),
    ]
