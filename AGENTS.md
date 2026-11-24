# AGENTS.md — REI

REI is a pocket diagnostic hub for **Raspberry Pi Zero 2 W** with a 1.3" SH1106 OLED
(128x64, 1-bit), a 5-way joystick and 3 physical buttons. Python, no build step, no
package manager, no CI.

- `AGENT.md` (singular) is the long-form **design spec** (hardware BOM, OLED geometry,
  threading rules). It is still referenced by code comments — keep it accurate when the
  design changes. This file is the working guide.
- All user-facing strings, docstrings and most comments are **in Spanish**. Identifiers,
  logger names and API paths are English. Keep new UI strings Spanish.
- `main` is far behind the active `endpoint` branch (it lacks `core/ducky.py`,
  `core/web_server.py`, `core/usb_modes.py`, `plugins/endpoints/` and the whole test
  suite). Check `git branch` before assuming a file exists on `main`.

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
`Pillow` installed — the rest of the suite passes without it (`tests/test_wifi_password_view.py`
skips instead of erroring). Install `requirements.txt` (or at least `Pillow`) before
trusting a test run. `ui/__init__.py` uses guarded imports, so `ui.input_handler` is
importable without Pillow but the rendering classes stay `None`.

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
| `plugins/endpoints/` | **Only** the HID endpoint plugins: `hid_windows.py`, `hid_linux.py` |
| `ui/display.py` | `ScreenManager`, `HeroCardDeckView`, `DetailCardView`, `UpdateProgressView`, `QRCodeView`, `IconRenderer` |
| `ui/input_handler.py` | GPIO joystick/buttons + `USBKeyboardListener` (evdev + stdin) |
| `ui/keyboard_view.py` | Virtual on-screen keyboard (`VirtualKeyboardInputView`) |
| `ui/keyboard_layouts.py` | USB keyboard layout detection + US/ES/Latam scancode maps |
| `ui/wifi_password_view.py` | `WiFiPasswordView` — USB-keyboard Wi-Fi password entry |
| `install.sh` | APT packages, `.venv`, systemd units, USB gadget setup; `do_install` / `do_uninstall` |
| `splash.py` | Standalone early-boot splash (runs as `rei-splash.service`) |

**These do not exist** (older docs claim them): `plugins/network/`,
`plugins/base_plugin.py`, `config/credentials.json`. Network plugins live in
`core/plugins.py`; `VaultPlugin` is a hardcoded stub.

`main.py::_build_menu_hierarchy()` is the single source of truth for the menu tree —
edit it there, never in `ui/display.py`.

## Stubs vs. real implementations

`core/plugins.py` contains several plugins that are **stubs**: they `time.sleep()` and
return literal strings without touching hardware — `CiscoSerialPlugin`, `CiscoSSHPlugin`,
`SNMPScanPlugin`, `WindowsRNDISPlugin`, `LinuxSSHPlugin`, `VaultPlugin`. `netmiko`,
`scrapli`, `pysnmp-lextudio` and `pypsrp` are in `requirements.txt` but **unused**.

Real I/O happens only in: `core/wifi.py`, `core/web_server.py`, `core/gemini_analyzer.py`,
`core/ducky.py`, `core/usb_modes.py`, `core/pisugar.py`, and the update plugins
(`apt-get` / `git pull`). Don't assume a plugin talks to the network because its name
suggests it — read its `run()`.

## Threading contract

`main.py::run()` is a fixed 30 FPS loop: consume input → poll results → render.
**Never** block it with sockets, subprocesses, HTTP calls, long sleeps, or
`/dev/hidg0` writes.

- `DiagnosticManager(max_workers=2)` runs plugins in worker threads; results arrive via
  `poll_result()` and are dispatched in `_process_background_results()`.
- Worker callbacks (`on_complete`, direct `_executor.submit` workers) must **not** touch
  `ScreenManager`. Wrap view transitions in `REIApp._post_ui(callable)`; the main loop
  drains the queue with `_drain_ui_actions()` each frame before polling results.
- `execute_async(..., on_progress=cb)` injects `progress_callback` into the plugin's
  `kwargs`. A plugin that wants progress must declare it explicitly:
  `def run(self, progress_callback: Optional[Callable] = None, **kwargs)`.
- Results are routed to views by **plugin name equality**
  (`plugin.name == result.plugin_name`). The HID plugins build a dynamic name
  (`f"WIN {category[:12]} ({layout.upper()})"`) — changing that format silently breaks
  result routing.
- `UpdateProgressView` swallows *all* input while `is_running` is set. That is how
  "blocked" screens are implemented; use it rather than adding ad-hoc guards.

## USB keyboard password flow (Wi-Fi)

Secured Wi-Fi networks open `ui/wifi_password_view.py::WiFiPasswordView` instead of the
virtual keyboard. The user types on a physical USB keyboard; `ENTER`/`KEY1`/`PRESS`
submits, `KEY2` toggles password visibility, `KEY3`/`Esc` cancels.

- `ui/keyboard_layouts.py` resolves the layout: `REI_KEYBOARD_LAYOUT` env →
  `settings["keyboard"]["layout"]` (main.py passes it to `GPIOInputHandler`) →
  `/etc/default/keyboard` `XKBLAYOUT` → `/etc/vconsole.conf` `KEYMAP` → `us`.
  Supported layouts: `us`, `es`, `latam` (with AltGr and dead keys).
- A device is only a keyboard if `is_keyboard_capabilities()` passes (full alphabet +
  Enter/Space/Backspace/Shift). This rejects fake keyboards like `vc4-hdmi`.
- Hot-plug: the listener rescans `/dev/input` every 2 s; the view polls
  `input_handler.has_text_input` each frame through its `keyboard_status` callback.
- The Zero 2 W has one OTG port: keyboard host mode and HID gadget mode
  (`/dev/hidg0`) are mutually exclusive. The view shows
  `MODO HID ACTIVO / USE MODO NORMAL` when the USB manager is not in `MODO_NORMAL`;
  it never switches modes automatically.
- `main.py` input loop uses `get_event_with_char()` and only forwards `char=` to views
  with `accepts_text_input = True` (`VirtualKeyboardInputView`, `WiFiPasswordView`).
- Passwords are never logged; `WiFiManager._redact()` strips them from errors and
  `nmcli`/`wpa_cli` run with `stdin=subprocess.DEVNULL`.

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
poweroff/reboot, apt/git updates and Wi-Fi scan/connect. Narrower overrides:
`REI_MOCK_POWER=1`, `REI_MOCK_WIFI=1`, `REI_MOCK_UPDATES=1`.

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

- `IconRenderer.draw_icon` dispatches to `_draw_<ICON_NAME>`; an unknown name silently
  falls back to `_draw_DEFAULT`. List the valid names with
  `grep -o 'def _draw_[A-Z_0-9]*' ui/display.py`.
- Hero card geometry is fixed: 1px border `(1,1)-(126,62)`, 20x20 icon centred at
  `(64, 24)`, uppercase title centred at `y=44`, carousel dots at
  `x = 122 - total*5`, `y = 4..6`.
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
