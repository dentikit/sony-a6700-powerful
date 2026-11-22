"""English wrapper for restoring a feature-state backup over a normal USB cable (no Pico).

It runs the app's own restore (`a6700-unlock device restore`) in two passes:
  1. a dry run that connects, reads the camera and shows exactly what would be
     written back, without writing anything;
  2. only after you type RESTORE, the real restore. It writes back only the
     feature fields this app manages, checks the camera's content before and
     after, and reads everything back independently.

If the camera's structure differs from what the backup expects, it refuses to
write. If restoring turns features off, the app first checks or resets the
related camera settings, the same way the Disable action does.

Usage (from the 应用 folder, with its virtual environment active):
  python scripts/feature_restore.py                  choose a backup from the list
  python scripts/feature_restore.py --backup FILE    restore a specific backup
  python scripts/feature_restore.py --dry-run        stop after the dry run
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import feature_backup as shared  # noqa: E402  (sets up paths and the English catalog)
from a6700_unlock import preparation  # noqa: E402
from a6700_unlock.backup import default_backup_dir, list_backups, load_backup  # noqa: E402
from a6700_unlock.catalog import load_catalog  # noqa: E402
from a6700_unlock.device_profile import load_device_profile  # noqa: E402
from a6700_unlock.device_protocol import DeviceError  # noqa: E402
from a6700_unlock.operations import execute_restore  # noqa: E402

say, tr, STATES = shared.say, shared.tr, shared.STATES
CONFIRM_WORD = 'RESTORE'
STOP = ('STOP: the result is not confirmed. Do not retry and do not unplug-and-repeat. Keep the operation '
        'records folder shown below, and ask for help before doing anything else with the camera.')
OUTCOMES = {
    'dry-run': 'Dry run finished. Nothing was written.',
    'no-op': 'Your camera already matches this backup. There is nothing to restore.',
    'restored': 'Restore complete, and the independent read-back matches the backup.',
    'settings-prepared': ('Camera settings were reset and read back. The firmware already matched the backup, '
                          'so nothing else was written.'),
    'blocked': ("Restore blocked: the camera's current content doesn't match what this backup expects. "
                'Nothing was written.'),
    'close-preparation-required': ('A camera setting needs attention before features can be turned off (see the '
                                   'reason). Firmware was not changed.'),
    'close-preparation-uncertain': ("A camera setting change couldn't be confirmed, so the restore stopped before "
                                    "touching firmware. Keep the records and don't retry right away."),
    'close-preparation-cancelled': 'Preparation was cancelled. Any confirmed setting changes stay; firmware was not changed.',
    'rolled-back': 'The camera stopped partway and rolled back. Read-back confirms the previous state.',
    'rejected': "The camera's own pre-write check refused the restore. Nothing changed.",
    'cancelled': 'Cancelled before anything was written.',
    'error': "The restore didn't finish. Nothing was written.",
    'verification-failed': STOP,
    'recovery-required': STOP,
    'uncertain': STOP,
}
SETTLED = ('restored', 'settings-prepared')
POWER_CYCLE = ('Now power-cycle the camera so the change takes effect:\n'
               '  1. Turn the camera off normally and wait for the access lamp to go out.\n'
               '  2. Unplug the USB cable and any external power.\n'
               '  3. Remove the battery and wait at least 10 seconds.\n'
               '  4. Put the battery back and turn the camera on.\n'
               '  5. Check the menus. With custom shutter sounds installed, keep USB unplugged and wait about '
               '90 seconds after power-on before checking.')


def choose_backup(directory, explicit, ask):
    if explicit:
        return explicit
    rows = list_backups(directory)
    if not rows:
        say(f'No feature backups found in {directory or default_backup_dir()}. Make one first.')
        return None
    say('Your feature backups (newest first):')
    for number, row in enumerate(rows, 1):
        say(f"  {number}. {row.get('created_at') or 'unknown date'}  {Path(row['path']).name}")
    answer = ask('Which one? Press Enter for the newest, or type a number: ').strip()
    if not answer:
        return Path(rows[0]['path'])
    if answer.isdigit() and 1 <= int(answer) <= len(rows):
        return Path(rows[int(answer) - 1]['path'])
    say('Not a valid choice. Nothing was done.')
    return None


def describe_backup(backup):
    manifest = backup.manifest
    say(f"Backup: {Path(backup.path).name}")
    say(f"  Made {backup.created_at} with app {manifest.get('tool_version')} for "
        f"{manifest.get('model')} firmware {manifest.get('firmware')}")
    say('  Feature states saved in it:')
    for row in backup.features:
        say(f"    {tr(row['name'])}: {STATES.get(row['state'], row['state'])}")


def report(result):
    status = result.get('status')
    say()
    say(OUTCOMES.get(status, f'Result: {status}'))
    if result.get('error'):
        say('Reason: ' + tr(result['error']))
    preparation_report = result.get('close_preparation') or {}
    for check in preparation_report.get('checks', []):
        if check['id'] in preparation_report.get('completed', []):
            state = f"restored to \"{tr(check.get('target_label', ''))}\" and read back"
        elif check.get('status') == 'safe':
            state = 'confirmed, no change needed'
        else:
            state = 'not yet confirmed safe to turn off'
        say(f"  Camera setting - {tr(check.get('name', check['id']))}: {state}")
        if check.get('reason'):
            say('    ' + tr(check['reason']))
    plan = result.get('plan') or {}
    if status == 'dry-run' and plan.get('changed_indices'):
        say(f"It would write back {plan.get('changed_bytes', 0)} bytes in {plan.get('changed_sectors', 0)} sectors.")
    if plan.get('issues'):
        say(f"  {len(plan['issues'])} content or structure mismatch(es); details are in the operation records.")
    features = result.get('final_features') or plan.get('features') or []
    if features and status in ('dry-run', 'no-op') + SETTLED:
        say('Feature states after the restore:' if status in SETTLED else 'Feature states the restore would produce:')
        for feature in features:
            say(f"  {tr(feature['name'])}: {STATES.get(feature['state'], feature['state'])}")
    if status in SETTLED:
        say()
        say(POWER_CYCLE)
    if status not in ('dry-run', 'no-op') and result.get('run_directory'):
        say('Operation records: ' + result['run_directory'])
    if result.get('cleanup_warning'):
        say('Note: ' + tr(result['cleanup_warning']))


def connect_and_run(profile, backup, dry_run, backend_factory, progress):
    try:
        return execute_restore(profile, backend_factory(), backup, None, progress, dry_run)
    except DeviceError as exc:
        return {'status': 'error', 'error': str(exc)}


def main(argv=None, ask=input, backend_factory=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--backup', type=Path, help='backup file to restore (default: choose from the list)')
    parser.add_argument('--backup-dir', type=Path, help=f'where backups are kept (default: {default_backup_dir()})')
    parser.add_argument('--dry-run', action='store_true', help='stop after the dry run; never write')
    args = parser.parse_args(argv)

    profile = load_device_profile(load_catalog())
    source = choose_backup(args.backup_dir, args.backup, ask)
    if source is None:
        return 2
    try:
        backup = load_backup(source, profile)  # checked fully before the camera is contacted
    except (DeviceError, ValueError, KeyError, OSError) as exc:
        say('This backup file failed its checks and will not be used: ' + tr(str(exc)))
        return 2
    describe_backup(backup)
    say()
    if shared.checklist(profile, ask) is None:
        return 2

    progress = lambda message: say('  ' + tr(message))  # noqa: E731
    if backend_factory is None:
        from a6700_unlock.usb.backend import UsbBackend
        backend_factory = lambda: UsbBackend(profile, progress, capture=True)  # noqa: E731

    say('\nPass 1 of 2: dry run. Connecting and reading; nothing will be written.')
    result = connect_and_run(profile, backup, True, backend_factory, progress)
    report(result)
    if result.get('status') != 'dry-run':
        return 0 if result.get('status') == 'no-op' else 2
    if not (result.get('plan') or {}).get('changed_indices'):
        say(OUTCOMES['no-op'])
        return 0
    if args.dry_run:
        say('Stopping after the dry run, as asked.')
        return 0

    say()
    say('Pass 2 of 2 writes to the camera. Keep it on and connected, with a charged battery, until it finishes.')
    try:
        answer = ask(f'Type {CONFIRM_WORD} (in capitals) to write the backup back, or anything else to stop: ').strip()
    except (EOFError, KeyboardInterrupt):
        answer = ''
    if answer != CONFIRM_WORD:
        say('Stopped. Nothing was written.')
        return 2
    try:
        again = ask('Confirm the camera is still on, still connected by USB in Mass Storage mode, and no other '
                    'camera software is open. Type y: ').strip().lower()
    except (EOFError, KeyboardInterrupt):
        again = ''
    if again not in ('y', 'yes'):
        say('Stopped. Nothing was written.')
        return 2
    confirmed = preparation.guide(profile, preparation.STEP_IDS, interactive=False, emit=lambda _message: None)
    if not confirmed['ready']:
        say('This PC is no longer ready (see the checks above). Nothing was written.')
        return 2

    say('\nRestoring. Do not unplug the cable or turn the camera off.')
    result = connect_and_run(profile, backup, False, backend_factory, progress)
    report(result)
    return 0 if result.get('status') in SETTLED + ('no-op',) else 2


if __name__ == '__main__':
    raise SystemExit(main())
