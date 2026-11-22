"""Powerful6700 tools menu: one place for the app, backups, restore and builds.

Start it by double-clicking A6700-Tools.cmd. It runs with your normal Python,
creates the app's environment the first time, and starts every tool with that
environment's Python, so PowerShell script settings don't matter.
"""
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent
APP = ROOT / '应用'
VENV = APP / '.venv'
WINDOWS = sys.platform == 'win32'
VENV_PY = VENV / ('Scripts/python.exe' if WINDOWS else 'bin/python')
VENV_PYW = VENV / 'Scripts/pythonw.exe'
REQUIREMENTS = APP / 'desktop/windows/requirements.txt'
BACKUPS = Path.home() / 'Documents' / 'A6700 Backups'
GUIDE_URL = 'https://github.com/dentikit/sony-a6700-powerful/blob/full-backup/FULL-BACKUP-WINDOWS.md'
ENV = dict(os.environ, PYTHONIOENCODING='utf-8')

MENU = [
    ('App', None),
    ('1', 'Open Powerful6700 (English)'),
    ('2', 'Open in demo mode (no camera needed)'),
    ('3', 'Build the Windows .exe'),
    ('Feature backup  (normal USB cable, no Pico)', None),
    ('4', 'Check camera and backup readiness (dry run, saves nothing)'),
    ('5', 'Make a feature backup'),
    ('6', 'List my feature backups'),
    ('7', 'Restore a feature backup (dry run first, then asks)'),
    ('8', 'Open my backups folder'),
    ('Full backup  (Pico)', None),
    ('9', 'Open the Pico full-backup guide'),
    ('Maintenance', None),
    ('10', 'First-time setup or repair (Python packages)'),
    ('11', 'Get the latest version from GitHub'),
    ('12', 'Add a desktop shortcut to this menu'),
    ('0', 'Exit'),
]


def pause():
    try:
        input('\nPress Enter to return to the menu...')
    except EOFError:
        pass


def branch():
    try:
        name = subprocess.run(['git', 'branch', '--show-current'], cwd=ROOT, capture_output=True, text=True,
                              timeout=10).stdout.strip()
        return name or 'unknown'
    except (OSError, subprocess.SubprocessError):
        return 'git not found'


def environment_ready():
    if not VENV_PY.is_file():
        return False
    check = subprocess.run([str(VENV_PY), '-c', 'import usb, libusb_package, PySide6, cryptography'],
                           cwd=APP, capture_output=True, env=ENV)
    return check.returncode == 0


def setup(force=False):
    """Create the app environment and install its pinned packages."""
    if not force and environment_ready():
        print('The app environment is already set up.')
        return True
    if not VENV_PY.is_file():
        print('Creating the app environment (one time)...')
        if subprocess.call([sys.executable, '-m', 'venv', str(VENV)], env=ENV) != 0:
            print('Could not create the environment. Is Python 3.13 installed?')
            return False
    print('Installing the pinned packages. This can take a few minutes the first time...')
    code = subprocess.call([str(VENV_PY), '-m', 'pip', 'install', '--disable-pip-version-check',
                            '-r', str(REQUIREMENTS)], cwd=APP, env=ENV)
    if code != 0:
        print('Package installation failed. Check your internet connection and try option 10 again.')
        return False
    print('Setup complete.')
    return True


def need_environment():
    if environment_ready():
        return True
    print('The app environment is not set up yet. Setting it up now...')
    return setup()


def run_tool(*arguments):
    """Run a script with the app environment's Python and wait for it."""
    if not need_environment():
        return 2
    return subprocess.call([str(VENV_PY), *arguments], cwd=APP, env=ENV)


def open_app(demo):
    if not need_environment():
        return
    python = VENV_PYW if WINDOWS and VENV_PYW.is_file() else VENV_PY
    command = [str(python), 'desktop/windows/main.py'] + (['--demo'] if demo else [])
    subprocess.Popen(command, cwd=APP, env=ENV)
    print('Powerful6700 is opening in its own window.' + (' Demo mode never touches a camera.' if demo else ''))
    if not demo:
        print('Camera mode only checks this PC until you prepare and connect a camera.')


def open_path(path):
    if WINDOWS:
        os.startfile(str(path))  # noqa: S606 - opens a folder or file the user asked for
    else:
        print(path)


