"""Run test cases for a skill and score the results."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from skills import skill_registry, skill_runner

logger = logging.getLogger(__name__)


async def test_skill(session: AsyncSession, skill_id: str) -> dict[str, Any]:
    skill = await skill_registry.get(session, skill_id)
    if not skill:
        return {"ok": False, "error": "skill_not_found", "score": 0.0}

    test_cases = skill.test_cases or []
    if not test_cases:
        return {"ok": True, "score": 1.0, "note": "no_test_cases", "results": []}

    passed = 0
    results = []

    for case in test_cases:
        result = await skill_runner.run(session, skill_id, input_data=case.get("input"))
        expected = case.get("expected_outcome", "success")
        actual = "success" if result["ok"] else "failure"
        case_passed = actual == expected
        if case_passed:
            passed += 1
        results.append(
            {
                "case": case.get("name", "unnamed"),
                "passed": case_passed,
                "expected": expected,
                "actual": actual,
            }
        )

    score = passed / len(test_cases)
    test_result = "passed" if score >= 0.8 else "failed"

    await skill_registry.update(session, skill_id, {"meta": {**(skill.meta or {}), "last_test_score": score}})

    return {
        "ok": score >= 0.8,
        "score": score,
        "test_result": test_result,
        "passed": passed,
        "total": len(test_cases),
        "results": results,
    }


async def score_skill(session: AsyncSession, skill_id: str) -> float:
    skill = await skill_registry.get(session, skill_id)
    if not skill:
        return 0.0
    total = skill.success_count + skill.failure_count
    if total == 0:
        return 0.5
    return skill.success_count / total
