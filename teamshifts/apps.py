import logging

from django.db.models.signals import post_migrate
from django.utils.translation import gettext_lazy as _

from . import __version__

logger = logging.getLogger(__name__)

try:
    from eventyay.base.plugins import PluginConfig
except ImportError as e:
    raise RuntimeError("Please use a later version of eventyay") from e


class TeamShiftsApp(PluginConfig):
    default = True
    name = "teamshifts"
    verbose_name = _("Team Shifts")

    class EventyayPluginMeta:
        name = _("Team Shifts")
        author = "FOSSASIA"
        description = _("Team and shift management plugin for eventyay")
        visible = True
        version = __version__
        category = "FEATURE"

    def ready(self):
        from . import signals, tasks  # noqa: F401 — registers signal receivers and celery tasks

        post_migrate.connect(self._ensure_beat_schedule, sender=self)

    @staticmethod
    def _ensure_beat_schedule(sender, **kwargs):
        from django_celery_beat.models import IntervalSchedule, PeriodicTask

        schedules = (
            ("teamshifts-dispatch-scheduled-emails", "teamshifts.dispatch_scheduled_emails", 60),
            ("teamshifts-send-shift-reminders", "teamshifts.send_shift_reminders", 900),
        )
        for name, task, every in schedules:
            try:
                schedule, _ = IntervalSchedule.objects.get_or_create(every=every, period=IntervalSchedule.SECONDS)
                PeriodicTask.objects.update_or_create(
                    name=name,
                    defaults={
                        "task": task,
                        "interval": schedule,
                        "enabled": True,
                    },
                )
            except Exception:
                logger.exception("[TeamShifts] Failed to register beat schedule for %s", task)
