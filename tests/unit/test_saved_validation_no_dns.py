"""Atomic saved-plan validation never waits for external DNS under SQLite."""

import unittest
from unittest.mock import patch

from jittersim.config.models import Profile
from jittersim.engine.compiler import PlanError
from tests.unit import test_dashboard_concurrency as concurrency


class SavedValidationTests(unittest.TestCase):
    setUp = concurrency.DashboardConcurrencyTests.setUp
    _stop = concurrency.DashboardConcurrencyTests._stop
    request = concurrency.DashboardConcurrencyTests.request

    def test_saving_a_list_with_domain_rules_never_starts_dns(self):
        self.repository.save_profile(
            Profile(
                id="normal",
                name="Normal",
                website_mode="allow_only",
                website_domains=("delayed.example",),
            )
        )
        self.repository.save_general_profile("normal")
        self.repository.update_runtime_state(
            observed_state="reset", last_action="reset", last_result="success", last_error=None
        )
        self.helper.check_service_list_support = lambda: {}
        with patch(
            "jittersim.engine.website._system_lookup", side_effect=OSError("DNS down")
        ) as lookup:
            status, _, body = self.request(
                "POST",
                "/api/service-lists",
                {
                    "id": "video",
                    "name": "Video",
                    "profile_id": "normal",
                    "hostnames": ["video.example"],
                },
            )
        assert status == 200, body
        assert lookup.call_count == 0
        assert self.repository.get_service_list("video") is not None
        assert self.repository.get_runtime_state().last_action == "reset"

    def test_invalid_structure_rolls_back_without_a_lookup_or_partial_saved_list(self):
        self.helper.check_service_list_support = lambda: {}
        with patch(
            "jittersim.api.server.compile_stored_plan", side_effect=PlanError("invalid shape")
        ):
            status, _, _ = self.request(
                "POST",
                "/api/service-lists",
                {
                    "id": "video",
                    "name": "Video",
                    "profile_id": "normal",
                    "hostnames": ["video.example"],
                },
            )
        assert status == 409
        assert self.repository.get_service_list("video") is None
        assert self.repository.get_runtime_state().last_action != "apply"
