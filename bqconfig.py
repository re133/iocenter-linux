#!/usr/bin/env python3
"""Kleine, verlustfreie Helfer für die gemeinsam genutzte config.toml."""

import os
import re
import tempfile


def atomic_write_text(path, text):
    """Text im selben Verzeichnis schreiben und anschließend atomar ersetzen."""
    path = os.path.abspath(os.path.expanduser(path))
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    mode = None
    try:
        mode = os.stat(path).st_mode & 0o777
    except OSError:
        pass

    fd, temporary = tempfile.mkstemp(
        prefix=".%s." % os.path.basename(path), dir=directory, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            if not text.endswith("\n"):
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def replace_sections(text, names, replacement):
    """Ausgewählte TOML-Tabellen ersetzen, alle anderen Bytes beibehalten.

    Die GUI besitzt die angegebenen Tabellen vollständig. Unbekannte Tabellen,
    Kommentare und insbesondere ``[ui]`` bleiben unverändert erhalten.
    """
    escaped = "|".join(re.escape(name) for name in names)
    pattern = re.compile(
        r"(?ms)^\[(?:%s)\][^\n]*\n.*?(?=^\[|\Z)" % escaped)
    kept = pattern.sub("", text).rstrip()
    rendered = replacement.strip()
    if kept and rendered:
        return kept + "\n\n" + rendered + "\n"
    return (kept or rendered) + "\n"
