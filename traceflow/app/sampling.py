"""
Tail-Based Sampling - decide KEEP or DROP after the whole trace has been analyzed.

Head-based sampling decides at the START of a request, before anyone knows
whether it will fail, so it randomly throws away important traces.
TraceFlow decides at the END (the "tail"), using the analysis results:

  Rule 1  trace contains an error            -> KEEP  (ERROR)
  Rule 2  total latency > threshold          -> KEEP  (HIGH_LATENCY)
  Rule 3  trace is incomplete (lost spans)   -> KEEP  (INCOMPLETE_TRACE)  [configurable]
  Rule 4  healthy trace in baseline sample   -> KEEP  (BASELINE_SAMPLE)   [rate configurable]
  else                                       -> DROP  (NORMAL_TRACE)
"""
from dataclasses import dataclass, field
from typing import List

from app.analysis import TraceAnalysis

KEEP = "KEEP"
DROP = "DROP"

REASON_ERROR = "ERROR"
REASON_HIGH_LATENCY = "HIGH_LATENCY"
REASON_INCOMPLETE = "INCOMPLETE_TRACE"
REASON_BASELINE = "BASELINE_SAMPLE"
REASON_NORMAL = "NORMAL_TRACE"


@dataclass(frozen=True)
class SamplingPolicy:
    baseline_sample_rate: float = 0.0
    keep_incomplete_traces: bool = True

    def __post_init__(self):
        if not 0.0 <= self.baseline_sample_rate <= 1.0:
            raise ValueError("baseline_sample_rate must be between 0.0 and 1.0")


@dataclass
class SamplingDecision:
    decision: str                                  # KEEP or DROP
    reason: str                                    # the main (first matching) reason
    matched_rules: List[str] = field(default_factory=list)

    @property
    def keep(self) -> bool:
        return self.decision == KEEP


def in_baseline_sample(trace_id: str, rate: float) -> bool:
    """Consistent sampling: the same trace_id always gets the same answer.
    The last 8 hex digits of a trace ID are random, so they act as a dice roll."""
    if rate <= 0.0:
        return False
    if rate >= 1.0:
        return True
    try:
        bucket = int(trace_id[-8:], 16) / 0xFFFFFFFF
    except ValueError:
        return False
    return bucket < rate


def decide(analysis: TraceAnalysis, is_complete: bool, policy: SamplingPolicy) -> SamplingDecision:
    matched: List[str] = []
    if analysis.is_error:
        matched.append(REASON_ERROR)
    if analysis.is_slow:
        matched.append(REASON_HIGH_LATENCY)
    if policy.keep_incomplete_traces and not is_complete:
        matched.append(REASON_INCOMPLETE)

    if matched:
        return SamplingDecision(decision=KEEP, reason=matched[0], matched_rules=matched)

    if in_baseline_sample(analysis.trace_id, policy.baseline_sample_rate):
        return SamplingDecision(decision=KEEP, reason=REASON_BASELINE, matched_rules=[REASON_BASELINE])

    return SamplingDecision(decision=DROP, reason=REASON_NORMAL, matched_rules=[])