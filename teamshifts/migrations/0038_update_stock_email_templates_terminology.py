from django.db import migrations
from i18nfield.strings import LazyI18nString

try:
    from django_scopes import scopes_disabled
except ImportError:
    from contextlib import nullcontext as scopes_disabled

STOCK_UPDATES = {
    "teamshifts.application.received": {
        "subject": (
            (
                "We received your application — {event_name}",
                "Team shifts: We received your application — {event_name}",
            ),
            "Team Shifts: We received your application — {event_name}",
        ),
        "body": (
            (
                (
                    "Hi {full_name},\n\n"
                    "Thanks for applying to join the team for {event_name}. "
                    "We have received your application and will get back to you soon.\n\n"
                    "Best regards,\n"
                    "The {event_name} team"
                ),
                (
                    "Hi {full_name},\n\n"
                    "Thanks for applying to join the event team through Team Shifts for {event_name}. "
                    "We have received your application and will get back to you soon.\n\n"
                    "Best regards,\n"
                    "The {event_name} team"
                ),
            ),
            (
                "Hi {full_name},\n\n"
                "Thanks for applying to join the {event_name} event team. "
                "We have received your application and will get back to you soon.\n\n"
                "Best regards,\n"
                "The {event_name} team"
            ),
        ),
    },
    "teamshifts.application.accepted": {
        "subject": (
            (
                "Your application was accepted — {event_name}",
                "Team shifts: Your application was accepted — {event_name}",
            ),
            "Team Shifts: Your application was accepted — {event_name}",
        ),
        "body": (
            (
                (
                    "Hi {full_name},\n\n"
                    "Great news — your application to volunteer at {event_name} has "
                    "been accepted. Welcome to the team!\n\n"
                    "You can now browse the shift schedule and claim your shifts here:\n"
                    "{shift_schedule_url}\n\n"
                    "Best regards,\n"
                    "The {event_name} team"
                ),
                (
                    "Hi {full_name},\n\n"
                    "Great news — your application to join the event team through Team Shifts for {event_name} has "
                    "been accepted. Welcome to the team!\n\n"
                    "You can now browse the shift schedule and claim your shifts here:\n"
                    "{shift_schedule_url}\n\n"
                    "Best regards,\n"
                    "The {event_name} team"
                ),
            ),
            (
                "Hi {full_name},\n\n"
                "Great news — your application to join the {event_name} event team has "
                "been accepted. Welcome to the team!\n\n"
                "You can now browse the shift schedule and claim your shifts here:\n"
                "{shift_schedule_url}\n\n"
                "Best regards,\n"
                "The {event_name} team"
            ),
        ),
    },
    "teamshifts.application.rejected": {
        "subject": (
            (
                "Update on your application — {event_name}",
                "Team shifts: Update on your application — {event_name}",
            ),
            "Team Shifts: Update on your application — {event_name}",
        ),
        "body": (
            (
                (
                    "Hi {full_name},\n\n"
                    "Thank you for your interest in joining the {event_name} team. "
                    "Unfortunately, we are unable to accept your application at this "
                    "time.\n\n"
                    "We appreciate your enthusiasm and hope you enjoy the event.\n\n"
                    "Best regards,\n"
                    "The {event_name} team"
                ),
                (
                    "Hi {full_name},\n\n"
                    "Thank you for your interest in joining the event team through Team Shifts for {event_name}. "
                    "Unfortunately, we are unable to accept your application at this "
                    "time.\n\n"
                    "We appreciate your enthusiasm and hope you enjoy the event.\n\n"
                    "Best regards,\n"
                    "The {event_name} team"
                ),
            ),
            (
                "Hi {full_name},\n\n"
                "Thank you for your interest in joining the {event_name} event team. "
                "Unfortunately, we are unable to accept your application at this "
                "time.\n\n"
                "We appreciate your enthusiasm and hope you enjoy the event.\n\n"
                "Best regards,\n"
                "The {event_name} team"
            ),
        ),
    },
    "teamshifts.application.organizer": {
        "subject": (
            (
                "New team member application — {event_name}",
                "Team shifts: New team member application — {event_name}",
            ),
            "Team Shifts: New team member application — {event_name}",
        ),
        "body": (
            (
                (
                    "A new team member application has been submitted for {event_name}.\n\n"
                    "Applicant: {applicant_name}\n\n"
                    "Submitted: {submitted_at}\n\n"
                    "Pending applications: {pending_count}\n\n"
                    "Review the application here:\n"
                    "{application_url}\n\n"
                    "The {event_name} team"
                ),
                (
                    "A new application has been submitted to join the event team through Team Shifts for {event_name}.\n\n"
                    "Applicant: {applicant_name}\n\n"
                    "Submitted: {submitted_at}\n\n"
                    "Pending applications: {pending_count}\n\n"
                    "Review the application here:\n"
                    "{application_url}\n\n"
                    "The {event_name} team"
                ),
            ),
            (
                "A new application has been submitted to join the {event_name} event team.\n\n"
                "Applicant: {applicant_name}\n\n"
                "Submitted: {submitted_at}\n\n"
                "Pending applications: {pending_count}\n\n"
                "Review the application here:\n"
                "{application_url}\n\n"
                "The {event_name} team"
            ),
        ),
    },
    "teamshifts.member.added_by_organizer": {
        "subject": (
            (
                "You have been added as a volunteer — {event_name}",
                "Team shifts: You have been added as a volunteer — {event_name}",
            ),
            "Team Shifts: You have been added to the team — {event_name}",
        ),
        "body": (
            (
                (
                    "Hi {full_name},\n\n"
                    "You have been added as a volunteer for {event_name} by the event organiser.\n\n"
                    "Event dates: {event_dates}\n"
                    "Event location: {event_location}\n\n"
                    "You can now claim your shifts here:\n"
                    "{shift_schedule_url}\n\n"
                    "If you have any questions, contact the organiser directly.\n\n"
                    "See you at the event!\n"
                    "The {event_name} team"
                ),
                (
                    "Hi {full_name},\n\n"
                    "You have been added to the event team through Team Shifts for {event_name} by the event organiser.\n\n"
                    "Event dates: {event_dates}\n"
                    "Event location: {event_location}\n\n"
                    "You can now claim your shifts here:\n"
                    "{shift_schedule_url}\n\n"
                    "If you have any questions, contact the organiser directly.\n\n"
                    "See you at the event!\n"
                    "The {event_name} team"
                ),
            ),
            (
                "Hi {full_name},\n\n"
                "You have been added to the {event_name} event team by the event organizer.\n\n"
                "Event dates: {event_dates}\n"
                "Event location: {event_location}\n\n"
                "You can now claim your shifts here:\n"
                "{shift_schedule_url}\n\n"
                "If you have any questions, contact the organiser directly.\n\n"
                "See you at the event!\n"
                "The {event_name} team"
            ),
        ),
    },
    "teamshifts.member.removed": {
        "subject": (
            (
                "Update on your participation — {event_name}",
                "Team shifts: Update on your participation — {event_name}",
            ),
            "Team Shifts: Update on your participation — {event_name}",
        ),
        "body": (
            (
                (
                    "Hi {full_name},\n\n"
                    "We wanted to let you know that you are no longer part of the {event_name} team, "
                    "and you have been removed from any upcoming shifts.\n\n"
                    "If you think this is a mistake or have any questions, please reply to this email.\n\n"
                    "Best regards,\n"
                    "The {event_name} team"
                ),
                (
                    "Hi {full_name},\n\n"
                    "We wanted to let you know that you are no longer part of the event team through Team Shifts for {event_name}, "
                    "and you have been removed from any upcoming shifts.\n\n"
                    "If you think this is a mistake or have any questions, please reply to this email.\n\n"
                    "Best regards,\n"
                    "The {event_name} team"
                ),
            ),
            (
                "Hi {full_name},\n\n"
                "We wanted to let you know that you are no longer part of the {event_name} event team, "
                "and you have been removed from any upcoming shifts.\n\n"
                "If you think this is a mistake or have any questions, please reply to this email.\n\n"
                "Best regards,\n"
                "The {event_name} team"
            ),
        ),
    },
    "teamshifts.voucher.sent": {
        "subject": (
            (
                "Your ticket voucher — {event_name}",
                "Team shifts: Your ticket voucher — {event_name}",
            ),
            "Team Shifts: Your ticket voucher — {event_name}",
        ),
        "body": None,
    },
    "teamshifts.certificate.generated": {
        "subject": (
            (
                "Your certificate of participation — {event_name}",
                "Team shifts: Your certificate of participation — {event_name}",
            ),
            "Team Shifts: Your certificate of participation — {event_name}",
        ),
        "body": None,
    },
    "teamshifts.shift.assigned_by_organizer": {
        "subject": (
            (
                "You have been assigned to a shift — {event_name}",
                "Team shifts: You have been assigned to a shift — {event_name}",
            ),
            "Team Shifts: You have been assigned to a shift — {event_name}",
        ),
        "body": None,
    },
    "teamshifts.shift.unassigned_by_organizer": {
        "subject": (
            (
                "You have been removed from a shift — {event_name}",
                "Team shifts: You have been removed from a shift — {event_name}",
            ),
            "Team Shifts: You have been removed from a shift — {event_name}",
        ),
        "body": None,
    },
    "teamshifts.shift.claimed_by_volunteer": {
        "subject": (
            (
                "Shift sign-up confirmation — {event_name}",
                "Team shifts: Shift sign-up confirmation — {event_name}",
            ),
            "Team Shifts: Shift sign-up confirmation — {event_name}",
        ),
        "body": None,
    },
    "teamshifts.shift.dropped_by_volunteer": {
        "subject": (
            (
                "Shift drop confirmation — {event_name}",
                "Team shifts: Shift drop confirmation — {event_name}",
            ),
            "Team Shifts: Shift drop confirmation — {event_name}",
        ),
        "body": None,
    },
    "teamshifts.shift.dropped_organizer": {
        "subject": (
            (
                "A team member dropped a shift — {event_name}",
                "Team shifts: A team member dropped a shift — {event_name}",
            ),
            "Team Shifts: A team member dropped a shift — {event_name}",
        ),
        "body": None,
    },
    "teamshifts.shift.summary": {
        "subject": (
            (
                "Your shifts for {event_name}",
                "Team shifts: Your shifts for {event_name}",
            ),
            "Team Shifts: Your shifts for {event_name}",
        ),
        "body": None,
    },
    "teamshifts.shift.reminder": {
        "subject": (
            (
                "Reminder: your shift overview for {event_name}",
                "Team shifts: Reminder: your shift overview for {event_name}",
            ),
            "Team Shifts: Your shift overview for {event_name}",
        ),
        "body": None,
    },
}


