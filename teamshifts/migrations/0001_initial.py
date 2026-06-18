import django.db.models.deletion
import i18nfield.fields
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("base", "0030_room_is_unscheduled_team_polls_questions"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CallForTeamMembers",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("active", models.BooleanField(default=False, verbose_name="Active")),
                (
                    "deadline",
                    models.DateTimeField(blank=True, null=True, verbose_name="Deadline"),
                ),
                (
                    "title",
                    models.CharField(
                        default="Call for Team Members",
                        help_text=("Displayed to applicants. Can be customised, e.g. 'Call for Volunteers' or 'Call for Staff'."),
                        max_length=200,
                        verbose_name="Title",
                    ),
                ),
                (
                    "description",
                    i18nfield.fields.I18nTextField(blank=True, null=True, verbose_name="Description"),
                ),
                (
                    "event",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="call_for_team_members",
                        to="base.event",
                    ),
                ),
            ],
            options={
                "verbose_name": "Call for Team Members",
                "verbose_name_plural": "Calls for Team Members",
            },
        ),
        migrations.CreateModel(
            name="TeamRole",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "name",
                    models.CharField(max_length=190, verbose_name="Role Name"),
                ),
                (
                    "description",
                    models.TextField(blank=True, verbose_name="Description"),
                ),
                (
                    "event",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="team_roles",
                        to="base.event",
                    ),
                ),
            ],
            options={
                "verbose_name": "Team Role",
                "verbose_name_plural": "Team Roles",
                "unique_together": {("event", "name")},
            },
        ),
        migrations.CreateModel(
            name="Shift",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "name",
                    models.CharField(blank=True, max_length=190, verbose_name="Shift Name"),
                ),
                (
                    "location",
                    models.CharField(blank=True, max_length=190, verbose_name="Location"),
                ),
                (
                    "start_time",
                    models.DateTimeField(verbose_name="Start Time"),
                ),
                (
                    "end_time",
                    models.DateTimeField(verbose_name="End Time"),
                ),
                (
                    "capacity",
                    models.PositiveIntegerField(default=1, verbose_name="Capacity"),
                ),
                (
                    "description",
                    models.TextField(blank=True, verbose_name="Description"),
                ),
                (
                    "event",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="shifts",
                        to="base.event",
                    ),
                ),
                (
                    "role",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="shifts",
                        to="teamshifts.teamrole",
                    ),
                ),
            ],
            options={
                "verbose_name": "Shift",
                "verbose_name_plural": "Shifts",
                "ordering": ["start_time"],
            },
        ),
        migrations.CreateModel(
            name="TeamMemberApplication",
            fields=[
                (
                    "id",
                    models.BigAutoField(
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
                            ("pending", "Pending"),
                            ("accepted", "Accepted"),
                            ("rejected", "Rejected"),
                        ],
                        default="pending",
                        max_length=20,
                        verbose_name="Status",
                    ),
                ),
                (
                    "availability_notes",
                    models.TextField(
                        blank=True,
                        help_text="Applicant's notes on their availability for shifts.",
                        verbose_name="Availability Notes",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Applied At"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Updated At"),
                ),
                (
                    "event",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="team_member_applications",
                        to="base.event",
                    ),
                ),
                (
                    "role",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="applications",
                        to="teamshifts.teamrole",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="team_member_applications",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Team Member Application",
                "verbose_name_plural": "Team Member Applications",
                "unique_together": {("event", "user", "role")},
            },
        ),
        migrations.CreateModel(
            name="ShiftAssignment",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "assigned_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Assigned At"),
                ),
                (
                    "is_moderator",
                    models.BooleanField(default=False, verbose_name="Is Moderator"),
                ),
                (
                    "notified",
                    models.BooleanField(default=False, verbose_name="Notified"),
                ),
                (
                    "assigned_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="assignments_made",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "shift",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="assignments",
                        to="teamshifts.shift",
                    ),
                ),
                (
                    "team_member",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="shift_assignments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Shift Assignment",
                "verbose_name_plural": "Shift Assignments",
                "unique_together": {("shift", "team_member")},
            },
        ),
        migrations.CreateModel(
            name="TeamApplicationQuestion",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "question",
                    i18nfield.fields.I18nTextField(verbose_name="Question"),
                ),
                (
                    "help_text",
                    i18nfield.fields.I18nTextField(blank=True, null=True, verbose_name="Help text"),
                ),
                (
                    "variant",
                    models.CharField(
                        choices=[
                            ("string", "Text (one-line)"),
                            ("text", "Multi-line text"),
                            ("number", "Number"),
                            ("boolean", "Confirmation (yes/no)"),
                            ("date", "Date"),
                            ("choices", "Choose one option"),
                            ("multiple_choice", "Choose one or more options"),
                        ],
                        default="string",
                        max_length=20,
                        verbose_name="Field type",
                    ),
                ),
                (
                    "required",
                    models.BooleanField(default=False, verbose_name="Required"),
                ),
                (
                    "position",
                    models.PositiveIntegerField(default=0, verbose_name="Position"),
                ),
                (
                    "options",
                    models.TextField(
                        blank=True,
                        help_text="One option per line. Only used for choice / multiple choice fields.",
                        verbose_name="Options",
                    ),
                ),
                (
                    "active",
                    models.BooleanField(default=True, verbose_name="Active"),
                ),
                (
                    "event",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="team_application_questions",
                        to="base.event",
                    ),
                ),
                (
                    "role",
                    models.ForeignKey(
                        blank=True,
                        help_text="Leave blank to ask this question for every role.",
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="application_questions",
                        to="teamshifts.teamrole",
                    ),
                ),
            ],
            options={
                "verbose_name": "Application Question",
                "verbose_name_plural": "Application Questions",
                "ordering": ["position", "pk"],
            },
        ),
        migrations.CreateModel(
            name="TeamApplicationAnswer",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "answer",
                    models.TextField(blank=True, verbose_name="Answer"),
                ),
                (
                    "application",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="answers",
                        to="teamshifts.teammemberapplication",
                    ),
                ),
                (
                    "question",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="answers",
                        to="teamshifts.teamapplicationquestion",
                    ),
                ),
            ],
            options={
                "verbose_name": "Application Answer",
                "verbose_name_plural": "Application Answers",
                "unique_together": {("application", "question")},
            },
        ),
    ]
