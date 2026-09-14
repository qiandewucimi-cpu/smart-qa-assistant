# -*- coding: utf-8 -*-
"""不依赖 LLM、网络和私有数据的核心回归测试。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from clean_text import desensitize  # noqa: E402
from eval_common import (  # noqa: E402
    fisher,
    framework_failure,
    judge_keyword,
    param_memory_answer,
    wilson,
)


class DesensitizeTests(unittest.TestCase):
    def test_structured_sensitive_fields_are_masked(self) -> None:
        text = "访问 https://example.com，内网 10.1.2.3，SWIFT Code: ABCDUS33，账号 1234-123456-789。"
        cleaned, counts = desensitize(text)
        self.assertIn("[链接]", cleaned)
        self.assertIn("[内网地址]", cleaned)
        self.assertIn("[银行代码]", cleaned)
        self.assertIn("[银行账号]", cleaned)
        self.assertEqual(counts["内网IP"], 1)
        self.assertEqual(counts["银行代码"], 1)
        self.assertEqual(counts["银行账号"], 1)


class EvaluationRuleTests(unittest.TestCase):
    def test_framework_failure_modes(self) -> None:
        self.assertEqual(framework_failure({"answer": "tool-iteration limit reached"}), "limit")
        self.assertEqual(framework_failure({"answer": "```json\n{}"}), "json_dump")
        self.assertEqual(
            framework_failure({"answer": "I found the following relevant project context ..."}),
            "raw_dump",
        )

    def test_negated_wrong_term_is_not_hallucination(self) -> None:
        answer = "待核单与已核单不同；这里不是等待审核，而是进入核单流程。"
        self.assertEqual(judge_keyword("D2", answer), "hit")

    def test_zero_reference_substantive_answer_is_risky(self) -> None:
        self.assertTrue(param_memory_answer(0, "根据通常做法，应该先提交审批。"))
        self.assertFalse(param_memory_answer(0, "当前知识库没有找到相关信息。"))

    def test_statistics_helpers(self) -> None:
        low, high = wilson(0, 21)
        self.assertEqual(low, 0.0)
        self.assertAlmostEqual(high, 0.1546, places=3)
        self.assertAlmostEqual(fisher(4, 1, 0, 5), 0.0238, places=3)


if __name__ == "__main__":
    unittest.main()
