"""Read-only full eMMC backup of a Sony A6700 over the Pico RAM fastboot route.

Commands
  plan      connect, check the RAM program identity, show what a full backup covers (reads no data)
  run       make (or --resume) a full backup into --out
  verify    check a backup folder against its fingerprints, without the camera
  assemble  join the user-area chunks into one image file

Run 'run' only after pico_fastboot.py has loaded the read-only RAM program and
'identify' succeeded. Nothing here writes to the camera.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from 核心.完整备份 import BackupError, assemble, build_plan, parse_partition_table, plan_bytes, run, verify  # noqa: E402


def find_libusb(explicit):
    """Use --libusb if given, else the DLL shipped by the libusb-package wheel (needed on Windows)."""
    if explicit:
        return explicit
    try:
        import libusb_package
    except ImportError:
        return None
    path = libusb_package.get_library_path()
    return Path(path) if path else None


def open_camera(libusb):
    from 核心.设备查询 import connect
    return connect(find_libusb(libusb))


def cmd_plan(args):
    from 核心.设备查询 import inspect, query
    with open_camera(args.libusb) as device:
        identity = inspect(device)
        lines = query(device, 'oem partition')['info']
    table = parse_partition_table(lines)
    plan = build_plan(table)
    print(f"RAM program: {identity['product']} ({identity['version']}) - read-only build confirmed")
    print('Partition table as reported by the camera:')
    for line in lines:
        print('  ' + line)
    print(f"A full backup reads {plan_bytes(plan) / 2**20:.0f} MiB in {len(plan)} files "
          f"(boot0, boot1, {len(plan) - 2} user-area chunks of up to 16 MiB), each read twice.")
    print('Leave at least that much free space plus 64 MiB on the destination drive.')
    print('Not covered: unpartitioned space after the last partition, and anything past the first '
          f"0x{table['boot_sectors']:x} sectors of each boot partition.")


def cmd_run(args):
    if not args.resume and args.out.exists() and any(args.out.iterdir()):
        raise BackupError(f'{args.out} is not empty. Choose a new folder, or add --resume to continue a backup.')
    def say(message):
        args.started = True
        print(message, flush=True)
    with open_camera(args.libusb) as device:
        run(device, args.out, resume=args.resume, say=say)
    print('Next: run "verify", then copy the whole folder to a second drive. Keep both copies private.')


def cmd_verify(args):
    return 0 if not verify(args.out) else 1


def cmd_assemble(args):
    if args.image.exists():
        raise BackupError(f'{args.image} already exists. Choose a new file name.')
    assemble(args.out, args.image)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    plan = commands.add_parser('plan', help='show what a full backup covers; reads no data')
    plan.add_argument('--libusb', type=Path, help='path to libusb-1.0 if it is not found automatically')
    make = commands.add_parser('run', help='make or resume a full backup')
    make.add_argument('--out', type=Path, required=True, help='new, empty folder for the backup')
    make.add_argument('--resume', action='store_true', help='continue an interrupted backup in --out')
    make.add_argument('--libusb', type=Path, help='path to libusb-1.0 if it is not found automatically')
    check = commands.add_parser('verify', help='check a backup folder without the camera')
    check.add_argument('--out', type=Path, required=True, help='backup folder to check')
    join = commands.add_parser('assemble', help='join user-area chunks into one image')
    join.add_argument('--out', type=Path, required=True, help='backup folder')
    join.add_argument('--image', type=Path, required=True, help='image file to create (must not exist)')
    args = parser.parse_args()
    handler = {'plan': cmd_plan, 'run': cmd_run, 'verify': cmd_verify, 'assemble': cmd_assemble}[args.command]
    try:
        return handler(args) or 0
    except KeyboardInterrupt:
        print('\nStopped. Everything read so far is saved; run again with --resume to continue.')
        return 130
    except (BackupError, RuntimeError, ValueError, OSError) as exc:
        print(f'\nError: {exc}')
        if getattr(args, 'started', False):
            print('Everything verified before this point is saved. Fix the problem, then run again with --resume.')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
