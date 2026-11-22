"""Read-only full eMMC backup through the fixed read-only RAM fastboot program.

What it reads: boot0 and boot1 (the 0x2000 sectors each that the RAM program
can address) and the whole user area from sector 0 to the end of the last
valid partition, in 16 MiB chunks. Every chunk is read twice and must match
byte for byte before it is saved; a chunk that keeps disagreeing stops the run.

What it sends: only the published read commands (identity, partition table,
range dump, upload), through the existing verified read_region(). The RAM
program it talks to is the read-only build, whose flash-write code is absent;
inspect() refuses any other identity.

What it does not cover: unpartitioned space after the last partition (the RAM
program cannot address it), anything beyond the first 0x2000 sectors of each
boot partition, and restore. Images are raw as stored on the chip, so
encrypted partitions stay encrypted and only fit the same camera.
"""
import datetime
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from .扇区备份 import read_region
from .设备查询 import inspect, query

FORMAT = 'powerful6700-full-backup'
FORMAT_VERSION = 1
SECTOR = 512
CHUNK_SECTORS = 0x8000          # 16 MiB: the verified per-read maximum
BOOT_SECTORS = 0x2000           # what the RAM program exposes per boot partition
MAX_ATTEMPTS = 3                # double-read pairs before giving up on a chunk
SPACE_MARGIN = 64 * 1024 * 1024
LIMITS = (
    'Covers boot0/boot1 (first 0x2000 sectors each, all the RAM program can address) '
    'and the user area up to the end of the last partition. Unpartitioned space after '
    'the last partition is not covered.',
    'Raw as stored on the chip: encrypted partitions stay encrypted. Only meaningful '
    'for the same camera.',
    'This is a backup only. No restore tool exists yet, and restoring has never been '
    'verified on a real camera.',
)
README = '''Powerful6700 full backup (read-only)

This folder is a raw copy of a Sony A6700's internal flash, made with the
read-only RAM fastboot program over a Pico. It contains data unique to that
camera. Keep it private: never upload it or share it.

Files
  manifest.json   what was read, from which camera state, and the SHA-256 of every file
  partitions.txt  the partition table exactly as the camera reported it
  boot0.raw       boot partition 0 (first 0x2000 sectors)
  boot1.raw       boot partition 1 (first 0x2000 sectors)
  user/*.raw      the user area in 16 MiB chunks, named by start sector (hex)
  SHA256SUMS.txt  fingerprints of every file, written when the backup completes

Check it any time without the camera:
  python 源码/脚本/full_backup.py verify --out <this folder>

Limits
''' + ''.join(f'  - {line}\n' for line in LIMITS)


class BackupError(RuntimeError):
    """A condition that makes the backup untrustworthy; nothing more is read."""


def now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def parse_partition_table(lines):
    """Parse the INFO lines of 'oem partition' into sizes and partitions."""
    rows = []
    for line in lines:
        fields = line.split()
        if not fields or fields[0] == 'device':
            continue
        if len(fields) != 4:
            raise BackupError(f'Unexpected partition table line: {line!r}')
        name, start, count, kind = fields
        try:
            rows.append({'name': name, 'start': int(start, 16), 'count': int(count, 16), 'type': kind})
        except ValueError:
            raise BackupError(f'Unexpected partition table line: {line!r}') from None
    by_name = {row['name']: row for row in rows}
    if len(by_name) != len(rows):
        raise BackupError('Partition table lists a device twice')
    for boot in ('nflashaB0', 'nflashaB1'):
        row = by_name.get(boot)
        if not row or row['start'] != 0 or row['count'] != BOOT_SECTORS:
            raise BackupError(f'{boot} is missing or not the expected 0x{BOOT_SECTORS:x} sectors')
    user = by_name.get('nflasha')
    if not user or user['start'] != 0 or not 0 < user['count'] < 0x100000000:
        raise BackupError('User area size missing or invalid in the partition table')
    partitions = [row for row in rows if row['name'][7:].isdigit() and row['name'].startswith('nflasha')]
    if not partitions:
        raise BackupError('Partition table has no partitions')
    for row in partitions:
        if row['count'] <= 0 or row['start'] + row['count'] > user['count']:
            raise BackupError(f"{row['name']} lies outside the reported user area")
    return {'boot_sectors': BOOT_SECTORS, 'user_sectors': user['count'], 'partitions': partitions}


def build_plan(table, chunk_sectors=CHUNK_SECTORS):
    """Ordered list of regions to read; file names are stable across runs."""
    if not 1 <= chunk_sectors <= CHUNK_SECTORS:
        raise ValueError('chunk size must be 1..0x8000 sectors')
    plan = [
        {'id': 'boot0', 'target': 'nflashaB0', 'start': 0, 'sectors': table['boot_sectors'], 'file': 'boot0.raw'},
        {'id': 'boot1', 'target': 'nflashaB1', 'start': 0, 'sectors': table['boot_sectors'], 'file': 'boot1.raw'},
    ]
    for start in range(0, table['user_sectors'], chunk_sectors):
        sectors = min(chunk_sectors, table['user_sectors'] - start)
        plan.append({'id': f'user-{start:08x}', 'target': 'nflasha', 'start': start,
                     'sectors': sectors, 'file': f'user/{start:08x}.raw'})
    return plan


