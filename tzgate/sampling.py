"""Exact hypergeometric acceptance math for pre-registered sample plans.

Pure integer combinatorics (``math.comb``) plus ``fractions.Fraction``: no
floating point and no statistical service. Given a frame of ``N`` documents,
a without-replacement sample of ``n`` and ``x`` observed conforming
documents, the one-sided 90% lower bound on the number of conforming
documents in the frame is one plus the largest population count ``K`` whose
hypergeometric upper-tail probability of drawing at least ``x`` conforming
documents is at most 10%.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from math import comb

MAX_FRAME_SIZE = 30
MAX_SAMPLE_SIZE = 12
ALPHA = Fraction(1, 10)

@dataclass(frozen=True)
class TailRow:
    """One candidate population success count and its exact tail bound."""

    population_successes: int
    tail_probability: Fraction

    @property
    def tail_text(self) -> str:
        return f"{self.tail_probability.numerator}/{self.tail_probability.denominator}"

    def to_dict(self) -> dict[str, object]:
        return {
            "population_successes": self.population_successes,
            "tail_probability": self.tail_text,
            "tail_value": round(float(self.tail_probability), 6),
        }

@dataclass(frozen=True)
class PopulationBound:
    frame_size: int
    sample_size: int
    observed_successes: int
    tail_table: tuple[TailRow, ...]
    lower_bound: int

    @property
    def lower_fraction_text(self) -> str:
        return f"{self.lower_bound}/{self.frame_size}"

def check_plan_limits(frame_size: int, sample_size: int) -> None:
    if type(frame_size) is not int or type(sample_size) is not int:
        raise ValueError("frame and sample sizes must be integers")
    if not 1 <= frame_size <= MAX_FRAME_SIZE:
        raise ValueError(f"frame size must be between 1 and {MAX_FRAME_SIZE}")
    if not 1 <= sample_size <= MAX_SAMPLE_SIZE:
        raise ValueError(f"sample size must be between 1 and {MAX_SAMPLE_SIZE}")
    if sample_size > frame_size:
        raise ValueError("sample size cannot exceed frame size")

def hypergeom_tail(frame_size: int, successes: int, sample_size: int, x: int) -> Fraction:
    """P(X >= x) for X ~ Hypergeometric(N, K=successes, n=sample_size)."""

    lowest = max(0, sample_size - (frame_size - successes))
    numerator = sum(
        comb(successes, i) * comb(frame_size - successes, sample_size - i)
        for i in range(lowest, min(successes, sample_size) + 1)
        if i >= x
    )
    return Fraction(numerator, comb(frame_size, sample_size))

def population_lower_bound(frame_size: int, sample_size: int, x: int) -> PopulationBound:
    """Invert the hypergeometric upper tail at the 90% one-sided level."""

    check_plan_limits(frame_size, sample_size)
    if not 0 <= x <= sample_size:
        raise ValueError("observed successes must be between 0 and sample size")
    rows, rejected = [], -1
    for successes in range(frame_size + 1):
        probability = hypergeom_tail(frame_size, successes, sample_size, x)
        rows.append(TailRow(successes, probability))
        if probability <= ALPHA:
            rejected = successes
    return PopulationBound(frame_size, sample_size, x, tuple(rows), rejected + 1)

def parse_fraction(text: str) -> Fraction:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("fraction threshold must be a non-empty string")
    try:
        value = Fraction(text.strip())
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"invalid fraction threshold: {text!r}") from exc
    if not 0 < value <= 1:
        raise ValueError("fraction threshold must be within (0, 1]")
    return value

