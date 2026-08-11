import ast
import os
import tempfile
import unittest
from unittest.mock import patch

import bqconfig
import bqdock
import bqi18n
import bqgui
import bqkeyd
import bqlink
import bqmeta
import bqpaths


class ConfigPreservationTests(unittest.TestCase):
    def test_managed_sections_change_without_losing_ui_or_custom_data(self):
        original = (
            "# persönliche Einstellungen\n"
            "[ui]\nlanguage = \"en\"\n\n"
            "[uinput]\nenabled = true\n\n"
            "[keys.key1]\ncommand = \"old\"\n\n"
            "[custom]\nkeep = 42\n"
        )
        replacement = (
            "[uinput]\nenabled = false\n\n"
            "[keys.key1]\nkey = \"F15\"\n"
        )
        result = bqconfig.replace_sections(
            original, ["uinput", "keys.key1"], replacement)
        self.assertIn("# persönliche Einstellungen", result)
        self.assertIn('[ui]\nlanguage = "en"', result)
        self.assertIn("[custom]\nkeep = 42", result)
        self.assertNotIn('command = "old"', result)
        self.assertIn('key = "F15"', result)

    def test_language_save_is_atomic_and_keeps_other_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.toml")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write('[ui]\nlanguage = "de"\n\n[custom]\nkeep = true\n')
            with patch.object(bqi18n, "_config_path", return_value=path):
                self.assertTrue(bqi18n.save_language("en"))
            with open(path, encoding="utf-8") as handle:
                result = handle.read()
            self.assertIn('language = "en"', result)
            self.assertIn("[custom]\nkeep = true", result)

    def test_legacy_config_is_migrated_once_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy = os.path.join(directory, "legacy.toml")
            target = os.path.join(directory, "config", "config.toml")
            with open(legacy, "w", encoding="utf-8") as handle:
                handle.write('[ui]\nlanguage = "en"\n\n[custom]\nkeep = 7\n')

            path, source = bqpaths.ensure_user_config(
                target=target, legacy_paths=(legacy,), example_paths=())
            self.assertEqual(path, target)
            self.assertEqual(source, legacy)
            with open(target, encoding="utf-8") as handle:
                self.assertIn("keep = 7", handle.read())

            with open(legacy, "w", encoding="utf-8") as handle:
                handle.write("changed = true\n")
            _path, second_source = bqpaths.ensure_user_config(
                target=target, legacy_paths=(legacy,), example_paths=())
            self.assertIsNone(second_source)
            with open(target, encoding="utf-8") as handle:
                self.assertNotIn("changed", handle.read())

    def test_legacy_backups_are_copied_without_replacing_newer_files(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy = os.path.join(directory, "legacy")
            target = os.path.join(directory, "state", "backups")
            os.makedirs(os.path.join(legacy, "dock"))
            os.makedirs(os.path.join(target, "dock"))
            with open(os.path.join(legacy, "key1.jpg"), "wb") as handle:
                handle.write(b"old-key")
            with open(os.path.join(legacy, "dock", "same.png"), "wb") as handle:
                handle.write(b"old-dock")
            with open(os.path.join(target, "dock", "same.png"), "wb") as handle:
                handle.write(b"new-dock")

            copied = bqpaths.migrate_legacy_backups(
                target=target, legacy_dirs=(legacy,))
            self.assertEqual(copied, 1)
            with open(os.path.join(target, "key1.jpg"), "rb") as handle:
                self.assertEqual(handle.read(), b"old-key")
            with open(os.path.join(target, "dock", "same.png"), "rb") as handle:
                self.assertEqual(handle.read(), b"new-dock")


class TranslationCoverageTests(unittest.TestCase):
    def test_every_literal_gui_translation_has_english_text(self):
        with open(bqgui.__file__, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        missing = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "tr":
                continue
            value = node.args[0]
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                if value.value not in bqi18n.ENGLISH:
                    missing.add(value.value)
        self.assertEqual(missing, set())


class ReleaseMetadataTests(unittest.TestCase):
    def test_release_files_share_version_and_app_id(self):
        project = os.path.dirname(os.path.abspath(bqgui.__file__))
        files = {
            "metainfo": os.path.join(
                project, "data", bqmeta.APP_ID + ".metainfo.xml"),
            "desktop": os.path.join(
                project, "data", bqmeta.APP_ID + ".desktop"),
            "arch": os.path.join(project, "packaging", "arch", "PKGBUILD"),
            "fedora": os.path.join(
                project, "packaging", "fedora", "iocenter-linux.spec"),
            "debian": os.path.join(project, "debian", "changelog"),
        }
        contents = {}
        for name, path in files.items():
            with open(path, encoding="utf-8") as handle:
                contents[name] = handle.read()
        self.assertIn(bqmeta.APP_ID, contents["desktop"])
        self.assertIn(bqmeta.APP_ID, contents["metainfo"])
        self.assertIn('version="%s"' % bqmeta.VERSION,
                      contents["metainfo"])
        self.assertIn("pkgver=%s" % bqmeta.VERSION, contents["arch"])
        self.assertIn("Version:        %s" % bqmeta.VERSION,
                      contents["fedora"])
        self.assertIn("(%s-1)" % bqmeta.VERSION, contents["debian"])

    def test_runtime_paths_are_user_writable_xdg_locations(self):
        self.assertTrue(bqpaths.CONFIG_PATH.startswith(bqpaths.CONFIG_DIR))
        self.assertTrue(bqpaths.BACKUP_DIR.startswith(bqpaths.STATE_DIR))
        self.assertNotEqual(bqpaths.CONFIG_PATH, bqpaths.SOURCE_CONFIG_PATH)

    def test_full_license_is_available_to_the_gui(self):
        text = bqgui.license_text()
        self.assertIn("GNU GENERAL PUBLIC LICENSE", text)
        self.assertIn("Version 3", text)


class DesktopLaunchTests(unittest.TestCase):
    def test_string_command_gets_own_user_unit(self):
        with patch.object(bqkeyd.shutil, "which",
                          return_value="/usr/bin/systemd-run"):
            self.assertEqual(
                bqkeyd.desktop_launch_args("echo hello"),
                ["/usr/bin/systemd-run", "--user", "--collect", "--quiet",
                 "--service-type=exec", "--", "/bin/sh", "-lc",
                 "echo hello"],
            )

    def test_argument_list_is_not_sent_through_a_shell(self):
        with patch.object(bqkeyd.shutil, "which",
                          return_value="/usr/bin/systemd-run"):
            args = bqkeyd.desktop_launch_args(["notify-send", "hello world"])
        self.assertEqual(args[-2:], ["notify-send", "hello world"])


class AutostartTests(unittest.TestCase):
    def test_generated_unit_uses_current_paths_and_user_default_target(self):
        unit = bqgui.service_unit_text()
        project = os.path.dirname(os.path.abspath(bqgui.__file__))
        self.assertIn("WorkingDirectory=%s" % bqgui.systemd_path(project), unit)
        self.assertIn(bqgui.systemd_quote(bqgui.CONFIG_PATH), unit)
        self.assertIn("WantedBy=default.target", unit)
        self.assertNotIn("%h/iocenter-linux", unit)

    def test_systemd_quote_escapes_specifiers_and_quotes(self):
        self.assertEqual(bqgui.systemd_quote('/tmp/50%/a"b'),
                         '"/tmp/50%%/a\\"b"')

    def test_install_and_remove_use_the_same_exact_unit_file(self):
        class Dummy:
            def __init__(self, path):
                self.path = path
                self.messages = []
                self.refreshed = 0
                self._service_running = False

            def service_path(self):
                return self.path

            def systemd_user_available(self):
                return True, ""

            def refresh_service_state(self):
                self.refreshed += 1

            def show_status(self, text, _timeout):
                self.messages.append(text)

        class Result:
            returncode = 0
            stderr = ""

        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, bqgui.SERVICE)
            dummy = Dummy(target)
            with patch.object(bqgui.shutil, "which", return_value="systemctl"), \
                    patch.object(bqgui.subprocess, "run", return_value=Result()):
                bqgui.MainWindow.install_service(dummy)
            self.assertTrue(os.path.isfile(target))
            with open(target, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), bqgui.service_unit_text())

            with patch.object(bqgui.shutil, "which", return_value="systemctl"), \
                    patch.object(bqgui.subprocess, "run", return_value=Result()), \
                    patch.object(bqgui.QMessageBox, "question",
                                 return_value=bqgui.QMessageBox.StandardButton.Yes):
                bqgui.MainWindow.remove_service(dummy)
            self.assertFalse(os.path.exists(target))


