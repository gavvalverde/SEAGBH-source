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
    duplicate_location_pairs: int = 0
    quote_mismatch_count: int = 0
    source_unavailable_count: int = 0
    short_line_count: int = 0
    invalid_reference_count: int = 0

    @property
    def requires_manual_review(self) -> bool:
        return self.unanchored_count > 0 or self.duplicate_location_pairs > 0


def assess(findings: list[dict], code_lines: dict) -> QualityAssessment:
    """Trusted bounded decision, no logging or return of untrusted strings."""
    if not isinstance(findings, list) or len(findings) > 25:
        raise ValueError("unvalidated_findings")
    if not isinstance(code_lines, dict):
        raise ValueError("invalid_diff_lines")
    statuses = [evidence.literal_anchor_status(finding, code_lines)
                for finding in findings]
    misses = sum(status != "anchored" for status in statuses)
    # Same exact path AND line is a collision signal, not proof of duplicate
    # causality. Hold the ENTIRE review; never choose a winner automatically.
    locations = {}
    collision_count = 0
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        path, line = finding.get("path"), finding.get("line")
        if not isinstance(path, str) or type(line) is not int:
            continue
        key = (path, line)
        collision_count += locations.get(key, 0)
        locations[key] = locations.get(key, 0) + 1
    return QualityAssessment(
        checked_count=len(findings), unanchored_count=misses,
        duplicate_location_pairs=collision_count,
        quote_mismatch_count=statuses.count("quote_mismatch"),
        source_unavailable_count=statuses.count("source_unavailable"),
        short_line_count=statuses.count("short_line"),
        invalid_reference_count=statuses.count("invalid_reference"),
    )
