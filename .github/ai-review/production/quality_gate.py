"""Conservative publication gate, operating only on already validated findings.

A literal quote anchors a claim to bytes in the checked, immutable diff.
It cannot prove the causal mechanism and must not masquerade as semantic review.
If any accepted finding lacks the anchor, block the ENTIRE automatic review,
preserving the status for manual follow-up rather than silently returning clean.
"""
from __future__ import annotations

from dataclasses import dataclass

import evidence


@dataclass(frozen=True)
class QualityAssessment:
    checked_count: int
    unanchored_count: int

    @property
    def requires_manual_review(self) -> bool:
        return self.unanchored_count > 0


def assess(findings: list[dict], code_lines: dict) -> QualityAssessment:
    """Trusted bounded decision, no logging or return of untrusted strings."""
    if not isinstance(findings, list) or len(findings) > 25:
        raise ValueError("unvalidated_findings")
    if not isinstance(code_lines, dict):
        raise ValueError("invalid_diff_lines")
    misses = sum(
        not evidence.has_literal_code_anchor(finding, code_lines)
        for finding in findings
    )
    return QualityAssessment(checked_count=len(findings), unanchored_count=misses)
