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
from typing import Any, Callable, Dict, List, Optional

from core.ducky import DuckyInjector
from core.errors import NoticeLevel, format_oled_error, severity_to_notice_level
from core.gemini_analyzer import GeminiDiagnosticAnalyzer
from core.interfaces import DiagnosticMetric, DiagnosticResult, DiagnosticStatus, Severity
from core.manager import DiagnosticManager
from core.parser import shorten_oled_label, wrap_oled_lines
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
    WiFiDisconnectPlugin,
    WiFiForgetPlugin,
    WiFiSavedListPlugin,
    WiFiScanPlugin,
    WindowsRNDISPlugin,
    execute_system_reboot,
)
from core.usb_modes import USBMode, USBModeManager
from core.web_server import REIWebServer
from core.wifi import WiFiManager
from plugins.endpoints.hid_linux import LinuxHIDPlugin
from plugins.endpoints.hid_windows import WindowsHIDPlugin
from plugins.switches import DEFAULT_SWITCH_REGISTRY, SWITCH_MENU_CATEGORIES
from ui.display import (
    BatteryTelemetryView,
    ConfirmDialogView,
    ConsoleCredentialView,
    DetailCardView,
    DetailHero,
    DetailPage,
    DetailRow,
    HeroCard,
    HeroCardDeckView,
    KeyboardInputView,
    NoticeView,
    QRCodeView,
    ScreenManager,
    SystemTelemetryView,
    UpdateProgressView,
    ViewAction,
    ViewActionType,
    VirtualKeyboardInputView,
    WiFiPasswordView,
)
from ui.input_handler import GPIOInputHandler, InputEvent

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
        input_cfg = self.settings.get("input", {})
        self.input_handler = GPIOInputHandler(
            keyboard_layout=input_cfg.get("keyboard_layout")
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

        self.wifi_manager = WiFiManager()
        self._ui_callbacks: queue.Queue = queue.Queue()

        # 4. Register Diagnostic Plugins
        self._register_plugins()

        # 5. Cache of Views for background updates
        self._detail_views: Dict[str, DetailCardView] = {}
        self._progress_views: Dict[str, UpdateProgressView] = {}

        # 6. Build Hierarchical Menu Tree (Strict AGENTS.md Spec)
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

    def _register_plugins(self) -> None:
        """Instantiates and registers all decoupled diagnostic plugins."""
        plugins = [
            IPAddressPlugin(),
            WiFiScanPlugin(wifi_mgr=self.wifi_manager),
            WiFiConnectPlugin(wifi_mgr=self.wifi_manager),
            WiFiSavedListPlugin(wifi_mgr=self.wifi_manager),
            WiFiDisconnectPlugin(wifi_mgr=self.wifi_manager),
            WiFiForgetPlugin(wifi_mgr=self.wifi_manager),
            BatteryStatusPlugin(),
            SystemStatusPlugin(),
            CiscoSerialPlugin(web_server=self.web_server),
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
            detail_view.set_content(
                lines=["Consultando datos..."],
                status="RUN",
                is_loading=True,
                level=NoticeLevel.INFO,
            )

        started = self.diag_manager.execute_async(
            plugin_id=task_id,
            on_complete=self._on_task_completed,
        )
        if not started and detail_view:
            detail_view.set_content(
                lines=["La tarea ya esta en ejecucion.", "Espere unos segundos."],
                status="AVISO",
                is_loading=False,
                level=NoticeLevel.WARNING,
            )

    def _trigger_update_task(self, task_id: str) -> None:
        """Submits a critical update task with streaming progress to worker pool."""
        progress_view = self._progress_views.get(task_id)
        if not progress_view:
            return

        progress_view.reset("Iniciando proceso...")
        progress_view._started = True

        def on_progress(msg: str, pct: float) -> None:
            progress_view.set_progress(msg, pct)

        started = self.diag_manager.execute_async(
            plugin_id=task_id,
            on_complete=self._on_task_completed,
            on_progress=on_progress,
        )
        if not started:
            progress_view.set_completed(
                success=False,
                summary="Proceso ya en curso",
                details=["Espere a que finalice la actualizacion actual."],
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

        def on_diagnostic_finished(result: DiagnosticResult) -> None:
            def finish_on_ui() -> None:
                if result.status != DiagnosticStatus.SUCCESS:
                    progress_view.set_completed(
                        success=False,
                        summary=result.summary or "Error de inyeccion",
                        details=result.details if result.details else ["Fallo en recoleccion de telemetria."],
                    )
                else:
                    if self.screen_manager.current_view is progress_view:
                        self.screen_manager.pop_view()
                    self._display_post_diagnostic_menu(result, os_type, layout, category)

            self._schedule_ui(finish_on_ui)

        # Configure plugin context and launch asynchronously
        plugin_id = "diag_win_hid" if os_type == "windows" else "diag_linux_hid"
        plugin = self.diag_manager.get_plugin(plugin_id)
        if isinstance(plugin, (WindowsHIDPlugin, LinuxHIDPlugin)):
            plugin.set_context(category=category, layout=layout, web_server=self.web_server)

        started = self.diag_manager.execute_async(
            plugin_id=plugin_id,
            on_complete=on_diagnostic_finished,
            on_progress=on_progress,
        )
        if not started:
            progress_view.set_completed(
                success=False,
                summary="Diagnostico ocupado",
                details=["Hay otro diagnostico HID en curso.", "Espere un momento e intente de nuevo."],
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
        ai_progress = UpdateProgressView(title="CONSULTANDO IA", indeterminate=True)
        ai_progress.stage_message = "Analizando datos..."
        ai_progress.is_running = True
        ai_progress.is_finished = False
        self.screen_manager.push_view(ai_progress)

        def worker_ai() -> None:
            try:
                report_id = result.metadata.get("report_id", "latest")
                stored_report = (
                    self.web_server.get_report(report_id)
                    if hasattr(self.web_server, "get_report")
                    else self.web_server.get_latest_report()
                )
                if stored_report is None:
                    stored_report = self.web_server.get_latest_report()

                telemetry_obj = getattr(stored_report, "telemetry", {}) if stored_report else {}
                switch_audit_obj = getattr(stored_report, "switch_audit", None) if stored_report else None
                if not switch_audit_obj and isinstance(result.metrics, dict):
                    switch_audit_obj = result.metrics.get("switch_audit")

                payload: Dict[str, Any] = {
                    "device_kind": result.metadata.get("device_kind", "ENDPOINT"),
                    "os_type": result.metadata.get("os_type") or getattr(stored_report, "os_type", "PC"),
                    "category": result.metadata.get("category_label") or result.summary,
                    "hostname": result.metadata.get("hostname") or getattr(stored_report, "hostname", "Host Remoto"),
                    "telemetry": telemetry_obj,
                }
                if switch_audit_obj:
                    payload["switch_audit"] = switch_audit_obj

                ai_res = self.gemini_analyzer.analyze_diagnostic(payload)
                result.ai_analysis = ai_res
                self.web_server.attach_ai_analysis(
                    report_id=report_id,
                    ai_data=ai_res,
                    overall_status=ai_res.get("overall_status", "OK"),
                )

                def on_done() -> None:
                    if self.screen_manager.current_view is ai_progress:
                        self.screen_manager.pop_view()
                    self._display_qr_report(result)

                time.sleep(0.25)
                self._schedule_ui(on_done)
            except Exception as ex:
                logger.exception(f"Error en analisis IA: {ex}")
                formatted = format_oled_error(ex, fallback_title="ERROR IA")

                def on_fail() -> None:
                    ai_progress.set_completed(
                        success=False,
                        summary=formatted.title,
                        details=formatted.lines,
                    )

                self._schedule_ui(on_fail)

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
    # SWITCH CONSOLE SERIAL DIAGNOSTIC ORCHESTRATION (AUTO/MANUAL + AUTH + QR)
    # =========================================================================

    def _start_switch_serial_diagnostic(
        self,
        profile_id: Optional[str] = None,
        category: str = "GENERAL",
        credentials: Optional[Dict[str, str]] = None,
        probe_hint: Optional[Dict[str, Any]] = None,
        skip_enable: bool = False,
    ) -> None:
        """
        Orquesta el diagnóstico de switches por consola serial:
        1. Valida que el puerto OTG no esté bloqueado en modo USB HID Gadget.
        2. Muestra UpdateProgressView bloqueante durante detección y recolección.
        3. Si el switch exige login o clave enable, abre ConsoleCredentialView por teclado USB.
        4. Al completar, presenta POST-DIAGNOSTICO (ANALISIS CON IA / INFORME SIN IA -> QR).
        """
        if self._is_usb_gadget_blocking_host():
            logger.warning("Consola serial bloqueada: USB OTG en modo HID Gadget.")
            self.screen_manager.push_view(
                NoticeView(
                    title="MODO HID ACTIVO",
                    lines=[
                        "USB en modo HID.",
                        "Cambie a MODO USB",
                        "NORMAL en Utilidades.",
                    ],
                    level=NoticeLevel.WARNING,
                )
            )
            return

        logger.info(
            f"Iniciando diagnostico consola serial (perfil={profile_id or 'AUTO'}, "
            f"categoria={category}, skip_enable={skip_enable})"
        )
        progress_view = UpdateProgressView(title="CONSOLA SERIAL")
        progress_view.stage_message = "Buscando cable serial..."
        progress_view.progress = 0.05
        progress_view.is_running = True
        progress_view.is_finished = False
        self.screen_manager.push_view(progress_view)

        def on_progress(msg: str, pct: float) -> None:
            progress_view.set_progress(msg, pct)

        def on_serial_finished(result: DiagnosticResult) -> None:
            def finish_on_ui() -> None:
                meta = result.metadata if isinstance(result.metadata, dict) else {}
                if meta.get("auth_required"):
                    if self.screen_manager.current_view is progress_view:
                        self.screen_manager.pop_view()
                    self._open_console_auth_flow(
                        auth_stage=str(meta.get("auth_stage") or "password"),
                        probe_hint=meta.get("probe") or probe_hint,
                        profile_id=profile_id,
                        category=category,
                        existing_credentials=dict(credentials or {}),
                    )
                    return

                if result.status != DiagnosticStatus.SUCCESS:
                    progress_view.set_completed(
                        success=False,
                        summary=result.summary or "Error consola serial",
                        details=result.details if result.details else ["No se pudo consultar el switch."],
                    )
                    return

                if self.screen_manager.current_view is progress_view:
                    self.screen_manager.pop_view()
                self._display_post_diagnostic_menu(
                    result=result,
                    os_type=str(meta.get("os_type") or "SWITCH"),
                    layout="serial",
                    category=str(meta.get("category_label") or category),
                )

            self._schedule_ui(finish_on_ui)

        started = self.diag_manager.execute_async(
            plugin_id="diag_cisco_serial",
            on_complete=on_serial_finished,
            on_progress=on_progress,
            profile_id=profile_id,
            category=category,
            credentials=credentials,
            probe_hint=probe_hint,
            skip_enable=skip_enable,
        )
        if not started:
            progress_view.set_completed(
                success=False,
                summary="Consola ocupada",
                details=["Hay otro diagnostico serial", "en curso. Espere un momento."],
            )

    def _open_console_auth_flow(
        self,
        auth_stage: str,
        probe_hint: Optional[Dict[str, Any]],
        profile_id: Optional[str],
        category: str,
        existing_credentials: Optional[Dict[str, str]] = None,
    ) -> None:
        """Abre vistas ConsoleCredentialView encadenadas por teclado físico USB."""
        if hasattr(self.input_handler, "request_keyboard_rescan"):
            self.input_handler.request_keyboard_rescan()

        creds = dict(existing_credentials or {})
        kb_status = getattr(self.input_handler, "has_typing_input", None)
        kb_cb = kb_status if callable(kb_status) else None
        layout_cb = lambda: getattr(self.input_handler, "keyboard_layout", "latam")
        hid_blocking = self._is_usb_gadget_blocking_host()

        def open_password_step() -> None:
            def on_pw_submit(password: str) -> None:
                creds["password"] = password
                if isinstance(self.screen_manager.current_view, ConsoleCredentialView):
                    self.screen_manager.pop_view()
                self._start_switch_serial_diagnostic(
                    profile_id=profile_id,
                    category=category,
                    credentials=creds,
                    probe_hint=probe_hint,
                )

            pw_view = ConsoleCredentialView(
                title="CONSOLA: CLAVE",
                field_label="Clave",
                masked=True,
                allow_empty=False,
                keyboard_status=kb_cb,
                layout_provider=layout_cb,
                usb_gadget_active=hid_blocking,
                on_submit=on_pw_submit,
            )
            self.screen_manager.push_view(pw_view)

        if auth_stage == "username":
            def on_user_submit(username: str) -> None:
                creds["username"] = username
                if isinstance(self.screen_manager.current_view, ConsoleCredentialView):
                    self.screen_manager.pop_view()
                open_password_step()

            user_view = ConsoleCredentialView(
                title="CONSOLA: USUARIO",
                field_label="Usuario",
                masked=False,
                allow_empty=False,
                keyboard_status=kb_cb,
                layout_provider=layout_cb,
                usb_gadget_active=hid_blocking,
                on_submit=on_user_submit,
            )
            self.screen_manager.push_view(user_view)
            return

        if auth_stage == "enable":
            def on_enable_submit(secret: str) -> None:
                if isinstance(self.screen_manager.current_view, ConsoleCredentialView):
                    self.screen_manager.pop_view()
                if secret:
                    creds["enable_secret"] = secret
                    self._start_switch_serial_diagnostic(
                        profile_id=profile_id,
                        category=category,
                        credentials=creds,
                        probe_hint=probe_hint,
                        skip_enable=False,
                    )
                else:
                    # ENTER vacío: continuar en modo lectura (>) sin abortar el diagnóstico
                    self._start_switch_serial_diagnostic(
                        profile_id=profile_id,
                        category=category,
                        credentials=creds,
                        probe_hint=probe_hint,
                        skip_enable=True,
                    )

            enable_view = ConsoleCredentialView(
                title="CLAVE ENABLE",
                field_label="Enable",
                masked=True,
                allow_empty=True,
                placeholder="Enter=solo lectura",
                keyboard_status=kb_cb,
                layout_provider=layout_cb,
                usb_gadget_active=hid_blocking,
                on_submit=on_enable_submit,
            )
            self.screen_manager.push_view(enable_view)
            return

        open_password_step()

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
        progress_view.stage_message = "Configurando USB..."
        progress_view.progress = 0.3
        self.screen_manager.push_view(progress_view)

        def worker_mode() -> None:
            try:
                success, msg = self.usb_manager.set_mode(target_mode)
                if success:
                    progress_view.set_progress("Reiniciando en 3s...", 0.8)
                    time.sleep(1.0)
                    progress_view.set_completed(
                        success=True,
                        summary="Modo USB actualizado",
                        details=["Gadget configurado correctamente.", "Reiniciando sistema..."],
                    )
                    time.sleep(2.0)
                    execute_system_reboot()
                else:
                    formatted = format_oled_error(msg, fallback_title="ERROR MODO USB")
                    progress_view.set_completed(
                        success=False,
                        summary=formatted.title,
                        details=formatted.lines,
                    )
            except Exception as ex:
                logger.exception(f"Error cambiando modo USB a {target_mode}: {ex}")
                formatted = format_oled_error(ex, fallback_title="ERROR MODO USB")
                progress_view.set_completed(
                    success=False,
                    summary=formatted.title,
                    details=formatted.lines,
                )

        self.diag_manager._executor.submit(worker_mode)

    # =========================================================================
    # WI-FI & NETWORK FLOWS
    # =========================================================================

    def _schedule_ui(self, callback: Callable[[], None]) -> None:
        """Queues a UI transition to execute safely on the 30 FPS main thread."""
        self._ui_callbacks.put(callback)

    def _finish_wifi_progress(
        self,
        progress_view: UpdateProgressView,
        result: DiagnosticResult,
        on_done_ui: Callable[[DiagnosticResult], None],
    ) -> None:
        """Pops blocking progress view if still active and invokes UI handler."""
        if self.screen_manager.current_view is progress_view:
            self.screen_manager.pop_view()
        on_done_ui(result)

    def _run_wifi_async_task(
        self,
        progress_title: str,
        stage_message: str,
        plugin_id: str,
        on_done_ui: Callable[[DiagnosticResult], None],
        **plugin_kwargs: Any,
    ) -> None:
        """Runs a Wi-Fi plugin asynchronously and dispatches completion onto the UI thread."""
        progress_view = UpdateProgressView(title=progress_title, indeterminate=True)
        progress_view.stage_message = stage_message
        self.screen_manager.push_view(progress_view)

        started = self.diag_manager.execute_async(
            plugin_id=plugin_id,
            on_complete=lambda res: self._schedule_ui(
                lambda r=res: self._finish_wifi_progress(progress_view, r, on_done_ui)
            ),
            **plugin_kwargs,
        )
        if not started:
            if self.screen_manager.current_view is progress_view:
                self.screen_manager.pop_view()
            self.screen_manager.push_view(
                NoticeView(
                    "OCUPADO",
                    ["Hay una operacion Wi-Fi en curso.", "Espere unos segundos y reintente."],
                    level=NoticeLevel.WARNING,
                )
            )

    @staticmethod
    def _format_wifi_card_title(ssid: str, in_use: bool = False, is_saved: bool = False) -> str:
        if in_use:
            return f"* {shorten_oled_label(ssid, 16)}"
        if is_saved:
            return f"[G] {shorten_oled_label(ssid, 14)}"
        return shorten_oled_label(ssid, 18)

    def _start_wifi_scan(self) -> None:
        """Initiates Wi-Fi scan and displays progress."""
        self._run_wifi_async_task(
            progress_title="ESCANER WI-FI",
            stage_message="Buscando redes...",
            plugin_id="diag_wifi_scan",
            on_done_ui=self._display_scanned_wifi_networks,
        )

    def _start_saved_wifi_list(self) -> None:
        """Loads saved Wi-Fi profiles and opens the saved networks deck."""
        self._run_wifi_async_task(
            progress_title="REDES GUARDADAS",
            stage_message="Cargando perfiles...",
            plugin_id="diag_wifi_saved",
            on_done_ui=self._display_saved_wifi_profiles,
            include_scan=True,
        )

    def _is_usb_gadget_blocking_host(self) -> bool:
        """Checks without blocking whether USB OTG is active in Gadget/Peripheral mode."""
        if os.getenv("REI_DRY_RUN") == "1":
            return False
        try:
            if os.path.exists("/dev/hidg0") and os.path.isdir("/sys/class/udc"):
                return len(os.listdir("/sys/class/udc")) > 0
        except OSError:
            pass
        return False

    def _open_wifi_password_prompt(
        self,
        ssid: str,
        force_update: bool = False,
        security_hint: Optional[str] = None,
        replace_top: bool = False,
        source_deck: Optional[HeroCardDeckView] = None,
    ) -> None:
        """Opens WiFiPasswordView to enter or update a Wi-Fi password via USB keyboard."""
        if replace_top and self.screen_manager.current_view is not None:
            self.screen_manager.pop_view()

        if hasattr(self.input_handler, "request_keyboard_rescan"):
            self.input_handler.request_keyboard_rescan()

        prefix = "NUEVA: " if force_update else "CLAVE: "
        kb_status = getattr(self.input_handler, "has_typing_input", None)
        pw_view = WiFiPasswordView(
            ssid=ssid,
            title=f"{prefix}{shorten_oled_label(ssid, 11)}",
            security=security_hint or "WPA2-PSK",
            masked=True,
            keyboard_status=kb_status if callable(kb_status) else None,
            layout_provider=lambda: getattr(self.input_handler, "keyboard_layout", "latam"),
            usb_gadget_active=self._is_usb_gadget_blocking_host(),
            on_submit=lambda s, pw: self._connect_to_wifi(
                ssid=s,
                password=pw,
                force_update=force_update,
                security_hint=security_hint,
                source_deck=source_deck,
            ),
        )
        self.screen_manager.push_view(pw_view)

    def _append_active_wifi_cards(
        self,
        deck: HeroCardDeckView,
        ssid: str,
        signal_pct: int = 0,
        security: str = "",
        frequency: str = "",
        source_deck: Optional[HeroCardDeckView] = None,
    ) -> None:
        """Appends status and disconnect cards for an active Wi-Fi connection."""
        ip_addr = self.wifi_manager.get_ip() or "Asignada"
        info_lines = [
            f"Red: {shorten_oled_label(ssid, 14)}",
            f"IP:  {shorten_oled_label(ip_addr, 14)}",
        ]
        if signal_pct:
            band = f" {frequency}" if frequency else ""
            info_lines.append(f"Senal: {signal_pct}%{band}")
        else:
            info_lines.append("Estado: Activa")
        if security:
            info_lines.append(f"Seg: {shorten_oled_label(security, 14)}")

        short_band = frequency.replace("GHz", "G").replace(" ", "") if frequency else ""
        short_sec = security.replace("-PSK", "").replace("WPA2/WPA3", "WPA3").strip() if security else ""
        summary_parts: List[str] = []
        if signal_pct:
            summary_parts.append(f"{signal_pct}%")
        if short_band:
            summary_parts.append(short_band)
        if short_sec:
            summary_parts.append(short_sec)
        info_value = " ".join(summary_parts) if summary_parts else "Activa"

        wifi_pages: List[DetailPage] = [
            DetailPage(
                title="RED ACTIVA",
                hero=DetailHero(
                    tag="WIFI",
                    value=ssid,
                    signal_pct=signal_pct if signal_pct > 0 else None,
                ),
                rows=(
                    DetailRow(label="IP", value=ip_addr),
                    DetailRow(label="Info", value=info_value),
                ),
            )
        ]
        if security and signal_pct:
            sec_rows: List[DetailRow] = [
                DetailRow(label="Seg.", value=security),
                DetailRow(label="Senal", value=f"{signal_pct}% {frequency}".strip()),
            ]
            wifi_pages.append(DetailPage(title="DETALLE RED", rows=tuple(sec_rows)))

        deck.add_card(
            HeroCard(
                title="ESTADO RED",
                icon_name="WIFI_OK",
                submenu=DetailCardView("RED ACTIVA", info_lines, level=NoticeLevel.OK, pages=wifi_pages),
            )
        )
        deck.add_card(
            HeroCard(
                title="DESCONECTAR",
                icon_name="WIFI_OFF",
                on_select=lambda: self._disconnect_wifi(ssid=ssid, action_menu=deck, source_deck=source_deck),
            )
        )

    def _build_active_wifi_menu(
        self,
        ssid: str,
        signal_pct: int = 0,
        security: str = "",
        frequency: str = "",
        profile_id: Optional[str] = None,
        source_deck: Optional[HeroCardDeckView] = None,
    ) -> HeroCardDeckView:
        """Builds status and management actions for the currently connected Wi-Fi network."""
        deck = HeroCardDeckView(shorten_oled_label(ssid, 16))
        self._append_active_wifi_cards(
            deck, ssid=ssid, signal_pct=signal_pct, security=security,
            frequency=frequency, source_deck=source_deck,
        )
        deck.add_card(
            HeroCard(
                title="OLVIDAR RED",
                icon_name="TRASH",
                on_select=lambda: self._forget_wifi(
                    ssid=ssid, profile_id=profile_id, action_menu=deck, source_deck=source_deck,
                ),
            )
        )
        return deck

    def _build_saved_profile_menu(
        self,
        prof: Dict[str, Any],
        source_deck: Optional[HeroCardDeckView] = None,
    ) -> HeroCardDeckView:
        """Builds action menu for a saved Wi-Fi profile."""
        ssid = prof.get("ssid", "Red Wi-Fi")
        profile_id = prof.get("profile_id")
        security = prof.get("security", "")
        in_use = bool(prof.get("in_use"))
        signal_pct = int(prof.get("signal_pct") or 0)

        deck = HeroCardDeckView(shorten_oled_label(ssid, 16))
        if in_use:
            self._append_active_wifi_cards(
                deck, ssid=ssid, signal_pct=signal_pct, security=security, source_deck=source_deck,
            )
        else:
            deck.add_card(
                HeroCard(
                    title="CONECTAR",
                    icon_name="WIFI_SAVED",
                    on_select=lambda: self._connect_to_wifi(
                        ssid=ssid,
                        password=None,
                        profile_id=profile_id,
                        use_saved=True,
                        security_hint=security,
                        replace_top_menu=deck,
                        source_deck=source_deck,
                    ),
                )
            )

        deck.add_card(
            HeroCard(
                title="CAMBIAR CLAVE",
                icon_name="KEYBOARD",
                on_select=lambda: self._open_wifi_password_prompt(
                    ssid=ssid,
                    force_update=True,
                    security_hint=security,
                    replace_top=True,
                    source_deck=source_deck,
                ),
            )
        )
        deck.add_card(
            HeroCard(
                title="OLVIDAR RED",
                icon_name="TRASH",
                on_select=lambda: self._forget_wifi(
                    ssid=ssid, profile_id=profile_id, action_menu=deck, source_deck=source_deck,
                ),
            )
        )
        return deck

    def _make_scanned_card_handler(
        self,
        net: Dict[str, Any],
        deck_wifi_nets: HeroCardDeckView,
    ) -> Callable[[], None]:
        """Creates the selection handler for a scanned Wi-Fi HeroCard."""
        ssid = net.get("ssid", "Red Wi-Fi")
        is_secured = bool(net.get("is_secured", True))
        is_enterprise = bool(net.get("is_enterprise", False))
        in_use = bool(net.get("in_use", False))
        is_saved = bool(net.get("is_saved", False))
        profile_id = net.get("saved_profile_id")
        security = net.get("security", "")

        def handler() -> None:
            if in_use:
                menu = self._build_active_wifi_menu(
                    ssid=ssid,
                    signal_pct=int(net.get("signal_pct") or 0),
                    security=security,
                    frequency=net.get("frequency", ""),
                    profile_id=profile_id,
                    source_deck=deck_wifi_nets,
                )
                self.screen_manager.push_view(menu)
            elif is_saved:
                self._connect_to_wifi(
                    ssid=ssid,
                    password=None,
                    profile_id=profile_id,
                    use_saved=True,
                    security_hint=security,
                    source_deck=deck_wifi_nets,
                )
            elif is_enterprise:
                self.screen_manager.push_view(
                    NoticeView(
                        "RED 802.1X",
                        [f"Red: {ssid}", "Requiere autenticacion 802.1X.", "No soportado desde este menu."],
                        level=NoticeLevel.WARNING,
                    )
                )
            elif is_secured:
                self._open_wifi_password_prompt(
                    ssid=ssid,
                    force_update=False,
                    security_hint=security,
                    source_deck=deck_wifi_nets,
                )
            else:
                self._connect_to_wifi(
                    ssid=ssid,
                    password=None,
                    security_hint=security,
                    source_deck=deck_wifi_nets,
                )

        return handler

    def _display_scanned_wifi_networks(self, result: DiagnosticResult) -> None:
        """Constructs a Hero Card Deck of discovered Wi-Fi networks."""
        networks = result.metrics.get("networks", []) if isinstance(result.metrics, dict) else []
        deck_wifi_nets = HeroCardDeckView("REDES WI-FI")
        deck_wifi_nets._rei_deck_kind = "scan"

        if result.status == DiagnosticStatus.FAILED and not networks:
            self.screen_manager.push_view(
                NoticeView(
                    "ERROR WI-FI",
                    result.details or [result.summary or "Error al escanear redes."],
                    level=NoticeLevel.ERROR,
                )
            )
            return
        if not networks:
            self.screen_manager.push_view(
                NoticeView(
                    "SIN REDES",
                    ["No se encontraron redes Wi-Fi.", "Verifique alcance e intente de nuevo."],
                    level=NoticeLevel.INFO,
                )
            )
            return
        for net in networks:
            ssid = net.get("ssid", "Red Wi-Fi")
            in_use = bool(net.get("in_use", False))
            is_saved = bool(net.get("is_saved", False))
            is_secured = bool(net.get("is_secured", True))

            if in_use:
                icon = "WIFI_OK"
            elif is_saved:
                icon = "WIFI_SAVED"
            elif is_secured:
                icon = "LOCK"
            else:
                icon = "WIFI"

            deck_wifi_nets.add_card(
                HeroCard(
                    title=self._format_wifi_card_title(ssid, in_use=in_use, is_saved=is_saved),
                    icon_name=icon,
                    on_select=self._make_scanned_card_handler(net, deck_wifi_nets),
                    metadata={"ssid": ssid, "net": dict(net)},
                )
            )

        self.screen_manager.push_view(deck_wifi_nets)

    def _display_saved_wifi_profiles(self, result: DiagnosticResult) -> None:
        """Constructs a Hero Card Deck of previously saved Wi-Fi profiles."""
        profiles = result.metrics.get("profiles", []) if isinstance(result.metrics, dict) else []
        deck_saved = HeroCardDeckView("REDES GUARDADAS")
        deck_saved._rei_deck_kind = "saved"

        if not profiles:
            self.screen_manager.push_view(
                NoticeView(
                    "REDES GUARDADAS",
                    ["No hay redes guardadas aun.", "Conectese desde Escanear Wi-Fi."],
                    level=NoticeLevel.INFO,
                )
            )
            return
        for prof in profiles:
            ssid = prof.get("ssid", "Red Wi-Fi")
            in_use = bool(prof.get("in_use", False))
            in_range = bool(prof.get("in_range", False))

            if in_use:
                icon = "WIFI_OK"
            elif in_range:
                icon = "WIFI_SAVED"
            else:
                icon = "INFO"

            def make_saved_handler(p: Dict[str, Any]) -> Callable[[], None]:
                def handler() -> None:
                    self.screen_manager.push_view(self._build_saved_profile_menu(p, source_deck=deck_saved))
                return handler

            deck_saved.add_card(
                HeroCard(
                    title=self._format_wifi_card_title(ssid, in_use=in_use, is_saved=False),
                    icon_name=icon,
                    on_select=make_saved_handler(prof),
                    metadata={"ssid": ssid, "net": dict(prof)},
                )
            )

        self.screen_manager.push_view(deck_saved)

    def _update_source_deck_state(
        self,
        source_deck: Optional[HeroCardDeckView],
        connected_ssid: Optional[str] = None,
        disconnected_ssid: Optional[str] = None,
        forgotten_ssid: Optional[str] = None,
    ) -> None:
        """Keeps cards in the previous scan/saved deck synchronized after state changes."""
        if not source_deck or not source_deck.cards:
            return

        deck_kind = getattr(source_deck, "_rei_deck_kind", "scan")

        if forgotten_ssid and deck_kind == "saved":
            remaining = [c for c in source_deck.cards if c.metadata.get("ssid") != forgotten_ssid]
            if not remaining:
                source_deck.cards = [
                    HeroCard(
                        title="SIN GUARDADAS",
                        icon_name="INFO",
                        submenu=DetailCardView("REDES GUARDADAS", ["Lista vacia."]),
                    )
                ]
                source_deck.active_index = 0
                return
            source_deck.cards = remaining
            source_deck.active_index = min(source_deck.active_index, len(remaining) - 1)

        for card in source_deck.cards:
            meta = card.metadata
            net = meta.get("net")
            ssid = meta.get("ssid")
            if not isinstance(net, dict) or not ssid:
                continue

            if forgotten_ssid and ssid == forgotten_ssid:
                net["in_use"] = False
                net["is_saved"] = False
                net["saved_profile_id"] = None
            elif connected_ssid:
                if ssid == connected_ssid:
                    net["in_use"] = True
                    net["is_saved"] = True
                elif net.get("in_use"):
                    net["in_use"] = False
                    net["is_saved"] = True
            elif disconnected_ssid and (ssid == disconnected_ssid or net.get("in_use")):
                net["in_use"] = False

            in_use = bool(net.get("in_use"))
            is_saved = bool(net.get("is_saved"))
            is_secured = bool(net.get("is_secured", True))

            if deck_kind == "saved":
                card.title = self._format_wifi_card_title(ssid, in_use=in_use, is_saved=False)
                if in_use:
                    card.icon_name = "WIFI_OK"
                elif net.get("in_range"):
                    card.icon_name = "WIFI_SAVED"
                else:
                    card.icon_name = "INFO"
                card.on_select = lambda p=net: self.screen_manager.push_view(
                    self._build_saved_profile_menu(p, source_deck=source_deck)
                )
            else:
                card.title = self._format_wifi_card_title(ssid, in_use=in_use, is_saved=is_saved)
                if in_use:
                    card.icon_name = "WIFI_OK"
                elif is_saved:
                    card.icon_name = "WIFI_SAVED"
                elif is_secured:
                    card.icon_name = "LOCK"
                else:
                    card.icon_name = "WIFI"
                card.on_select = self._make_scanned_card_handler(net, source_deck)

    def _connect_to_wifi(
        self,
        ssid: str,
        password: Optional[str],
        profile_id: Optional[str] = None,
        use_saved: bool = False,
        force_update: bool = False,
        security_hint: Optional[str] = None,
        replace_top_menu: Optional[HeroCardDeckView] = None,
        source_deck: Optional[HeroCardDeckView] = None,
    ) -> None:
        """Launches Wi-Fi connection worker and shows progress."""
        curr = self.screen_manager.current_view
        if isinstance(curr, (WiFiPasswordView, VirtualKeyboardInputView, KeyboardInputView)) or getattr(
            curr, "accepts_text_input", False
        ):
            self.screen_manager.pop_view()
        elif replace_top_menu is not None and curr is replace_top_menu:
            self.screen_manager.pop_view()

        self._run_wifi_async_task(
            progress_title="CONECTANDO...",
            stage_message=f"Enlazando {shorten_oled_label(ssid, 9)}...",
            plugin_id="sys_wifi_connect",
            on_done_ui=lambda res: self._display_wifi_result_hero_card(
                res,
                ssid,
                used_saved=use_saved,
                force_update=force_update,
                security_hint=security_hint,
                source_deck=source_deck,
            ),
            ssid=ssid,
            password=password,
            profile_id=profile_id,
            use_saved=use_saved,
            force_update=force_update,
            security_hint=security_hint,
        )

    def _run_wifi_profile_action(
        self,
        plugin_id: str,
        progress_title: str,
        stage_message: str,
        ok_title: str,
        ok_icon: str,
        detail_title: str,
        ssid: str,
        action_menu: Optional[HeroCardDeckView],
        source_deck: Optional[HeroCardDeckView],
        disconnected_ssid: Optional[str] = None,
        forgotten_ssid: Optional[str] = None,
        **plugin_kwargs: Any,
    ) -> None:
        """Executes a Wi-Fi profile state mutation (disconnect/forget) and updates UI stack."""
        def on_done(result: DiagnosticResult) -> None:
            is_ok = (result.status == DiagnosticStatus.SUCCESS)
            if is_ok:
                self._update_source_deck_state(
                    source_deck,
                    disconnected_ssid=disconnected_ssid,
                    forgotten_ssid=forgotten_ssid,
                )
                if action_menu is not None and self.screen_manager.current_view is action_menu:
                    self.screen_manager.pop_view()

            self.screen_manager.push_view(
                NoticeView(
                    title=ok_title if is_ok else "ERROR WI-FI",
                    lines=result.details or [result.summary or detail_title],
                    level=NoticeLevel.OK if is_ok else NoticeLevel.ERROR,
                )
            )

        self._run_wifi_async_task(
            progress_title=progress_title,
            stage_message=stage_message,
            plugin_id=plugin_id,
            on_done_ui=on_done,
            ssid=ssid,
            **plugin_kwargs,
        )

    def _disconnect_wifi(
        self,
        ssid: str,
        action_menu: Optional[HeroCardDeckView] = None,
        source_deck: Optional[HeroCardDeckView] = None,
    ) -> None:
        """Disconnects active Wi-Fi cleanly and updates navigation stack."""
        self._run_wifi_profile_action(
            plugin_id="sys_wifi_disconnect",
            progress_title="DESCONECTANDO",
            stage_message=f"Cerrando {shorten_oled_label(ssid, 10)}...",
            ok_title="DESCONECTADO",
            ok_icon="WIFI",
            detail_title="DESCONECTAR",
            ssid=ssid,
            action_menu=action_menu,
            source_deck=source_deck,
            disconnected_ssid=ssid,
        )

    def _forget_wifi(
        self,
        ssid: str,
        profile_id: Optional[str] = None,
        action_menu: Optional[HeroCardDeckView] = None,
        source_deck: Optional[HeroCardDeckView] = None,
    ) -> None:
        """Deletes a saved Wi-Fi profile and updates navigation stack."""
        self._run_wifi_profile_action(
            plugin_id="sys_wifi_forget",
            progress_title="OLVIDANDO RED",
            stage_message=f"Borrando {shorten_oled_label(ssid, 9)}...",
            ok_title="RED OLVIDADA",
            ok_icon="INFO",
            detail_title="OLVIDAR RED",
            ssid=ssid,
            action_menu=action_menu,
            source_deck=source_deck,
            forgotten_ssid=ssid,
            profile_id=profile_id,
        )

    def _display_wifi_result_hero_card(
        self,
        result: DiagnosticResult,
        ssid: str,
        used_saved: bool = False,
        force_update: bool = False,
        security_hint: Optional[str] = None,
        source_deck: Optional[HeroCardDeckView] = None,
    ) -> None:
        """Displays Wi-Fi connection outcome as concise, actionable Hero Cards."""
        is_success = (result.status == DiagnosticStatus.SUCCESS)
        metrics = result.metrics if isinstance(result.metrics, dict) else {}

        if is_success:
            self._update_source_deck_state(source_deck, connected_ssid=ssid)
            already_conn = bool(metrics.get("already_connected"))
            hero_title = "YA CONECTADA" if already_conn else "CONECTADO"
            self.screen_manager.push_view(
                NoticeView(
                    title=hero_title,
                    lines=result.details if result.details else [result.summary or f"Conectado a {ssid}"],
                    level=NoticeLevel.OK,
                    pop_to_root_on_ok=True,
                )
            )
            return

        error_code = str(metrics.get("error_code", "UNKNOWN"))
        needs_password = bool(metrics.get("needs_password", False))

        title_map = {
            "AUTH_FAILED": "CLAVE INVALIDA" if used_saved else "CLAVE INCORRECTA",
            "SECRETS_REQUIRED": "CLAVE REQUERIDA",
            "NO_NETWORK_FOUND": "SIN ALCANCE",
            "TIMEOUT": "TIEMPO AGOTADO",
            "DHCP_FAILED": "ERROR DHCP",
            "WIFI_DISABLED": "WI-FI APAGADO",
            "INTERFACE_UNAVAILABLE": "INTERFAZ CAIDA",
            "ENTERPRISE_UNSUPPORTED": "REQ. 802.1X",
        }
        hero_title = title_map.get(error_code, "ERROR CONEXION")
        err_lines = result.details if result.details else [result.summary or "No se pudo conectar."]

        if error_code in ("AUTH_FAILED", "SECRETS_REQUIRED") or needs_password:
            retry_action = lambda: self._open_wifi_password_prompt(
                ssid=ssid,
                force_update=True,
                security_hint=security_hint,
                replace_top=True,
                source_deck=source_deck,
            )
            self.screen_manager.push_view(
                NoticeView(
                    title=hero_title,
                    lines=err_lines,
                    level=NoticeLevel.ERROR,
                    action_label="CLAVE",
                    on_action=retry_action,
                )
            )
        else:
            self.screen_manager.push_view(
                NoticeView(
                    title=hero_title,
                    lines=err_lines,
                    level=NoticeLevel.ERROR,
                )
            )

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
                icon_name="WIFI_SAVED",
                on_select=self._start_saved_wifi_list,
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

        def confirm_poweroff() -> None:
            def on_confirm() -> None:
                self.screen_manager.replace_view(view_poweroff_detail)
                self._trigger_task("sys_poweroff")

            self.screen_manager.push_view(
                ConfirmDialogView(
                    title="APAGAR",
                    question="APAGAR EQUIPO?",
                    icon_name="POWEROFF",
                    cancel_label="NO",
                    confirm_label="APAGAR",
                    on_confirm=on_confirm,
                    default_confirm=False,
                )
            )

        def confirm_reboot() -> None:
            def on_confirm() -> None:
                self.screen_manager.replace_view(view_reboot_detail)
                self._trigger_task("sys_reboot")

            self.screen_manager.push_view(
                ConfirmDialogView(
                    title="REINICIAR",
                    question="REINICIAR EQUIPO?",
                    icon_name="REBOOT",
                    cancel_label="NO",
                    confirm_label="REINICIAR",
                    on_confirm=on_confirm,
                    default_confirm=False,
                )
            )

        deck_alimentacion = HeroCardDeckView("ALIMENTACION")
        deck_alimentacion.add_card(
            HeroCard(
                title="APAGAR",
                icon_name="POWEROFF",
                on_select=confirm_poweroff,
            )
        )
        deck_alimentacion.add_card(
            HeroCard(
                title="REINICIAR",
                icon_name="REBOOT",
                on_select=confirm_reboot,
            )
        )

        # ----------------------------------------------------
        # LEVEL 1: Sub-menus for UTILIDADES
        # ----------------------------------------------------
        view_battery_detail = BatteryTelemetryView(
            title="BATERIA",
            initial_lines=["Consultando bateria..."],
            on_refresh=lambda: self._trigger_task("diag_battery"),
        )
        self._detail_views["diag_battery"] = view_battery_detail

        view_system_detail = SystemTelemetryView(
            title="SISTEMA",
            initial_lines=["Consultando sistema..."],
            on_refresh=lambda: self._trigger_task("diag_system"),
        )
        self._detail_views["diag_system"] = view_system_detail

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
        # LEVEL 1: Sub-menus for SWITCHES
        # ----------------------------------------------------
        def create_switch_category_deck(profile_id: Optional[str]) -> HeroCardDeckView:
            deck = HeroCardDeckView("TIPO PROBLEMA")
            for cat_key, cat_label, cat_icon in SWITCH_MENU_CATEGORIES:
                deck.add_card(
                    HeroCard(
                        title=cat_label,
                        icon_name=cat_icon,
                        on_select=lambda p=profile_id, c=cat_key: self._start_switch_serial_diagnostic(
                            profile_id=p,
                            category=c,
                        ),
                    )
                )
            return deck

        deck_serial_manual = HeroCardDeckView("PERFIL MANUAL")
        for prof in DEFAULT_SWITCH_REGISTRY.list_profiles():
            deck_serial_manual.add_card(
                HeroCard(
                    title=prof.display_name,
                    icon_name="SWITCH",
                    submenu=create_switch_category_deck(profile_id=prof.vendor_id),
                )
            )

        deck_consola_serial = HeroCardDeckView("CONSOLA SERIAL")
        deck_consola_serial.add_card(
            HeroCard(
                title="AUTO-DETECTAR",
                icon_name="SERIAL",
                submenu=create_switch_category_deck(profile_id=None),
            )
        )
        deck_consola_serial.add_card(
            HeroCard(
                title="PERFIL MANUAL",
                icon_name="TOOLS",
                submenu=deck_serial_manual,
            )
        )

        view_cisco_ssh_detail = self._create_detail_view("diag_cisco_ssh", "CISCO SSH")
        view_snmp_detail = self._create_detail_view("diag_snmp_scan", "ESCANER SNMP")

        deck_switches = HeroCardDeckView("SWITCHES")
        deck_switches.add_card(
            HeroCard(
                title="CONSOLA SERIAL",
                icon_name="SERIAL",
                submenu=deck_consola_serial,
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
        root_deck.add_card(HeroCard(title="SWITCHES", icon_name="SWITCH", submenu=deck_switches))
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
        """Polls completed diagnostic tasks and UI callbacks without blocking the UI loop."""
        while True:
            try:
                ui_cb = self._ui_callbacks.get_nowait()
            except queue.Empty:
                break
            try:
                ui_cb()
            except Exception as ex:
                logger.exception(f"Error executing UI callback: {ex}")

        while True:
            result = self.diag_manager.poll_result()
            if not result:
                break

            # Find matching detail view
            for task_id, detail_view in self._detail_views.items():
                plugin = self.diag_manager.get_plugin(task_id)
                if plugin and plugin.name == result.plugin_name:
                    status_badge = "OK" if result.status == DiagnosticStatus.SUCCESS else "ERROR"
                    notice_level = (
                        severity_to_notice_level(result.overall_status)
                        if result.status == DiagnosticStatus.SUCCESS
                        else severity_to_notice_level(result.status)
                    )
                    lines = result.details if result.details else [result.summary]
                    detail_view.set_content(
                        lines=lines,
                        status=status_badge,
                        is_loading=False,
                        level=notice_level,
                        metrics=result.metrics if isinstance(result.metrics, dict) else None,
                    )

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
                event = self.input_handler.get_event()
                while event is not None:
                    curr_view = self.screen_manager.current_view
                    if curr_view:
                        try:
                            if isinstance(curr_view, (WiFiPasswordView, KeyboardInputView)) or getattr(
                                curr_view, "accepts_text_input", False
                            ):
                                action = curr_view.handle_input(event, char=self.input_handler.get_last_char())
                            else:
                                action = curr_view.handle_input(event)

                            if action.action_type == ViewActionType.PUSH_VIEW and action.target_view:
                                self.screen_manager.push_view(action.target_view)
                            elif action.action_type == ViewActionType.REPLACE_VIEW and action.target_view:
                                self.screen_manager.replace_view(action.target_view)
                            elif action.action_type == ViewActionType.POP_VIEW:
                                self.screen_manager.pop_view()
                            elif action.action_type == ViewActionType.POP_TO_ROOT:
                                self.screen_manager.pop_to_root()
                            elif action.action_type == ViewActionType.EXECUTE_TASK and action.task_id:
                                self._trigger_task(action.task_id)
                        except Exception as ui_ex:
                            logger.exception(f"Error procesando entrada '{event}' en '{curr_view.title}': {ui_ex}")
                            formatted = format_oled_error(ui_ex, fallback_title="ERROR INTERFAZ")
                            self.screen_manager.push_view(
                                NoticeView(
                                    title=formatted.title,
                                    lines=formatted.lines,
                                    level=formatted.level,
                                )
                            )

                    event = self.input_handler.get_event()

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
