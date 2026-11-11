"""Timestamped file and action logging for the desktop application.

Existing modules use ``print`` for diagnostics, including worker threads. Tee
stdout and stderr into one per-run log so those messages remain available in a
windowed/frozen build while still appearing in a developer console when present.
"""

from datetime import datetime
import atexit
import json
import os
import re
import sys
import threading
import time


_ACTIVE_SESSION = None


def _timestamp():
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


class _TeeStream:
    def __init__(self, log_file, console_stream, lock, stream_name):
        self._log_file = log_file
        self._console_stream = console_stream
        self._lock = lock
        self._stream_name = stream_name
        self._pending = ""

    @property
    def encoding(self):
        return getattr(self._console_stream, "encoding", None) or "utf-8"

    @property
    def errors(self):
        return getattr(self._console_stream, "errors", None) or "replace"

    def write(self, text):
        if not isinstance(text, str):
            text = str(text)
        if not text:
            return 0
        with self._lock:
            self._pending += text
            while "\n" in self._pending:
                line, self._pending = self._pending.split("\n", 1)
                self._log_file.write(
                    f"{_timestamp()} [{self._stream_name}] {line}\n"
                )
            self._log_file.flush()
            if self._console_stream is not None:
                try:
                    self._console_stream.write(text)
                except (OSError, ValueError):
                    # A GUI build may lose its console after startup.
                    pass
        return len(text)

    def flush(self):
        with self._lock:
            if self._pending:
                self._log_file.write(
                    f"{_timestamp()} [{self._stream_name}] {self._pending}"
                )
                self._pending = ""
            self._log_file.flush()
            if self._console_stream is not None:
                try:
                    self._console_stream.flush()
                except (OSError, ValueError):
                    pass

    def isatty(self):
        if self._console_stream is None:
            return False
        try:
            return self._console_stream.isatty()
        except (AttributeError, OSError, ValueError):
            return False

    def fileno(self):
        # Expose the log descriptor when there is no console. With a console,
        # forward its descriptor for compatibility with terminal libraries.
        stream = self._console_stream or self._log_file
        return stream.fileno()

    def writable(self):
        return True

    def __getattr__(self, name):
        stream = self._console_stream or self._log_file
        return getattr(stream, name)


class LogSession:
    """Owns a log file and restores stdout/stderr when the app exits."""

    def __init__(self, path, log_file, original_stdout, original_stderr, lock):
        self.path = path
        self._log_file = log_file
        self._original_stdout = original_stdout
        self._original_stderr = original_stderr
        self._lock = lock
        self._closed = False
        self.stdout_stream = _TeeStream(log_file, original_stdout, lock, "STDOUT")
        self.stderr_stream = _TeeStream(log_file, original_stderr, lock, "STDERR")
        sys.stdout = self.stdout_stream
        sys.stderr = self.stderr_stream

    def log_action(self, action, **details):
        if self._closed:
            return
        action = str(action).replace("\n", " ").replace("\r", " ")
        payload = json.dumps(details, ensure_ascii=False, sort_keys=True, default=str)
        with self._lock:
            self._log_file.write(f"{_timestamp()} [ACTION] {action} {payload}\n")
            self._log_file.flush()

    def flush(self):
        if self._closed:
            return
        with self._lock:
            self.stdout_stream.flush()
            self.stderr_stream.flush()
            self._log_file.flush()

    def close(self):
        global _ACTIVE_SESSION
        if self._closed:
            return
        with self._lock:
            if sys.stdout is self.stdout_stream:
                sys.stdout = self._original_stdout
            if sys.stderr is self.stderr_stream:
                sys.stderr = self._original_stderr
            try:
                self.stdout_stream.flush()
                self.stderr_stream.flush()
                self._log_file.flush()
            finally:
                self._log_file.close()
                self._closed = True
                if _ACTIVE_SESSION is self:
                    _ACTIVE_SESSION = None


def _application_directory():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _log_directory_candidates(base_dir):
    candidates = [os.path.join(base_dir, "logs")]
    if os.name == "nt":
        user_root = os.environ.get("LOCALAPPDATA")
        if user_root:
            candidates.append(os.path.join(user_root, "LiveTraffic", "logs"))
    else:
        user_root = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
        candidates.append(os.path.join(user_root, "livetraffic", "logs"))
    return list(dict.fromkeys(os.path.abspath(path) for path in candidates))


