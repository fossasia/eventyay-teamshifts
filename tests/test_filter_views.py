from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils.timezone import now
from django_scopes import scope
from eventyay.base.models import Team

from teamshifts.models import (
    ApplicationStatus,
    Shift,
    ShiftLocation,
    TeamMemberApplication,
    TeamRole,
    TeamShiftsEmailQueue,
    TeamShiftsEmailQueueRecipient,
)


@pytest.fixture
def orga_client(client, event, user, settings):
    settings.SITE_URL = "https://testserver"
    with scope(event=event):
        team = Team.objects.create(
            organizer=event.organizer,
            name="Orga Team",
            can_change_event_settings=True,
            all_events=True,
        )
        team.members.add(user)
    client.force_login(user)
    return client


@pytest.fixture
def test_data(event, django_user_model):
    with scope(event=event):
        role_lead = TeamRole.objects.create(event=event, name="Lead")
        TeamRole.objects.create(event=event, name="Helper")
        loc1 = ShiftLocation.objects.create(event=event, name="Main Stage")

        u1 = django_user_model.objects.create_user(email="alice@test.com", fullname="Alice Wonder")
        u2 = django_user_model.objects.create_user(email="bob@test.com", fullname="Bob Builder")

        app1 = TeamMemberApplication.objects.create(event=event, user=u1, status=ApplicationStatus.ACCEPTED, arrived=True)
        app2 = TeamMemberApplication.objects.create(event=event, user=u2, status=ApplicationStatus.PENDING, arrived=False)

        s1 = Shift.objects.create(
            event=event,
            name="Morning Shift",
            location=loc1,
            start_time=now(),
            end_time=now() + timedelta(hours=2),
        )

        outbox_email = TeamShiftsEmailQueue.objects.create(
            event=event,
            user=u1,
            subject="Outbox Notification",
            message="Content",
        )
        TeamShiftsEmailQueueRecipient.objects.create(queue=outbox_email, email="alice@test.com")

        sent_email = TeamShiftsEmailQueue.objects.create(
            event=event,
            user=u1,
            subject="Sent Newsletter",
            message="Content",
            sent_at=now(),
        )
        TeamShiftsEmailQueueRecipient.objects.create(queue=sent_email, email="bob@test.com")

        return {
            "app1": app1,
            "app2": app2,
            "shift1": s1,
            "outbox": outbox_email,
            "sent": sent_email,
            "role_lead": role_lead,
            "loc1": loc1,
        }


@pytest.mark.django_db
def test_application_list_filter_and_ajax(orga_client, event, test_data):
    url = reverse("plugins:teamshifts:applications", kwargs={"organizer": event.organizer.slug, "event": event.slug})

    resp = orga_client.get(url, {"query": "Alice"})
    assert resp.status_code == 200
    assert "filter_form" in resp.context
    assert b"data-ajax-results-region" in resp.content
    assert b"Alice Wonder" in resp.content
    assert b"Bob Builder" not in resp.content

    ajax_resp = orga_client.get(
        url,
        {"status": ApplicationStatus.PENDING},
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )
    assert ajax_resp.status_code == 200
    assert b"Bob Builder" in ajax_resp.content
    assert b"Alice Wonder" not in ajax_resp.content


