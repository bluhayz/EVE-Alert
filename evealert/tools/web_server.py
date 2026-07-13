"""Minimal async HTTP status server for EVE Alert.

When enabled in Settings (web_ui.enabled = true), serves a read-only
JSON API and a self-refreshing HTML dashboard at http://localhost:{port}/.

Runs as an asyncio task inside the alert daemon thread — zero new
dependencies (uses Python's built-in asyncio streams only).

Endpoints:
  GET /          — HTML dashboard (auto-refreshes every 3 s)
  GET /api/status — JSON: running, session_alarms, total_alarms, version, uptime
  GET /api/log   — JSON: last 50 log lines (newest first)
  GET /api/alarm/latest  — JSON: most-recent alarm payload (null when none)
"""

import asyncio
import json
import logging
import time
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from evealert.statistics import AlarmStatistics

logger = logging.getLogger("alert.web")

_LOG_BUFFER: deque[str] = deque(maxlen=50)
_START_TIME: float = time.time()
# Latest alarm payload for /api/alarm/latest (#153)
_LATEST_ALARM: dict | None = None


def set_latest_alarm(payload: dict) -> None:
    """Update the latest-alarm slot (called by alertmanager on each alarm)."""
    global _LATEST_ALARM  # noqa: PLW0603
    _LATEST_ALARM = payload

_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="3">
<title>EVE Alert Status</title>
<style>
body {{ font-family: monospace; background:#1a1a2e; color:#eee; padding:20px; }}
h1 {{ color:#00d4ff; }}
.card {{ background:#16213e; border:1px solid #0f3460; border-radius:8px;
         padding:16px; margin:10px 0; }}
.running {{ color:#4ade80; }} .stopped {{ color:#f87171; }}
.log-line {{ border-bottom:1px solid #0f3460; padding:4px 0; font-size:12px; }}
</style>
</head>
<body>
<h1>EVE Alert v{version} — Status</h1>
<div class="card">
  <b>Detection:</b>
  <span class="{status_class}">{status}</span>
  &nbsp;|&nbsp; <b>Uptime:</b> {uptime}
</div>
<div class="card">
  <b>Session alarms:</b> {session_alarms} &nbsp;|&nbsp;
  <b>Lifetime alarms:</b> {total_alarms}
</div>
<div class="card">
  <b>Log (last 50 lines)</b>
  <div>
    {log_lines}
  </div>
</div>
</body>
</html>"""


class WebStatusServer:
    """Minimal asyncio HTTP server — no third-party dependencies."""

    def __init__(
        self, port: int, stats_ref: "AlarmStatistics", running_ref: list[bool]
    ) -> None:
        self._port = port
        self._stats = stats_ref
        self._running_ref = running_ref  # mutable list so the server sees live state
        self._server: asyncio.AbstractServer | None = None
        self._stopped = False

    def stop(self) -> None:
        self._stopped = True
        if self._server:
            self._server.close()

    async def serve(self) -> None:
        """Start the server and run until stop() is called."""
        try:
            self._server = await asyncio.start_server(
                self._handle, "127.0.0.1", self._port
            )
            logger.info("Web status UI running at http://127.0.0.1:%d/", self._port)
            async with self._server:
                while not self._stopped:
                    await asyncio.sleep(0.5)
        except OSError as exc:
            logger.warning(
                "Web status server could not start on port %d: %s", self._port, exc
            )

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            data = await asyncio.wait_for(reader.read(1024), timeout=2.0)
            request = data.decode("utf-8", errors="replace")
            path = self._parse_path(request)

            if path == "/api/status":
                body = self._json_status()
                response = self._http_response("200 OK", "application/json", body)
            elif path == "/api/alarm/latest":
                body = json.dumps(_LATEST_ALARM)
                response = self._http_response("200 OK", "application/json", body)
            elif path == "/api/log":
                body = json.dumps({"lines": list(_LOG_BUFFER)})
                response = self._http_response("200 OK", "application/json", body)
            else:
                body = self._html_page()
                response = self._http_response(
                    "200 OK", "text/html; charset=utf-8", body
                )

            writer.write(response.encode("utf-8"))
            await writer.drain()
        except Exception as exc:
            logger.debug("Web server handler error: %s", exc)
        finally:
            writer.close()

    # ------------------------------------------------------------------
    # Response helpers
    # ------------------------------------------------------------------

    def _json_status(self) -> str:
        running = self._running_ref[0] if self._running_ref else False
        elapsed = int(time.time() - _START_TIME)
        h, r = divmod(elapsed, 3600)
        m, s = divmod(r, 60)
        from evealert import __version__  # pylint: disable=import-outside-toplevel

        return json.dumps(
            {
                "running": running,
                "version": __version__,
                "uptime": f"{h:02d}:{m:02d}:{s:02d}",
                "session_alarms": self._stats.session_alarms,
                "total_alarms": self._stats.total_alarms,
            }
        )

    def _html_page(self) -> str:
        running = self._running_ref[0] if self._running_ref else False
        status_str = "RUNNING" if running else "STOPPED"
        elapsed = int(time.time() - _START_TIME)
        h, r = divmod(elapsed, 3600)
        m, s = divmod(r, 60)
        from evealert import __version__  # pylint: disable=import-outside-toplevel

        log_html = (
            "\n".join(
                f'<div class="log-line">{line}</div>' for line in list(_LOG_BUFFER)
            )
            or "<div>No log entries yet.</div>"
        )

        return _HTML.format(
            version=__version__,
            status_class="running" if running else "stopped",
            status=status_str,
            uptime=f"{h:02d}:{m:02d}:{s:02d}",
            session_alarms=self._stats.session_alarms,
            total_alarms=self._stats.total_alarms,
            log_lines=log_html,
        )

    @staticmethod
    def _parse_path(request: str) -> str:
        try:
            return request.split(" ")[1]
        except IndexError:
            return "/"

    @staticmethod
    def _http_response(status: str, content_type: str, body: str) -> str:
        encoded = body.encode("utf-8")
        return (
            f"HTTP/1.1 {status}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(encoded)}\r\n"
            "Connection: close\r\n"
            "\r\n" + body
        )


def append_to_log_buffer(line: str) -> None:
    """Append a log line to the web server's in-memory circular buffer."""
    _LOG_BUFFER.append(line)
