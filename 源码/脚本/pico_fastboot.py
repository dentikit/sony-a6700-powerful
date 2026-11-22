"""English entry for the Pico route: load the read-only RAM fastboot program, then identify it.

Commands
  ports           list serial ports (the Pico is VID 2E8A)
  check-payload   verify the bundled read-only RAM program file; touches no hardware
  ping            check the Pico firmware version on --port; starts nothing
  arm             wait for the camera's cold start and send the RAM program once (read hardware guide first)
  identify        after moving the USB cable to the camera: confirm the read-only program and print partitions

The record stream and its timing come unchanged from 核心/注入入口.transmit();
only the messages are shown in English. Only the fixed read-only payload is accepted.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '源码'))
from 核心.记录协议 import boot_records, verified_payload  # noqa: E402

DEFAULT_PAYLOAD = ROOT / '已验证文件' / '相机只读引导.bin'
MESSAGES = {
    'ARMED：低基线通过。现在插入测试板、安装电池、相机拨ON。':
        'ARMED: baseline is LOW. Now plug the breakout into the camera, insert the battery, and switch the camera ON.',
    '检测到相机释放高电平，开始按顺序发送一次记录流。':
        'Camera power-up detected. Sending the record stream once...',
    '完整记录已发送一次。保持电池和ON，先从相机拔下整个测试板。':
        'All records sent once. Keep the battery in and the camera ON. First unplug the whole breakout from the camera.',
    '再把Pico数据线移到相机，随后检查Fastboot身份和分区表。':
        'Then move the USB cable from the Pico to the camera, and run "identify".',
}


def say(message, **kwargs):
    print(MESSAGES.get(message, message), **kwargs)


def records_for(path):
    return boot_records(verified_payload(path))


def cmd_ports(_args):
    from serial.tools import list_ports
    found = list(list_ports.comports())
    for port in found:
        ids = f'{port.vid:04X}:{port.pid:04X}' if port.vid is not None else '----:----'
        hint = '  <- looks like a Raspberry Pi Pico' if port.vid == 0x2E8A else ''
        print(f'{port.device:12} {ids}  {port.description}{hint}')
    if not found:
        print('No serial ports found. Is the Pico plugged in with a data cable?')


def cmd_check_payload(args):
    records = records_for(args.payload)
    print(f'RAM program verified: {args.payload.name}, {len(records)} records. No serial port or USB was used.')


def open_pico(port):
    import serial
    return serial.Serial(port, 115200, timeout=5, write_timeout=5, exclusive=True)


def cmd_ping(args):
    from 核心.Pico通信 import Pico
    with open_pico(args.port) as port:
        Pico(port).ping()
    print('Pico firmware a6700_pio_od 2 confirmed. Nothing was started.')


def cmd_arm(args):
    from 核心.Pico通信 import Pico
    from 核心.注入入口 import transmit
    if not 1 <= args.activation_timeout <= 3600:
        raise ValueError('--activation-timeout must be 1..3600 seconds')
    records = records_for(args.payload)
    print('Read the hardware guide first. Camera OFF, battery out for at least 5 seconds, '
          'breakout NOT plugged into the camera yet.')
    input('Keep the Pico connected to this PC by USB, with the breakout powered from Pico VBUS. '
          'Press Enter to start; power the camera only after you see ARMED: ')
    with open_pico(args.port) as port:
        transmit(Pico(port), records, args.activation_timeout, say=say)


def cmd_identify(args):
    sys.path.insert(0, str(ROOT / '源码' / '脚本'))
    from full_backup import find_libusb
    from 核心.设备查询 import connect, inspect
    with connect(find_libusb(args.libusb)) as device:
        print(json.dumps(inspect(device, table=True), ensure_ascii=False, indent=2))
    print('Read-only RAM program confirmed. You can run full_backup.py plan / run now.')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('ports', help='list serial ports')
    payload = commands.add_parser('check-payload', help='verify the RAM program file only')
    payload.add_argument('--payload', type=Path, default=DEFAULT_PAYLOAD)
    ping = commands.add_parser('ping', help='check the Pico firmware version')
    ping.add_argument('--port', required=True, help='the Pico serial port, e.g. COM5')
    arm = commands.add_parser('arm', help='send the read-only RAM program at camera cold start')
    arm.add_argument('--port', required=True, help='the Pico serial port, e.g. COM5')
    arm.add_argument('--payload', type=Path, default=DEFAULT_PAYLOAD)
    arm.add_argument('--activation-timeout', type=float, default=900, help='seconds to wait for the camera')
    ident = commands.add_parser('identify', help='confirm the RAM program over USB and print partitions')
    ident.add_argument('--libusb', type=Path, help='path to libusb-1.0 if it is not found automatically')
    args = parser.parse_args()
    handler = {'ports': cmd_ports, 'check-payload': cmd_check_payload, 'ping': cmd_ping,
               'arm': cmd_arm, 'identify': cmd_identify}[args.command]
    try:
        handler(args)
    except KeyboardInterrupt:
        print('\nStopped.')
        return 130
    except (RuntimeError, ValueError, OSError, TimeoutError) as exc:
        print(f'\nError: {exc}')
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
