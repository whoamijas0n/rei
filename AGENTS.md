# AGENTS.md — REI

REI is a pocket diagnostic hub for **Raspberry Pi Zero 2 W** with a 1.3" SH1106 OLED
(128x64, 1-bit), a 5-way joystick and 3 physical buttons. Python, no build step, no
package manager, no CI.

- This is the **single** project guide: design constraints (hardware BOM, boot-time
  requirements, data contract) plus the verified operational facts below. When design and
  code disagree, read the code and update this file.
- All user-facing strings, docstrings and most comments are **in Spanish**. Identifiers,
  logger names and API paths are English. Keep new UI strings Spanish.
- `main` is far behind the active `endpoint` branch (it lacks `core/ducky.py`,
  `core/web_server.py`, `core/usb_modes.py`, `plugins/endpoints/` and the whole test
  suite). Check `git branch` before assuming a file exists on `main`.

## Hardware (BOM) and boot-time requirements

| Component | Model / chipset | Constraint |
| :--- | :--- | :--- |
| SBC | Raspberry Pi Zero 2 W | Quad-core BCM2710A1 @ 1 GHz, **512 MB RAM** (424 MB usable). |
| I/O HAT | Waveshare ETH/USB HUB HAT | RTL8152B 10/100 Ethernet + 3-port USB 2.0 hub on the OTG port. |
| Display | 1.3" SH1106 OLED | 128x64, 1-bit, SPI0 (I2C `0x3C` fallback), `DC=24`, `RST=25`, `rotate=2`. |
| Controls | 5-way joystick + 3 buttons | `gpiozero` with interrupts, 80 ms debounce (see pin map). |
| UPS | PiSugar 3 | 1200 mAh Li-Po, I2C bus 1 at `0x57`, RTC + safe-shutdown button. |
| Console | USB→RJ45 Cisco cable | FTDI FT232R / CP2102 → `/dev/ttyUSB0`, 9600–115200 bps. |
| Storage | 32 GB industrial microSD | `/` on **OverlayFS (read-only)**, `/data` ext4 persistent, `/tmp` + `/run` tmpfs. |

SPI0, I2C1 and USB OTG are **boot-time** peripherals: the firmware only reads `config.txt`
at boot, so these `dtparam`/`dtoverlay` lines need a reboot to exist.

| Boot parameter | Device node | Consumer |
| :--- | :--- | :--- |
| `dtparam=spi=on` | `/dev/spidev0.0` | SH1106 OLED + splash |
| `dtparam=i2c_arm=on` | `/dev/i2c-1` | PiSugar 3 UPS (`0x57`) |
| `dtoverlay=dwc2,dr_mode=peripheral` | `/dev/hidg0` | HID injection / USB gadget |

The lines must sit in the **unfiltered region** of `config.txt` (before the first
`[section]` header); appended at the end they land in the last section (`[cm5]`, `[pi5]`, …)
and are silently ignored on a Zero 2 W. **Keep SPI1 disabled** — GPIO19/20/21/26 are used
by the joystick/buttons.

### USB modes (`core/usb_modes.py`)

`UTILIDADES → MODO USB` switches between **normal** (host/storage, gadget service off) and
**keyboard HID** (`dwc2` peripheral + `libcomposite` with `functions/hid.usb0` and an
RNDIS/ECM network function). The change regenerates `/usr/local/bin/usb_gadget.sh`,
enables `usb_gadget.service` and may require a reboot or a UDC reload. On the Zero 2 W the
single OTG port means HID gadget and USB-keyboard host mode are mutually exclusive.

## Commands

```bash
# Tests (run from repo root)
python3 -m unittest discover -s tests -v

# Single test file / single test
python3 -m unittest tests.test_ducky -v
python3 -m unittest discover -s tests -p "test_ducky.py"
python3 -m unittest tests.test_gemini.TestGeminiAnalyzer.test_local_fallback_analysis_ok

# Run the app without touching real hardware (see Dry-run below)
REI_DRY_RUN=1 python3 main.py

# Install / uninstall as root (systemd autostart)
sudo ./install.sh            # interactive menu
sudo ./install.sh --install
sudo ./install.sh --uninstall
```

There is **no linter, formatter, type-checker or CI workflow** configured (no
`pyproject.toml`, no `.github/`). Don't invent one; match surrounding style.

`tests/test_ui.py` imports PIL at module level, so the whole module errors out without
`Pillow` installed — the other 97 tests pass without it. Install `requirements.txt` (or
at least `Pillow`) before trusting a test run. The QR tests additionally need
`qrcode[pil]`; without it they fail with `_qr_image is None`.

