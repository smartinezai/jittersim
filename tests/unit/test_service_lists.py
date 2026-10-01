import tempfile
import unittest

from jittersim.config.models import Impairment, Profile, ServiceList, ValidationError
from jittersim.config.service_catalogue import (
    SERVICE_CATALOGUE,
    catalogue_service,
    expand_service_domains,
)
from jittersim.config.storage import ConfigRepository


class ServiceCatalogueTests(unittest.TestCase):
    def test_includes_the_services_people_ask_for_first(self) -> None:
        keys = {service.key for service in SERVICE_CATALOGUE}

        self.assertTrue({"linkedin", "microsoft_teams", "chatgpt", "claude", "netflix"} <= keys)

    def test_every_entry_has_a_name_and_only_valid_hostnames(self) -> None:
        for service in SERVICE_CATALOGUE:
            with self.subTest(service=service.key):
                self.assertTrue(service.name)
                self.assertTrue(service.domains)
                ServiceList(
                    id="check", name="Check", profile_id="slow", hostnames=service.domains
                )

    def test_no_hostname_belongs_to_two_services_so_any_mix_can_be_listed(self) -> None:
        owners = {}
        for service in SERVICE_CATALOGUE:
            for domain in service.domains:
                for other, other_owner in owners.items():
                    if other_owner == service.key:
                        continue
                    self.assertFalse(
                        domain == other
                        or domain.endswith("." + other)
                        or other.endswith("." + domain),
                        f"{domain} overlaps {other}",
                    )
                owners[domain] = service.key

    def test_unknown_service_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            catalogue_service("not-a-service")

    def test_expansion_keeps_catalogue_order_then_custom_names_without_duplicates(self) -> None:
        domains = expand_service_domains(("netflix",), ("Example.com", "netflix.com"))

        self.assertEqual(domains[: len(catalogue_service("netflix").domains)],
                         catalogue_service("netflix").domains)
        self.assertEqual(domains[-1], "example.com")
        self.assertEqual(len(domains), len(set(domains)))


class ServiceListModelTests(unittest.TestCase):
    def test_combines_catalogue_services_and_custom_hostnames(self) -> None:
        service_list = ServiceList(
            id="work-apps",
            name="  Work apps ",
            profile_id="slow",
            services=["linkedin", "claude"],
            hostnames=["Intranet.Example.com."],
        )

        self.assertEqual(service_list.name, "Work apps")
        self.assertEqual(service_list.services, ("linkedin", "claude"))
        self.assertEqual(service_list.hostnames, ("intranet.example.com",))
        self.assertIn("linkedin.com", service_list.domains)
        self.assertIn("claude.ai", service_list.domains)
        self.assertEqual(service_list.domains[-1], "intranet.example.com")
        self.assertTrue(service_list.enabled)

    def test_requires_something_to_match(self) -> None:
        with self.assertRaisesRegex(ValidationError, "at least one"):
            ServiceList(id="empty", name="Empty", profile_id="slow")

    def test_rejects_unknown_services_duplicates_and_server_addresses(self) -> None:
        for kwargs in (
            {"services": ("not-a-service",)},
            {"services": ("netflix", "netflix")},
            {"hostnames": ("203.0.113.9",)},
            {"hostnames": ("not a hostname",)},
            {"hostnames": "netflix.com"},
            {"services": "netflix"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                ServiceList(id="bad", name="Bad", profile_id="slow", **kwargs)

    def test_rejects_unsafe_identifiers_names_and_flags(self) -> None:
        for kwargs in (
            {"id": "Bad Id"},
            {"name": " "},
            {"profile_id": "../slow"},
            {"enabled": "yes"},
        ):
            values = {"id": "ok", "name": "Ok", "profile_id": "slow", "services": ("netflix",)}
            values.update(kwargs)
            with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                ServiceList(**values)

    def test_limits_the_expanded_hostname_count(self) -> None:
        hostnames = tuple(f"host{index}.example.com" for index in range(101))

        with self.assertRaisesRegex(ValidationError, "no more than 100"):
            ServiceList(id="big", name="Big", profile_id="slow", hostnames=hostnames)


class ServiceListStorageTests(unittest.TestCase):
    def test_lists_survive_reopen_in_stable_order_and_can_be_removed(self) -> None:
        streaming = ServiceList(
            id="streaming", name="Streaming", profile_id="slow", services=("netflix",)
        )
        work = ServiceList(
            id="work",
            name="Work",
            profile_id="slow",
            services=("linkedin",),
            hostnames=("intranet.example.com",),
            enabled=False,
        )
        with tempfile.TemporaryDirectory() as directory:
            database = f"{directory}/settings.db"
            first = ConfigRepository(database)
            try:
                first.save_profile(Profile(id="slow", name="Slow", upload=Impairment(delay_ms=900)))
                first.save_service_list(streaming)
                first.save_service_list(work)
            finally:
                first.close()
            reopened = ConfigRepository(database)
            try:
                self.assertEqual(reopened.list_service_lists(), (streaming, work))
                self.assertEqual(reopened.get_service_list("work"), work)
                self.assertTrue(reopened.delete_service_list("work"))
                self.assertFalse(reopened.delete_service_list("work"))
                self.assertIsNone(reopened.get_service_list("work"))
                self.assertEqual(reopened.list_service_lists(), (streaming,))
            finally:
                reopened.close()

    def test_saving_updates_in_place_and_changes_the_configuration_version(self) -> None:
        repository = ConfigRepository(":memory:")
        self.addCleanup(repository.close)
        repository.save_profile(Profile(id="slow", name="Slow"))
        repository.save_profile(Profile(id="slower", name="Slower"))
        original = ServiceList(id="s", name="S", profile_id="slow", services=("netflix",))
        before = repository.get_runtime_state().configuration_version

        repository.save_service_list(original)
        repository.save_service_list(
            ServiceList(id="s", name="S2", profile_id="slower", services=("claude",))
        )

        self.assertGreater(repository.get_runtime_state().configuration_version, before)
        (saved,) = repository.list_service_lists()
        self.assertEqual((saved.name, saved.profile_id, saved.services), ("S2", "slower", ("claude",)))

    def test_a_list_cannot_reference_a_missing_profile(self) -> None:
        from jittersim.config.storage import StorageError

        repository = ConfigRepository(":memory:")
        self.addCleanup(repository.close)

        with self.assertRaises(StorageError):
            repository.save_service_list(
                ServiceList(id="s", name="S", profile_id="missing", services=("netflix",))
            )
        self.assertEqual(repository.list_service_lists(), ())

    def test_lists_do_not_need_any_saved_device(self) -> None:
        repository = ConfigRepository(":memory:")
        self.addCleanup(repository.close)
        repository.save_profile(Profile(id="slow", name="Slow"))

        repository.save_service_list(
            ServiceList(id="s", name="S", profile_id="slow", services=("netflix",))
        )

        self.assertEqual(repository.list_devices(), ())
        self.assertEqual(len(repository.list_service_lists()), 1)
