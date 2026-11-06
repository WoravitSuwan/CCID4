"""The test-case sheet (tools/test_cases.py) only cites automated tests that exist."""
import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_every_cited_test_exists():
    spec = importlib.util.spec_from_file_location("test_cases_doc", ROOT / "tools" / "test_cases.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    missing = []
    for _group, cases in mod.CASES:
        for name, _who, _steps, _expected, auto in cases:
            for ref in re.findall(r"(test_\w+\.py)(?:::(\w+))?", auto):
                path = ROOT / "tests" / ref[0]
                if not path.exists():
                    missing.append(f"{name}: {ref[0]}")
                elif ref[1] and f"def {ref[1]}(" not in path.read_text(encoding="utf-8"):
                    missing.append(f"{name}: {ref[0]}::{ref[1]}")
    assert missing == []


def test_markdown_copy_is_up_to_date(tmp_path):
    spec = importlib.util.spec_from_file_location("test_cases_doc", ROOT / "tools" / "test_cases.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = tmp_path / "TEST_CASES.md"
    mod.write_markdown(out)
    assert out.read_text(encoding="utf-8") == (ROOT / "docs" / "TEST_CASES.md").read_text(encoding="utf-8"), \
        "run: python tools/test_cases.py"
