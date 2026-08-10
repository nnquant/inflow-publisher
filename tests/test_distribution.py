from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "inflow-publisher"


class DistributionTest(unittest.TestCase):
    def test_required_release_files_exist(self) -> None:
        required = [
            ROOT / "README.md",
            ROOT / "LICENSE",
            ROOT / "CHANGELOG.md",
            ROOT / "SECURITY.md",
            ROOT / "VERSION",
            SKILL / "SKILL.md",
            SKILL / "agents" / "openai.yaml",
            SKILL / "references" / "api.md",
            SKILL / "scripts" / "inflow.py",
        ]
        missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
        self.assertEqual([], missing)

    def test_skill_identity_and_release_version(self) -> None:
        skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertRegex(skill_text, r"(?m)^name: inflow-publisher$")
        self.assertEqual("0.1.1", (ROOT / "VERSION").read_text(encoding="utf-8").strip())

    def test_distribution_contains_no_live_api_key(self) -> None:
        findings: list[str] = []
        for path in ROOT.rglob("*"):
            if not path.is_file() or ".git" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for match in re.findall(r"ifk_[A-Za-z0-9_-]{16,}", text):
                if "test-secret-never-print" not in match:
                    findings.append(f"{path.relative_to(ROOT)}:{match[:8]}...")
        self.assertEqual([], findings)


if __name__ == "__main__":
    unittest.main()