def start_file_logging(app_name="LiveTraffic", base_dir=None):
    """Create a timestamped log file and tee Python output and actions into it.

    The executable directory is preferred. If it is read-only, use the current
    user's writable application-data directory instead. Logging setup must not
    prevent the GUI itself from launching if both locations are unavailable.
    """
    global _ACTIVE_SESSION
    base_dir = os.path.abspath(base_dir or _application_directory())
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(app_name)).strip("._") or "LiveTraffic"
    original_stdout, original_stderr = sys.stdout, sys.stderr
    log_file = None
    log_path = None
    last_error = None

    for log_dir in _log_directory_candidates(base_dir):
        try:
            os.makedirs(log_dir, exist_ok=True)
            stamp = datetime.now().astimezone()
            filename = f"{safe_name}_{stamp:%Y%m%d_%H%M%S}_{stamp.microsecond:06d}_{os.getpid()}.log"
            log_path = os.path.join(log_dir, filename)
            log_file = open(log_path, "a", encoding="utf-8", buffering=1)
            break
        except OSError as exc:
            last_error = exc

    if log_file is None:
        if original_stderr is not None:
            try:
                original_stderr.write(f"[Logging] Could not create a log file: {last_error}\n")
                original_stderr.flush()
            except (OSError, ValueError):
                pass
        return _NullLogSession()

    session = LogSession(
        log_path, log_file, original_stdout, original_stderr, threading.RLock()
    )
    _ACTIVE_SESSION = session
    atexit.register(session.close)
    started = datetime.now().astimezone().isoformat(timespec="seconds")
    print(f"=== {safe_name} log started: {started} ===")
    print(f"Log file: {log_path}")
    return session


def log_action(action, **details):
    """Write one timestamped, structured record for a user or system action."""
    session = _ACTIVE_SESSION
    if session is not None:
        session.log_action(action, **details)


MAX_RESPONSE_LOG_CHARS = 8192


def response_debug_payload(response, max_chars=MAX_RESPONSE_LOG_CHARS):
    """Return a bounded body excerpt for diagnosing invalid HTTP responses.

    Only the body and status code are included; request/response headers are
    deliberately omitted to avoid copying credentials or cookies into logs.
    """
    if response is None:
        return None
    try:
        body = response.text
    except Exception:
        body = None
    if body is None:
        try:
            content = response.content
            body = (content.decode("utf-8", errors="replace")
                    if isinstance(content, bytes) else str(content))
        except Exception:
            body = "<response body unavailable>"
    elif not isinstance(body, str):
        body = str(body)
    limit = max(0, int(max_chars))
    return {
        "status_code": getattr(response, "status_code", None),
        "body_excerpt": body[:limit],
        "body_chars": len(body),
        "truncated": len(body) > limit,
    }


class ErrorStateTracker:
    """Deduplicate recurring errors and emit one event when they recover."""

    def __init__(self, clock=None):
        self._lock = threading.Lock()
        self._states = {}
        self._clock = clock or time.monotonic

    def record_failure(self, key, action, error, now=None, **details):
        error_text = str(error)
        now = self._clock() if now is None else now
        with self._lock:
            state = self._states.get(key)
            first_failure = state is None
            has_response = details.get("response") is not None
            if state is None:
                state = {
                    "since": now,
                    "count": 1,
                    "first_error": error_text,
                    "last_error": error_text,
                    "response_logged": has_response,
                }
                self._states[key] = state
                should_log = True
            else:
                state["count"] += 1
                state["last_error"] = error_text
                # A transport error may start the outage before an HTTP body
                # becomes available. Capture the first response later, once.
                should_log = has_response and not state["response_logged"]
                if should_log:
                    state["response_logged"] = True
        if should_log:
            log_action(action, error=error_text, **details)
        return first_failure

    def record_recovery(self, key, action, now=None, **details):
        now = self._clock() if now is None else now
        with self._lock:
            state = self._states.pop(key, None)
        if state is None:
            return False
        log_action(
            action,
            failure_duration_sec=round(max(0.0, now - state["since"]), 1),
            failure_count=state["count"],
            first_error=state["first_error"],
            last_error=state["last_error"],
            **details,
        )
        return True


class _NullLogSession:
    """No-op session returned when all log locations are unavailable."""

    path = None

    def flush(self):
        return None

    def close(self):
        return None
