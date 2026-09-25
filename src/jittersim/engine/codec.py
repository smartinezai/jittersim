"""Encode and validate network plans for the control-plane wire format."""

import re
from dataclasses import asdict
from ipaddress import AddressValueError, IPv4Address
from typing import Any, Dict, List, Mapping, Set

from jittersim.config.models import (
    MAX_UNAFFECTED_DEVICES,
    Device,
    Impairment,
    MacPolicy,
    Randomness,
    ValidationError,
    normalize_mac_address,
    normalize_service_hostname,
)

from .compiler import (
    MAX_DEVICES_PER_PLAN,
    MAX_SERVICE_ASSIGNMENT_DOMAINS_PER_PLAN,
    MAX_SERVICE_QUOTAS_PER_PLAN,
    MAX_SHAPED_MARKS_PER_PLAN,
)
from .plan import (
    EVERYONE_ELSE_QUEUE_ID,
    Direction,
    EveryoneElseRule,
    MarkRule,
    NetworkPlan,
    QueueSpec,
    ServiceAssignmentRule,
    ServiceQuotaRule,
    WebsiteFilter,
)


class PlanCodecError(ValueError):
    """Raised when a serialized network plan is malformed or unsafe."""


_SAFE_QUOTA_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _as_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise PlanCodecError(f"{field_name} must be an object")
    return value


def _as_list(value: Any, field_name: str) -> List[Any]:
    if not isinstance(value, list):
        raise PlanCodecError(f"{field_name} must be an array")
    return value


