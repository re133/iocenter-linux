#!/usr/bin/env python3
"""Schreibbare XDG-Pfade und verlustfreie Migration alter Projektpfade."""

import os
import shutil

import bqconfig


PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIRNAME = "iocenter-linux"


def _xdg_home(variable, fallback):
    value = os.environ.get(variable, "").strip()
    return os.path.abspath(os.path.expanduser(value or fallback))


CONFIG_DIR = os.path.join(
    _xdg_home("XDG_CONFIG_HOME", "~/.config"), APP_DIRNAME)
STATE_DIR = os.path.join(
    _xdg_home("XDG_STATE_HOME", "~/.local/state"), APP_DIRNAME)
CACHE_DIR = os.path.join(
    _xdg_home("XDG_CACHE_HOME", "~/.cache"), APP_DIRNAME)
DATA_HOME = _xdg_home("XDG_DATA_HOME", "~/.local/share")
DATA_DIR = os.path.join(DATA_HOME, APP_DIRNAME)

CONFIG_PATH = os.path.join(CONFIG_DIR, "config.toml")
BACKUP_DIR = os.path.join(STATE_DIR, "backups")
DOCK_BACKUP_DIR = os.path.join(BACKUP_DIR, "dock")
IMAGE_DIR = os.path.join(STATE_DIR, "images")
DOCK_WRITE_STAMP_PATH = os.path.join(CACHE_DIR, "dock-last-write")

SOURCE_CONFIG_PATH = os.path.join(PROJECT_DIR, "config.toml")
SOURCE_EXAMPLE_PATH = os.path.join(PROJECT_DIR, "config.example.toml")
USER_EXAMPLE_PATH = os.path.join(DATA_DIR, "config.example.toml")
SYSTEM_EXAMPLE_PATH = "/usr/share/iocenter-linux/config.example.toml"

DEFAULT_CONFIG_TEXT = """# IO Center für Linux

[uinput]
enabled = true

[keys.key1]
key = "F13"

[keys.key2]
key = "F14"

[keys.key3]
key = "F15"

[keys.key4]
key = "F16"

[keys.key5]
key = "F17"

[keys.key6]
key = "F18"

[keys.key7]
key = "F19"

[keys.key8]
key = "F20"
"""


def ensure_user_config(target=None, legacy_paths=None, example_paths=None):
    """Erzeugt die Benutzerkonfiguration oder übernimmt die alte unverändert.

    Der Rückgabewert ist ``(ziel, quelle)``. ``quelle`` ist nur gesetzt, wenn
    tatsächlich aus einer vorhandenen Datei migriert bzw. kopiert wurde.
    Bestehende Ziele werden niemals überschrieben.
    """
    target = os.path.abspath(os.path.expanduser(target or CONFIG_PATH))
    if os.path.isfile(target):
        return target, None

    if legacy_paths is None:
        legacy_paths = (SOURCE_CONFIG_PATH,)
    if example_paths is None:
        example_paths = (SOURCE_EXAMPLE_PATH, USER_EXAMPLE_PATH,
                         SYSTEM_EXAMPLE_PATH)

    target_identity = os.path.realpath(target)
    for candidate in tuple(legacy_paths) + tuple(example_paths):
        candidate = os.path.abspath(os.path.expanduser(candidate))
        if os.path.realpath(candidate) == target_identity:
            continue
        try:
            with open(candidate, encoding="utf-8") as handle:
                text = handle.read()
        except OSError:
            continue
        bqconfig.atomic_write_text(target, text)
        return target, candidate

    bqconfig.atomic_write_text(target, DEFAULT_CONFIG_TEXT)
    return target, None


def migrate_legacy_backups(target=None, legacy_dirs=None):
    """Kopiert alte Sicherungen einmalig; Originale bleiben als Rückfallebene."""
    target = os.path.abspath(os.path.expanduser(target or BACKUP_DIR))
    if legacy_dirs is None:
        legacy_dirs = (
            os.path.join(PROJECT_DIR, "extracted", "backup"),
            os.path.expanduser("~/iocenter-linux/extracted/backup"),
        )

    copied = 0
    seen = set()
    for legacy in legacy_dirs:
        legacy = os.path.abspath(os.path.expanduser(legacy))
        if legacy in seen or not os.path.isdir(legacy):
            continue
        seen.add(legacy)
        for directory, _subdirectories, filenames in os.walk(legacy):
            relative = os.path.relpath(directory, legacy)
            destination_dir = (target if relative == "."
                               else os.path.join(target, relative))
            for filename in filenames:
                source = os.path.join(directory, filename)
                destination = os.path.join(destination_dir, filename)
                if os.path.exists(destination):
                    continue
                os.makedirs(destination_dir, exist_ok=True)
                try:
                    shutil.copy2(source, destination)
                except OSError:
                    continue
                copied += 1
    return copied
