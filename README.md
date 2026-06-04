# eventyay-teamshifts

A plugin for [eventyay](https://github.com/fossasia/eventyay) that will provide team coordination and shift management for events. This initial package bootstraps plugin registration; future phases will add team role management, application review, shift scheduling, and team member assignment within eventyay.

## Planned Features

- Team role configuration per event (open/close, description, capacity)
- Team member role management (name, capacity, description)
- Public team member sign-up form at `/teamshifts/event/<org>/<event>/apply/`
- Organiser application review panel (accept / reject)
- Shift schedule builder with a Vue 3 grid editor
- Team member assignment with capacity enforcement
- Moderator delegation for per-shift oversight
- Email notifications via Celery (assignment confirmations, reminders)
- 24-hour shift reminder via Celery Beat
- Personal calendar view
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

This plugin enforces code style via `ruff` (import sorting + formatting) and `flake8`. CI runs these checks automatically on every PR.

To check locally:

```bash
ruff check --select I .
ruff format --check .
flake8 .
```

To auto-fix:

```bash
ruff check --select I --fix .
ruff format .
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
  models.py         CallForEventTeam, EventStaff, Shift, ShiftAssignment
  views.py          Organiser control panel views
  urls.py           URL routing under /teamshifts/event/<org>/<event>/
  signals.py        Sidebar nav registration and dashboard widgets
  tasks.py          Celery tasks for email notifications
  forms.py          Django forms for team settings, roles, sign-up
  serializers.py    DRF serializers for shift editor API
  api.py            REST API endpoints (shifts, assignments)
  templates/        Django HTML templates
  static/           Vue 3 shift editor (Vite build output)
  migrations/       Database migrations
```