def plan_bytes(plan):
    return sum(item['sectors'] for item in plan) * SECTOR


def read_verified(device, target, start, sectors, *, reader=read_region, attempts=MAX_ATTEMPTS):
    """Read a region twice until both reads agree; return (data, attempts used)."""
    expected = sectors * SECTOR
    for attempt in range(1, attempts + 1):
        first = reader(device, target, start, sectors)
        second = reader(device, target, start, sectors)
        if len(first) != expected or len(second) != expected:
            raise BackupError(f'{target} @0x{start:x}: wrong data length from camera')
        if first == second:
            return first, attempt
    raise BackupError(f'{target} @0x{start:x}: two reads disagreed {attempts} times in a row; '
                      'stopped instead of saving untrustworthy data')


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def write_atomic(path, data):
    """Write to a temporary file, flush to disk, then move into place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.part')
    with open(temporary, 'wb') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def save_manifest(out, manifest):
    write_atomic(out / 'manifest.json', (json.dumps(manifest, ensure_ascii=False, indent=1) + '\n').encode('utf-8'))


def load_manifest(out):
    try:
        manifest = json.loads((out / 'manifest.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise BackupError(f'No manifest.json in {out}') from None
    except (OSError, ValueError) as exc:
        raise BackupError(f'manifest.json is unreadable: {exc}') from None
    if manifest.get('format') != FORMAT or manifest.get('format_version') != FORMAT_VERSION:
        raise BackupError('manifest.json is not a Powerful6700 full backup of a supported version')
    return manifest


def _progress(say, done_bytes, total_bytes, session_bytes, started, clock):
    elapsed = max(clock() - started, 1e-6)
    rate = session_bytes / elapsed
    remaining = (total_bytes - done_bytes) / rate if rate > 0 else 0
    say(f'  {done_bytes / total_bytes:6.1%}  {done_bytes / 2**20:8.0f} / {total_bytes / 2**20:.0f} MiB'
        f'  {rate / 2**20:5.1f} MiB/s  about {remaining / 60:4.0f} min left')


def run(device, out, *, resume=False, chunk_sectors=CHUNK_SECTORS, reader=read_region,
        say=print, clock=time.monotonic, disk_usage=shutil.disk_usage):
    """Create or resume a full backup in ``out``. Progress is saved after every region."""
    out = Path(out)
    identity = inspect(device)
    table_lines = query(device, 'oem partition')['info']
    table = parse_partition_table(table_lines)
    table_sha = hashlib.sha256('\n'.join(table_lines).encode('utf-8')).hexdigest()
    plan = build_plan(table, chunk_sectors)

    if (out / 'manifest.json').exists():
        if not resume:
            raise BackupError(f'{out} already holds a backup. Use --resume to continue it, or choose a new folder.')
        manifest = load_manifest(out)
        if manifest['device'] != identity or manifest['partition_table']['sha256'] != table_sha:
            raise BackupError('The camera reports a different program or partition table than this backup. '
                              'Start a new folder instead of mixing backups.')
        if manifest['chunk_sectors'] != chunk_sectors:
            raise BackupError('This backup was started with a different chunk size.')
        _spot_check(device, out, manifest, reader, say)
    else:
        if out.exists() and any(out.iterdir()):
            raise BackupError(f'{out} is not empty. Choose a new or empty folder.')
        out.mkdir(parents=True, exist_ok=True)
        manifest = {
            'format': FORMAT, 'format_version': FORMAT_VERSION, 'created_utc': now(),
            'tool': 'full_backup.py', 'device': identity,
            'partition_table': {'lines': table_lines, 'sha256': table_sha},
            'boot_sectors': table['boot_sectors'], 'user_sectors': table['user_sectors'],
            'partitions': table['partitions'], 'chunk_sectors': chunk_sectors,
            'limits': list(LIMITS), 'regions': {}, 'complete': False,
        }
        (out / 'README.txt').write_text(README, encoding='utf-8')
        (out / 'partitions.txt').write_text('\n'.join(table_lines) + '\n', encoding='utf-8')
        save_manifest(out, manifest)

    pending = []
    for item in plan:
        record = manifest['regions'].get(item['id'])
        path = out / item['file']
        if record and path.is_file() and path.stat().st_size == item['sectors'] * SECTOR \
                and sha256_file(path) == record['sha256']:
            continue
        if record:
            say(f"  {item['file']} no longer matches its saved fingerprint; reading it again.")
            del manifest['regions'][item['id']]
        pending.append(item)

    total = plan_bytes(plan)
    remaining = plan_bytes(pending)
    free = disk_usage(out).free
    if remaining + SPACE_MARGIN > free:
        raise BackupError(f'Not enough free space in {out}: need about {(remaining + SPACE_MARGIN) / 2**30:.2f} GiB, '
                          f'have {free / 2**30:.2f} GiB.')
    say(f'{len(plan) - len(pending)} of {len(plan)} regions already saved; {len(pending)} to read '
        f'({remaining / 2**20:.0f} MiB, each read twice).')

    started = clock()
    session = 0
    done = total - remaining
    for item in pending:
        data, attempts = read_verified(device, item['target'], item['start'], item['sectors'], reader=reader)
        path = out / item['file']
        write_atomic(path, data)
        manifest['regions'][item['id']] = {
            'target': item['target'], 'start': item['start'], 'sectors': item['sectors'],
            'file': item['file'], 'sha256': hashlib.sha256(data).hexdigest(),
            'attempts': attempts, 'saved_utc': now(),
        }
        save_manifest(out, manifest)
        session += len(data)
        done += len(data)
        _progress(say, done, total, session, started, clock)

    manifest['user_area_sha256'] = _user_area_sha(out, manifest, plan)
    manifest['complete'] = True
    manifest['completed_utc'] = now()
    save_manifest(out, manifest)
    sums = ''.join(f"{manifest['regions'][item['id']]['sha256']}  {item['file']}\n" for item in plan)
    write_atomic(out / 'SHA256SUMS.txt', sums.encode('utf-8'))
    say(f'Backup complete: {total / 2**20:.0f} MiB in {len(plan)} files. User area SHA-256: '
        f"{manifest['user_area_sha256']}")
    return manifest


def _spot_check(device, out, manifest, reader, say):
    """Re-read the newest saved region once; refuse to continue if the camera differs."""
    records = sorted(manifest['regions'].values(), key=lambda record: record['saved_utc'])
    if not records:
        return
    record = records[-1]
    data = reader(device, record['target'], record['start'], record['sectors'])
    if hashlib.sha256(data).hexdigest() != record['sha256']:
        raise BackupError(f"The camera's {record['file']} no longer matches the saved copy. This is a different "
                          'camera, or it was used normally since the last session. Start a new folder.')
    say(f"Resume check passed: {record['file']} still matches the camera.")


def _user_area_sha(out, manifest, plan):
    digest = hashlib.sha256()
    for item in plan:
        if item['target'] != 'nflasha':
            continue
        with open(out / item['file'], 'rb') as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(block)
    return digest.hexdigest()


def verify(out, say=print):
    """Check a backup folder against its manifest without the camera; return a list of problems."""
    out = Path(out)
    problems = []
    try:
        manifest = load_manifest(out)
    except BackupError as exc:
        return [str(exc)]
    table = {'boot_sectors': manifest['boot_sectors'], 'user_sectors': manifest['user_sectors']}
    plan = build_plan(table, manifest['chunk_sectors'])
    for item in plan:
        record = manifest['regions'].get(item['id'])
        path = out / item['file']
        if record is None:
            problems.append(f"{item['file']}: not read yet")
        elif (record['target'], record['start'], record['sectors']) != (item['target'], item['start'], item['sectors']):
            problems.append(f"{item['file']}: manifest range does not match the plan")
        elif not path.is_file():
            problems.append(f"{item['file']}: file missing")
        elif path.stat().st_size != item['sectors'] * SECTOR:
            problems.append(f"{item['file']}: wrong size")
        elif sha256_file(path) != record['sha256']:
            problems.append(f"{item['file']}: contents changed since it was saved")
    extra = set(manifest['regions']) - {item['id'] for item in plan}
    problems.extend(f'{name}: unexpected entry in manifest' for name in sorted(extra))
    if not problems:
        if not manifest.get('complete'):
            problems.append('every file checks out, but the backup was never marked complete')
        elif _user_area_sha(out, manifest, plan) != manifest.get('user_area_sha256'):
            problems.append('user area fingerprint does not match')
    if problems:
        say(f'Backup check FAILED ({len(problems)} problem(s)):')
        for problem in problems:
            say('  - ' + problem)
    else:
        say(f'Backup check passed: {len(plan)} files, {plan_bytes(plan) / 2**20:.0f} MiB, all fingerprints match.')
    return problems


def assemble(out, image, say=print):
    """Join the verified user-area chunks into one image file (never overwrites)."""
    out, image = Path(out), Path(image)
    problems = verify(out, say=say)
    if problems:
        raise BackupError('Backup check failed; image not assembled.')
    manifest = load_manifest(out)
    plan = build_plan({'boot_sectors': manifest['boot_sectors'], 'user_sectors': manifest['user_sectors']},
                      manifest['chunk_sectors'])
    digest = hashlib.sha256()
    with open(image, 'xb') as target:
        for item in plan:
            if item['target'] != 'nflasha':
                continue
            data = (out / item['file']).read_bytes()
            digest.update(data)
            target.write(data)
        target.flush()
        os.fsync(target.fileno())
    if digest.hexdigest() != manifest['user_area_sha256']:
        raise BackupError('Assembled image fingerprint does not match the manifest.')
    say(f"User area image written: {image} (SHA-256 {digest.hexdigest()})")
    return digest.hexdigest()
