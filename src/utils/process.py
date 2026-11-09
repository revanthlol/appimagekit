"""
src/utils/process.py — Non-blocking asynchronous worker thread pool for AppImageKit.

Dispatches long-running background tasks (installations, update downloads, checksums,
and network requests) to a background thread pool and posts completion callbacks back to the
GTK/GLib main context via `GLib.idle_add`.
"""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Optional

logger = logging.getLogger("appimagekit.async_worker")


class AsyncWorker:
    """
    Thread pool runner that executes background tasks and dispatches completion
    and error callbacks to the GLib main loop.
    """

    def __init__(self, max_workers: int = 4, sync_dispatch: bool = False) -> None:
        self.max_workers = max_workers
        self.sync_dispatch = sync_dispatch
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="appimagekit-worker",
        )
        self._is_shutdown = False

    def _dispatch_idle(self, callback: Callable[..., Any], *args: Any) -> None:
        """Post a callback to be executed on the GLib main thread."""
        def _idle_cb() -> bool:
            try:
                callback(*args)
            except Exception as e:
                logger.exception("Exception in AsyncWorker callback: %s", e)
            return False  # GLib.SOURCE_REMOVE

        if self.sync_dispatch:
            _idle_cb()
            return

        try:
            from gi.repository import GLib
            GLib.idle_add(_idle_cb)
        except (ImportError, Exception):
            # Fallback for headless non-GLib environments or unit test harnesses
            _idle_cb()

    def run_async(
        self,
        task: Callable[..., Any],
        on_success: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
        *args: Any,
        **kwargs: Any,
    ) -> Future:
        """
        Execute `task(*args, **kwargs)` in the background thread pool.
        Upon completion:
        - If successful and `on_success` is provided, calls `on_success(result)` via GLib.idle_add.
        - If an exception is raised and `on_error` is provided, calls `on_error(exc)` via GLib.idle_add.
        """
        def _worker_wrapper() -> Any:
            try:
                result = task(*args, **kwargs)
                if on_success is not None:
                    self._dispatch_idle(on_success, result)
                return result
            except Exception as exc:
                logger.exception("AsyncWorker caught exception executing %s: %s", task, exc)
                if on_error is not None:
                    self._dispatch_idle(on_error, exc)
                raise

        return self._executor.submit(_worker_wrapper)

    def submit(self, task: Callable[..., Any], *args: Any, **kwargs: Any) -> Future:
        """Submit a task to the thread pool directly returning a Future."""
        return self._executor.submit(task, *args, **kwargs)

    def shutdown(self, wait: bool = False) -> None:
        """Shutdown the underlying thread pool executor."""
        if not self._is_shutdown:
            self._is_shutdown = True
            try:
                self._executor.shutdown(wait=wait, cancel_futures=True)
            except TypeError:
                # Python < 3.9 compatibility for cancel_futures
                self._executor.shutdown(wait=wait)


# Global default worker instance
_default_worker: Optional[AsyncWorker] = None


def get_default_worker() -> AsyncWorker:
    """Retrieve or initialize the global default AsyncWorker singleton."""
    global _default_worker
    if _default_worker is None:
        _default_worker = AsyncWorker()
    return _default_worker


def run_async(
    task: Callable[..., Any],
    on_success: Optional[Callable[[Any], None]] = None,
    on_error: Optional[Callable[[Exception], None]] = None,
    *args: Any,
    **kwargs: Any,
) -> Future:
    """Convenience helper to dispatch tasks to the default AsyncWorker."""
    return get_default_worker().run_async(task, on_success, on_error, *args, **kwargs)
