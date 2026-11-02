"""Offline source inventory for FixFlow RC1. No imports from the application."""
from pathlib import Path
import re
root = Path(__file__).resolve().parent.parent
print("FixFlow RC1 offline inventory")
for name in ("backend", "frontend", "tests", "policies", "scripts", "eval"):
    p = root / name
    print(f"{name}: {sum(1 for _ in p.rglob('*') if _.is_file()) if p.exists() else 'NOT PRESENT'} files")
for p in (root / "tests").rglob("test_*.py") if (root / "tests").exists() else []:
    s = p.read_text(encoding="utf-8", errors="replace")
    gates = sorted(set(re.findall(r"FIXFLOW_RUN_[A-Z0-9_]+|FIXFLOW_TEST_[A-Z0-9_]+", s)))
    if gates: print("TEST GATES", p.relative_to(root), ", ".join(gates))
for p in root.rglob("*"):
    if p.is_file() and ("__pycache__" in p.parts or p.suffix in (".pyc", ".sqlite3") or p.name == ".env"):
        print("EXCLUDE FROM RELEASE", p.relative_to(root))
print("Inventory only: does not certify security, grounding, or release readiness.")
