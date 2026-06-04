import importlib


def test_plugin_version():
    import teamshifts

    assert teamshifts.__version__ == "0.1.0"


def test_appconfig_meta():
    try:
        from teamshifts.apps import TeamShiftsApp
    except RuntimeError:
        import pytest

        pytest.skip("eventyay not installed, skipping AppConfig test")

    assert TeamShiftsApp.name == "teamshifts"
    meta = TeamShiftsApp.EventyayPluginMeta
    assert meta.author == "FOSSASIA"
    assert meta.visible is True


def test_plugin_entry_point():
    from importlib.metadata import entry_points

    eps = entry_points(group="pretix.plugin")
    names = [ep.name for ep in eps]
    assert "teamshifts" in names
