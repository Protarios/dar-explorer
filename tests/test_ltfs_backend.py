import unittest
from ltfs_backend.operations import (
    build_mount_command, build_unmount_command, filesystem_at,
    is_ltfs_filesystem, validate_mountpoint,
)

class LtfsBackendTests(unittest.TestCase):
    def test_mount_command_quotable_argv(self):
        self.assertEqual(
            build_mount_command('/dev/sg3', '/mnt/LTO Library', executable='/usr/bin/ltfs'),
            ['/usr/bin/ltfs', '-o', 'devname=/dev/sg3', '/mnt/LTO Library'],
        )

    def test_unmount_uses_no_lazy_or_force(self):
        self.assertEqual(
            build_unmount_command('/mnt/LTO Library', command=['/usr/bin/fusermount3', '-u']),
            ['/usr/bin/fusermount3', '-u', '/mnt/LTO Library'],
        )

    def test_rejects_missing_device(self):
        with self.assertRaises(ValueError):
            build_mount_command('   ', '/mnt/ltfs', executable='ltfs')

    def test_rejects_relative_mountpoint(self):
        with self.assertRaises(ValueError):
            validate_mountpoint('mnt/ltfs')

    def test_escape_mountinfo(self):
        rows = (
            '41 30 0:100 / /mnt/LTO\\040Library rw,nosuid - fuse.ltfs ltfs rw\n'
            '42 30 0:101 / /mnt/data rw - ext4 /dev/sda1 rw\n'
        )
        self.assertEqual(filesystem_at('/mnt/LTO Library', mountinfo=rows), 'fuse.ltfs')
        self.assertEqual(filesystem_at('/mnt/data', mountinfo=rows), 'ext4')
        self.assertIsNone(filesystem_at('/mnt/unknown', mountinfo=rows))

    def test_recognize_only_ltfs_type(self):
        self.assertTrue(is_ltfs_filesystem('fuse.ltfs'))
        self.assertTrue(is_ltfs_filesystem('ltfs'))
        self.assertFalse(is_ltfs_filesystem('ext4'))
        self.assertFalse(is_ltfs_filesystem(None))

if __name__ == '__main__':
    unittest.main()
