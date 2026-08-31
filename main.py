"""
REI - Main Application Entry Point
Pocket autonomous network & endpoint diagnostic tool for Raspberry Pi Zero 2 W.
Hardware: Waveshare 1.3" OLED HAT (SH1106, 128x64 px) with 5-way joystick & 3 buttons.
Architecture: 100% OOP, Decoupled Producer/Consumer, Fixed 30 FPS UI Event Loop.
"""

import logging
import signal
import sys
import time
from typing import Dict, Optional

from core.interfaces import DiagnosticResult, DiagnosticStatus
from core.manager import DiagnosticManager
from core.plugins import (
    IPAddressPlugin,
    WiFiScanPlugin,
    BatteryStatusPlugin,
    SystemStatusPlugin,
    CiscoSerialPlugin,
    CiscoSSHPlugin,
    SNMPScanPlugin,
    WindowsRNDISPlugin,
    LinuxSSHPlugin,
    VaultPlugin,
    PoweroffPlugin,
    RebootPlugin,
    SystemUpdatePlugin,
    AppUpdatePlugin,
)
from ui.display import (
    ScreenManager,
    HeroCardDeckView,
    DetailCardView,
    UpdateProgressView,
    HeroCard,
    ViewAction,
    ViewActionType,
)
from ui.input_handler import GPIOInputHandler, InputEvent

# Configure high-performance logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("REI.Main")