def fetch_pinned(name, hash_field, folder, opener=urllib.request.urlopen):
    """Download a pinned source archive once and check it against the project's SHA-256 pin."""
    pins = json.loads((APP / 'desktop/runtime-dependencies.json').read_text(encoding='utf-8'))
    pin = pins[name]
    target = folder / pin['filename']
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == pin[hash_field]:
        return target
    print(f"Downloading {pin['filename']}...")
    data = opener(pin['url'], timeout=120).read()
    if hashlib.sha256(data).hexdigest() != pin[hash_field]:
        raise RuntimeError(f"{pin['filename']} did not match its pinned SHA-256; not used.")
    folder.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def build_exe(opener=urllib.request.urlopen):
    if not need_environment():
        return
    dist = APP / 'dist'
    try:
        libusb = fetch_pinned('libusb', 'sha256', dist, opener)
        libwdi = fetch_pinned('libwdi', 'source_sha256', dist, opener)
    except (OSError, RuntimeError) as exc:
        print(f'Could not get the build sources: {exc}')
        return
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    output = dist / 'Windows' / f'Powerful6700-{stamp}'
    print(f'Building into {output} (a few minutes; lots of output is normal)...')
    code = subprocess.call([str(VENV_PY), 'scripts/build_windows_app.py', '--libusb-source', str(libusb),
                            '--libwdi-source', str(libwdi), '--output', str(output)], cwd=APP, env=ENV)
    if code == 0:
        print(f'Done. Double-click Powerful6700.exe in:\n  {output}\nKeep the whole folder together.')
        open_path(output)
    else:
        print('The build failed. Scroll up for the first error, or send it to Claude.')


def update():
    if shutil.which('git') is None:
        print('Git is not installed, so updating is not available.')
        return
    print(f'Updating branch {branch()} from GitHub...')
    if subprocess.call(['git', 'pull', '--ff-only'], cwd=ROOT) == 0:
        print('Up to date. If packages changed, run option 10.')
    else:
        print('Update stopped. If you edited files here, Git will say which ones.')


def desktop_shortcut():
    if not WINDOWS:
        print('Desktop shortcuts are only made on Windows.')
        return
    launcher = str(ROOT / 'A6700-Tools.cmd').replace("'", "''")
    folder = str(ROOT).replace("'", "''")
    script = ("$d=[Environment]::GetFolderPath('Desktop');"
              "$s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'Powerful6700 Tools.lnk'));"
              f"$s.TargetPath='{launcher}';$s.WorkingDirectory='{folder}';"
              "$s.Description='Powerful6700 tools menu';$s.Save()")
    code = subprocess.call(['powershell', '-NoProfile', '-NonInteractive', '-Command', script])
    print('Shortcut "Powerful6700 Tools" added to your desktop.' if code == 0 else 'Could not create the shortcut.')


def show_menu():
    print('\n' + '=' * 62)
    print(f'  Powerful6700 tools        branch: {branch()}')
    print('=' * 62)
    for key, label in MENU:
        if label is None:
            print(f'\n {key}')
        else:
            print(f'  {key:>2}  {label}')


ACTIONS = {
    '1': lambda: open_app(False),
    '2': lambda: open_app(True),
    '3': build_exe,
    '4': lambda: run_tool('scripts/feature_backup.py', '--dry-run'),
    '5': lambda: run_tool('scripts/feature_backup.py'),
    '6': lambda: run_tool('scripts/feature_backup.py', '--list'),
    '7': lambda: run_tool('scripts/feature_restore.py'),
    '8': lambda: (BACKUPS.mkdir(parents=True, exist_ok=True), open_path(BACKUPS)),
    '9': lambda: webbrowser.open(GUIDE_URL),
    '10': lambda: setup(force=True),
    '11': update,
    '12': desktop_shortcut,
}


def main(ask=input):
    if not APP.is_dir():
        print('This menu must stay in the sony-a6700-powerful folder next to the 应用 folder.')
        return 1
    while True:
        show_menu()
        try:
            choice = ask('\nChoose a number and press Enter: ').strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if choice == '0':
            return 0
        action = ACTIONS.get(choice)
        if action is None:
            print('Not a menu number.')
            continue
        try:
            action()
        except KeyboardInterrupt:
            print('\nStopped.')
        pause()


if __name__ == '__main__':
    raise SystemExit(main())