@pytest.mark.django_db
def test_members_list_filter_and_ajax(orga_client, event, test_data):
    url = reverse("plugins:teamshifts:members", kwargs={"organizer": event.organizer.slug, "event": event.slug})

    resp = orga_client.get(url, {"arrived": "yes"})
    assert resp.status_code == 200
    assert "filter_form" in resp.context
    assert b"data-ajax-results-region" in resp.content
    assert b"Alice Wonder" in resp.content
    assert b"Bob Builder" not in resp.content

    ajax_resp = orga_client.get(url, {"query": "Alice"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    assert ajax_resp.status_code == 200
    assert b"Alice Wonder" in ajax_resp.content


@pytest.mark.django_db
def test_shifts_list_filter_and_ajax(orga_client, event, test_data):
    url = reverse("plugins:teamshifts:shifts", kwargs={"organizer": event.organizer.slug, "event": event.slug})

    resp = orga_client.get(url, {"query": "Morning"})
    assert resp.status_code == 200
    assert "filter_form" in resp.context
    assert b"data-ajax-results-region" in resp.content
    assert b"Morning Shift" in resp.content

    resp_empty = orga_client.get(url, {"query": "Nonexistent"})
    assert resp_empty.status_code == 200
    assert b"Morning Shift" not in resp_empty.content

    ajax_resp = orga_client.get(url, {"location": test_data["loc1"].pk}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    assert ajax_resp.status_code == 200
    assert b"Morning Shift" in ajax_resp.content


@pytest.mark.django_db
def test_emails_outbox_and_sent_filter_views(orga_client, event, test_data):
    outbox_url = reverse("plugins:teamshifts:email_outbox", kwargs={"organizer": event.organizer.slug, "event": event.slug})
    sent_url = reverse("plugins:teamshifts:email_sent", kwargs={"organizer": event.organizer.slug, "event": event.slug})

    resp_outbox = orga_client.get(outbox_url, {"query": "Notification"})
    assert resp_outbox.status_code == 200
    assert "filter_form" in resp_outbox.context
    assert b"data-ajax-results-region" in resp_outbox.content
    assert b"Outbox Notification" in resp_outbox.content

    resp_sent = orga_client.get(sent_url, {"query": "Newsletter"})
    assert resp_sent.status_code == 200
    assert "filter_form" in resp_sent.context
    assert b"data-ajax-results-region" in resp_sent.content
    assert b"Sent Newsletter" in resp_sent.content


@pytest.mark.django_db
def test_bulk_action_redirect_preserves_next_url(orga_client, event, test_data):
    bulk_url = reverse("plugins:teamshifts:application_bulk_action", kwargs={"organizer": event.organizer.slug, "event": event.slug})
    next_page = reverse("plugins:teamshifts:applications", kwargs={"organizer": event.organizer.slug, "event": event.slug}) + "?status=pending&page=2"

    resp = orga_client.post(
        bulk_url,
        {
            "application_ids": [test_data["app2"].pk],
            "action": "accept",
            "next": next_page,
        },
    )
    assert resp.status_code == 302
    assert resp["Location"] == next_page


@pytest.mark.django_db
def test_bulk_action_redirect_rejects_unsafe_next_url(orga_client, event, test_data):
    bulk_url = reverse("plugins:teamshifts:application_bulk_action", kwargs={"organizer": event.organizer.slug, "event": event.slug})
    fallback = reverse("plugins:teamshifts:applications", kwargs={"organizer": event.organizer.slug, "event": event.slug})

    resp = orga_client.post(
        bulk_url,
        {
            "application_ids": [test_data["app2"].pk],
            "action": "accept",
            "next": "https://malicious.example.com/steal-creds",
        },
    )
    assert resp.status_code == 302
    assert resp["Location"] == fallback


def test_get_safe_next_url_helper(rf):
    from teamshifts.views import get_safe_next_url

    fallback = "/fallback/path/"

    # Empty next returns fallback
    req_empty = rf.get("/current/")
    assert get_safe_next_url(req_empty, fallback) == fallback

    # Relative next URL is allowed
    req_rel = rf.get("/current/?next=/allowed/path/?foo=bar")
    assert get_safe_next_url(req_rel, fallback) == "/allowed/path/?foo=bar"

    # POST next parameter is also read
    req_post = rf.post("/current/", {"next": "/from/post/"})
    assert get_safe_next_url(req_post, fallback) == "/from/post/"

    # External domain is rejected
    req_ext = rf.get("/current/?next=https://evil.com/phish")
    assert get_safe_next_url(req_ext, fallback) == fallback

    # HTTP scheme on an HTTPS request is rejected
    req_https = rf.get("/current/?next=http://testserver/safe/", secure=True)
    assert get_safe_next_url(req_https, fallback) == fallback

    # HTTPS scheme on HTTPS request with allowed host is accepted
    req_https_ok = rf.get("/current/?next=https://testserver/safe/", secure=True)
    assert get_safe_next_url(req_https_ok, fallback) == "https://testserver/safe/"
