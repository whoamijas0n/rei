"""
OmniDiag Hub - Diagnostic Manager
Handles asynchronous execution of diagnostic plugins using a worker thread pool.
"""

from concurrent.futures import ThreadPoolExecutor, Future
import logging
import queue
import time
from typing import Callable, Dict, Optional

from .interfaces import IDiagnosticPlugin, DiagnosticResult, DiagnosticStatus

logger = logging.getLogger("OmniDiag.Core.Manager")


class DiagnosticManager:
    """
    Decoupled Producer for diagnostic routines.
    Executes background tasks in worker threads to prevent UI freezes.
    """

    def __init__(self, max_workers: int = 2):
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="OmniDiagWorker"
        )
        self._plugins: Dict[str, IDiagnosticPlugin] = {}
        self._active_tasks: Dict[str, Future] = {}
        self._result_queue: queue.Queue = queue.Queue()

    def register_plugin(self, plugin: IDiagnosticPlugin) -> None:
        """Registers a diagnostic plugin instance."""
        self._plugins[plugin.id] = plugin
        logger.info(f"Registered diagnostic plugin: {plugin.name} [{plugin.id}]")

    def get_plugin(self, plugin_id: str) -> Optional[IDiagnosticPlugin]:
        """Retrieves a registered plugin by ID."""
        return self._plugins.get(plugin_id)

    def execute_async(
        self,
        plugin_id: str,
        on_complete: Optional[Callable[[DiagnosticResult], None]] = None,
        **kwargs
    ) -> bool:
        """
        Submits a plugin execution task to the background thread pool.
        Non-blocking to the caller.
        """
        plugin = self.get_plugin(plugin_id)
        if not plugin:
            logger.error(f"Plugin ID '{plugin_id}' not found.")
            return False

        # Cancel or skip if already running
        if plugin_id in self._active_tasks and not self._active_tasks[plugin_id].done():
            logger.warning(f"Plugin '{plugin_id}' is already executing.")
            return False

        def _worker_wrapper():
            start_time = time.monotonic()
            try:
                result = plugin.run(**kwargs)
            except Exception as ex:
                logger.exception(f"Unhandled error executing plugin '{plugin_id}': {ex}")
                result = DiagnosticResult(
                    plugin_name=plugin.name,
                    status=DiagnosticStatus.FAILED,
                    summary="Error de ejecución",
                    details=[str(ex)],
                )
            result.elapsed_seconds = round(time.monotonic() - start_time, 2)
            self._result_queue.put(result)
            if on_complete:
                try:
                    on_complete(result)
                except Exception as cb_err:
                    logger.error(f"Callback error for '{plugin_id}': {cb_err}")
            return result

        future = self._executor.submit(_worker_wrapper)
        self._active_tasks[plugin_id] = future
        return True

    def poll_result(self) -> Optional[DiagnosticResult]:
        """Non-blocking retrieval of completed diagnostic results."""
        try:
            return self._result_queue.get_nowait()
        except queue.Empty:
            return None

    def is_running(self, plugin_id: str) -> bool:
        """Checks if a given plugin is currently executing."""
        task = self._active_tasks.get(plugin_id)
        return task is not None and not task.done()

    def shutdown(self, wait: bool = False) -> None:
        """Shuts down the worker thread pool."""
        self._executor.shutdown(wait=wait)
