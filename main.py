"""
REI - Main Application Entry Point
Pocket autonomous network & endpoint diagnostic tool for Raspberry Pi Zero 2 W.
Hardware: Waveshare 1.3" OLED HAT (SH1106, 128x64 px) with 5-way joystick & 3 buttons.
Architecture: 100% OOP, Decoupled Producer/Consumer, Fixed 30 FPS UI Event Loop.
"""

import json
import logging
import os
import queue
import signal
import sys
import time
from typing import Callable, Dict, List, Optional

from core.ducky import DuckyInjector
from core.gemini_analyzer import GeminiDiagnosticAnalyzer
from core.interfaces import DiagnosticMetric, DiagnosticResult, DiagnosticStatus, Severity
from core.manager import DiagnosticManager
from core.plugins import (
    AppUpdatePlugin,
    BatteryStatusPlugin,
    CiscoSerialPlugin,
    CiscoSSHPlugin,
    IPAddressPlugin,
    LinuxSSHPlugin,
    PoweroffPlugin,
    RebootPlugin,
    SNMPScanPlugin,
    SystemStatusPlugin,
    SystemUpdatePlugin,
    VaultPlugin,
    WiFiConnectPlugin,
    WiFiScanPlugin,
    WindowsRNDISPlugin,
    execute_system_reboot,
)
from core.usb_modes import USBMode, USBModeManager
from core.web_server import REIWebServer
from plugins.endpoints.hid_linux import LinuxHIDPlugin
from plugins.endpoints.hid_windows import WindowsHIDPlugin
from ui.display import (
    DetailCardView,
    HeroCard,
    HeroCardDeckView,
    KeyboardInputView,
    QRCodeView,
    ScreenManager,
    UpdateProgressView,
    ViewAction,
    ViewActionType,
    VirtualKeyboardInputView,
)
from ui.input_handler import GPIOInputHandler, InputEvent
from ui.wifi_password_view import WiFiPasswordView
from ui.wifi_result_view import WiFiResultView

# Configure high-performance logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("REI.Main")


