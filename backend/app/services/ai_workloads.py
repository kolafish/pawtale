from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.services.ai_timeouts import AITextTimeoutProfile


class AITextWorkload(StrEnum):
    REALTIME_STRUCTURED = "realtime_structured"
    INTERACTIVE_STRUCTURED = "interactive_structured"
    GENERATION_STRUCTURED = "generation_structured"
    LONG_GENERATION_STRUCTURED = "long_generation_structured"
    GENERATION_TEXT = "generation_text"


@dataclass(frozen=True)
class AITextWorkloadPolicy:
    timeout_profile: AITextTimeoutProfile
    max_tokens: int
    max_attempts: int


_POLICIES = {
    AITextWorkload.REALTIME_STRUCTURED: AITextWorkloadPolicy(
        timeout_profile=AITextTimeoutProfile.REALTIME,
        max_tokens=512,
        max_attempts=1,
    ),
    AITextWorkload.INTERACTIVE_STRUCTURED: AITextWorkloadPolicy(
        timeout_profile=AITextTimeoutProfile.INTERACTIVE,
        max_tokens=2400,
        max_attempts=2,
    ),
    AITextWorkload.GENERATION_STRUCTURED: AITextWorkloadPolicy(
        timeout_profile=AITextTimeoutProfile.GENERATION,
        max_tokens=4000,
        max_attempts=2,
    ),
    AITextWorkload.LONG_GENERATION_STRUCTURED: AITextWorkloadPolicy(
        timeout_profile=AITextTimeoutProfile.GENERATION,
        max_tokens=8000,
        max_attempts=2,
    ),
    AITextWorkload.GENERATION_TEXT: AITextWorkloadPolicy(
        timeout_profile=AITextTimeoutProfile.GENERATION,
        max_tokens=5000,
        max_attempts=1,
    ),
}


def ai_text_workload_policy(
    workload: AITextWorkload,
) -> AITextWorkloadPolicy:
    return _POLICIES[workload]
