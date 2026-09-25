"""Where a running scenario is at a given moment."""

import math
from dataclasses import dataclass
from typing import Optional

from jittersim.config.models import Scenario


@dataclass(frozen=True)
class ScenarioPosition:
    """The step a scenario run is on and how long is left."""

    step_index: int
    profile_id: str
    step_remaining_seconds: int
    # None while the scenario repeats.
    total_remaining_seconds: Optional[int]


def scenario_position(
    scenario: Scenario, *, started_at: int, now: float
) -> Optional[ScenarioPosition]:
    """Return the current step, or None once a scenario that doesn't repeat has ended."""

    elapsed = max(0.0, now - started_at)
    total = scenario.total_seconds
    if not scenario.loop and elapsed >= total:
        return None
    position = elapsed % total if scenario.loop else elapsed
    step_end = 0
    for index, step in enumerate(scenario.steps):
        step_end += step.duration_seconds
        if position < step_end:
            return ScenarioPosition(
                step_index=index,
                profile_id=step.profile_id,
                # Rounded up, so the time left reaches 0 only when the step ends.
                step_remaining_seconds=math.ceil(step_end - position),
                total_remaining_seconds=None if scenario.loop else math.ceil(total - elapsed),
            )
    return None
