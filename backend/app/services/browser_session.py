"""One persistent, visible Chromium the user owns and logs into.

WHY A DEDICATED THREAD
Playwright's async API launches the browser with asyncio.create_subprocess_exec,
which on Windows only works on a ProactorEventLoop. Uvicorn's loop factory picks
SelectorEventLoop whenever it runs with subprocesses (that is, under --reload), so
the async API dies with a bare NotImplementedError there.

Rather than forbid --reload, this module runs Playwright's *sync* API on a private
daemon thread that owns the browser for the process lifetime. The thread has no
asyncio loop of its own, which is exactly what the sync API wants, and the server's
loop choice becomes irrelevant. It also satisfies Playwright's rule that sync
objects are only touched from the thread that created them.

Design constraints that keep this within site expectations:
  - Never headless. The window is visible and the user can take over at any moment.
  - A persistent profile directory, so logins survive restarts. Credentials are
    never seen, stored or transmitted by this app - the browser holds its own
    cookies exactly as it would if the user opened Chrome themselves.
  - Human-paced navigation; one browser, one command at a time.
"""
import asyncio
import contextlib
import queue
import random
import threading
import time
from concurrent.futures import Future

from app.core import settings

_STOP = object()

# Playwright reports a closed window several ways depending on where it lands.
CLOSED_MARKERS = (
    "targetclosederror", "target page, context or browser has been closed",
    "browser has been closed", "connection closed", "target closed",
)


def _is_closed_error(error: BaseException) -> bool:
    blob = f"{type(error).__name__} {error}".casefold()
    return any(marker in blob for marker in CLOSED_MARKERS)


def _first_line(error: BaseException | None) -> str:
    """The one readable sentence out of a Playwright launch failure.

    A failed launch carries the whole command line and the browser's stderr. The
    first line is the part worth showing someone.
    """
    if error is None:
        return "no reason recorded"
    first = next((line.strip() for line in str(error).splitlines() if line.strip()), "")
    return first or type(error).__name__