class SafetyNoticeTests(unittest.TestCase):
    def test_notice_is_remembered_only_for_current_version(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = bqgui.QSettings(
                os.path.join(directory, "settings.ini"),
                bqgui.QSettings.Format.IniFormat)
            self.assertFalse(bqgui.safety_notice_accepted(settings))

            settings.setValue(bqgui.SAFETY_NOTICE_KEY,
                              bqgui.SAFETY_NOTICE_VERSION - 1)
            self.assertFalse(bqgui.safety_notice_accepted(settings))

            bqgui.remember_safety_notice(settings)
            self.assertTrue(bqgui.safety_notice_accepted(settings))


class UdevDiagnosticsTests(unittest.TestCase):
    def test_real_access_can_be_ready_through_another_rule(self):
        with patch.object(bqgui.bqkeyd, "find_vendor_node",
                          return_value=["hidraw5"]), \
                patch.object(bqgui.bqlamp, "find_lamparray_node",
                             return_value=["hidraw6"]), \
                patch.object(bqgui.os.path, "isfile", return_value=False), \
                patch.object(bqgui.os.path, "exists", return_value=True), \
                patch.object(bqgui.os, "access", return_value=True):
            result = bqgui.udev_diagnostics()

        self.assertTrue(result["hidraw_ready"])
        self.assertTrue(result["uinput_ready"])
        self.assertEqual(result["rule_paths"], [])

    def test_install_command_is_explicit_and_scoped_to_project_rule(self):
        command = bqgui.udev_install_command()
        self.assertIn(bqgui.UDEV_RULE_NAME, command)
        self.assertIn("/etc/udev/rules.d/", command)
        self.assertIn("udevadm control --reload-rules", command)
        self.assertEqual(command.count("sudo "), 3)

    def test_remove_command_targets_only_the_manual_project_rule(self):
        command = bqgui.udev_remove_command()
        self.assertIn(
            "/etc/udev/rules.d/%s" % bqgui.UDEV_RULE_NAME, command)
        self.assertTrue(command.splitlines()[0].startswith("sudo rm -- /etc/"))
        self.assertEqual(command.count("sudo "), 3)

    def test_warning_appears_for_connected_but_blocked_hid(self):
        status = {
            "nodes": ["hidraw8"],
            "hidraw_ready": False,
            "uinput_ready": True,
        }
        self.assertEqual(bqgui.access_setup_reasons(status), ["hidraw"])

    def test_optional_uinput_warns_only_when_enabled(self):
        status = {
            "nodes": [],
            "hidraw_ready": False,
            "uinput_ready": False,
        }
        self.assertEqual(bqgui.access_setup_reasons(status), [])
        self.assertEqual(
            bqgui.access_setup_reasons(status, uinput_required=True),
            ["uinput"])


class QLinkFrameTests(unittest.TestCase):
    def test_frame_round_trip_and_crc(self):
        frame = bqlink.build_frame(7, 12, (0x10, 0x06), b"abc")
        self.assertEqual(len(frame), bqlink.PACKET_SIZE)
        self.assertEqual(frame[2], 7)
        self.assertEqual(frame[4], 12)
        self.assertEqual(bqlink.frame_payload(frame), b"abc")
        expected = frame[62] | (frame[63] << 8)
        self.assertEqual(bqlink.crc16_modbus(frame[:62]), expected)


class DockWearProtectionTests(unittest.TestCase):
    def test_write_interval_survives_a_new_dock_instance(self):
        pixels = bytes(bqdock.DISPLAY_SIZE[0] * bqdock.DISPLAY_SIZE[1] * 2)
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(bqdock, "WRITE_STAMP_PATH",
                             os.path.join(directory, "stamp")), \
                patch.object(bqdock.time, "time", side_effect=[100.0, 100.0,
                                                               105.0]):
            previous = bqdock._last_write[0]
            bqdock._last_write[0] = 0.0
            try:
                first = object.__new__(bqdock.Dock)
                first._request = lambda *_args, **_kwargs: b""
                first.write_image(pixels)

                second = object.__new__(bqdock.Dock)
                second._request = lambda *_args, **_kwargs: b""
                with self.assertRaisesRegex(RuntimeError, "Zu kurz"):
                    second.write_image(pixels)
            finally:
                bqdock._last_write[0] = previous


if __name__ == "__main__":
    unittest.main()
