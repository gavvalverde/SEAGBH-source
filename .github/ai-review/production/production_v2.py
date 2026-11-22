"""Orquestração v2, isolada do runner v1 até passar nas provas offline.

Não executa fontes do PR, jamais retenta chamadas pagas e publica apenas após
cobertura completa, validação local, âncoras intactas e checagem final do HEAD.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess

import contract_v2 as contract
import planning_v2 as planning
import runner
from transport import Problem, REASONING_CONTROL_CODES


def _usage_sum(previous, current):
    prior = dict(previous or {})
    if isinstance(current, dict):
        for key in ("prompt_tokens", "completion_tokens", "total_tokens",
                    "cost_micro_usd", "reasoning_tokens", "visible_tokens"):
            value = current.get(key)
            if type(value) is int and value >= 0:
                prior[key] = prior.get(key, 0) + value
    return prior or None


def _adjudication_chunk(plan):
    """Refutation sees ALL changed diff chunks, not just supporting citations.

    If the complete context cannot fit, the review remains unpublished.
    """
    return planning.Chunk(
        0,
        tuple(part for chunk in plan.chunks for part in chunk.parts),
        tuple(row for chunk in plan.chunks for row in chunk.anchors),
    )


def run_review_v2(event, enabled, github, model, *, version):
    try:
        pinned = runner.event_snapshot(event, enabled)
    except Problem as error:
        return runner.Outcome(error.code,
                              0 if error.code.startswith("skip_") else 1)
    key = (runner.REPOSITORY, pinned.number)
    with runner._LOCK_GUARD:
        lock = runner._LOCKS.setdefault(key, runner.threading.Lock())
    if not lock.acquire(blocking=False):
        return runner.Outcome("skip_busy")
    stage = "identity"
    coverage = None
    usage = None

    def finish(code, exit_code=0, **details):
        return runner.Outcome(code, exit_code, stage=stage, usage=usage,
                              coverage=coverage, **details)

    try:
        runner.ensure_current(github, pinned)
        bot_id = github.bot()
        if runner.already_reviewed(github.reviews(pinned.number), pinned, bot_id, version):
            return finish("skip_duplicate")

        stage = "planning"
        plan = planning.build_plan(github, pinned)
        coverage = plan.coverage
        payloads = [contract.make_payload(pinned, plan, chunk) for chunk in plan.chunks]
        # Reserve enough for ONE adversarial refutation before any paid request.
        contract.worst_case_usd(payloads, reserve_refutation=True)

        candidates = []
        for chunk, payload in zip(plan.chunks, payloads):
            runner.ensure_current(github, pinned)
            stage = "model"
            content = model.complete(payload)
            usage = _usage_sum(usage, getattr(model, "last_usage", None))
            if usage is not None and usage.get("cost_micro_usd", 0) > contract.MAX_TOTAL_USD * 1_000_000:
                raise Problem("v2_reported_budget_exceeded")
            stage = "validation"
            result = contract.validate_reply(
                content, frozenset(a[0] for a in chunk.anchors)
            )
            candidates.extend(result["findings"])
            coverage["chunks_completed"] += 1
            if len(candidates) > contract.MAX_CANDIDATES:
                raise Problem("v2_candidate_limit")

        if candidates:
            stage = "refutation"
            # If the full diff plus candidates exceeds the per-request ceiling,
            # no claim is published and none is silently discarded.
            refute_chunk = _adjudication_chunk(plan)
            refute_payload = contract.make_payload(
                pinned, plan, refute_chunk, candidates=candidates
            )
            # Also enforce the aggregate budget with the REAL refutation bytes.
            contract.worst_case_usd(payloads + [refute_payload],
                                    reserve_refutation=False)
            runner.ensure_current(github, pinned)
            content = model.complete(refute_payload)
            usage = _usage_sum(usage, getattr(model, "last_usage", None))
            if usage is not None and usage.get("cost_micro_usd", 0) > contract.MAX_TOTAL_USD * 1_000_000:
                raise Problem("v2_reported_budget_exceeded")
            stage = "validation"
            allowed = frozenset(c["anchor_id"] for c in candidates)
            verdict = contract.validate_reply(content, allowed)
            original = {(x["anchor_id"], x["category"], x["title"]) for x in candidates}
            if any((x["anchor_id"], x["category"], x["title"]) not in original
                   for x in verdict["findings"]):
                raise Problem("v2_refutation_changed_claim")
            candidates = verdict["findings"]

        # A matching anchor is not a semantic verdict. Retain distinct mechanisms
        # at a location; only deduplicate identical root-cause descriptions.
        seen = set()
        independent = []
        for finding in candidates:
            a = plan.anchors[finding["anchor_id"]]
            identity = (a[1], a[2], finding["category"],
                        finding["mechanism"].casefold().strip())
            if identity not in seen:
                seen.add(identity)
                independent.append(finding)

        if runner.already_reviewed(github.reviews(pinned.number), pinned, bot_id, version):
            return finish("skip_duplicate")
        stage = "publication"
        body = contract.render(independent, plan, pinned, bot_id, version)
        runner.ensure_current(github, pinned)
        try:
            posted = github.post(pinned, body)
        except Problem as error:
            if error.code == "authentication_error":
                raise
            if error.status == 422:
                runner.ensure_current(github, pinned)
            if error.status is not None and 400 <= error.status < 500:
                raise Problem("v2_publication_error", error.status) from None
            if runner.already_reviewed(github.reviews(pinned.number), pinned, bot_id, version):
                return finish("published_reconciled", published=True)
            raise Problem("v2_publication_uncertain") from None
        if not runner.already_reviewed([posted], pinned, bot_id, version):
            raise Problem("v2_publication_unconfirmed")
        return finish("published", published=True)
    except Problem as error:
        return finish(error.code, 0 if error.code == "skip_closed" else 1)
    except Exception:
        return finish("v2_unexpected_execution_error", 1)
    finally:
        lock.release()


def reviewer_version():
    here = Path(__file__).resolve().parent
    hash_ = hashlib.sha256()
    hash_.update(runner.reviewer_version().encode("ascii"))
    for name in ("planning_v2.py", "contract_v2.py", "production_v2.py"):
        file = here / name
        hash_.update(name.encode("utf-8") + b"\0" + file.read_bytes() + b"\0")
    schema = here.parent / "review-schema-v2.json"
    hash_.update(schema.read_bytes())
    return hash_.hexdigest()


def main():
    if os.environ.get("AI_REVIEW_ENABLED") != "true":
        print('{"code":"skip_disabled"}')
        return 0
    if os.environ.get("GITHUB_RUN_ATTEMPT") != "1":
        print('{"code":"skip_rerun"}')
        return 0
    model = None
    try:
        trusted = os.environ.get("AI_REVIEW_TRUSTED_SHA")
        if (os.environ.get("GITHUB_EVENT_NAME") != "pull_request_target"
                or os.environ.get("GITHUB_REPOSITORY") != runner.REPOSITORY
                or not runner.core._full_sha(trusted)
                or trusted != os.environ.get("GITHUB_SHA")):
            raise Problem("untrusted_execution_context")
        checked = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
        if checked != trusted:
            raise Problem("untrusted_checkout")
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        runner.event_snapshot(event, "true")
        from transport import OpenRouter
        model = OpenRouter(os.environ.get("OPENROUTER_API_KEY"),
                           max_attempts=1, require_reasoning_usage=True)
        outcome = run_review_v2(
            event, "true", runner.GitHub(os.environ.get("GITHUB_TOKEN")),
            model, version=reviewer_version()
        )
    except Problem as error:
        outcome = runner.Outcome(error.code,
                                 0 if error.code.startswith("skip_") else 1)
    except Exception:
        outcome = runner.Outcome("v2_unexpected_execution_error", 1)
    diag = {"code": outcome.code, "stage": outcome.stage,
            "published": outcome.published}
    if outcome.coverage is not None:
        diag["coverage"] = outcome.coverage
    if outcome.usage is not None:
        diag["usage"] = outcome.usage
    if model is not None and model.last_reasoning_control in REASONING_CONTROL_CODES:
        diag["reasoning_control"] = model.last_reasoning_control
    print(json.dumps(diag, sort_keys=True))
    return outcome.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