## Where things actually live

| Path | Contents |
| :--- | :--- |
| `main.py` | `REIApp` — entrypoint, plugin registration, **menu tree**, 30 FPS loop |
| `core/plugins.py` | **All** system/network plugins (IP, Wi-Fi, battery, Cisco, SNMP, RNDIS, Vault, power, updates) |
| `core/interfaces.py` | `IDiagnosticPlugin`, `DiagnosticResult`, `DiagnosticMetric`, `Severity`, `DiagnosticStatus` |
| `core/manager.py` | `DiagnosticManager` — thread pool + result queue |
| `core/web_server.py` | FastAPI/uvicorn telemetry server + mobile HTML reports |
| `core/gemini_analyzer.py` | Gemini client with local heuristic fallback |
| `core/ducky.py` | HID scancode tables (US + ES/ISO dead keys) and `/dev/hidg0` writer |
| `core/usb_modes.py` | USB gadget ConfigFS script generator |
| `core/wifi.py` | `nmcli` / `wpa_cli` Wi-Fi scan + connect |
| `core/pisugar.py` | PiSugar 3 UPS telemetry (I2C / sysfs / socket / simulated) |
| `core/serial_console.py` | Serial port discovery (`/dev/ttyUSB*`, `/dev/ttyACM*`), auto-baud & passive vendor detection |
| `core/errors.py` | `NoticeLevel`, `FormattedNotice`, `format_oled_error` — traducción de errores a avisos OLED |
| `plugins/endpoints/` | HID endpoint plugins: `hid_windows.py`, `hid_linux.py` |
| `plugins/switches/` | Switch CLI profiles (`base.py`, `cisco_ios.py`, `tplink_jetstream.py`, `generic_serial.py`) |
| `ui/display.py` | `ScreenManager`, `HeroCardDeckView`, `DetailCardView`, `NoticeView`, `UpdateProgressView`, `QRCodeView` |
| `ui/icons.py` | `IconRenderer` — librería de iconos pixel-art 20x20 monocromáticos cacheados |
| `ui/theme.py` | Sistema visual OLED (cabecera/pie unificados, ajuste de texto en píxeles, badges, spinner, scrollbar) |
| `ui/input_handler.py` | GPIO joystick/buttons + `USBKeyboardListener` (evdev + stdin) |
| `ui/keyboard_view.py` | Virtual on-screen keyboard (`VirtualKeyboardInputView`) |
| `ui/keyboard_layouts.py` | USB keyboard layout detection + US/ES/Latam scancode maps |
| `ui/wifi_password_view.py` | `WiFiPasswordView` — USB-keyboard Wi-Fi password entry |
| `ui/console_auth_view.py` | `ConsoleCredentialView` — USB-keyboard console serial credentials (user/password/optional enable) |
| `install.sh` | APT packages, `.venv`, systemd units, USB gadget setup; `do_install` / `do_uninstall` |
| `splash.py` | Standalone early-boot splash (runs as `rei-splash.service`) |

**These do not exist** (older docs claim them): `plugins/network/`,
`plugins/base_plugin.py`, `config/credentials.json`. Network plugins live in
`core/plugins.py`; `VaultPlugin` is a hardcoded stub.

`main.py::_build_menu_hierarchy()` is the single source of truth for the menu tree —
edit it there, never in `ui/display.py`.

## Data contract (`core/interfaces.py`)

All plugins return a `DiagnosticResult`. Key fields: `plugin_name` (must equal `plugin.name`
for view routing), `status` (`DiagnosticStatus`), `overall_status` (`Severity`), `summary`,
`details: List[str]` (rendered by `DetailCardView`),
`metrics: Union[List[DiagnosticMetric], Dict[str, Any]]`, `raw_output`, `ai_analysis`,
`metadata`, `timestamp`, `elapsed_seconds`. `Severity` is `OK` / `INFO` / `WARNING` /
`CRITICAL`. `core/interfaces.py` is the source of truth — read it before changing a model.

## Stubs vs. real implementations

`core/plugins.py` contains several plugins that are **stubs**: they `time.sleep()` and
return literal strings without touching hardware — `CiscoSSHPlugin`,
`SNMPScanPlugin`, `WindowsRNDISPlugin`, `LinuxSSHPlugin`, `VaultPlugin`. `scrapli`,
`pysnmp-lextudio` and `pypsrp` are in `requirements.txt` but **unused**.

