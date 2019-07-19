# pylint: disable=no-name-in-module
from PyQt5.QtCore import QSettings
# pylint: enable=no-name-in-module

DEFAULTS = {
    "ran": False,
    "threshold": 18,
    "api_key": "",
    "dark_theme": 0,
    "rainbow_accent": 0,
    "caching": 0,
    "cache_dir": ".",
    "log_save": 0,
    "log_dir": "./logs/",
    "log_mode": 3,
    "log_output": 0,
    "local_replay_dir": "./examples/replays/",
    # string settings
    "message_loading_replays": "[{ts:%X}] Loading {num_replays} Replays",
    "message_starting_comparing": "[{ts:%X}] Comparing Replays",
    "message_finished_comparing": "[{ts:%X}] Done",
    "message_cheater_found": "[{ts:%X}] {similarity:.1f} similarity. {replay1_name} vs {replay2_name}, {later_name} set later",
    "string_result_text": "[{ts:%x} {ts:%H}:{ts:%M}] {similarity:.1f} similarity. {replay1_name} vs {replay2_name}"
}


def reset_defaults():
    SETTINGS.clear()
    for key, value in DEFAULTS.items():
        SETTINGS.setValue(key, value)
    SETTINGS.sync()


SETTINGS = QSettings("Circleguard", "Circleguard")
if not SETTINGS.contains("ran"):
    reset_defaults()


def get_setting(name):
    return SETTINGS.value(name)


def update_default(name, value):
    SETTINGS.setValue(name, value)


# add setting if missing (occurs between updates if we add a new default setting)
for key, value in DEFAULTS.items():
    if not SETTINGS.contains(key):
        SETTINGS.setValue(key, value)
