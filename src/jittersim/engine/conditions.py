"""Describe each device's network condition as normal, degraded or offline."""

from dataclasses import dataclass
from typing import Dict, List, Tuple

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


def device_conditions(plan: NetworkPlan) -> Dict[str, DeviceCondition]:
    """Offline when nothing gets through, degraded with any effect, else normal."""

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
        for direction in (Direction.UPLOAD, Direction.DOWNLOAD):
            impairment = impairments.get((rule.device_id, direction), Impairment())
            if impairment.loss_percent >= 100:
                offline.append(f"{direction.value}: every packet lost")
            effects = _effects(impairment)
            if effects:
                degraded.append(f"{direction.value}: " + ", ".join(effects))
        if offline:
            conditions[rule.device_id] = DeviceCondition("offline", tuple(offline))
        elif degraded:
            conditions[rule.device_id] = DeviceCondition("degraded", tuple(degraded))
        else:
            conditions[rule.device_id] = DeviceCondition("normal")
    return conditions
