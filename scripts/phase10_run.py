"""Run consolidated offline local acceptance checks and write a JSON report."""
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / 'phase10_acceptance_report.json'

def run(label, args, env):
    print(f'\n=== {label} ===', flush=True)
    result = subprocess.run(args, cwd=ROOT, env=env, text=True)
    return {'name': label, 'passed': result.returncode == 0, 'exit_code': result.returncode}

def main():
    os.chdir(ROOT)
    env = os.environ.copy()
    env.pop('FIXFLOW_RUN_LIVE_SEARCH_BEHAVIOR', None)
    env.pop('FIXFLOW_RUN_LIVE_MCP', None)
    env.pop('FIXFLOW_RUN_LIVE_AGENTS', None)
    env.pop('FIXFLOW_DB', None)
    checks = [
        run('Dependency consistency', [sys.executable, '-m', 'pip', 'check'], env),
        run('Python syntax', [sys.executable, '-m', 'compileall', '-q', 'backend', 'frontend', 'tests'], env),
        run('Full offline regression and Phase 10 acceptance',
            [sys.executable, '-m', 'pytest', 'tests', '-q', '--tb=short'], env),
    ]
    report = {
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'scope': 'offline local checks; live providers and manual Streamlit UI not verified',
        'checks': checks,
        'all_passed': all(c['passed'] for c in checks),
    }
    REPORT.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('\nAcceptance report:', REPORT)
    print('RESULT:', 'PASS' if report['all_passed'] else 'FAIL')
    return 0 if report['all_passed'] else 1

if __name__ == '__main__':
    sys.exit(main())
