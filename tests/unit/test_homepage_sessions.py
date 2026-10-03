import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from jittersim.api.homepage import PairingStore, PublicResolver, controlled_page

EXTENSION = "a" * 32
ORIGIN = "chrome-extension://" + EXTENSION
PAGE = "https://example.com/article"


class Resolver:
    def resolve(self, _):
        return ("93.184.216.34",)

    def close(self):
        pass


class Helper:
    def __init__(self):
        self.mac = "02:00:00:00:00:02"
        self.epoch = "boot"
        self.chance = 100
        self.protected = False
        self.confirmed = True
        self.delay = None

    def homepage_snapshot(self, **_):
        if self.delay:
            self.delay[0].set()
            assert self.delay[1].wait(2)
        return {
            "verified": True,
            "mac": self.mac,
            "epoch": self.epoch,
            "protected": self.protected,
            "chance": self.chance,
            "receipt": {},
        }

    def homepage_confirm(self, _):
        return self.confirmed


@pytest.fixture
def setup():
    now = [0]
    helper = Helper()
    store = PairingStore(helper, resolver=Resolver(), clock=lambda: now[0])
    return store, helper, now


def pair(store):
    code = store.create(EXTENSION, [PAGE])["code"]
    return store.pair(code, ORIGIN, "192.168.50.2")


def policy(store, session):
    return store.policy(
        session["capability"], ORIGIN, "192.168.50.2", "https://example.com", "a" * 32
    )


def test_single_use_replacement_and_default_approval(setup):
    store, _, _ = setup
    code = store.create(EXTENSION, [PAGE])["code"]
    session = store.pair(code, ORIGIN, "192.168.50.2")
    assert policy(store, session)["chance"] == 100
    with pytest.raises(PermissionError):
        store.pair(code, ORIGIN, "192.168.50.2")
    pair(store)
    with pytest.raises(PermissionError):
        policy(store, session)
    assert len(store.sessions) == 1


def test_wrong_origin_and_unenrolled_target_refused(setup):
    store, _, _ = setup
    session = pair(store)
    with pytest.raises(PermissionError):
        store.policy(
            session["capability"],
            "chrome-extension://" + "b" * 32,
            "192.168.50.2",
            "https://example.com",
            "a" * 32,
        )
    with pytest.raises(PermissionError):
        store.policy(session["capability"], ORIGIN, "192.168.50.2", "https://other.com", "a" * 32)


def test_code_and_session_expiry(setup):
    store, _, now = setup
    code = store.create(EXTENSION, [PAGE])["code"]
    now[0] = 120
    with pytest.raises(PermissionError):
        store.pair(code, ORIGIN, "192.168.50.2")
    session = pair(store)
    now[0] += 3600
    with pytest.raises(PermissionError):
        policy(store, session)


@pytest.mark.parametrize("field,value", [("mac", "02:00:00:00:00:03"), ("epoch", "restart")])
def test_changed_identity_or_helper_restart_revokes(setup, field, value):
    store, helper, _ = setup
    session = pair(store)
    setattr(helper, field, value)
    with pytest.raises(PermissionError):
        policy(store, session)
    assert not store.sessions


def test_protection_or_failed_confirmation_never_grants(setup):
    store, helper, _ = setup
    session = pair(store)
    helper.protected = True
    assert not policy(store, session)["enabled"]
    helper.protected = False
    helper.confirmed = False
    assert not policy(store, session)["enabled"]


def test_revoke_during_delayed_snapshot_cannot_return_approval(setup):
    store, helper, _ = setup
    session = pair(store)
    entered, release = threading.Event(), threading.Event()
    helper.delay = (entered, release)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(policy, store, session)
        assert entered.wait(1)
        store.revoke()
        release.set()
        with pytest.raises(PermissionError):
            future.result(1)


def test_burst_and_fixed_capacities(setup):
    store, _, now = setup
    session = pair(store)
    assert all(policy(store, session)["enabled"] for _ in range(4))
    assert not policy(store, session)["enabled"]
    now[0] += 0.5
    assert policy(store, session)["enabled"]
    for _ in range(8):
        store.create(EXTENSION, [PAGE])
    with pytest.raises(ValueError):
        store.create(EXTENSION, [PAGE])


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/article",
        "https://localhost/article",
        "https://192.168.50.1/article",
        "https://netlab.local/article",
        "https://example.com/",
        "https://example.com/article?q=x",
        "https://user@example.com/article",
        "https://example.com:8443/article",
        "https://example.com/%2f",
        "https://example.com/article#x",
    ],
)
def test_ambiguous_or_private_test_page_is_not_enrolled(url):
    with pytest.raises(ValueError):
        controlled_page(url)


def test_mixed_dns_answers_are_refused(monkeypatch):
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *_args, **_kwargs: [
            (2, 1, 6, "", ("93.184.216.34", 443)),
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ],
    )
    with pytest.raises(ValueError):
        PublicResolver.lookup("example.com")


def test_resolver_cancellation_keeps_physical_capacity(monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def blocked(_):
        entered.set()
        release.wait(2)
        return ("93.184.216.34",)

    resolver = PublicResolver()
    monkeypatch.setattr(resolver, "lookup", blocked)
    try:
        for _ in range(2):
            with pytest.raises(ValueError):
                resolver.resolve("example.com")
        assert entered.is_set()
        with pytest.raises(ValueError, match="busy"):
            resolver.resolve("example.com")
    finally:
        release.set()
        resolver.close()
