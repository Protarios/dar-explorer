"""Schutzregeln für manuelle LTFS-Formatierung (ohne Bandhardware)."""
import unittest
from unittest.mock import patch

from ltfs_backend.operations import (
    any_ltfs_mounted, build_format_command, validate_cartridge_label,
    validate_format_device,
)


class FormatCommandTests(unittest.TestCase):
    def test_minimal_command_never_forces_or_wipes(self):
        cmd = build_format_command('/dev/sg3', executable='/usr/bin/mkltfs')
        self.assertEqual(cmd, ['/usr/bin/mkltfs', '-d', '/dev/sg3'])
        self.assertFalse({'-f', '--force', '-w', '--wipe', '--long-wipe'} & set(cmd))

    def test_volume_name_is_separate_argv(self):
        self.assertEqual(
            build_format_command('/dev/nst0', volume_name='Archiv Band 01', executable='mkltfs'),
            ['mkltfs', '-d', '/dev/nst0', '-n', 'Archiv Band 01'],
        )

    def test_backend_does_not_require_hardware(self):
        self.assertEqual(validate_format_device('9A700L0077'), '9A700L0077')
        self.assertEqual(validate_format_device('/dev/tape/by-id/scsi-xyz-nst'),
                         '/dev/tape/by-id/scsi-xyz-nst')

    def test_rejects_shell_options_and_newlines(self):
        bad = ('', 'relative/path', '-f', '/dev/sg3; touch /tmp/x',
               '/dev/sg3\nrm -rf /', '/etc/passwd', '/dev/stX')
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_format_device(value)

    def test_rejects_control_characters_in_volume(self):
        for name in ('abc\n--force', 'abc\r', 'abc\x00'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                build_format_command('/dev/sg3', volume_name=name, executable='mkltfs')

    def test_not_installed_fails_closed(self):
        with patch('ltfs_backend.operations.mkltfs_executable', return_value=None):
            with self.assertRaises(FileNotFoundError):
                build_format_command('/dev/sg3')

    def test_label_verification(self):
        self.assertEqual(validate_cartridge_label(' ABC123 '), 'ABC123')
        for bad in ('', 'a', 'tape 1', '../x', 'ABC123\n'):
            # Newline input with strip is normalized; test explicit internal newline.
            if bad == 'ABC123\n':
                bad = 'ABC\n123'
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_cartridge_label(bad)

    def test_other_mounted_ltfs_blocks_format(self):
        entries = (
            '41 30 0:100 / /mnt/other rw - fuse.ltfs ltfs rw\n'
            '42 30 0:101 / /mnt/data rw - ext4 /dev/sda1 rw\n'
        )
        self.assertTrue(any_ltfs_mounted(mountinfo=entries))
        self.assertFalse(any_ltfs_mounted(mountinfo='42 30 0:101 / /mnt/data rw - ext4 /dev/sda1 rw'))

    def test_unreadable_mount_table_refuses_to_assume_idle(self):
        with patch('builtins.open', side_effect=OSError('no mountinfo')):
            with self.assertRaises(RuntimeError):
                any_ltfs_mounted()


if __name__ == '__main__':
    unittest.main()