Real I/O happens in: `core/wifi.py`, `core/web_server.py`, `core/gemini_analyzer.py`,
`core/ducky.py`, `core/usb_modes.py`, `core/pisugar.py`, `core/serial_console.py` +
`plugins/switches/` (`CiscoSerialPlugin` via `pyserial` + `netmiko`), and the update plugins
(`apt-get` / `git pull`). Don't assume a plugin talks to the network because its name
suggests it — read its `run()`.

## Library policy

Do not substitute libraries when implementing the network stubs:

- Consoles / SSH: `netmiko` (4.3+) or `scrapli`. Never raw `paramiko` without pagination.
- Windows remoting: `pypsrp`. Never `pywinrm`.
- SNMP: `pysnmp-lextudio`. Never legacy `pysnmp` 4.4.x.
- AI: `google-genai`; the current model is `gemini-3.5-flash` (`DEFAULT_MODEL`).
- HID: the native `/dev/hidg0` writer in `core/ducky.py` — no external HID library.
- Graphics / QR: `luma.oled` (`sh1106`), `Pillow`, `qrcode[pil]`.

Every new dependency goes in `requirements.txt`, gets installed into `.venv`, and its
import stays guarded so the app still degrades gracefully (see "Optional hardware
dependencies").

## Threading contract

`main.py::run()` is a fixed 30 FPS loop: consume input → poll results → render.
**Never** block it with sockets, subprocesses, HTTP calls, long sleeps, or
`/dev/hidg0` writes.

- `DiagnosticManager(max_workers=2)` runs plugins in worker threads; results arrive via
  `poll_result()` and are dispatched in `_process_background_results()`.
- `execute_async(..., on_progress=cb)` injects `progress_callback` into the plugin's
  `kwargs`. A plugin that wants progress must declare it explicitly:
  `def run(self, progress_callback: Optional[Callable] = None, **kwargs)`.
- Results are routed to views by **plugin name equality**
  (`plugin.name == result.plugin_name`). The HID plugins build a dynamic name
  (`f"WIN {category[:12]} ({layout.upper()})"`) — changing that format silently breaks
  result routing.
- `UpdateProgressView` swallows *all* input while `is_running` is set. That is how
  "blocked" screens are implemented; use it rather than adding ad-hoc guards.
- Worker callbacks (`on_complete`, direct `_executor.submit` workers) must **not** touch
  `ScreenManager`. Wrap the transition in `REIApp._schedule_ui(callable)`, which queues it
  in `self._ui_callbacks` for the 30 FPS loop to drain.

## USB keyboard password flow (Wi-Fi)

Secured Wi-Fi networks open `ui/wifi_password_view.py::WiFiPasswordView` instead of the
virtual keyboard: the user types on a physical USB keyboard.

- Keys: `ENTER` / `KEY1` / joystick `PRESS` submits, `KEY2` / `Tab` toggles password
  visibility, `BACKSPACE` deletes, `KEY3` / `ESC` / `BACK` cancels back to the menu.
- `ui/keyboard_layouts.py::detect_system_layout()` resolves the layout in this order:
  `settings.json` → `keyboard_layout` (unless `auto`) → `REI_KEYBOARD_LAYOUT` →
  `/etc/default/keyboard` `XKBLAYOUT` → `/etc/vconsole.conf` `KEYMAP` → `us`.
  Supported layouts: `us`, `es`, `latam` (AltGr + Shift/CapsLock, dead keys resolved
  through the composed-character path).
- `is_real_keyboard_capabilities()` decides whether an evdev node is a real keyboard
  (full alphabet + Enter/Space/Backspace/Shift) and rejects false positives such as
  `vc4-hdmi` and consumer-control/mouse nodes.
- Hot-plug: `USBKeyboardListener` rescans `/dev/input` every 2 s
  (`SCAN_INTERVAL_CONNECTED_S`) and `request_keyboard_rescan()` forces one when the prompt
  opens. The view polls availability every frame through its `keyboard_status` callback,
  wired to `input_handler.has_typing_input`.
- The Zero 2 W has one OTG port: keyboard host mode and HID gadget mode (`/dev/hidg0`)
  are mutually exclusive. `main.py` passes `usb_gadget_active` from
  `_is_usb_gadget_blocking_host()` and the view then prompts `USB en modo HID...`; it
  never switches modes automatically.
- The input loop forwards typed characters only to views that accept text —
  `WiFiPasswordView`, `KeyboardInputView`, or anything with `accepts_text_input = True` —
  passing `char=self.input_handler.get_last_char()`.
- Every string drawn on the panel passes through `core/parser.py`
  (`sanitize_oled_text`, `shorten_oled_label`, `wrap_oled_lines`): the bitmap font has no
  accented glyphs.
- Passwords never reach the logs or the OLED: `WiFiManager._sanitize_error()` replaces raw
  `nmcli`/`wpa_cli` stderr with a catalog message (`WiFiErrorCode`). Note that the password
  itself is passed as an `nmcli` argument, so it is visible in `ps` while connecting.

## Endpoint (HID) flow

The injected payload is a **micro-stager**, not the full script:

1. REI types `powershell -w h -NoProfile -NonInteractive -ep bypass -EncodedCommand <b64>`
   (Linux: a `curl|wget ... |sh` one-liner) into the target.
2. The target downloads the real script from REI's own server —
   `GET /w/{CATEGORY}` (Windows) or `GET /l/{CATEGORY}` (Linux) — and executes it.
3. The script POSTs telemetry back to `POST /api/v1/endpoint/report`.

Canonical categories (see `normalize_category` in `plugins/endpoints/hid_windows.py`):
`RED`, `HARDWARE`, `MALWARE`, `LOGS`, `COMPLETO`. Anything else collapses to `COMPLETO`.

**Race guard:** capture `web_server.get_report_watermark()` *before* injecting, then
`wait_for_report(watermark=...)`. A report that arrives while the injection is still
typing must not be mistaken for the current run.

## Dry-run / simulation

`REI_DRY_RUN=1` is the master switch — it stubs HID injection, USB gadget scripting,
poweroff/reboot, apt/git updates, serial console probing/collection and Wi-Fi scan/connect.
Narrower overrides: `REI_MOCK_POWER=1`, `REI_MOCK_WIFI=1`, `REI_MOCK_UPDATES=1`,
`REI_MOCK_SERIAL=1` (with optional `REI_MOCK_SERIAL_SCENARIO`).

**Always** use `REI_DRY_RUN=1` when running `main.py` on a non-Pi machine; otherwise the
update plugins will really run `apt-get` and `git pull`, and the power plugins can really
shut the machine down.

## Configuration

`config/settings.json` is gitignored and **not committed**. Only
`config/settings.example.json` exists — copy it to `config/settings.json` to run with
real settings.

- `main.py::_load_settings()` looks at `config/settings.json` and `settings.json` only
  (it does *not* read `settings.local.json`).
- `core/gemini_analyzer.py` additionally understands `.env` and
  `config/settings.local.json`, and the `GEMINI_API_KEY` / `GEMINI_MODEL` env vars.
  **But `main.py` always passes `api_key=gemini_cfg.get("api_key")` explicitly**, which
  bypasses that fallback. Put the Gemini key in `config/settings.json` →
  `gemini.api_key`; setting `GEMINI_API_KEY` alone will not authenticate.
- Without a key, `GeminiDiagnosticAnalyzer` silently returns the local heuristic analysis
  (`summary` / `overall_status` / `root_causes` / `action_plan`) instead of calling the API.

## Optional hardware dependencies

The app degrades instead of crashing: no `luma` → virtual framebuffer; no `FastAPI` →
stdlib `http.server` fallback (same core routes, but **single-threaded** — it serializes
requests, unlike the uvicorn path); no `gpiozero` → no joystick/buttons; no `evdev` → no
USB keyboard. When adding a dependency, keep the import guarded.

`USBKeyboardListener` reads **stdin** when it is an interactive TTY, which drives the UI
without hardware: arrow keys = joystick, `Enter` = PRESS, `Tab` = KEY2, `Esc` = ESCAPE,
`Backspace` = BACKSPACE.

## OLED rendering conventions

- `IconRenderer.draw_icon` (defined in `ui/icons.py`, re-exported from `ui/display.py`)
  renders cached 20x20 monochrome pixel-art bitmaps (`_ICON_SPRITES` + `_ICON_ALIASES`);
  an unknown name silently falls back to `DEFAULT`. List valid names with
  `IconRenderer.list_icons()`.
- Unified chrome geometry (`ui/theme.py`): 1px border `(1,1)-(126,62)`, unified top
  header bar `y=2..12` + divider `y=13`, dotted footer divider `y=51` + hints `y=52..61`.
- Hero card geometry: clean top area without menu-name header bar, pagination dots
  in top-right (or `N/M` if >10), sleek 1px-stroke 20x20 icon centred at `(64, 24)`
  with `< >` side chevrons when >1 card, uppercase title centred at `y=46` fitted by
  real pixel width.
- `KeyboardInputView` is an alias of `VirtualKeyboardInputView`; both are re-exported
  from `ui/display.py` and `ui/keyboard_view.py`.

## Hardware pin map (`ui/input_handler.py::DEFAULT_PINS`)

BCM numbering for the Waveshare 1.3" OLED HAT: `JOY_UP=6`, `JOY_DOWN=19`,
`JOY_LEFT=5`, `JOY_RIGHT=26`, `JOY_PRESS=13`, `KEY1=21`, `KEY2=20`, `KEY3=16`.
Debounce is 80 ms. The OLED itself is SPI (`DC=24`, `RST=25`, `rotate=2`) with an I2C
fallback at `0x3C`; the PiSugar 3 UPS is on I2C bus 1 at `0x57`. In the UI, `KEY3` is
"back" (`pop_view`) and `KEY2` is the secondary action (e.g. toggle QR URL).

## Working with the Raspberry Pi (target device)

The Pi Zero 2 W is the **deployment target**, not the dev machine. It runs
Raspberry Pi OS 32-bit (`armv7l`), 424 MB RAM. OpenCode only ships `x64` and
`arm64` binaries, so **opencode cannot run on the Pi** — it runs on the
workstation and drives the Pi over SSH.

- SSH alias `rei-pi` → `raspi@192.168.1.222` (key-based, passwordless; defined in
  `~/.ssh/config`). `sudo` on the Pi is passwordless too.
- The project lives at `/home/raspi/rei` on the Pi, branch `endpoint`, same git
  remote as the workstation. `rei.service` runs it as root from the venv.
- **Deploy**: `tools/deploy_pi.sh` (tar over SSH — no `rsync` on the workstation).
  It never touches `config/settings.json`, `.env` or the Pi's `.venv`.
  Flags: `--deps`, `--tests`, `--clean`, `--no-restart`.
- **OpenCode commands**: `/deploy-pi` (deploy + tests + restart), `/pi <cmd>`
  (run a command on the Pi), `/pi-logs` (tail `rei.service` journal).
- Pi-side facts: `systemctl is-active rei.service`, `sudo journalctl -u rei.service`,
  tests run with `cd ~/rei && .venv/bin/python -m unittest discover -s tests`.

## Deploy / install

- `install.sh` creates a `.venv` at the repo root (tries `--system-site-packages`, then a
  plain venv, then falls back to a global pip install) and installs `requirements.txt`
  into it.
- systemd units `rei.service` (runs `main.py`) and `rei-splash.service` (runs
  `splash.py`) execute the **venv** python, not system python. After changing
  `requirements.txt` you must reinstall into `.venv`, or the service keeps the old deps.
- Any new OS-level rule or service must be added to **both** `do_install` and
  `do_uninstall` in `install.sh`.
- `splash.py` imports `PIL` at module level, so `Pillow` must be in the venv.
- **`install.sh` requires a reboot to take effect.** The Pi firmware only reads
  `config.txt` at boot, so the `dtparam=spi=on` / `dtparam=i2c_arm=on` /
  `dtoverlay=dwc2,dr_mode=peripheral` lines it writes only materialise on the
  *next* boot. Without that reboot there is no `/dev/spidev0.0` (OLED + splash
  dead), no `/dev/i2c-1` (PiSugar) and no `/dev/hidg0` (USB HID endpoint).
  `install.sh` writes those lines in the **unfiltered region** of `config.txt`
  (before the first `[section]` header) — appending at the end would scope them
  to whatever section happens to be last (`[cm5]`, `[pi5]`, …) and they would be
  ignored on a Zero 2 W.
- Root filesystem is OverlayFS (read-only); `/data` is the persistent partition.
  `data/reports/` is listed in `.gitignore` but the web server keeps reports **in
  memory only** — nothing writes there today.

## Code rules for agents

- Full type hints on every function (`Optional`, `Union`, `List`, `Dict`, `Callable`,
  `Tuple`).
- Never swallow errors with a bare `except Exception: pass`; handle I/O, HID timeouts,
  Gemini HTTP errors and socket failures separately.
- Hardware/OS actions (HID injection, gadget config, poweroff/reboot, `apt-get`, `git pull`,
  Wi-Fi scan/connect) must honour `REI_DRY_RUN` and the narrower `REI_MOCK_*` overrides.
- Any OS-level rule or service change goes in **both** `do_install` and `do_uninstall`
  (see "Deploy / install"); any dependency change gets reinstalled into `.venv`.
- Keep user-facing strings and docstrings in Spanish; identifiers, logger names and API
  paths in English.
