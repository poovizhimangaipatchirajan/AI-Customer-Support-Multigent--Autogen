"""FixFlow Phase 10 read-only local preflight. Does not invoke APIs or mutate DB."""
from __future__ import annotations
import ast
import importlib.metadata
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
CHECKS = 0
FAILURES = []

def check(label, ok, detail=''):
    global CHECKS
    CHECKS += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f' — {detail}' if detail else ''))
    if not ok:
        FAILURES.append(label)


def inspect_python(relative, required_names):
    path = ROOT / relative
    if not path.is_file():
        check(f'{relative} exists', False)
        return
    check(f'{relative} exists', True)
    try:
        tree = ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
    except (SyntaxError, UnicodeError) as exc:
        check(f'{relative} parses', False, type(exc).__name__)
        return
    check(f'{relative} parses', True)
    names = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    for name in required_names:
        check(f'{relative} defines {name}', name in names)


def main():
    print('FixFlow AI — Phase 10 read-only preflight')
    print('Python:', sys.version.split()[0])
    print('Root:', ROOT)
    check('Python >= 3.11', sys.version_info >= (3, 11))
    inspect_python('backend/agents/team.py', ('make_team',))
    inspect_python('backend/worker/main.py', ('process', 'claim', 'recover', 'persist', 'finish'))
    inspect_python('backend/rag/hybrid.py', ('HybridRetriever',))
    inspect_python('backend/services/mcp_adapter.py', ('call_novacart_tool',))
    inspect_python('backend/services/public_search_router.py', ('search_approved_topic',))
    check('Tests folder exists', (ROOT / 'tests').is_dir())
    check('Environment template present (.env.example or .env.sample)', any((ROOT / name).is_file() for name in ('.env.example', '.env.sample')))
    for dist in ('pytest', 'fastapi', 'streamlit', 'autogen-agentchat', 'autogen-ext', 'mcp'):
        try:
            version = importlib.metadata.version(dist)
            check(f'{dist} installed', True, version)
        except importlib.metadata.PackageNotFoundError:
            check(f'{dist} installed', False)
    # Only report presence; never read or print credentials.
    print('OPENAI_API_KEY in process environment:', 'set' if os.getenv('OPENAI_API_KEY') else 'not set (may be loaded from .env at runtime)')
    print(f'\nChecks: {CHECKS}; failures: {len(FAILURES)}')
    if FAILURES:
        print('Investigate failures before proceeding; do not modify working code automatically.')
    return 1 if FAILURES else 0

if __name__ == '__main__':
    raise SystemExit(main())
