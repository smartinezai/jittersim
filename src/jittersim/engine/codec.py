"""Encode and validate network plans for the control-plane wire format."""

from typing import Any, Dict, List, Mapping, Set

from jittersim.config.models import Device, Impairment, ValidationError

from .compiler import MAX_DEVICES_PER_PLAN
from .plan import Direction, MarkRule, NetworkPlan, QueueSpec


class PlanCodecError(ValueError):
    """Raised when a serialized network plan is malformed or unsafe."""


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
                "impairment": {
                    "delay_ms": queue.impairment.delay_ms,
                    "jitter_ms": queue.impairment.jitter_ms,
                    "loss_percent": queue.impairment.loss_percent,
                    "bandwidth_kbit": queue.impairment.bandwidth_kbit,
                    "reorder_percent": queue.impairment.reorder_percent,
                    "duplicate_percent": queue.impairment.duplicate_percent,
                    "corrupt_percent": queue.impairment.corrupt_percent,
                },
            }
            for queue in plan.queues
        ],
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
    if len(queue_data) != len(mark_data) * 2:
        raise PlanCodecError("a plan must contain two queues per device")

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
        if device_id not in marks_by_device:
            raise PlanCodecError(f"queues[{index}] references an unknown device")
        if marks_by_device[device_id] != mark:
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
        raise PlanCodecError("each device must have upload and download queues")
    if len(directions_by_device) != len(marks_by_device):
        raise PlanCodecError("each device must have upload and download queues")
    return NetworkPlan(mark_rules=tuple(mark_rules), queues=tuple(queues))