class _BrowserWorker:
    """Owns the Playwright instance and the persistent context on one thread."""

    def __init__(self) -> None:
        self._commands: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._start_error: BaseException | None = None
        self._playwright = None
        self._context = None
        self._context_closed = True
        self._channel_used = ""
        self._channel_wanted = ""
        self._channel_note = ""
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- thread
    def _loop(self) -> None:
        try:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
        except BaseException as error:  # noqa: BLE001 - reported to callers
            self._start_error = error
            self._ready.set()
            return

        self._ready.set()
        while True:
            item = self._commands.get()
            if item is _STOP:
                break
            function, future = item
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(function(self))
            except BaseException as error:  # noqa: BLE001 - surfaced to caller
                if _is_closed_error(error):
                    # The user closed the window mid-flight. Relaunch and try once
                    # more rather than making them retry by hand.
                    self.reset()
                    try:
                        future.set_result(function(self))
                        continue
                    except BaseException as retry_error:  # noqa: BLE001
                        error = retry_error
                future.set_exception(error)

        try:
            self.reset()
        except Exception:
            pass
        try:
            self._playwright.stop()
        except Exception:
            pass
        self._context = None
        self._playwright = None

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._ready.clear()
            self._start_error = None
            self._thread = threading.Thread(
                target=self._loop, name="playwright-browser", daemon=True
            )
            self._thread.start()
        self._ready.wait(timeout=60)
        if self._start_error is not None:
            raise RuntimeError(
                "Playwright failed to start. Run "
                "'.venv\\Scripts\\python.exe -m playwright install chromium' "
                f"in the project folder. Original error: {self._start_error}"
            )

    # --------------------------------------------------------------- context
    def context(self):
        """Launch the persistent context if it is not already open. Thread-only.

        The user can close the browser window at any time, which kills the
        context. Reading `.pages` on a dead context does not reliably raise, so
        liveness is tracked by Playwright's own close event instead of probing.
        """
        if self._context is not None and not self._context_closed:
            return self._context
        self._context = None

        options = {
            "user_data_dir": str(settings.browser_profile_path()),
            "headless": False,
            "args": ["--disable-blink-features=AutomationControlled", "--start-maximized"],
            "viewport": None,
            "ignore_default_args": ["--enable-automation"],
        }

        # Prefer the real installed Chrome. Google refuses OAuth sign-in ("this
        # browser or app may not be secure") in Playwright's unbranded Chromium,
        # which blocks "Sign in with Google" on Indeed, Handshake and others.
        # Branded Chrome with a persistent profile behaves like a normal browser.
        channel = settings.load().get("browser_channel") or "chrome"

        # The chosen browser is tried TWICE before anything else is considered.
        #
        # Chrome and Edge share this one profile directory, and each stamps it
        # with its own version on exit. Edge ships ahead of Chrome, so after Edge
        # has run once the directory looks like a downgrade to Chrome: it moves
        # the too-new data aside (chrome/browser/downgrade/downgrade_utils.cc)
        # and exits during that first launch, which Playwright reports as
        # "Target page, context or browser has been closed". The migration
        # succeeded - the very next launch works, with cookies and logins intact.
        #
        # Trying the substitute on that first failure is what made this permanent:
        # Edge launched, re-stamped the directory with its own newer version, and
        # restored the exact condition for next time. The chosen browser never got
        # the second chance that would have fixed it, and nothing said so.
        attempts = [channel, channel, "msedge", None] if channel != "chromium" else [None, None]

        self._channel_wanted = channel
        self._channel_note = ""
        last_error: Exception | None = None
        for index, attempt in enumerate(attempts):
            try:
                self._context = self._playwright.chromium.launch_persistent_context(
                    **({**options, "channel": attempt} if attempt else options)
                )
                self._channel_used = attempt or "chromium"
                if self._channel_used != channel:
                    # A substitute is never silent. The setting said one thing and
                    # the window is another, which the user can see; they must be
                    # able to find out why without reading the log.
                    self._channel_note = (
                        f"{channel} could not start, so {self._channel_used} was used "
                        f"instead: {_first_line(last_error)}"
                    )
                last_error = None
                break
            except Exception as error:
                last_error = error
                if "existing browser session" in str(error) or "already in use" in str(error):
                    break   # a channel fallback will not help; report it properly
                if index == 0:
                    continue   # the retry above, before any substitute

        if last_error is not None:
            error = last_error
            # A Chromium from an earlier backend run still holds the profile lock.
            # Restarting the backend does not close it, so say so explicitly rather
            # than surfacing Playwright's wall of launch flags.
            if "existing browser session" in str(error) or "already in use" in str(error):
                raise RuntimeError(
                    "A browser window using this profile is already open - usually one "
                    "left over from a previous run. Press 'Close browser' on the Sources "
                    "tab to release it, then try again. Your login lives in the profile "
                    "folder and survives closing the window."
                ) from error
            raise error
        self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        # Closing the window (or a crash) must invalidate our handle immediately,
        # otherwise the next new_page() fails with TargetClosedError.
        self._context_closed = False
        self._context.on("close", self._on_context_closed)
        return self._context

    def _on_context_closed(self, _context=None) -> None:
        self._context_closed = True

    def reset(self) -> None:
        """Drop the current context so the next call relaunches the browser."""
        if self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass
        self._context = None
        self._context_closed = True

    def has_context(self) -> bool:
        return self._context is not None and not self._context_closed

    # ---------------------------------------------------------------- submit
    def submit(self, function) -> Future:
        self._ensure_thread()
        future: Future = Future()
        self._commands.put((function, future))
        return future

    def shutdown(self) -> None:
        with self._lock:
            thread = self._thread
            self._thread = None
        if thread is not None and thread.is_alive():
            self._commands.put(_STOP)
            thread.join(timeout=30)


_worker = _BrowserWorker()


class Cancelled(RuntimeError):
    """The caller gave up on this operation, so it stopped between actions."""


class Busy(RuntimeError):
    """Another operation is already running against this application."""


# One in-flight browser operation per application, and the page each application
# is being filled on. Both live here rather than in the autofiller because the
# browser thread is the thing being shared.
_ACTIVE: dict[str, str] = {}
_CANCELLED: set[str] = set()
_PAGES: dict[str, object] = {}
_STATE_LOCK = threading.Lock()


def bind_page(application_id: str, page) -> None:
    """Remember which page an application is being filled on."""
    if application_id:
        with _STATE_LOCK:
            _PAGES[application_id] = page


def bound_page(application_id: str):
    """That page, if it is still open. A closed page is forgotten rather than used."""
    with _STATE_LOCK:
        page = _PAGES.get(application_id)
    if page is None:
        return None
    try:
        if page.is_closed():
            with _STATE_LOCK:
                _PAGES.pop(application_id, None)
            return None
    except Exception:
        return None
    return page


def cancelled(application_id: str) -> bool:
    with _STATE_LOCK:
        return application_id in _CANCELLED


def check_cancelled(application_id: str) -> None:
    """Raise between actions if the caller has given up. Call this in any loop."""
    if application_id and cancelled(application_id):
        raise Cancelled(
            "Stopped at your request. Whatever had already been filled is still "
            "on the page."
        )


def cancel(application_id: str) -> bool:
    """Ask a running operation to stop at its next action boundary."""
    with _STATE_LOCK:
        if application_id not in _ACTIVE:
            return False
        _CANCELLED.add(application_id)
        return True