def _as_integer(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PlanCodecError(f"{field_name} must be an integer")
    return value


def _impairment_from_payload(value: Any) -> Impairment:
    impairment_data = _as_mapping(value, "impairment")
    try:
        for field_name in (
            "delay_randomness",
            "jitter_randomness",
            "loss_randomness",
            "bandwidth_randomness",
            "reorder_randomness",
            "duplicate_randomness",
            "corrupt_randomness",
        ):
            randomness_data = impairment_data.get(field_name)
            if isinstance(randomness_data, dict):
                impairment_data[field_name] = Randomness(**randomness_data)
        return Impairment(**impairment_data)
    except (TypeError, ValueError) as error:
        raise PlanCodecError("impairment is invalid") from error


def plan_to_payload(plan: NetworkPlan) -> Dict[str, Any]:
    """Convert a compiled plan into JSON-compatible values."""

    return {
        "mark_rules": [
            {
                "device_id": rule.device_id,
                "ipv4_address": str(rule.ipv4_address),
                "mark": rule.mark,
            }
            for rule in plan.mark_rules
        ],
        "queues": [
            {
                "device_id": queue.device_id,
                "direction": queue.direction.value,
                "mark": queue.mark,
                "impairment": asdict(queue.impairment),
            }
            for queue in plan.queues
        ],
        "mac_policy": {
            "mode": plan.mac_policy.mode,
            "allowlist": list(plan.mac_policy.allowlist),
            "denylist": list(plan.mac_policy.denylist),
        },
        "global_blocked_addresses": [
            str(address) for address in plan.global_blocked_addresses
        ],
        "website_filters": [
            {
                "device_ip": str(website_filter.device_ip),
                "allowed_addresses": (
                    None
                    if website_filter.allowed_addresses is None
                    else [str(address) for address in website_filter.allowed_addresses]
                ),
                "blocked_addresses": [
                    str(address) for address in website_filter.blocked_addresses
                ],
            }
            for website_filter in plan.website_filters
        ],
        "blocked_device_addresses": [
            str(address) for address in plan.blocked_device_addresses
        ],
        "service_quota_rules": [
            {
                "quota_id": rule.quota_id,
                "device_id": rule.device_id,
                "device_ip": str(rule.device_ip),
                "destination_addresses": [
                    str(address) for address in rule.destination_addresses
                ],
                "mark": rule.mark,
                "upload_impairment": asdict(rule.upload_impairment),
                "download_impairment": asdict(rule.download_impairment),
                "active": rule.active,
            }
            for rule in plan.service_quota_rules
        ],
        "service_assignment_rules": [
            {
                "assignment_id": rule.assignment_id,
                "device_id": rule.device_id,
                "device_ip": str(rule.device_ip),
                "domains": list(rule.domains),
                "mark": rule.mark,
                "upload_impairment": asdict(rule.upload_impairment),
                "download_impairment": asdict(rule.download_impairment),
            }
            for rule in plan.service_assignment_rules
        ],
        "everyone_else": (
            None
            if plan.everyone_else is None
            else {
                "mark": plan.everyone_else.mark,
                "upload_impairment": asdict(plan.everyone_else.upload_impairment),
                "download_impairment": asdict(plan.everyone_else.download_impairment),
                "unaffected_macs": list(plan.everyone_else.unaffected_macs),
                "blocked": plan.everyone_else.blocked,
            }
        ),
    }


def plan_from_payload(payload: Any) -> NetworkPlan:
    """Validate and decode a serialized plan received by a privileged boundary."""

    plan_data = _as_mapping(payload, "plan")
    mark_data = _as_list(plan_data.get("mark_rules"), "mark_rules")
    queue_data = _as_list(plan_data.get("queues"), "queues")
    if len(mark_data) > MAX_DEVICES_PER_PLAN:
        raise PlanCodecError(
            f"a plan cannot contain more than {MAX_DEVICES_PER_PLAN} devices"
        )

    mark_rules: List[MarkRule] = []
    marks_by_device: Dict[str, int] = {}
    addresses_by_device: Dict[str, str] = {}
    for index, raw_rule in enumerate(mark_data):
        rule_data = _as_mapping(raw_rule, f"mark_rules[{index}]")
        try:
            device_id = rule_data["device_id"]
            ipv4_address = rule_data["ipv4_address"]
            mark = _as_integer(rule_data["mark"], f"mark_rules[{index}].mark")
            validated_device = Device(
                id=device_id,
                name=device_id,
                ipv4_address=ipv4_address,
                profile_id="plan",
            )
        except (KeyError, TypeError, ValidationError) as error:
            raise PlanCodecError(f"mark_rules[{index}] is invalid") from error
        if mark < 1 or mark > MAX_DEVICES_PER_PLAN:
            raise PlanCodecError(f"mark_rules[{index}].mark is outside the safe range")
        if device_id in marks_by_device:
            raise PlanCodecError(f"duplicate mark rule for device {device_id}")
        if mark in marks_by_device.values():
            raise PlanCodecError(f"duplicate mark value {mark}")
        if str(validated_device.ipv4_address) in addresses_by_device.values():
            raise PlanCodecError(f"duplicate IPv4 address {ipv4_address}")
        marks_by_device[device_id] = mark
        addresses_by_device[device_id] = str(validated_device.ipv4_address)
        mark_rules.append(
            MarkRule(
                device_id=device_id,
                ipv4_address=validated_device.ipv4_address,
                mark=mark,
            )
        )

    service_quota_rules: List[ServiceQuotaRule] = []
    service_marks: Dict[str, int] = {}
    service_data = _as_list(
        plan_data.get("service_quota_rules", []),
        "service_quota_rules",
    )
    if len(service_data) > MAX_SERVICE_QUOTAS_PER_PLAN:
        raise PlanCodecError(
            f"a plan cannot contain more than {MAX_SERVICE_QUOTAS_PER_PLAN} service quotas"
        )
    for index, raw_rule in enumerate(service_data):
        rule_data = _as_mapping(raw_rule, f"service_quota_rules[{index}]")
        try:
            quota_id = rule_data["quota_id"]
            device_id = rule_data["device_id"]
            device_ip = IPv4Address(rule_data["device_ip"])
            destination_addresses = tuple(
                IPv4Address(value)
                for value in _as_list(
                    rule_data["destination_addresses"],
                    f"service_quota_rules[{index}].destination_addresses",
                )
            )
            mark = _as_integer(rule_data["mark"], f"service_quota_rules[{index}].mark")
            active = rule_data.get("active", False)
            upload_impairment = _impairment_from_payload(rule_data["upload_impairment"])
            download_impairment = _impairment_from_payload(rule_data["download_impairment"])
        except (AddressValueError, KeyError, TypeError, ValueError) as error:
            raise PlanCodecError(f"service_quota_rules[{index}] is invalid") from error
        if not isinstance(quota_id, str) or not _SAFE_QUOTA_ID_PATTERN.fullmatch(quota_id):
            raise PlanCodecError(f"service_quota_rules[{index}].quota_id is invalid")
        if not isinstance(device_id, str) or device_id not in marks_by_device:
            raise PlanCodecError(f"service_quota_rules[{index}] references an unknown device")
        if device_ip != IPv4Address(addresses_by_device[device_id]):
            raise PlanCodecError(f"service_quota_rules[{index}] has the wrong device address")
        if not destination_addresses:
            raise PlanCodecError(f"service_quota_rules[{index}] has no destinations")
        if not isinstance(active, bool):
            raise PlanCodecError(f"service_quota_rules[{index}].active is invalid")
        if (
            mark <= len(mark_data)
            or mark > MAX_SHAPED_MARKS_PER_PLAN
        ):
            raise PlanCodecError(f"service_quota_rules[{index}].mark is outside the safe range")
        service_key = f"quota:{quota_id}"
        if (
            service_key in service_marks
            or mark in marks_by_device.values()
            or mark in service_marks.values()
        ):
            raise PlanCodecError(f"duplicate service quota mark {mark}")
        service_marks[service_key] = mark
        service_quota_rules.append(
            ServiceQuotaRule(
                quota_id=quota_id,
                device_id=device_id,
                device_ip=device_ip,
                destination_addresses=destination_addresses,
                mark=mark,
                upload_impairment=upload_impairment,
                download_impairment=download_impairment,
                active=active,
            )
        )

    assignment_data = _as_list(
        plan_data.get("service_assignment_rules", []), "service_assignment_rules"
    )
    if len(mark_data) + len(service_quota_rules) + len(assignment_data) > MAX_SHAPED_MARKS_PER_PLAN:
        raise PlanCodecError("a plan has too many shaped traffic classes")
    if sum(
        len(_as_list(_as_mapping(rule, "service assignment").get("domains"), "domains"))
        for rule in assignment_data
    ) > MAX_SERVICE_ASSIGNMENT_DOMAINS_PER_PLAN:
        raise PlanCodecError("a plan has too many browser service domains")
    service_assignment_rules: List[ServiceAssignmentRule] = []
    domains_by_device: Dict[str, List[str]] = {}
    for index, raw_rule in enumerate(assignment_data):
        rule_data = _as_mapping(raw_rule, f"service_assignment_rules[{index}]")
        try:
            assignment_id = rule_data["assignment_id"]
            device_id = rule_data["device_id"]
            device_ip = IPv4Address(rule_data["device_ip"])
            domains = tuple(
                normalize_service_hostname(domain)
                for domain in _as_list(rule_data["domains"], "domains")
            )
            mark = _as_integer(rule_data["mark"], "service assignment mark")
            upload_impairment = _impairment_from_payload(rule_data["upload_impairment"])
            download_impairment = _impairment_from_payload(rule_data["download_impairment"])
        except (AddressValueError, KeyError, TypeError, ValueError) as error:
            raise PlanCodecError(f"service_assignment_rules[{index}] is invalid") from error
        if not isinstance(assignment_id, str) or not _SAFE_QUOTA_ID_PATTERN.fullmatch(assignment_id):
            raise PlanCodecError(f"service_assignment_rules[{index}].assignment_id is invalid")
        if device_id not in marks_by_device or str(device_ip) != addresses_by_device[device_id]:
            raise PlanCodecError(f"service_assignment_rules[{index}] references an unknown device")
        if not domains or len(domains) > 100 or len(set(domains)) != len(domains):
            raise PlanCodecError(f"service_assignment_rules[{index}].domains is invalid")
        if mark <= len(mark_data) + len(service_quota_rules) or mark > MAX_SHAPED_MARKS_PER_PLAN:
            raise PlanCodecError(f"service_assignment_rules[{index}].mark is outside the safe range")
        key = f"assignment:{assignment_id}"
        if (
            key in service_marks
            or mark in marks_by_device.values()
            or mark in service_marks.values()
        ):
            raise PlanCodecError(f"duplicate service assignment mark {mark}")
        prior_domains = domains_by_device.setdefault(device_id, [])
        for domain in domains:
            if any(
                domain == prior or domain.endswith("." + prior) or prior.endswith("." + domain)
                for prior in prior_domains
            ):
                raise PlanCodecError(f"overlapping service assignment domains on {device_id}")
            prior_domains.append(domain)
        service_marks[key] = mark
        service_assignment_rules.append(
            ServiceAssignmentRule(
                assignment_id=assignment_id,
                device_id=device_id,
                device_ip=device_ip,
                domains=domains,
                mark=mark,
                upload_impairment=upload_impairment,
                download_impairment=download_impairment,
            )
        )

    everyone_else = None
    everyone_else_data = plan_data.get("everyone_else")
    if everyone_else_data is not None:
        rule_data = _as_mapping(everyone_else_data, "everyone_else")
        try:
            mark = _as_integer(rule_data["mark"], "everyone_else.mark")
            upload_impairment = _impairment_from_payload(rule_data["upload_impairment"])
            download_impairment = _impairment_from_payload(rule_data["download_impairment"])
            unaffected_macs = tuple(
                normalize_mac_address(value)
                for value in _as_list(
                    rule_data.get("unaffected_macs", []), "everyone_else.unaffected_macs"
                )
            )
        except (KeyError, TypeError, ValueError) as error:
            raise PlanCodecError("everyone_else is invalid") from error
        blocked = rule_data.get("blocked", False)
        if not isinstance(blocked, bool):
            raise PlanCodecError("everyone_else.blocked is invalid")
        if (
            len(unaffected_macs) > MAX_UNAFFECTED_DEVICES
            or len(set(unaffected_macs)) != len(unaffected_macs)
        ):
            raise PlanCodecError("everyone_else.unaffected_macs is invalid")
        if (
            mark <= len(mark_data) + len(service_quota_rules) + len(service_assignment_rules)
            or mark > MAX_SHAPED_MARKS_PER_PLAN
            or mark in marks_by_device.values()
            or mark in service_marks.values()
        ):
            raise PlanCodecError("everyone_else.mark is outside the safe range")
        service_marks[EVERYONE_ELSE_QUEUE_ID] = mark
        everyone_else = EveryoneElseRule(
            mark=mark,
            upload_impairment=upload_impairment,
            download_impairment=download_impairment,
            unaffected_macs=unaffected_macs,
            blocked=blocked,
        )

    if len(queue_data) != (len(mark_data) + len(service_marks)) * 2:
        raise PlanCodecError("a plan must contain two queues per device or service")

    queues: List[QueueSpec] = []
    directions_by_device: Dict[str, Set[Direction]] = {}
    for index, raw_queue in enumerate(queue_data):
        queue_data_item = _as_mapping(raw_queue, f"queues[{index}]")
        try:
            device_id = queue_data_item["device_id"]
            direction = Direction(queue_data_item["direction"])
            mark = _as_integer(queue_data_item["mark"], f"queues[{index}].mark")
            impairment = _impairment_from_payload(queue_data_item["impairment"])
        except (KeyError, TypeError, ValueError) as error:
            raise PlanCodecError(f"queues[{index}] is invalid") from error
        expected_mark = marks_by_device.get(device_id, service_marks.get(device_id))
        if expected_mark is None:
            raise PlanCodecError(f"queues[{index}] references an unknown traffic class")
        if expected_mark != mark:
            raise PlanCodecError(f"queues[{index}] has the wrong mark")
        directions = directions_by_device.setdefault(device_id, set())
        if direction in directions:
            raise PlanCodecError(f"duplicate {direction.value} queue for device {device_id}")
        directions.add(direction)
        queues.append(
            QueueSpec(
                device_id=device_id,
                direction=direction,
                mark=mark,
                impairment=impairment,
            )
        )

    if any(
        directions != {Direction.UPLOAD, Direction.DOWNLOAD}
        for directions in directions_by_device.values()
    ):
        raise PlanCodecError("each device or service must have upload and download queues")
    if len(directions_by_device) != len(marks_by_device) + len(service_marks):
        raise PlanCodecError("each device or service must have upload and download queues")
    queue_impairments = {
        (queue.device_id, queue.direction): queue.impairment for queue in queues
    }
    for key, upload, download in (
        (
            f"quota:{rule.quota_id}",
            rule.upload_impairment,
            rule.download_impairment,
        )
        for rule in service_quota_rules
    ):
        if (
            queue_impairments[(key, Direction.UPLOAD)] != upload
            or queue_impairments[(key, Direction.DOWNLOAD)] != download
        ):
            raise PlanCodecError("service queue impairment does not match its rule")
    for rule in service_assignment_rules:
        key = f"assignment:{rule.assignment_id}"
        if (
            queue_impairments[(key, Direction.UPLOAD)] != rule.upload_impairment
            or queue_impairments[(key, Direction.DOWNLOAD)] != rule.download_impairment
        ):
            raise PlanCodecError("browser service queue impairment does not match its rule")
    if everyone_else is not None and (
        queue_impairments[(EVERYONE_ELSE_QUEUE_ID, Direction.UPLOAD)]
        != everyone_else.upload_impairment
        or queue_impairments[(EVERYONE_ELSE_QUEUE_ID, Direction.DOWNLOAD)]
        != everyone_else.download_impairment
    ):
        raise PlanCodecError("everyone-else queue impairment does not match its rule")

    try:
        mac_policy_data = _as_mapping(plan_data.get("mac_policy", {}), "mac_policy")
        mac_policy = MacPolicy(
            mode=mac_policy_data.get("mode", "disabled"),
            allowlist=tuple(mac_policy_data.get("allowlist", ())),
            denylist=tuple(mac_policy_data.get("denylist", ())),
        )
        global_blocked_addresses = tuple(
            IPv4Address(value)
            for value in _as_list(
                plan_data.get("global_blocked_addresses", []),
                "global_blocked_addresses",
            )
        )
        website_filters_data = _as_list(
            plan_data.get("website_filters", []),
            "website_filters",
        )
        website_filters = []
        filter_addresses = set()
        for index, raw_filter in enumerate(website_filters_data):
            filter_data = _as_mapping(raw_filter, f"website_filters[{index}]")
            device_ip = IPv4Address(filter_data["device_ip"])
            allowed_data = filter_data.get("allowed_addresses", [])
            allowed_addresses = (
                None
                if allowed_data is None
                else tuple(
                    IPv4Address(value)
                    for value in _as_list(
                        allowed_data,
                        f"website_filters[{index}].allowed_addresses",
                    )
                )
            )
            blocked_filter_addresses = tuple(
                IPv4Address(value)
                for value in _as_list(
                    filter_data.get("blocked_addresses", []),
                    f"website_filters[{index}].blocked_addresses",
                )
            )
            if str(device_ip) not in addresses_by_device.values():
                raise PlanCodecError(
                    f"website_filters[{index}] references an unknown device"
                )
            if device_ip in filter_addresses:
                raise PlanCodecError(
                    f"duplicate website filter for device address {device_ip}"
                )
            filter_addresses.add(device_ip)
            website_filters.append(
                WebsiteFilter(
                    device_ip=device_ip,
                    allowed_addresses=allowed_addresses,
                    blocked_addresses=blocked_filter_addresses,
                )
            )
        blocked_device_addresses = tuple(
            IPv4Address(value)
            for value in _as_list(
                plan_data.get("blocked_device_addresses", []),
                "blocked_device_addresses",
            )
        )
    except (AddressValueError, KeyError, TypeError, ValueError) as error:
        if isinstance(error, PlanCodecError):
            raise
        raise PlanCodecError("network policy is invalid") from error

    return NetworkPlan(
        mark_rules=tuple(mark_rules),
        queues=tuple(queues),
        mac_policy=mac_policy,
        global_blocked_addresses=global_blocked_addresses,
        website_filters=tuple(website_filters),
        blocked_device_addresses=blocked_device_addresses,
        service_quota_rules=tuple(service_quota_rules),
        service_assignment_rules=tuple(service_assignment_rules),
        everyone_else=everyone_else,
    )
