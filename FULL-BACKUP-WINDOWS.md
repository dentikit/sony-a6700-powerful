# Full backup before you modify your A6700 (Windows)

This makes a raw copy of the camera's internal flash **before** you unlock anything, using the project's existing Pico route. It is the first half of a recovery plan: without a backup there is nothing to restore later.

**What it does:** loads the project's read-only RAM program into the camera through a hidden boot mode in the chip's ROM, then reads boot0, boot1 and the whole user area over USB. Every 16 MiB chunk is read twice and must match byte for byte before it is saved. The RAM program has no flash-writing code, and the tool refuses to run against any other program.

**What it does not do (yet):**
- **No restore.** There is no tool to write a backup back, and restoring has never been tested on a real camera.
- **Not every byte of the chip.** It covers boot0 and boot1 (the first 0x2000 sectors of each, all the RAM program can address) and the user area up to the end of the last partition. Unpartitioned space after that is not covered.
- **Same camera only.** Images are raw as stored, so encrypted partitions stay encrypted and only fit the camera they came from.

> **Honest status:** the read protocol is the project's existing one, and the maintainer has done these reads by hand on a Mac. The Windows tools here were tested against a simulated camera, not a real one. The hardware step carries its own risk: a wiring mistake can damage the camera's USB port.

## 1. Parts

| Part | Notes |
|---|---|
| Raspberry Pi Pico (RP2040) | The original Pico. A Pico 2 has not been validated. Pre-soldered headers make it easier. |
| USB-C 24-pin **male** breakout board | Must expose VBUS, GND, A5 (CC1), B5 (CC2) and A7 (D−, next to A8). |
| 56 kΩ resistor × 2 | One each for CC1 and CC2 |
| 470 Ω resistor × 1 | Between GP28 and D− |
| Breadboard, short jumper wires | Short wires are better |
| Multimeter | For the checks before the camera is ever connected |
| USB data cable | Must carry data, not charge-only |

## 2. Wiring

| From (Pico) | Through | To (USB-C breakout) |
|---|---|---|
| **VBUS** (physical pin 40) | direct | VBUS |
| VBUS | 56 kΩ | A5 (CC1) |
| VBUS | 56 kΩ | B5 (CC2) |
| GND (e.g. physical pin 38) | direct | GND |
| **GP28** (physical pin **34**, not pin 28) | 470 Ω | A7 (D−) |

- GP28 is a GPIO number. On the Pico it is physical pin 34. **Never** connect 5 V directly to GP28.
- Use **VBUS** (pin 40), not VSYS or 3V3. Leave the Pico's 3V3 and every other breakout pin unconnected.
- A5 and B5 each go to 5 V **through** their own resistor. Never short them together.

**Before the camera is ever connected**, with the Pico plugged into the PC and the breakout connected to nothing: VBUS, A5 and B5 should each read close to 5 V against GND, with no shorts anywhere. Insulate any exposed contacts.

## 3. Software (once)

In PowerShell (not as administrator):

```powershell
cd ~\sony-a6700-powerful
git fetch origin
git switch full-backup
py -3.13 -m venv .venv-backup
.venv-backup\Scripts\Activate.ps1
pip install -r 源码\requirements-full-backup.txt
python 源码\脚本\pico_fastboot.py check-payload
```

The last line should say `RAM program verified ... 45 records`. Copy-paste the commands, because some folder names are Chinese.

Next time, you only need the `cd` and `Activate.ps1` lines.

## 4. Flash the Pico (once)

1. With the Pico **not** connected to the breakout or camera, hold its **BOOTSEL** button and plug it into the PC.
2. A drive called `RPI-RP2` opens. Copy `已验证文件\Pico-v2.uf2` from the project folder onto it. The Pico restarts by itself.
3. Find its port, then check the firmware:

```powershell
python 源码\脚本\pico_fastboot.py ports
python 源码\脚本\pico_fastboot.py ping --port COM5
```

Use the port marked `looks like a Raspberry Pi Pico` instead of `COM5`. Expect `Pico firmware a6700_pio_od 2 confirmed`.

## 5. Load the read-only program (every session)

The order matters.

1. Camera **OFF**, normal USB unplugged, battery out for at least 5 seconds. The breakout is **not** in the camera.
2. The Pico is connected to the PC by one USB data cable. It powers the breakout; no other power supply.
3. Run this and press Enter when asked:

   ```powershell
   python 源码\脚本\pico_fastboot.py arm --port COM5
   ```

4. **Only after you see `ARMED`:** plug the breakout into the battery-less camera, insert the battery, switch the camera ON.
5. Wait for `RECORD 45/45 ACK`.
6. Keep the battery in and the camera ON. **First** unplug the whole breakout from the camera. **Then** move the USB cable from the Pico to the camera. The PC end stays where it is.

## 6. USB driver (first time only)

Windows has no driver for the RAM program yet. With the camera connected as in step 5:

1. Download Zadig from https://zadig.akeo.ie/ and open it.
2. **Options → List All Devices**.
3. Pick the device whose USB ID shows **18D1 4EE0**. Nothing else.
4. Driver: **WinUSB**. Click **Install Driver**.

Don't disable driver signing or Windows security for this.

## 7. Identify, plan, back up

```powershell
python 源码\脚本\pico_fastboot.py identify
python 源码\脚本\full_backup.py plan
python 源码\脚本\full_backup.py run --out D:\A6700-backup-2026-11-22
```

- `identify` must show `A6700-AP-read-candidate`. If it doesn't, stop.
- `plan` shows the partition table and the size (roughly 1.9 GB). It reads no data.
- `run` needs a **new, empty** folder on a drive with enough free space. Keep the camera ON and untouched until it says `Backup complete`. Progress shows speed and time left.

**If it stops partway** (cable bumped, battery low, Ctrl+C): everything verified so far is saved. Do **not** start the camera normally in between, because normal use changes the flash. Repeat step 5 if the camera was turned off, then:

```powershell
python 源码\脚本\full_backup.py run --out D:\A6700-backup-2026-11-22 --resume
```

On resume it re-reads the last saved chunk and compares it. If the camera no longer matches (different camera, or it was used normally), it refuses and you start a new folder.

## 8. Check and store it

```powershell
python 源码\脚本\full_backup.py verify --out D:\A6700-backup-2026-11-22
```

Then copy the whole folder to a **second** drive and run `verify` on the copy too. Keep both copies private: they contain data unique to your camera. Never upload them.

## 9. Leave the RAM program

Camera OFF → disconnect every cable → battery out for at least 5 seconds → power on with the battery only.

## Troubleshooting

| Problem | What to check |
|---|---|
| Pico version mismatch | Board type, the UF2 file, the port. Don't skip `ping`. |
| `Baseline already HIGH` | The breakout must not be in the camera when you press Enter. Check the shared GND. Power the camera only after `ARMED`. |
| Records stop before 45/45 | Stop, disconnect, do a full cold start (battery out). Never continue a half-sent stream. |
| All sent, but no USB device | Unplug the breakout **before** moving the cable. Keep the battery in and the camera ON. Use a data cable. |
| `found 0` devices | Driver (step 6), cable, and that the camera is still ON |
| `two reads disagreed` | Stop. Reseat the cable, reload the program (step 5), then `--resume`. |
| `different program or partition table` | Start a new backup folder. Don't mix backups. |

Files: `源码\脚本\pico_fastboot.py`, `源码\脚本\full_backup.py`, `源码\核心\完整备份.py`, simulated-camera tests in `源码\测试\`.
