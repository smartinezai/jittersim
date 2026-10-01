"""Keep service list address sets populated from the Pi's own DNS lookups.

Clients resolve the same names through the Pi, but the Pi sees only answers
that clients already received. Resolving the listed names here covers devices
that cached an answer earlier, and it is the only source when no client has
looked a name up yet.
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait as wait_for_futures
from ipaddress import IPv4Address
from typing import Callable, Dict, List, Optional, Sequence

from jittersim.networking.helper import HelperClientError

from .website import resolve_domains

LOGGER = logging.getLogger(__name__)
MAX_SEED_HOSTNAMES_PER_LIST = 100  # as many as a list can hold
RESEND_SECONDS = 30 * 60
RESOLVE_WAIT_SECONDS = 10.0
RESOLVER_THREADS = 8


def _resolve_one(domain: str) -> Sequence[IPv4Address]:
    return resolve_domains(domains=(domain,))


class ServiceAddressRefresher:
    """Resolve the hostnames of the applied service lists and send the answers to the helper."""

    def __init__(
        self,
        *,
        applied_plan: Callable[[], Optional[dict]],
        helper_client,
        resolver: Callable[[str], Sequence[IPv4Address]] = _resolve_one,
        clock: Callable[[], float] = time.monotonic,
        poll_seconds: float = 5.0,
    ) -> None:
        self._applied_plan = applied_plan
        self._helper_client = helper_client
        self._resolver = resolver
        self._clock = clock
        self._poll_seconds = poll_seconds
        self._last_signature: object = None
        self._last_plan: Optional[dict] = None
        self._last_sent: Dict[str, List[IPv4Address]] = {}
        self._last_sent_at = 0.0
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="jittersim-service-addresses", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=RESOLVE_WAIT_SECONDS + 5)
        self._thread = None

    def _run(self) -> None:
        while not self._stop_event.wait(self._poll_seconds):
            try:
                self.refresh()
            except Exception:  # keep the loop alive whatever one round did
                LOGGER.exception("service address refresh failed")

    def refresh(self) -> bool:
        """Resolve and send when the lists or answers changed. Return whether it sent."""

        plan = self._applied_plan()
        # Every apply publishes a new plan object and starts the sets over, so
        # send again even when the answers have not changed.
        reapplied = plan is not self._last_plan
        rules = plan.get("service_list_rules") if isinstance(plan, dict) else None
        lists = {
            rule["list_id"]: list(rule["domains"])[:MAX_SEED_HOSTNAMES_PER_LIST]
            for rule in (rules or ())
        }
        signature = tuple(sorted((list_id, tuple(domains)) for list_id, domains in lists.items()))
        if not lists:
            self._last_signature = signature
            self._last_sent = {}
            self._last_plan = plan
            return False
        answers = self._resolve(lists)
        if not answers:
            return False
        now = self._clock()
        if (
            not reapplied
            and signature == self._last_signature
            and answers == self._last_sent
            and now - self._last_sent_at < RESEND_SECONDS
        ):
            return False
        send = getattr(self._helper_client, "service_addresses", None)
        if send is None:
            return False
        try:
            send(answers)
        except HelperClientError as error:
            if getattr(error, "reason", None) != "helper-busy":
                LOGGER.warning("service addresses could not be sent: %s", error)
            return False
        self._last_signature = signature
        self._last_sent = answers
        self._last_sent_at = now
        self._last_plan = plan
        return True

    def _resolve(self, lists: Dict[str, List[str]]) -> Dict[str, List[IPv4Address]]:
        names = sorted({domain for domains in lists.values() for domain in domains})
        results: Dict[str, Sequence[IPv4Address]] = {}
        pool = ThreadPoolExecutor(max_workers=RESOLVER_THREADS)
        try:
            futures = {pool.submit(self._resolver, name): name for name in names}
            done, _ = wait_for_futures(futures, timeout=RESOLVE_WAIT_SECONDS)
            for future in done:
                try:
                    results[futures[future]] = future.result()
                except Exception:  # one unresolvable name must not hide the others
                    LOGGER.debug("could not resolve %s", futures[future], exc_info=True)
        finally:
            # A lookup stuck in the system resolver must not hold up the next round.
            pool.shutdown(wait=False, cancel_futures=True)
        answers = {}
        for list_id, domains in lists.items():
            addresses = sorted(
                {address for domain in domains for address in results.get(domain, ())}, key=int
            )
            if addresses:
                answers[list_id] = addresses
        return answers
