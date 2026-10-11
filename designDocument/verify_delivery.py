"""Validate delivery completeness, local links, and Python syntax (stdlib only)."""

import ast
import re
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    for number in range(1, 12):
        code = f"R{number:02d}"
        designs = list(root.glob(f"{code}_*.md"))
        specs = list((root / "specs").glob(f"{code}.spec.md"))
        tests = list((root / "tests").glob(f"test_{code.lower()}_*.py"))
        assert len(designs) == len(specs) == len(tests) == 1, f"Missing/duplicate files for {code}"
        tree = ast.parse(tests[0].read_text(encoding="utf-8"))
        functions = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}
        spec = specs[0].read_text(encoding="utf-8")
        mapped = set(re.findall(r"`(test_\w+)`", spec))
        assert mapped == functions, f"Spec/test mapping mismatch for {code}: {mapped ^ functions}"
        assert re.search(rf"{code}-S\d+", spec), f"Missing spec IDs for {code}"

    for path in root.rglob("*.md"):
        for destination in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            if "://" in destination or destination.startswith("#"):
                continue
            target = (path.parent / destination.split("#")[0]).resolve()
            assert target.exists(), f"Broken link in {path.relative_to(root)}: {destination}"
    # Do not traverse pytest scratch directories, which may contain generated scripts.
    sources = [root / "__init__.py", root / "verify_delivery.py", *sorted((root / "tests").glob("*.py"))]
    for path in sources:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    print("OK: 11 designs, 11 specs, 11 test modules; all test functions mapped; local links and syntax valid.")


if __name__ == "__main__":
    main()