@contextlib.contextmanager
def exclusive(application_id: str, operation: str):
    """Hold this application against concurrent operations for the duration.

    Two fills racing on one page interleave keystrokes and leave a form that
    neither of them would have produced, so the second caller is refused rather
    than queued - the applicant can see the first one finish.
    """
    if not application_id:
        yield
        return
    with _STATE_LOCK:
        running = _ACTIVE.get(application_id)
        if running:
            raise Busy(
                f"{running} is already running for this application. Wait for it to "
                "finish, or press Stop."
            )
        _ACTIVE[application_id] = operation
        _CANCELLED.discard(application_id)
    try:
        yield
    finally:
        with _STATE_LOCK:
            _ACTIVE.pop(application_id, None)
            _CANCELLED.discard(application_id)


async def run(function, timeout: float = 300.0, application_id: str = ""):
    """Run `function(worker)` on the browser thread and await its result.

    A timeout used to abandon the future and return, leaving the browser thread
    still typing into a page nobody was watching any more. Now the operation is
    marked cancelled first, so it stops at its next action boundary instead of
    running on unattended.
    """
    future = _worker.submit(function)
    try:
        return await asyncio.wait_for(asyncio.wrap_future(future), timeout=timeout)
    except asyncio.TimeoutError:
        if application_id:
            with _STATE_LOCK:
                _CANCELLED.add(application_id)
        raise TimeoutError(
            f"The browser did not finish within {int(timeout)} seconds. It has been "
            "told to stop; look at the browser window to see how far it reached."
        ) from None


def run_sync(function, timeout: float = 300.0):
    """Same, for callers that are not on an event loop."""
    return _worker.submit(function).result(timeout=timeout)


def human_pause(low: float = 0.6, high: float = 1.6) -> None:
    """Blocking pause - called from the browser thread, never the event loop."""
    time.sleep(random.uniform(low, high))


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

async def start():
    await run(lambda worker: bool(worker.context()), timeout=120)
    return True


async def stop() -> None:
    _worker.shutdown()


def is_open() -> bool:
    return _worker.has_context()


def channel_in_use() -> str:
    """Which browser build the session actually launched."""
    return _worker._channel_used


def channel_status() -> dict:
    """What was asked for, what is running, and why they differ.

    The setting is a promise about which browser opens. When it cannot be kept,
    saying so is part of keeping it honest - otherwise the only symptom is an
    Edge window appearing for someone who chose Chrome.
    """
    wanted = _worker._channel_wanted or settings.load().get("browser_channel") or "chrome"
    used = _worker._channel_used
    return {
        "browser_wanted": wanted,
        "browser_in_use": used,
        "browser_substituted": bool(used) and used != wanted,
        "browser_note": _worker._channel_note,
    }


async def open_at(url: str):
    """Open a new tab at `url` and leave it open for the user."""

    def command(worker):
        page = worker.context().new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        return page.url

    return await run(command, timeout=120)


async def render_html(url: str, wait_selector: str | None = None, timeout: int = 20000) -> str:
    """Fetch fully-rendered HTML for a page that needs JavaScript."""

    def command(worker):
        page = worker.context().new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=timeout)
                except Exception:
                    pass
            else:
                page.wait_for_timeout(2500)
            return page.content()
        finally:
            try:
                page.close()
            except Exception:
                pass

    return await run(command, timeout=150)


def release_profile() -> dict:
    """Close a Chromium left holding the profile by an earlier backend run.

    Restarting the backend orphans its browser, and the orphan keeps the profile
    locked, so every later launch fails with "existing browser session". Nothing
    in-process can close it - it belongs to a dead parent.

    Deliberately narrow: matched on this project's profile directory, which no
    other browser would ever use, so a personal Chrome is never affected. The
    binary path is NOT part of the match - the session runs the installed Chrome
    or Edge, not only Playwright's bundled Chromium.
    """
    import subprocess

    profile = str(settings.browser_profile_path())
    script = (
        "Get-CimInstance Win32_Process "
        "-Filter \"Name='chrome.exe' OR Name='msedge.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{profile}*' }} | "
        "Select-Object -ExpandProperty ProcessId"
    )
    try:
        found = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, text=True, timeout=30,
        )
        pids = [line.strip() for line in found.stdout.splitlines() if line.strip().isdigit()]
    except Exception as error:
        return {"released": 0, "error": f"{type(error).__name__}: {error}"}

    killed = 0
    for pid in pids:
        try:
            subprocess.run(["taskkill", "/F", "/PID", pid],
                           capture_output=True, timeout=15)
            killed += 1
        except Exception:
            continue
    return {"released": killed, "profile": profile}