def _update_i18n_field(val, from_text, to_text, is_body=False):
    if not val or not from_text or not to_text:
        return val, False

    from_texts = [from_text] if isinstance(from_text, str) else list(from_text)

    def matches(candidate):
        if not isinstance(candidate, str):
            return False
        if is_body:
            c = candidate.replace("\r\n", "\n")
            return any(c == ft.replace("\r\n", "\n") for ft in from_texts)
        return any(candidate == ft for ft in from_texts)

    if isinstance(val, LazyI18nString):
        data = val.data
        if isinstance(data, dict):
            new_data = dict(data)
            changed = False
            for loc, text in data.items():
                if matches(text):
                    new_data[loc] = to_text
                    changed = True
            return (LazyI18nString(new_data), True) if changed else (val, False)
        elif isinstance(data, str):
            if matches(data):
                return LazyI18nString(to_text), True
            return val, False
    elif isinstance(val, dict):
        new_dict = dict(val)
        changed = False
        for loc, text in val.items():
            if matches(text):
                new_dict[loc] = to_text
                changed = True
        return (new_dict, True) if changed else (val, False)
    elif isinstance(val, str):
        if matches(val):
            return to_text, True
        return val, False

    return val, False


def _apply_updates(apps, reverse=False):
    TeamShiftsEmailTemplate = apps.get_model("teamshifts", "TeamShiftsEmailTemplate")
    with scopes_disabled():
        for template in TeamShiftsEmailTemplate.objects.all():
            config = STOCK_UPDATES.get(template.role)
            if not config:
                continue

            fields_to_update = []
            if config.get("subject"):
                old_sub, new_sub = config["subject"]
                canonical_old_sub = old_sub[0] if isinstance(old_sub, (tuple, list)) else old_sub
                from_sub, to_sub = (new_sub, canonical_old_sub) if reverse else (old_sub, new_sub)
                updated_sub, changed = _update_i18n_field(template.subject, from_sub, to_sub, is_body=False)
                if changed:
                    template.subject = updated_sub
                    fields_to_update.append("subject")

            if config.get("body"):
                old_body, new_body = config["body"]
                canonical_old_body = old_body[0] if isinstance(old_body, (tuple, list)) else old_body
                from_body, to_body = (new_body, canonical_old_body) if reverse else (old_body, new_body)
                updated_body, changed = _update_i18n_field(template.body, from_body, to_body, is_body=True)
                if changed:
                    template.body = updated_body
                    fields_to_update.append("body")

            if fields_to_update:
                template.save(update_fields=fields_to_update)


def migrate_stock_templates(apps, schema_editor):
    _apply_updates(apps, reverse=False)


def reverse_migrate_stock_templates(apps, schema_editor):
    _apply_updates(apps, reverse=True)


class Migration(migrations.Migration):
    dependencies = [
        ("teamshifts", "0037_teamshiftsemailqueue_recipient_group"),
    ]

    operations = [
        migrations.RunPython(migrate_stock_templates, reverse_migrate_stock_templates),
    ]

    operations = [
        migrations.RunPython(migrate_stock_templates, reverse_migrate_stock_templates),
    ]
