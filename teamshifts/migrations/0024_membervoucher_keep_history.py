import django.db.models.deletion
from django.db import migrations, models
from django.db.models import OuterRef, Subquery


def backfill_event(apps, schema_editor):
    MemberVoucher = apps.get_model("teamshifts", "MemberVoucher")
    TeamMemberApplication = apps.get_model("teamshifts", "TeamMemberApplication")
    MemberVoucher.objects.update(
        event_id=Subquery(TeamMemberApplication.objects.filter(pk=OuterRef("application_id")).values("event_id")[:1]),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("base", "0001_initial"),
        ("teamshifts", "0023_shiftlocation_linked_room"),
    ]

    operations = [
        migrations.AddField(
            model_name="membervoucher",
            name="event",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="teamshifts_member_vouchers",
                to="base.event",
            ),
        ),
        migrations.AlterField(
            model_name="membervoucher",
            name="application",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="voucher_assignment",
                to="teamshifts.teammemberapplication",
            ),
        ),
        migrations.RunPython(backfill_event, migrations.RunPython.noop),
        migrations.RunSQL("SET CONSTRAINTS ALL IMMEDIATE", migrations.RunSQL.noop),
        migrations.AlterField(
            model_name="membervoucher",
            name="event",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="teamshifts_member_vouchers",
                to="base.event",
            ),
        ),
    ]
