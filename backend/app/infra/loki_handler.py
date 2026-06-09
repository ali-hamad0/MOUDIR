"""Grafana Loki log shipper for structured logs (Phase 8, Task 8.6).

Ships JSON log lines to Loki's push API in a background daemon thread so the
request path is never blocked. If Loki is unreachable, log lines are dropped
silently — observability must never degrade the application.

Architecture:
- LokiShipper: queue + daemon thread that batches and POSTs to Loki
- LokiHandler: stdlib logging.Handler added to the root logger; formats records
  to JSON using the same ProcessorFormatter as the stream/file handlers, then
  enqueues them for the shipper thread
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import urllib.request
from typing import Any

_BATCH_SIZE = 50
_FLUSH_INTERVAL = 2.0  # seconds between Loki pushes


class LokiShipper:
    """Background daemon thread that drains a queue and POSTs batches to Loki."""

    def __init__(self, push_url: str) -> None:
        self._url = push_url
        self._q: queue.Queue[tuple[float, dict[str, Any]]] = queue.Queue(maxsize=10_000)
        t = threading.Thread(target=self._run, daemon=True, name="loki-shipper")
        t.start()

    def enqueue(self, created: float, event: dict[str, Any]) -> None:
        try:
            self._q.put_nowait((created, event))
        except queue.Full:
            pass

    def _run(self) -> None:
        while True:
            batch: list[tuple[float, dict[str, Any]]] = []
            deadline = time.monotonic() + _FLUSH_INTERVAL
            while time.monotonic() < deadline:
                try:
                    batch.append(self._q.get_nowait())
                    if len(batch) >= _BATCH_SIZE:
                        break
                except queue.Empty:
                    time.sleep(0.05)
            if batch:
                self._push(batch)

    def _push(self, batch: list[tuple[float, dict[str, Any]]]) -> None:
        # Group by (level, tenant_id) → one Loki stream per combination.
        streams: dict[tuple[str, str], list[list[str]]] = {}
        for created, event in batch:
            level = str(event.get("level", "info"))
            tenant_id = str(event.get("tenant_id", ""))
            key = (level, tenant_id)
            ts_ns = str(int(created * 1_000_000_000))
            line = json.dumps(event, default=str)
            streams.setdefault(key, []).append([ts_ns, line])

        payload = json.dumps(
            {
                "streams": [
                    {
                        "stream": {"app": "modir", "level": k[0], "tenant_id": k[1]},
                        "values": vals,
                    }
                    for k, vals in streams.items()
                ]
            }
        ).encode()

        try:
            req = urllib.request.Request(
                self._url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass  # Loki unreachable → drop silently


class LokiHandler(logging.Handler):
    """stdlib logging handler that ships JSON-formatted log lines to Loki."""

    def __init__(self, loki_url: str) -> None:
        super().__init__()
        push_url = loki_url.rstrip("/") + "/loki/api/v1/push"
        self._shipper = LokiShipper(push_url)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            formatted = self.format(record)
            try:
                event: dict[str, Any] = json.loads(formatted)
            except (json.JSONDecodeError, ValueError):
                event = {"event": record.getMessage(), "level": record.levelname.lower()}
            self._shipper.enqueue(record.created, event)
        except Exception:
            self.handleError(record)