class REIApp:
    """
    Main Application Controller for REI.
    Orchestrates the ScreenManager, GPIOInputHandler, and DiagnosticManager.
    """

    TARGET_FPS = 30
    FRAME_DURATION = 1.0 / float(TARGET_FPS)

    def __init__(self):
        self.running = False

        # 1. Initialize Subsystems
        self.input_handler = GPIOInputHandler()
        self.screen_manager = ScreenManager(width=128, height=64)
        self.diag_manager = DiagnosticManager(max_workers=2)

        # 2. Register Diagnostic Plugins
        self._register_plugins()

        # 3. Cache of Views for background updates
        self._detail_views: Dict[str, DetailCardView] = {}
        self._progress_views: Dict[str, UpdateProgressView] = {}

        # 4. Build Hierarchical Menu Tree (Strict Prototype Fidelity)
        self._build_menu_hierarchy()

        # 5. Handle POSIX termination signals
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _register_plugins(self) -> None:
        """Instantiates and registers all decoupled diagnostic plugins."""
        plugins = [
            IPAddressPlugin(),
            WiFiScanPlugin(),
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
            on_refresh=trigger_diagnostic
        )
        self._detail_views[task_id] = view
        return view

    def _create_update_view(self, task_id: str, title: str) -> UpdateProgressView:
        """Factory for blocking update progress views wired to asynchronous updates."""
        def on_start():
            self._trigger_update_task(task_id)

        view = UpdateProgressView(
            title=title,
            on_start=on_start
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
            on_complete=self._on_task_completed
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
            on_progress=on_progress
        )

    def _on_task_completed(self, result: DiagnosticResult) -> None:
        """Callback invoked by worker thread when diagnostic completes."""
        logger.info(f"Task completed: {result.plugin_name} ({result.status.name})")

    def _build_menu_hierarchy(self) -> None:
        """
        Builds the exact navigation hierarchy:
        - Level 0 (Main): UTILIDADES, SWITCHES / RED, ENDPOINTS PC, BOVEDA / VAULT
        - Level 1 (Utilidades): CONEXION DE RED, ESTADO BATERIA, ESTADO SISTEMA, ACTUALIZACIONES, ALIMENTACION
        - Level 2 (Conexion): VER DIRECCION IP, ESCANEAR WI-FI
        - Level 2 (Actualizaciones): ACTUALIZAR SISTEMA, ACTUALIZAR REI
        - Level 2 (Alimentacion): APAGAR, REINICIAR
        """

        # ----------------------------------------------------
        # LEVEL 2: Sub-menus for CONEXION DE RED
        # ----------------------------------------------------
        view_ip_detail = self._create_detail_view("diag_ip_address", "DIRECCION IP")
        view_wifi_detail = self._create_detail_view("diag_wifi_scan", "ESCANER WI-FI")

        deck_net_conn = HeroCardDeckView("CONEXION DE RED")
        deck_net_conn.add_card(
            HeroCard(
                title="VER DIRECCION IP",
                icon_name="IP",
                submenu=view_ip_detail,
                on_select=lambda: self._trigger_task("diag_ip_address")
            )
        )
        deck_net_conn.add_card(
            HeroCard(
                title="ESCANEAR WI-FI",
                icon_name="WIFI",
                submenu=view_wifi_detail,
                on_select=lambda: self._trigger_task("diag_wifi_scan")
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
                on_select=lambda: view_apt_update.start()
            )
        )
        deck_actualizaciones.add_card(
            HeroCard(
                title="ACTUALIZAR REI",
                icon_name="GIT",
                submenu=view_git_update,
                on_select=lambda: view_git_update.start()
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
                on_select=lambda: self._trigger_task("sys_poweroff")
            )
        )
        deck_alimentacion.add_card(
            HeroCard(
                title="REINICIAR",
                icon_name="REBOOT",
                submenu=view_reboot_detail,
                on_select=lambda: self._trigger_task("sys_reboot")
            )
        )

        # ----------------------------------------------------
        # LEVEL 1: Sub-menus for UTILIDADES
        # ----------------------------------------------------
        view_battery_detail = self._create_detail_view("diag_battery", "ESTADO BATERIA")
        view_system_detail = self._create_detail_view("diag_system", "ESTADO SISTEMA")

        deck_utilidades = HeroCardDeckView("UTILIDADES")
        deck_utilidades.add_card(
            HeroCard(
                title="CONEXION DE RED",
                icon_name="NETWORK",
                submenu=deck_net_conn
            )
        )
        deck_utilidades.add_card(
            HeroCard(
                title="ESTADO BATERIA",
                icon_name="BATTERY",
                submenu=view_battery_detail,
                on_select=lambda: self._trigger_task("diag_battery")
            )
        )
        deck_utilidades.add_card(
            HeroCard(
                title="ESTADO SISTEMA",
                icon_name="CPU",
                submenu=view_system_detail,
                on_select=lambda: self._trigger_task("diag_system")
            )
        )
        deck_utilidades.add_card(
            HeroCard(
                title="ACTUALIZACIONES",
                icon_name="UPDATE",
                submenu=deck_actualizaciones
            )
        )
        deck_utilidades.add_card(
            HeroCard(
                title="ALIMENTACION",
                icon_name="POWER",
                submenu=deck_alimentacion
            )
        )

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
                on_select=lambda: self._trigger_task("diag_cisco_serial")
            )
        )
        deck_switches.add_card(
            HeroCard(
                title="CISCO SSH",
                icon_name="SSH",
                submenu=view_cisco_ssh_detail,
                on_select=lambda: self._trigger_task("diag_cisco_ssh")
            )
        )
        deck_switches.add_card(
            HeroCard(
                title="ESCANER SNMP",
                icon_name="SNMP",
                submenu=view_snmp_detail,
                on_select=lambda: self._trigger_task("diag_snmp_scan")
            )
        )

        # ----------------------------------------------------
        # LEVEL 1: Sub-menus for ENDPOINTS PC
        # ----------------------------------------------------
        view_win_rndis_detail = self._create_detail_view("diag_win_rndis", "WINDOWS RNDIS")
        view_linux_ssh_detail = self._create_detail_view("diag_linux_ssh", "LINUX SSH")

        deck_endpoints = HeroCardDeckView("ENDPOINTS PC")
        deck_endpoints.add_card(
            HeroCard(
                title="WINDOWS USB-RNDIS",
                icon_name="WINDOWS",
                submenu=view_win_rndis_detail,
                on_select=lambda: self._trigger_task("diag_win_rndis")
            )
        )
        deck_endpoints.add_card(
            HeroCard(
                title="LINUX SSH",
                icon_name="LINUX",
                submenu=view_linux_ssh_detail,
                on_select=lambda: self._trigger_task("diag_linux_ssh")
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
                on_select=lambda: self._trigger_task("diag_vault")
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
                        details=result.details
                    )

    def _handle_signal(self, signum, frame):
        """Signal handler for graceful shutdown."""
        logger.info(f"Received shutdown signal ({signum}). Exiting...")
        self.running = False

    def run(self) -> None:
        """
        Executes the fixed 30 FPS UI Event Loop.
        Consumes GPIO events, processes asynchronous diagnostic outputs,
        and renders double-buffered OLED frames with high temporal precision.
        """
        self.running = True
        logger.info("REI UI Event Loop started at 30 FPS.")

        next_frame_time = time.monotonic()

        try:
            while self.running:
                frame_start = time.monotonic()

                # 1. Non-blocking Input Consumption
                event = self.input_handler.get_event()
                while event is not None:
                    curr_view = self.screen_manager.current_view
                    if curr_view:
                        action = curr_view.handle_input(event)
                        if action.action_type == ViewActionType.PUSH_VIEW and action.target_view:
                            self.screen_manager.push_view(action.target_view)
                        elif action.action_type == ViewActionType.POP_VIEW:
                            self.screen_manager.pop_view()
                        elif action.action_type == ViewActionType.EXECUTE_TASK and action.task_id:
                            self._trigger_task(action.task_id)

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
        """Releases hardware and thread pool resources."""
        logger.info("Shutting down REI...")
        self.running = False
        self.input_handler.close()
        self.diag_manager.shutdown(wait=False)
        self.screen_manager.clear()
        logger.info("Shutdown complete.")

if __name__ == "__main__":
    app = REIApp()
    app.run()
