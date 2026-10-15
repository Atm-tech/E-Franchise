"""Periodic profit release with restart catch-up and graceful shutdown."""
import logging
import threading

log = logging.getLogger(__name__)


class ProfitWorker:
    def __init__(self, release, interval=60):
        self.release = release
        self.interval = interval
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name="profit-credit-worker", daemon=True)

    def start(self):
        self.thread.start()

    def run(self):
        while not self.stop_event.is_set():
            try:
                count = self.release()
                if count:
                    log.info("Profit worker processed %s due orders", count)
            except Exception:
                log.exception("Profit credit check failed; retrying on the next interval")
            self.stop_event.wait(self.interval)

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=20)
