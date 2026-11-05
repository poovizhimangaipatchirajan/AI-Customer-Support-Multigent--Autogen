"""Run FixFlow local tests in separate pytest processes.

This intentionally does NOT bypass production authentication or reset live server
rate limits. Restart your LOCAL FastAPI process before the E2E stage if it has
already consumed the test account's login allowance.
"""
import argparse
import os
import subprocess
import sys

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--e2e", action="store_true", help="Run browser E2E after backend tests")
    args = parser.parse_args()
    env = os.environ.copy()
    stages = [
        ("Backend / offline tests", [sys.executable, "-m", "pytest", "-q", "tests", "--ignore=tests/e2e", "--tb=short"]),
    ]
    if args.e2e:
        stages.append(("Browser E2E (local running FastAPI and Streamlit required)",
                       [sys.executable, "-m", "pytest", "-q", "tests/e2e", "--tb=short"]))
    for label, command in stages:
        print(f"\n=== {label} ===", flush=True)
        result = subprocess.run(command, env=env, check=False)
        if result.returncode:
            print(f"\nFAILED: {label} (exit code {result.returncode})", flush=True)
            return result.returncode
    print("\nAll selected test stages passed.", flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
