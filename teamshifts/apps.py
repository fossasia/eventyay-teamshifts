from django.utils.translation import gettext_lazy as _

from . import __version__

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
        from . import signals  # NOQA
