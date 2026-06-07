import app.config


def test_settings_has_hh_fields():
    assert hasattr(app.config.Settings, "HH_CLIENT_ID")
    assert hasattr(app.config.Settings, "HH_CLIENT_SECRET")
    assert hasattr(app.config.Settings, "HH_USER_AGENT")
    assert "WEI-Group-Vacancy-Analysis" in app.config.Settings.HH_USER_AGENT
