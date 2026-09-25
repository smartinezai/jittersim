"""Describe each device's network condition as normal, degraded or offline."""

from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Tuple

from jittersim.config.models import Impairment

from .plan import Direction, NetworkPlan


@dataclass(frozen=True)
class DeviceCondition:
    """How a plan treats one device, with the reasons behind it."""

    condition: str
    reasons: Tuple[str, ...] = ()


def _effects(impairment: Impairment) -> List[str]:
    effects = []
    if impairment.delay_ms:
        effects.append(f"delay {impairment.delay_ms} ms")
    if impairment.jitter_ms:
        effects.append(f"jitter {impairment.jitter_ms} ms")
    if impairment.loss_percent:
        bursts = " in bursts" if impairment.loss_burst_packets >= 2 else ""
        effects.append(f"loss {impairment.loss_percent}%{bursts}")
    if impairment.bandwidth_kbit is not None:
        effects.append(f"limit {impairment.bandwidth_kbit} kbit/s")
    # Without a delay the renderer leaves reordering out.
    if impairment.reorder_percent and impairment.delay_ms:
        effects.append(f"reordering {impairment.reorder_percent}%")
    if impairment.duplicate_percent:
        effects.append(f"duplication {impairment.duplicate_percent}%")
    if impairment.corrupt_percent:
        effects.append(f"corruption {impairment.corrupt_percent}%")
    return effects


def _rule_effects(kind: str, name: str, upload: Impairment, download: Impairment) -> List[str]:
    reasons = []
    for direction, impairment in (("upload", upload), ("download", download)):
        effects = _effects(impairment)
        if effects:
            reasons.append(f"{kind} {name}, {direction}: " + ", ".join(effects))
    return reasons


def _direction_reasons(upload: Impairment, download: Impairment) -> Tuple[List[str], List[str]]:
    offline = []
    degraded = []
    for direction, impairment in (("upload", upload), ("download", download)):
        if impairment.loss_percent >= 100:
            offline.append(f"{direction}: every packet lost")
        effects = _effects(impairment)
        if effects:
            degraded.append(f"{direction}: " + ", ".join(effects))
    return offline, degraded


def _condition(offline: List[str], degraded: List[str]) -> DeviceCondition:
    if offline:
        return DeviceCondition("offline", tuple(offline))
    if degraded:
        return DeviceCondition("degraded", tuple(degraded))
    return DeviceCondition("normal")


def device_conditions(
    plan: NetworkPlan, *, mac_addresses: Optional[Mapping[str, Optional[str]]] = None
) -> Dict[str, DeviceCondition]:
    """Offline when nothing gets through, degraded with any effect, else normal.

    The plan does not hold MAC addresses, so pass the devices' saved ones to
    check the MAC policy.
    """

    blocked = set(plan.blocked_device_addresses)
    # An allowlist whose domains resolved to nothing drops all traffic.
    closed = {
        website_filter.device_ip
        for website_filter in plan.website_filters
        if website_filter.allowed_addresses == ()
    }
    impairments = {(queue.device_id, queue.direction): queue.impairment for queue in plan.queues}
    conditions = {}
    for rule in plan.mark_rules:
        offline = []
        degraded = []
        if rule.ipv4_address in blocked:
            offline.append("outage")
        if rule.ipv4_address in closed:
            offline.append("no allowed site resolved")
        mac = (mac_addresses or {}).get(rule.device_id)
        policy = plan.mac_policy
        if mac and policy.mode == "denylist" and mac in policy.denylist:
            offline.append("MAC address denied")
        elif mac and policy.mode == "allowlist" and mac not in policy.allowlist:
            offline.append("MAC address not allowed")
        direction_offline, direction_degraded = _direction_reasons(
            impairments.get((rule.device_id, Direction.UPLOAD), Impairment()),
            impairments.get((rule.device_id, Direction.DOWNLOAD), Impairment()),
        )
        offline.extend(direction_offline)
        degraded.extend(direction_degraded)
        # Service rules shape only some of the device's traffic.
        for quota in plan.service_quota_rules:
            if quota.active and quota.device_id == rule.device_id:
                degraded.extend(
                    _rule_effects(
                        "service limit",
                        quota.quota_id,
                        quota.upload_impairment,
                        quota.download_impairment,
                    )
                )
        for assignment in plan.service_assignment_rules:
            if assignment.device_id == rule.device_id:
                degraded.extend(
                    _rule_effects(
                        "browser service profile",
                        assignment.assignment_id,
                        assignment.upload_impairment,
                        assignment.download_impairment,
                    )
                )
        conditions[rule.device_id] = _condition(offline, degraded)
    return conditions


def everyone_else_condition(plan: NetworkPlan) -> Optional[DeviceCondition]:
    """How the plan treats test Wi-Fi clients that are neither saved nor unaffected."""

    rule = plan.everyone_else
    if rule is None:
        return None
    offline, degraded = _direction_reasons(rule.upload_impairment, rule.download_impairment)
    if rule.blocked:
        offline.insert(0, "outage")
    return _condition(offline, degraded)
