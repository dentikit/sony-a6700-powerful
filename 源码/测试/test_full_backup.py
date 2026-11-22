"""Camera-free tests for 核心/完整备份.py against a simulated RAM fastboot device."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from collections import namedtuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from 核心 import 完整备份 as fb  # noqa: E402

PRODUCT = 'A6700-AP-read-candidate'
PARTS = [('nflasha1', 0x4, 0x40, 'ext2'), ('nflasha2', 0x44, 0xc0, 'fat'), ('nflasha3', 0x104, 0x28, 'wbi2')]
USER_SECTORS = 0x12c
CHUNK = 64
Usage = namedtuple('Usage', 'total used free')
PLENTY = lambda _path: Usage(1 << 40, 0, 1 << 40)  # noqa: E731


def line(name, start, count, kind):
    return f'{name.ljust(11)}{start:08x} {count:08x} {kind}'


class FakeCamera:
    """Implements the read-only RAM fastboot protocol over an in-memory flash."""

    def __init__(self, seed='camera-1', product=PRODUCT, flaky=None, unstable=(), unplug_after=None):
        self.seed, self.product = seed, product
        self.flaky = dict(flaky or {})          # (target, start) -> number of corrupted reads
        self.unstable = set(unstable)           # (target, start) -> every read differs
        self.unplug_after = unplug_after        # uploads before simulated USB loss
        self.uploads = 0
        self.commands = []
        self.queue = []
        self.staged = None
        self.noise = 0

    def sectors(self, target, start, count):
        out = bytearray()
        for index in range(start, start + count):
            block = hashlib.sha256(f'{self.seed}/{target}/{index}'.encode()).digest()
            out += block * 16
        return bytes(out)

    def sizes(self):
        return {'nflashaB0': fb.BOOT_SECTORS, 'nflashaB1': fb.BOOT_SECTORS, 'nflasha': USER_SECTORS}

    def write(self, endpoint, data, timeout=None):
        assert endpoint == 0x01
        command = bytes(data).decode('ascii')
        self.commands.append(command)
        if command == 'getvar:product':
            self.queue.append(b'OKAY' + self.product.encode())
        elif command == 'getvar:version':
            self.queue.append(b'OKAYA6700-RAM-fastboot')
        elif command == 'oem partition':
            lines = ['device     start    count    type', line('nflashaB0', 0, fb.BOOT_SECTORS, 'raw'),
                     line('nflashaB1', 0, fb.BOOT_SECTORS, 'raw'), line('nflasha', 0, USER_SECTORS, 'raw')]
            lines += [line(*part) for part in PARTS]
            self.queue.extend(b'INFO' + text.encode() for text in lines)
            self.queue.append(b'OKAY')
        elif command.startswith('oem partition dump '):
            target, offset, count = command.split()[3:]
            offset, count = int(offset, 16), int(count, 16)
            size = self.sizes()[target]
            if offset >= size:
                self.queue.append(b'FAILoffset past end of partition')
            else:
                count = min(count, size - offset)
                self.staged = (target, offset, count)
                self.queue += [b'INFOhint: fastboot get_staged ' + target.encode() + b'.bin', b'OKAY']
        elif command == 'upload':
            if self.unplug_after is not None and self.uploads >= self.unplug_after:
                raise IOError('simulated USB disconnect')
            target, offset, count = self.staged
            payload = bytearray(self.sectors(target, offset, count))
            key = (target, offset)
            if self.flaky.get(key):
                self.flaky[key] -= 1
                payload[100] ^= 0xFF
            if key in self.unstable:
                self.noise += 1
                payload[200] = self.noise & 0xFF
            self.uploads += 1
            self.queue += [b'DATA%08x' % len(payload), bytes(payload), b'OKAY']
        else:
            self.queue.append(b'FAILunknown command')
        return len(data)

    def read(self, endpoint, size, timeout=None):
        assert endpoint == 0x81
        item = self.queue[0]
        if len(item) <= size:
            return self.queue.pop(0)
        self.queue[0] = item[size:]
        return item[:size]


ALLOWED = ('getvar:product', 'getvar:version', 'oem partition', 'upload')


class FullBackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='fullbackup-test-'))
        self.out = self.tmp / 'backup'
        self.log = []

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def backup(self, camera, **kwargs):
        kwargs.setdefault('disk_usage', PLENTY)
        return fb.run(camera, self.out, chunk_sectors=CHUNK, say=self.log.append, **kwargs)

    def assert_read_only(self, camera):
        for command in camera.commands:
            self.assertTrue(command in ALLOWED or command.startswith('oem partition dump '), command)

    def test_full_backup_matches_camera_and_verifies(self):
        camera = FakeCamera()
        manifest = self.backup(camera)
        self.assertTrue(manifest['complete'])
        self.assertEqual((self.out / 'boot0.raw').read_bytes(), camera.sectors('nflashaB0', 0, fb.BOOT_SECTORS))
        self.assertEqual((self.out / 'boot1.raw').read_bytes(), camera.sectors('nflashaB1', 0, fb.BOOT_SECTORS))
        chunks = sorted((self.out / 'user').glob('*.raw'))
        self.assertEqual(len(chunks), 5)  # 0x12c sectors in chunks of 64: 4 full + 1 partial
        joined = b''.join(path.read_bytes() for path in chunks)
        self.assertEqual(joined, camera.sectors('nflasha', 0, USER_SECTORS))
        self.assertEqual(manifest['user_area_sha256'], hashlib.sha256(joined).hexdigest())
        self.assertEqual(fb.verify(self.out, say=self.log.append), [])
        self.assertEqual(len((self.out / 'SHA256SUMS.txt').read_text().splitlines()), 7)
        self.assertEqual([p['name'] for p in manifest['partitions']], ['nflasha1', 'nflasha2', 'nflasha3'])
        image = self.tmp / 'user.img'
        self.assertEqual(fb.assemble(self.out, image, say=self.log.append), manifest['user_area_sha256'])
        self.assertEqual(image.read_bytes(), joined)
        self.assert_read_only(camera)
        self.assertEqual(camera.uploads, 14)  # 7 regions, each read twice

    def test_transient_mismatch_is_reread(self):
        camera = FakeCamera(flaky={('nflasha', 64): 1})
        manifest = self.backup(camera)
        self.assertEqual(manifest['regions']['user-00000040']['attempts'], 2)
        self.assertEqual((self.out / 'user/00000040.raw').read_bytes(), camera.sectors('nflasha', 64, CHUNK))

    def test_unstable_region_stops_without_saving_it(self):
        with self.assertRaises(fb.BackupError):
            self.backup(FakeCamera(unstable={('nflasha', 128)}))
        manifest = json.loads((self.out / 'manifest.json').read_text())
        self.assertIn('user-00000040', manifest['regions'])
        self.assertNotIn('user-00000080', manifest['regions'])
        self.assertFalse((self.out / 'user/00000080.raw').exists())
        self.assertFalse(manifest['complete'])

    def test_resume_after_usb_loss_reads_only_what_is_missing(self):
        with self.assertRaises(IOError):
            self.backup(FakeCamera(unplug_after=7))  # boot0, boot1, user-0 saved; fails mid user-40
        camera = FakeCamera()
        manifest = self.backup(camera, resume=True)
        self.assertTrue(manifest['complete'])
        self.assertEqual(camera.uploads, 1 + 4 * 2)  # one spot-check read + 4 remaining regions twice
        self.assertEqual(fb.verify(self.out, say=self.log.append), [])
        self.assertTrue(any('Resume check passed' in message for message in self.log))

    def test_resume_refuses_a_different_camera(self):
        with self.assertRaises(IOError):
            self.backup(FakeCamera(unplug_after=7))
        with self.assertRaises(fb.BackupError) as caught:
            self.backup(FakeCamera(seed='camera-2'), resume=True)
        self.assertIn('different', str(caught.exception))

    def test_refuses_write_capable_program(self):
        camera = FakeCamera(product='ILCE-7M4')
        with self.assertRaises(ValueError):
            self.backup(camera)
        self.assertFalse(any(command.startswith('oem partition dump') for command in camera.commands))
        self.assertFalse(self.out.exists())

    def test_existing_backup_needs_resume_flag(self):
        self.backup(FakeCamera())
        with self.assertRaises(fb.BackupError):
            self.backup(FakeCamera())

    def test_refuses_non_empty_folder(self):
        self.out.mkdir()
        (self.out / 'other.txt').write_text('x')
        with self.assertRaises(fb.BackupError):
            self.backup(FakeCamera())

    def test_insufficient_space_stops_before_reading(self):
        camera = FakeCamera()
        with self.assertRaises(fb.BackupError):
            self.backup(camera, disk_usage=lambda _path: Usage(1 << 30, 0, 1 << 20))
        self.assertEqual(camera.uploads, 0)

    def test_verify_detects_changed_and_missing_files(self):
        self.backup(FakeCamera())
        chunk = self.out / 'user/00000080.raw'
        data = bytearray(chunk.read_bytes())
        data[0] ^= 1
        chunk.write_bytes(bytes(data))
        (self.out / 'boot1.raw').unlink()
        problems = fb.verify(self.out, say=self.log.append)
        self.assertTrue(any('00000080' in problem and 'changed' in problem for problem in problems))
        self.assertTrue(any('boot1.raw' in problem and 'missing' in problem for problem in problems))
        with self.assertRaises(fb.BackupError):
            fb.assemble(self.out, self.tmp / 'user.img', say=self.log.append)
        self.assertFalse((self.tmp / 'user.img').exists())

    def test_resume_rereads_a_damaged_saved_chunk(self):
        with self.assertRaises(IOError):
            self.backup(FakeCamera(unplug_after=9))  # boot0, boot1, user-0, user-40 saved
        saved = self.out / 'user/00000000.raw'
        saved.write_bytes(b'\0' * saved.stat().st_size)
        camera = FakeCamera()
        self.backup(camera, resume=True)
        self.assertEqual(saved.read_bytes(), camera.sectors('nflasha', 0, CHUNK))
        self.assertTrue(any('reading it again' in message for message in self.log))
        self.assertEqual(fb.verify(self.out, say=self.log.append), [])

    def test_partition_table_parsing_rejects_bad_tables(self):
        good = ['device     start    count    type', line('nflashaB0', 0, 0x2000, 'raw'),
                line('nflashaB1', 0, 0x2000, 'raw'), line('nflasha', 0, 0x100, 'raw'), line('nflasha1', 0, 0x100, 'ext2')]
        self.assertEqual(fb.parse_partition_table(good)['user_sectors'], 0x100)
        for broken in (good[:2] + good[3:],                                  # boot1 missing
                       good[:4] + [line('nflasha1', 0xf0, 0x20, 'ext2')],    # outside user area
                       good[:4],                                             # no partitions
                       good + ['garbage line']):
            with self.assertRaises(fb.BackupError):
                fb.parse_partition_table(broken)


if __name__ == '__main__':
    unittest.main()
