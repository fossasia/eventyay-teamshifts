# eventyay-teamshifts

A plugin for [eventyay](https://github.com/fossasia/eventyay) that will provide volunteer and shift management for events. This initial package bootstraps plugin registration; future phases will add Calls for Volunteers, role management, application review, shift scheduling, and volunteer assignment within the eventyay control panel.

## Planned Features

- Call for Volunteers configuration per event (open/close, description)
- Volunteer role management (name, capacity, description)
- Public volunteer sign-up form at `/event/{slug}/volunteers/`
- Organiser application review panel (accept / reject)
- Shift schedule builder with a Vue 3 grid editor
- Volunteer assignment with capacity enforcement
- Moderator delegation for per-shift oversight
- Email notifications via Celery (assignment confirmations, reminders)
- 24-hour shift reminder via Celery Beat
- Volunteer personal calendar view
- iCal export for assigned shifts (stretch goal)
- Public shift board (stretch goal)

## Requirements

- eventyay (latest)
- Python 3.12+
- Redis (for Celery)

## Development Setup

1. Make sure you have a working [eventyay development setup](https://github.com/fossasia/eventyay?tab=readme-ov-file#getting-started).

2. Clone this repository:
   ```bash
   git clone https://github.com/fossasia/eventyay-teamshifts
   ```

3. Activate the virtual environment you use for eventyay development.

4. Install the plugin in editable mode:
   ```bash
   uv pip install -e .
   ```

5. Run migrations:
   ```bash
   python manage.py migrate
   ```

6. Restart your local eventyay server. Enable the plugin from the **Plugins** tab in your event settings.

## Code Style

This plugin enforces code style via `black`, `isort`, and `flake8`. To check locally:

```bash
black --check .
isort -c .
flake8 .
```

To auto-fix:

```bash
isort .
black .
```

To enforce checks automatically before each commit:

```bash
./.install-hooks.sh
```

## Running Tests

```bash
pytest tests/
```

## Planned Project Structure

```
teamshifts/
  apps.py           Plugin AppConfig and EventyayPluginMeta
  models.py         CallForVolunteers, VolunteerRole, VolunteerApplication, Shift, ShiftAssignment
  views.py          Organiser control panel views
  urls.py           URL routing under /control/event/<org>/<event>/teamshifts/
  signals.py        Sidebar nav registration and log entry display
  tasks.py          Celery tasks for email notifications
  forms.py          Django forms for CFV settings, roles, sign-up
  serializers.py    DRF serializers for shift editor API
  api.py            REST API endpoints (shifts, assignments)
  templates/        Django HTML templates
  static/           Vue 3 shift editor (Vite build output)
  migrations/       Database migrations
```

