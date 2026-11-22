"""English wrapper for the app's feature-state backup over a normal USB cable (no Pico).

It runs the same steps as `a6700-unlock device backup`: the camera checklist,
a fresh read-only preflight, then a local .a6700backup archive of the fixed
sectors this app manages (feature fields plus structure guards). It never
writes to the camera. Messages come from the Windows app's English catalog.

Usage (from the 应用 folder, with its virtual environment active):
  python scripts/feature_backup.py --dry-run     check everything, write no file
  python scripts/feature_backup.py               make the backup
  python scripts/feature_backup.py --list        list backups on this PC

This is not a full flash image and cannot rescue a camera that won't boot.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'desktop' / 'windows'))

import i18n  # noqa: E402
from a6700_unlock import preparation  # noqa: E402
from a6700_unlock.backup import default_backup_dir, list_backups  # noqa: E402
from a6700_unlock.catalog import load_catalog  # noqa: E402
from a6700_unlock.device_profile import load_device_profile  # noqa: E402
from a6700_unlock.device_protocol import DeviceError  # noqa: E402
from a6700_unlock.operations import execute_backup  # noqa: E402

i18n._language = 'en'
tr = i18n.tr
STATES = {'enabled': 'unlocked', 'disabled': 'not unlocked', 'partial': 'partially unlocked', 'unknown': 'unknown'}
OUTCOMES = {
    'backup-completed': 'Feature backup saved. It holds only the sectors this app manages; it is not a full flash image.',
    'dry-run': 'Preflight finished. The camera is readable and compatible; no backup file was written.',
    'blocked': 'Compatibility check failed, so no backup was made. Nothing was written to the camera.',
    'cancelled': 'Cancelled. Nothing was written to the camera.',
}


def say(message=''):
    print(message, flush=True)


def checklist(profile, ask=input):
    """Show the environment checks and walk through the camera checklist in English."""
    report = preparation.assess(profile)
    say('Checks on this PC:')
    for check in report['environment']['checks']:
        say(f"  {'OK ' if check['ok'] else 'FIX'} {tr(check['name'])}: {tr(check['detail'])}")
        if not check['ok'] and check.get('remediation'):
            say('      How to fix: ' + tr(check['remediation']))
    if not report['environment']['ready']:
        say('Fix the items above first. The camera was not contacted.')
        return None
    say()
    say('Camera checklist (the app cannot read these settings, so confirm each one):')
    confirmed = []
    for number, step in enumerate(report['steps'], 1):
        say(f"\n{number}. {tr(step['title'])}")
        say('   ' + tr(step['instruction']))
        for menu in step['menu_guides']:
            say('   Menu: ' + tr(' → '.join(menu['path'])) + ' → ' + tr(menu['target']))
        if step.get('skip_label'):
            say('   If there is no such menu: ' + tr(step['skip_label']))
        try:
            answer = ask('   Done? Type y and press Enter (anything else stops here): ').strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ''
        if answer not in ('y', 'yes'):
            say('Stopped before connecting. Nothing was sent to the camera.')
            return None
        confirmed.append(step['id'])
    final = preparation.guide(profile, confirmed, interactive=False, emit=lambda _message: None)
    return final if final['ready'] else None


def summary(result):
    status = result.get('status')
    say()
    say(OUTCOMES.get(status, f'Result: {status}'))
    if result.get('error'):
        say('Reason: ' + tr(result['error']))
    features = result.get('features') or result.get('plan', {}).get('features', [])
    if features:
        say('Features found on the camera:')
        for feature in features:
            say(f"  {tr(feature['name'])}: {STATES.get(feature['state'], feature['state'])}")
    if result.get('backup_path'):
        say('Backup file: ' + result['backup_path'])
    if result.get('backup_sha256'):
        say('SHA-256:     ' + result['backup_sha256'])
    return 0 if status in ('backup-completed', 'dry-run') else 2


def list_local(directory):
    rows = list_backups(directory)
    if not rows:
        say(f'No feature backups in {directory or default_backup_dir()}.')
        return 0
    for row in rows:
        say(f"{row.get('created_at') or 'unknown date'}  {row['path']}")
    return 0


def main(argv=None, ask=input, backend_factory=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dry-run', action='store_true', help='check everything but write no backup file')
    parser.add_argument('--backup-dir', type=Path, help=f'where to save backups (default: {default_backup_dir()})')
    parser.add_argument('--list', action='store_true', help='list backups on this PC; does not contact the camera')
    args = parser.parse_args(argv)
    if args.list:
        return list_local(args.backup_dir)
    profile = load_device_profile(load_catalog())
    if checklist(profile, ask) is None:
        return 2
    say('\nConnecting. Keep the camera on and the cable in until this finishes.')
    progress = lambda message: say('  ' + tr(message))  # noqa: E731
    if backend_factory is None:
        from a6700_unlock.usb.backend import UsbBackend
        backend_factory = lambda: UsbBackend(profile, progress, capture=True)  # noqa: E731
    try:
        result = execute_backup(profile, backend_factory(), args.backup_dir, None, progress, args.dry_run)
    except KeyboardInterrupt:
        say('\nStopped. Backups never write to the camera; you can simply run this again.')
        return 130
    except DeviceError as exc:
        say('\nStopped: ' + tr(str(exc)))
        say('Nothing was written to the camera. Fix the problem and run this again.')
        return 2
    return summary(result)


if __name__ == '__main__':
    raise SystemExit(main())