class REIApp:
    """
    Main Application Controller for REI.
    Orchestrates ScreenManager, GPIOInputHandler, DiagnosticManager,
    FastAPI Telemetry Server, USB Mode Manager, and Gemini AI Analyzer.
    """

    TARGET_FPS = 30
    FRAME_DURATION = 1.0 / float(TARGET_FPS)

    def __init__(self):
        self.running = False

        # 1. Load operational settings
        self.settings = self._load_settings()

        # 2. Initialize Core Subsystems
        self._ui_actions: "queue.Queue[Callable[[], None]]" = queue.Queue()
        keyboard_cfg = self.settings.get("keyboard", {}) if isinstance(self.settings, dict) else {}
        self.input_handler = GPIOInputHandler(
            keyboard_layout=keyboard_cfg.get("layout", "auto"),
        )
        self.screen_manager = ScreenManager(
            width=self.settings.get("display", {}).get("width", 128),
            height=self.settings.get("display", {}).get("height", 64),
            rotate=self.settings.get("display", {}).get("rotate", 2),
        )
        if not getattr(self.screen_manager, "display_ready", True):
            logger.error(
                "El panel OLED NO está disponible: la UI se renderiza en un framebuffer "
                "virtual y la pantalla física quedará en blanco. Revisa /dev/spidev0.0 "
                "(lsmod spidev, dtparam=spi=on en config.txt y reinicio)."
            )
        self.diag_manager = DiagnosticManager(max_workers=2)

        # 3. Initialize Autonomous Network, USB & AI Services
        server_cfg = self.settings.get("server", {})
        self.web_server = REIWebServer(
            host=server_cfg.get("host", "0.0.0.0"),
            port=server_cfg.get("port", 8000),
            base_url=server_cfg.get("report_base_url", "http://10.0.0.1:8000"),
            qr_preferred_interface=server_cfg.get("qr_preferred_interface", "wlan0"),
        )
        self.web_server.start()

        self.usb_manager = USBModeManager()
        self.ducky_injector = DuckyInjector()
        gemini_cfg = self.settings.get("gemini", {})
        self.gemini_analyzer = GeminiDiagnosticAnalyzer(
            api_key=gemini_cfg.get("api_key"),
            model=gemini_cfg.get("model"),
            timeout_seconds=gemini_cfg.get("timeout_seconds"),
        )

        # 4. Register Diagnostic Plugins
        self._register_plugins()

        # 5. Cache of Views for background updates
        self._detail_views: Dict[str, DetailCardView] = {}
        self._progress_views: Dict[str, UpdateProgressView] = {}

        # 6. Build Hierarchical Menu Tree (Strict AGENT.md Spec)
        self._build_menu_hierarchy()

        # 7. Handle POSIX termination signals
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _load_settings(self) -> dict:
        """Loads configuration from config/settings.json or project root."""
        possible_paths = [
            "config/settings.json",
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "settings.json"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json"),
            "settings.json",
        ]
        for config_path in possible_paths:
            if os.path.isfile(config_path):
                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception as ex:
                    logger.warning(f"Error loading settings from {config_path}: {ex}")
        return {}

    def _post_ui(self, action: Callable[[], None]) -> None:
        """
        Schedules a UI action to run on the main 30 FPS thread.

        Worker threads must never mutate ``ScreenManager`` directly; they call
        this method so view transitions stay serialized with rendering.
        """
        if callable(action):
            self._ui_actions.put(action)

    def _drain_ui_actions(self) -> None:
        """Runs pending UI actions in FIFO order on the main thread."""
        while True:
            try:
                action = self._ui_actions.get_nowait()
            except queue.Empty:
                break
            try:
                action()
            except Exception:
                logger.exception("Unhandled error while applying a UI action")

    def _register_plugins(self) -> None:
        """Instantiates and registers all decoupled diagnostic plugins."""
        plugins = [
            IPAddressPlugin(),
            WiFiScanPlugin(),
            WiFiConnectPlugin(),
            BatteryStatusPlugin(),
            SystemStatusPlugin(),
            CiscoSerialPlugin(),
            CiscoSSHPlugin(),
            SNMPScanPlugin(),
            WindowsRNDISPlugin(),
            LinuxSSHPlugin(),
            VaultPlugin(),
            PoweroffPlugin(),
            RebootPlugin(),
            SystemUpdatePlugin(),
            AppUpdatePlugin(),
            WindowsHIDPlugin(injector=self.ducky_injector, web_server=self.web_server),
            LinuxHIDPlugin(injector=self.ducky_injector, web_server=self.web_server),
        ]
        for plugin in plugins:
            self.diag_manager.register_plugin(plugin)

    def _create_detail_view(self, task_id: str, title: str) -> DetailCardView:
        """Factory for detail card views wired to asynchronous plugin triggers."""
        def trigger_diagnostic():
            self._trigger_task(task_id)

        view = DetailCardView(
            title=title,
            initial_lines=["Iniciando diagnostico..."],
            on_refresh=trigger_diagnostic,
        )
        self._detail_views[task_id] = view
        return view

    def _create_update_view(self, task_id: str, title: str) -> UpdateProgressView:
        """Factory for blocking update progress views wired to asynchronous updates."""
        def on_start():
            self._trigger_update_task(task_id)

        view = UpdateProgressView(
            title=title,
            on_start=on_start,
        )
        self._progress_views[task_id] = view
        return view

    def _trigger_task(self, task_id: str) -> None:
        """Submits a diagnostic task to the background worker pool."""
        detail_view = self._detail_views.get(task_id)
        if detail_view:
            detail_view.set_content(lines=["Ejecutando...", "Consultando bus/red..."], status="RUN", is_loading=True)

        self.diag_manager.execute_async(
            plugin_id=task_id,
            on_complete=self._on_task_completed,
        )

    def _trigger_update_task(self, task_id: str) -> None:
        """Submits a critical update task with streaming progress to worker pool."""
        progress_view = self._progress_views.get(task_id)
        if not progress_view:
            return

        progress_view.stage_message = "Iniciando proceso..."
        progress_view.progress = 0.0
        progress_view.is_running = True
        progress_view.is_finished = False

        def on_progress(msg: str, pct: float):
            progress_view.set_progress(msg, pct)

        self.diag_manager.execute_async(
            plugin_id=task_id,
            on_complete=self._on_task_completed,
            on_progress=on_progress,
        )

    def _on_task_completed(self, result: DiagnosticResult) -> None:
        """Callback invoked by worker thread when diagnostic completes."""
        logger.info(f"Task completed: {result.plugin_name} ({result.status.name})")

    # =========================================================================
    # ENDPOINT PC DIAGNOSTIC ORCHESTRATION (RUBBER DUCKY + AI + QR)
    # =========================================================================

    def _start_endpoint_diagnostic(self, os_type: str, layout: str, category: str) -> None:
        """
        Executes USB HID Rubber Ducky diagnostic workflow:
        1. Displays strictly locked UpdateProgressView.
        2. Injects OS-specific payload and awaits HTTP telemetry.
        3. Offers Post-Diagnostic Hero Cards: [ANALISIS CON IA] vs [INFORME SIN IA].
        4. Renders dynamic QR code on OLED for smartphone downloading.
        """
        os_name = "WINDOWS" if os_type == "windows" else "LINUX"
        progress_view = UpdateProgressView(title=f"DIAG {os_name[:6]}")
        progress_view.stage_message = "Preparando Ducky..."
        progress_view.progress = 0.05
        progress_view.is_running = True
        progress_view.is_finished = False
        self.screen_manager.push_view(progress_view)

        def on_progress(msg: str, pct: float):
            progress_view.set_progress(msg, pct)

        def on_diagnostic_finished(result: DiagnosticResult):
            def apply() -> None:
                if result.status != DiagnosticStatus.SUCCESS:
                    progress_view.set_completed(
                        success=False,
                        summary=result.summary or "Error de Inyección",
                        details=result.details if result.details else ["Fallo en recolección"],
                    )
                else:
                    # Transition to Post-Diagnostic Options Menu
                    self.screen_manager.pop_view()
                    self._display_post_diagnostic_menu(result, os_type, layout, category)

            # Worker thread -> main thread hand-off.
            self._post_ui(apply)

        # Configure plugin context and launch asynchronously
        plugin_id = "diag_win_hid" if os_type == "windows" else "diag_linux_hid"
        plugin = self.diag_manager.get_plugin(plugin_id)
        if isinstance(plugin, (WindowsHIDPlugin, LinuxHIDPlugin)):
            plugin.set_context(category=category, layout=layout, web_server=self.web_server)

        self.diag_manager.execute_async(
            plugin_id=plugin_id,
            on_complete=on_diagnostic_finished,
            on_progress=on_progress,
        )

    def _display_post_diagnostic_menu(
        self,
        result: DiagnosticResult,
        os_type: str,
        layout: str,
        category: str,
    ) -> None:
        """Constructs Hero Card deck offering AI analysis vs direct offline report."""
        deck_post = HeroCardDeckView("POST-DIAGNOSTICO")

        # Option 1: AI Analysis (Gemini)
        def handle_ai_choice():
            self._execute_ai_analysis(result)

        card_ai = HeroCard(
            title="ANALISIS CON IA",
            icon_name="AI",
            on_select=handle_ai_choice,
        )

        # Option 2: Offline Report (Sin IA)
        def handle_direct_report():
            self._display_qr_report(result)

        card_no_ai = HeroCard(
            title="INFORME SIN IA",
            icon_name="REPORT",
            on_select=handle_direct_report,
        )

        deck_post.add_card(card_ai)
        deck_post.add_card(card_no_ai)
        self.screen_manager.push_view(deck_post)

    def _execute_ai_analysis(self, result: DiagnosticResult) -> None:
        """Runs Gemini AI analysis in background and presents QR code upon completion."""
        ai_progress = UpdateProgressView(title="CONSULTANDO IA")
        ai_progress.stage_message = "Sintetizando telemetria..."
        ai_progress.progress = 0.5
        ai_progress.is_running = True
        ai_progress.is_finished = False
        self.screen_manager.push_view(ai_progress)

        def worker_ai():
            report_id = result.metadata.get("report_id", "latest")
            latest_report = self.web_server.get_latest_report()
            payload = {
                "os_type": result.metadata.get("os_type", "PC"),
                "category": result.summary,
                "hostname": getattr(latest_report, "hostname", "Host Remoto"),
                "telemetry": getattr(latest_report, "telemetry", {}),
            }

            ai_res = self.gemini_analyzer.analyze_diagnostic(payload)
            result.ai_analysis = ai_res
            self.web_server.attach_ai_analysis(
                report_id=report_id,
                ai_data=ai_res,
                overall_status=ai_res.get("overall_status", "OK"),
            )

            # Return to UI loop on the main thread
            def on_done():
                self.screen_manager.pop_view()
                self._display_qr_report(result)

            self._post_ui(on_done)

        # Submit AI task to diagnostic manager worker pool
        self.diag_manager._executor.submit(worker_ai)

    def _display_qr_report(self, result: DiagnosticResult) -> None:
        """Pushes the QRCodeView to screen allowing the technician to scan with smartphone."""
        report_id = result.metadata.get("report_id", "latest")
        primary_url, primary_label = self.web_server.get_qr_url(report_id)
        usb_url, _ = self.web_server.get_qr_url(report_id, force_interface="usb")

        # If primary is already USB (no Wi-Fi/LAN), don't set alt_url
        alt_url = usb_url if primary_url != usb_url else None
        alt_label = "USB" if alt_url else None

        qr_view = QRCodeView(
            title="REPORTE MOVIL",
            url=primary_url,
            subtitle="Escanea con tu movil",
            alt_url=alt_url,
            net_label=primary_label,
            alt_label=alt_label,
        )
        self.screen_manager.push_view(qr_view)

    # =========================================================================
    # USB MODES SWITCHING ORCHESTRATION
    # =========================================================================

    def _switch_usb_mode(self, target_mode: USBMode) -> None:
        """Switches USB controller profile, displays status and automatically reboots."""
        if target_mode == USBMode.NORMAL:
            title = "MODO NORMAL"
        elif target_mode == USBMode.HID_WINDOWS:
            title = "HID WINDOWS"
        else:
            title = "HID LINUX"
        progress_view = UpdateProgressView(title=title)
        progress_view.stage_message = "Configurando Gadget Universal..."
        progress_view.progress = 0.3
        self.screen_manager.push_view(progress_view)

        def worker_mode():
            success, msg = self.usb_manager.set_mode(target_mode)
            if success:
                progress_view.set_progress("Reiniciando en 3s...", 0.8)
                time.sleep(1.0)
                progress_view.set_completed(
                    success=True,
                    summary="Reiniciando...",
                    details=["Gadget Universal OK", "Reiniciando sistema..."],
                )
                time.sleep(2.0)
                execute_system_reboot()
            else:
                progress_view.set_completed(
                    success=False,
                    summary="Error de Modo",
                    details=[msg[:19], "OK/KEY3: Salir"],
                )

        self.diag_manager._executor.submit(worker_mode)

    # =========================================================================
    # WI-FI & NETWORK FLOWS
    # =========================================================================

    def _start_wifi_scan(self, saved_only: bool = False) -> None:
        """Initiates a Wi-Fi scan (or a saved-profile listing) and shows progress."""
        screen_title = "REDES GUARDADAS" if saved_only else "ESCANER WI-FI"
        progress_view = UpdateProgressView(title=screen_title)
        progress_view.stage_message = (
            "Consultando perfiles..." if saved_only else "Buscando redes..."
        )
        progress_view.progress = 0.5
        self.screen_manager.push_view(progress_view)

        def on_scan_done(result: DiagnosticResult):
            def apply() -> None:
                # Remove the progress screen only if it is still on display;
                # KEY3 may have already taken the user back to the root menu.
                if isinstance(self.screen_manager.current_view, UpdateProgressView):
                    self.screen_manager.pop_view()
                self._display_scanned_wifi_networks(result)

            self._post_ui(apply)

        started = self.diag_manager.execute_async(
            plugin_id="diag_wifi_scan",
            on_complete=on_scan_done,
            saved_only=saved_only,
        )
        if not started:
            self.screen_manager.pop_view()
            self.screen_manager.push_view(
                WiFiResultView(
                    title="WI-FI",
                    icon_name="WIFI_FAIL",
                    reason="ESCANER EN CURSO",
                    hint="K3=SALIR",
                )
            )

    def _display_scanned_wifi_networks(self, result: DiagnosticResult) -> None:
        """Constructs a Hero Card Deck of discovered Wi-Fi networks."""
        metrics = result.metrics if isinstance(result.metrics, dict) else {}
        networks = metrics.get("networks", [])
        deck_title = "REDES GUARDADAS" if metrics.get("saved_only") else "REDES WI-FI"
        deck_wifi_nets = HeroCardDeckView(deck_title)

        if not networks:
            no_net_card = HeroCard(
                title="SIN REDES",
                icon_name="WIFI_FAIL",
                submenu=DetailCardView("SIN REDES", ["No se encontraron", "redes Wi-Fi."]),
            )
            deck_wifi_nets.add_card(no_net_card)
        else:
            for net in networks:
                ssid = str(net.get("ssid", "Red Wi-Fi"))
                is_secured = bool(net.get("is_secured", True))
                is_active = bool(net.get("in_use", False))
                is_saved = bool(net.get("saved", False))
                security = net.get("security", "WPA2")
                icon = "WIFI_OK" if is_active else ("LOCK" if (is_secured or is_saved) else "WIFI")

                # Marker: a network REI already knows, selectable without
                # retyping the password.
                clean_ssid = ssid.strip().upper()
                title = f"{clean_ssid[:14]} *" if (is_active or is_saved) else clean_ssid[:16]

                def make_select_handler(
                    target_ssid: str,
                    secured: bool,
                    target_security: str,
                    known: bool,
                ):
                    def handler():
                        if secured and not known:
                            self._prompt_wifi_password(target_ssid, target_security)
                        else:
                            self._connect_to_wifi(
                                ssid=target_ssid, password=None, auto=known
                            )
                    return handler

                deck_wifi_nets.add_card(
                    HeroCard(
                        title=title,
                        icon_name=icon,
                        on_select=make_select_handler(
                            ssid, is_secured, security, is_active or is_saved
                        ),
                    )
                )

        self.screen_manager.push_view(deck_wifi_nets)

    def _prompt_wifi_password(
        self,
        ssid: str,
        security: str = "WPA2-PSK",
        notice: str = "",
    ) -> None:
        """Opens the USB-keyboard password screen for a secured network."""
        password_view = WiFiPasswordView(
            ssid=ssid,
            on_submit=self._connect_to_wifi,
            security=security,
            keyboard_status=lambda: self.input_handler.has_text_input,
            usb_gadget_active=(
                self.usb_manager.get_current_mode() != USBMode.NORMAL
            ),
            notice=notice,
            layout_name=self.input_handler.keyboard_layout,
        )
        self.screen_manager.push_view(password_view)

    def _connect_to_wifi(
        self,
        ssid: str,
        password: Optional[str],
        auto: bool = False,
    ) -> None:
        """Launches Wi-Fi connection worker and shows progress."""
        if isinstance(
            self.screen_manager.current_view,
            (WiFiPasswordView, VirtualKeyboardInputView, KeyboardInputView),
        ):
            self.screen_manager.pop_view()

        conn_view = UpdateProgressView(title="CONECTANDO...")
        conn_view.stage_message = f"Enlazando {ssid[:10]}..."
        conn_view.progress = 0.5
        conn_view.is_running = True
        conn_view.is_finished = False
        self.screen_manager.push_view(conn_view)

        def on_connect_done(result: DiagnosticResult):
            def apply() -> None:
                if isinstance(self.screen_manager.current_view, UpdateProgressView):
                    self.screen_manager.pop_view()
                metrics = result.metrics if isinstance(result.metrics, dict) else {}
                if (
                    auto
                    and result.status != DiagnosticStatus.SUCCESS
                    and metrics.get("needs_password")
                ):
                    # Auto-connect to a known network whose stored secret no
                    # longer works: ask for the new password right away.
                    self._prompt_wifi_password(ssid, notice="CLAVE CAMBIADA?")
                    return
                self._display_wifi_result_hero_card(result, ssid)

            self._post_ui(apply)

        started = self.diag_manager.execute_async(
            plugin_id="sys_wifi_connect",
            ssid=ssid,
            password=password,
            on_complete=on_connect_done,
        )
        if not started:
            self.screen_manager.pop_view()
            self.screen_manager.push_view(
                WiFiResultView(
                    title="WI-FI",
                    icon_name="WIFI_FAIL",
                    reason="CONEXION EN CURSO",
                    hint="K3=SALIR",
                )
            )

    def _display_wifi_result_hero_card(self, result: DiagnosticResult, ssid: str) -> None:
        """Displays the Wi-Fi connection outcome as a dedicated result screen."""
        is_success = (result.status == DiagnosticStatus.SUCCESS)
        metrics = result.metrics if isinstance(result.metrics, dict) else {}

        icon_name = "WIFI_OK" if is_success else "WIFI_FAIL"
        lines = result.details if result.details else [result.summary]
        reason = lines[0] if lines else ""

        on_retry: Optional[Callable[[], None]] = None
        if is_success:
            hint = "K3=SALIR"
        else:
            retryable = bool(metrics.get("retryable"))
            needs_password = bool(metrics.get("needs_password"))
            if not retryable:
                hint = "K3=SALIR"
            elif needs_password:
                hint = "K2=NUEVA CLAVE"
                on_retry = lambda: self._retry_wifi_connect(ssid, True)
            else:
                hint = "K2=REINTENTAR"
                on_retry = lambda: self._retry_wifi_connect(ssid, False)

        self.screen_manager.push_view(
            WiFiResultView(
                title=ssid or result.plugin_name,
                icon_name=icon_name,
                reason=reason,
                hint=hint,
                on_retry=on_retry,
            )
        )

    def _retry_wifi_connect(self, ssid: str, needs_password: bool) -> None:
        """KEY2 on the result screen: retry the connection or retype the key."""
        if isinstance(self.screen_manager.current_view, WiFiResultView):
            self.screen_manager.pop_view()
        if needs_password:
            self._prompt_wifi_password(ssid, notice="REINGRESE LA CLAVE")
        else:
            self._connect_to_wifi(ssid=ssid, password=None, auto=True)

    # =========================================================================
    # HIERARCHICAL MENU BUILDER
    # =========================================================================

    def _build_menu_hierarchy(self) -> None:
        """
        Constructs the exact Hero Cards navigation hierarchy:
        - Level 0 (Main): UTILIDADES, SWITCHES / RED, ENDPOINTS PC, BOVEDA / VAULT
        - Level 1 (Utilidades): CONEXION DE RED, ESTADO BATERIA, ESTADO SISTEMA, MODO USB, ACTUALIZACIONES, ALIMENTACION
        - Level 1 (Endpoints PC): WINDOWS HOST, LINUX HOST
        - Level 2 (Endpoints PC): SELECCIÓN TECLADO -> TIPO PROBLEMA
        """

        # ----------------------------------------------------
        # LEVEL 2: Sub-menus for CONEXION DE RED
        # ----------------------------------------------------
        view_ip_detail = self._create_detail_view("diag_ip_address", "DIRECCION IP")

        deck_net_conn = HeroCardDeckView("CONEXION DE RED")
        deck_net_conn.add_card(
            HeroCard(
                title="VER DIRECCION IP",
                icon_name="IP",
                submenu=view_ip_detail,
                on_select=lambda: self._trigger_task("diag_ip_address"),
            )
        )
        deck_net_conn.add_card(
            HeroCard(
                title="ESCANEAR WI-FI",
                icon_name="WIFI",
                on_select=self._start_wifi_scan,
            )
        )
        deck_net_conn.add_card(
            HeroCard(
                title="REDES GUARDADAS",
                icon_name="LOCK",
                on_select=lambda: self._start_wifi_scan(saved_only=True),
            )
        )

        # ----------------------------------------------------
        # LEVEL 2: Sub-menus for MODO USB (core/usb_modes.py)
        # ----------------------------------------------------
        deck_usb_modes = HeroCardDeckView("MODO USB")
        deck_usb_modes.add_card(
            HeroCard(
                title="MODO USB NORMAL",
                icon_name="USB_NORMAL",
                on_select=lambda: self._switch_usb_mode(USBMode.NORMAL),
            )
        )
        deck_usb_modes.add_card(
            HeroCard(
                title="TECLADO HID LINUX",
                icon_name="USB_GADGET",
                on_select=lambda: self._switch_usb_mode(USBMode.HID_LINUX),
            )
        )
        deck_usb_modes.add_card(
            HeroCard(
                title="TECLADO HID WIN",
                icon_name="USB_GADGET",
                on_select=lambda: self._switch_usb_mode(USBMode.HID_WINDOWS),
            )
        )

        # ----------------------------------------------------
        # LEVEL 2: Sub-menus for ACTUALIZACIONES
        # ----------------------------------------------------
        view_apt_update = self._create_update_view("sys_apt_update", "ACTUALIZAR SISTEMA")
        view_git_update = self._create_update_view("sys_git_update", "ACTUALIZAR REI")

        deck_actualizaciones = HeroCardDeckView("ACTUALIZACIONES")
        deck_actualizaciones.add_card(
            HeroCard(
                title="ACTUALIZAR SISTEMA",
                icon_name="APT",
                submenu=view_apt_update,
                on_select=lambda: view_apt_update.start(),
            )
        )
        deck_actualizaciones.add_card(
            HeroCard(
                title="ACTUALIZAR REI",
                icon_name="GIT",
                submenu=view_git_update,
                on_select=lambda: view_git_update.start(),
            )
        )

        # ----------------------------------------------------
        # LEVEL 2: Sub-menus for ALIMENTACION
        # ----------------------------------------------------
        view_poweroff_detail = self._create_detail_view("sys_poweroff", "APAGAR")
        view_reboot_detail = self._create_detail_view("sys_reboot", "REINICIAR")

        deck_alimentacion = HeroCardDeckView("ALIMENTACION")
        deck_alimentacion.add_card(
            HeroCard(
                title="APAGAR",
                icon_name="POWEROFF",
                submenu=view_poweroff_detail,
                on_select=lambda: self._trigger_task("sys_poweroff"),
            )
        )
        deck_alimentacion.add_card(
            HeroCard(
                title="REINICIAR",
                icon_name="REBOOT",
                submenu=view_reboot_detail,
                on_select=lambda: self._trigger_task("sys_reboot"),
            )
        )

        # ----------------------------------------------------
        # LEVEL 1: Sub-menus for UTILIDADES
        # ----------------------------------------------------
        view_battery_detail = self._create_detail_view("diag_battery", "ESTADO BATERIA")
        view_system_detail = self._create_detail_view("diag_system", "ESTADO SISTEMA")

        deck_utilidades = HeroCardDeckView("UTILIDADES")
        deck_utilidades.add_card(HeroCard(title="CONEXION DE RED", icon_name="NETWORK", submenu=deck_net_conn))
        deck_utilidades.add_card(
            HeroCard(
                title="ESTADO BATERIA",
                icon_name="BATTERY",
                submenu=view_battery_detail,
                on_select=lambda: self._trigger_task("diag_battery"),
            )
        )
        deck_utilidades.add_card(
            HeroCard(
                title="ESTADO SISTEMA",
                icon_name="CPU",
                submenu=view_system_detail,
                on_select=lambda: self._trigger_task("diag_system"),
            )
        )
        deck_utilidades.add_card(HeroCard(title="MODO USB", icon_name="USB", submenu=deck_usb_modes))
        deck_utilidades.add_card(HeroCard(title="ACTUALIZACIONES", icon_name="UPDATE", submenu=deck_actualizaciones))
        deck_utilidades.add_card(HeroCard(title="ALIMENTACION", icon_name="POWER", submenu=deck_alimentacion))

        # ----------------------------------------------------
        # LEVEL 1: Sub-menus for SWITCHES / RED
        # ----------------------------------------------------
        view_serial_detail = self._create_detail_view("diag_cisco_serial", "CISCO SERIAL")
        view_cisco_ssh_detail = self._create_detail_view("diag_cisco_ssh", "CISCO SSH")
        view_snmp_detail = self._create_detail_view("diag_snmp_scan", "ESCANER SNMP")

        deck_switches = HeroCardDeckView("SWITCHES / RED")
        deck_switches.add_card(
            HeroCard(
                title="CISCO SERIAL",
                icon_name="SERIAL",
                submenu=view_serial_detail,
                on_select=lambda: self._trigger_task("diag_cisco_serial"),
            )
        )
        deck_switches.add_card(
            HeroCard(
                title="CISCO SSH",
                icon_name="SSH",
                submenu=view_cisco_ssh_detail,
                on_select=lambda: self._trigger_task("diag_cisco_ssh"),
            )
        )
        deck_switches.add_card(
            HeroCard(
                title="ESCANER SNMP",
                icon_name="SNMP",
                submenu=view_snmp_detail,
                on_select=lambda: self._trigger_task("diag_snmp_scan"),
            )
        )

        # ----------------------------------------------------
        # LEVEL 1: Sub-menus for ENDPOINTS PC (RUBBER DUCKY)
        # ----------------------------------------------------
        def create_category_deck(os_type: str, layout: str) -> HeroCardDeckView:
            deck = HeroCardDeckView("TIPO PROBLEMA")
            categories = [
                ("RED / CONEXION", "NETWORK"),
                ("ANALISIS HARDWARE", "HARDWARE"),
                ("ANALISIS MALWARE", "MALWARE"),
                ("OTROS PROBLEMAS", "OTROS"),
                ("ANALISIS COMPLETO", "COMPLETO"),
            ]
            for cat_name, icon in categories:
                deck.add_card(
                    HeroCard(
                        title=cat_name,
                        icon_name=icon,
                        on_select=lambda c=cat_name: self._start_endpoint_diagnostic(os_type, layout, c),
                    )
                )
            return deck

        # Windows Keyboard Submenu
        deck_win_keyboards = HeroCardDeckView("SELECCION TECLADO")
        deck_win_keyboards.add_card(
            HeroCard(
                title="TECLADO ESPAÑOL",
                icon_name="ESPAÑOL",
                submenu=create_category_deck("windows", "es"),
            )
        )
        deck_win_keyboards.add_card(
            HeroCard(
                title="TECLADO INGLES",
                icon_name="INGLES",
                submenu=create_category_deck("windows", "us"),
            )
        )

        # Linux Keyboard Submenu
        deck_linux_keyboards = HeroCardDeckView("SELECCION TECLADO")
        deck_linux_keyboards.add_card(
            HeroCard(
                title="TECLADO ESPAÑOL",
                icon_name="ESPAÑOL",
                submenu=create_category_deck("linux", "es"),
            )
        )
        deck_linux_keyboards.add_card(
            HeroCard(
                title="TECLADO INGLES",
                icon_name="INGLES",
                submenu=create_category_deck("linux", "us"),
            )
        )

        deck_endpoints = HeroCardDeckView("ENDPOINTS PC")
        deck_endpoints.add_card(
            HeroCard(
                title="WINDOWS HOST",
                icon_name="WINDOWS",
                submenu=deck_win_keyboards,
            )
        )
        deck_endpoints.add_card(
            HeroCard(
                title="LINUX HOST",
                icon_name="LINUX",
                submenu=deck_linux_keyboards,
            )
        )

        # ----------------------------------------------------
        # LEVEL 1: Sub-menu for BOVEDA / VAULT
        # ----------------------------------------------------
        view_vault_detail = self._create_detail_view("diag_vault", "BOVEDA / VAULT")

        # ----------------------------------------------------
        # LEVEL 0: MAIN ROOT DECK
        # ----------------------------------------------------
        root_deck = HeroCardDeckView("MAIN")
        root_deck.add_card(HeroCard(title="UTILIDADES", icon_name="TOOLS", submenu=deck_utilidades))
        root_deck.add_card(HeroCard(title="SWITCHES / RED", icon_name="NETWORK", submenu=deck_switches))
        root_deck.add_card(HeroCard(title="ENDPOINTS PC", icon_name="ENDPOINT", submenu=deck_endpoints))
        root_deck.add_card(
            HeroCard(
                title="BOVEDA / VAULT",
                icon_name="VAULT",
                submenu=view_vault_detail,
                on_select=lambda: self._trigger_task("diag_vault"),
            )
        )

        self.screen_manager.set_root_view(root_deck)

    def _process_background_results(self) -> None:
        """Polls completed diagnostic tasks without blocking the UI loop."""
        while True:
            result = self.diag_manager.poll_result()
            if not result:
                break

            # Find matching detail view
            for task_id, detail_view in self._detail_views.items():
                plugin = self.diag_manager.get_plugin(task_id)
                if plugin and plugin.name == result.plugin_name:
                    status_badge = "OK" if result.status == DiagnosticStatus.SUCCESS else "FAIL"
                    lines = result.details if result.details else [result.summary]
                    detail_view.set_content(lines=lines, status=status_badge, is_loading=False)

            # Find matching update progress view
            for task_id, progress_view in self._progress_views.items():
                plugin = self.diag_manager.get_plugin(task_id)
                if plugin and plugin.name == result.plugin_name:
                    is_ok = (result.status == DiagnosticStatus.SUCCESS)
                    progress_view.set_completed(
                        success=is_ok,
                        summary=result.summary,
                        details=result.details,
                    )

    def _handle_signal(self, signum, frame):
        """Signal handler for graceful shutdown."""
        logger.info(f"Received shutdown signal ({signum}). Exiting...")
        self.running = False

    def run(self) -> None:
        """
        Executes the fixed 30 FPS UI Event Loop.
        Consumes GPIO and USB keyboard events, processes asynchronous diagnostic outputs,
        and renders double-buffered OLED frames with high temporal precision.
        """
        self.running = True
        logger.info("REI UI Event Loop started at 30 FPS.")

        next_frame_time = time.monotonic()

        try:
            while self.running:
                # 1. Non-blocking Input Consumption (GPIO + USB Keyboard)
                item = self.input_handler.get_event_with_char()
                while item is not None:
                    event, char = item
                    curr_view = self.screen_manager.current_view
                    if curr_view:
                        try:
                            if getattr(curr_view, "accepts_text_input", False):
                                action = curr_view.handle_input(event, char=char)
                            else:
                                action = curr_view.handle_input(event)

                            if action.action_type == ViewActionType.PUSH_VIEW and action.target_view:
                                self.screen_manager.push_view(action.target_view)
                            elif action.action_type == ViewActionType.POP_VIEW:
                                self.screen_manager.pop_view()
                            elif action.action_type == ViewActionType.POP_TO_ROOT:
                                self.screen_manager.pop_to_root()
                            elif action.action_type == ViewActionType.EXECUTE_TASK and action.task_id:
                                self._trigger_task(action.task_id)
                        except Exception:
                            logger.exception(
                                "Error handling input event %s in %s",
                                event.name,
                                type(curr_view).__name__,
                            )

                    item = self.input_handler.get_event_with_char()

                # 1b. Apply UI transitions requested by worker threads
                self._drain_ui_actions()

                # 2. Non-blocking Background Diagnostic Result Processing
                self._process_background_results()

                # 3. Double-buffered OLED Rendering
                self.screen_manager.render()

                # 4. Strict 30 FPS Frame Timing Compensation
                next_frame_time += self.FRAME_DURATION
                sleep_time = next_frame_time - time.monotonic()

                if sleep_time > 0:
                    time.sleep(sleep_time)
                else:
                    # Frame drop compensation: reset timer to avoid drift
                    next_frame_time = time.monotonic()

        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received.")
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        """Releases hardware, server, and thread pool resources."""
        logger.info("Shutting down REI...")
        self.running = False
        self.web_server.stop()
        self.input_handler.close()
        self.diag_manager.shutdown(wait=False)
        self.screen_manager.clear()
        logger.info("Shutdown complete.")


if __name__ == "__main__":
    app = REIApp()
    app.run()
