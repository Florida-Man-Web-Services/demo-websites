#!/usr/bin/env python3
"""Run the deterministic AI 411 pre-release checks.

The gate deliberately runs each voice test module in its own subprocess. The
voice tests reload environment-sensitive modules, so a single pytest process
can retain a stale module object between files. CI uses harmless placeholder
values for required settings; this script never reads or prints credentials.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOICE_TESTS = ROOT / "voice-agent" / "tests"
LANDING = ROOT / "hosting" / "ai411" / "index.html"
HOSTING_DOCKERFILE = ROOT / "hosting" / "Dockerfile"
CONFIG = ROOT / "voice-agent" / "config.py"
REALTIME = ROOT / "voice-agent" / "realtime.py"
BUSINESSES = ROOT / "voice-agent" / "businesses.py"

# These values are intentionally non-secret and override any developer .env
# values. Tests must not dial, call an LLM, send SMS, or write the local call DB.
TEST_ENV = {
    "AGENT_MODE": "sales",
    "CALL_DB": "0",
    "CALL_LOG_DUAL_WRITE_CSV": "0",
    "VALIDATE_TWILIO_WEBHOOKS": "0",
    "VOICE_BACKEND": "grok-realtime",
    "TWILIO_ACCOUNT_SID": "ACtest",
    "TWILIO_AUTH_TOKEN": "test-token",
    "TWILIO_PHONE_NUMBER": "+18449030365",
    "PUBLIC_BASE_URL": "https://voice.example.test",
    "ANTHROPIC_API_KEY": "sk-ant-test",
    "DEEPINFRA_API_KEY": "di-test",
    "XAI_API_KEY": "xai-test",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _check(label: str, condition: bool, detail: str) -> bool:
    state = "PASS" if condition else "FAIL"
    print(f"{state} {label}: {detail}")
    return condition


def static_checks() -> tuple[bool, dict[str, int]]:
    """Check source/build invariants without importing the application."""
    ok = True
    for path in (LANDING, HOSTING_DOCKERFILE, CONFIG, REALTIME, BUSINESSES):
        ok &= _check("file", path.is_file(), str(path.relative_to(ROOT)))
    if not all(path.is_file() for path in (LANDING, HOSTING_DOCKERFILE, CONFIG, REALTIME, BUSINESSES)):
        return bool(ok), {"generated_sites": 0, "landing_bytes": 0, "landing_sections": 0}

    landing = _read(LANDING)
    dockerfile = _read(HOSTING_DOCKERFILE)
    config = _read(CONFIG)
    realtime = _read(REALTIME)
    businesses = _read(BUSINESSES)

    landing_markers = {
        "landing identity": "Gainesville AI 411" in landing,
        "landing canonical": 'rel="canonical" href="https://ai411.floridamanweb.online/"' in landing,
        "callback form": 'id="cb-form"' in landing and 'source: "ai411_web"' in landing,
        "callback endpoint": "/api/onboarding/register" in landing,
        "personal opt-in": "Free personal page (opt-in)" in landing,
        "reduced motion": "prefers-reduced-motion" in landing,
    }
    for label, passed in landing_markers.items():
        ok &= _check(label, passed, "required marker present" if passed else "required marker missing")

    parity_markers = {
        "Docker copies AI411 landing": "COPY hosting/ai411/index.html /usr/share/nginx/html/ai411/index.html" in dockerfile,
        "Docker uses 12-char sha256": 'sha256sum "$f"' in dockerfile and 'cut -c1-12' in dockerfile,
        "voice hash uses sha256 bytes": "hashlib.sha256(path.read_bytes()).hexdigest()[:12]" in businesses,
        "realtime PCMU": '"audio/pcmu"' in realtime,
        "realtime 8 kHz": '"rate": 8000' in realtime,
        "console agent ID not required": 'if config.XAI_VOICE_AGENT_ID' in realtime,
        "all supported modes declared": all(
            f'"{mode}"' in config
            for mode in ("sales", "ai411", "owner_updates", "unified", "onboarding", "auto")
        ),
    }
    for label, passed in parity_markers.items():
        ok &= _check(label, passed, "source invariant present" if passed else "source invariant missing")

    generated = sorted((ROOT / "generated-sites").glob("*.html"))
    metrics = {
        "generated_sites": len(generated),
        "landing_bytes": len(landing.encode("utf-8")),
        "landing_sections": len(re.findall(r"<section\b", landing, re.IGNORECASE)),
    }
    print(
        "METRIC generated_sites={generated_sites} landing_bytes={landing_bytes} "
        "landing_sections={landing_sections}".format(**metrics)
    )
    ok &= _check("landing sections", metrics["landing_sections"] >= 5, "at least five sections")

    if generated:
        sample = generated[0]
        digest = hashlib.sha256(sample.read_bytes()).hexdigest()[:12]
        ok &= _check("content hash shape", bool(re.fullmatch(r"[0-9a-f]{12}", digest)), "12 lowercase hex")

    return bool(ok), metrics


def _summary(output: str) -> tuple[int, int]:
    """Return passed and failed/error counts from a quiet pytest summary."""
    passed = sum(int(value) for value in re.findall(r"(\d+) passed", output))
    failed = sum(int(value) for value in re.findall(r"(\d+) failed", output))
    failed += sum(int(value) for value in re.findall(r"(\d+) error", output))
    return passed, failed


def run_tests() -> tuple[bool, int, int, int]:
    files = sorted(VOICE_TESTS.glob("test_*.py"))
    if not files:
        print(f"FAIL tests: no test modules under {VOICE_TESTS.relative_to(ROOT)}")
        return False, 0, 0, 0

    env = os.environ.copy()
    env.update(TEST_ENV)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    total_passed = 0
    total_failed = 0
    passed_files = 0
    for path in files:
        command = [sys.executable, "-m", "pytest", str(path.relative_to(ROOT)), "-q"]
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        output = (result.stdout or "") + (result.stderr or "")
        passed, failed = _summary(output)
        total_passed += passed
        total_failed += failed
        if result.returncode == 0:
            passed_files += 1
            print(f"PASS tests/{path.name}: {passed} passed")
        else:
            print(f"FAIL tests/{path.name}: exit={result.returncode} passed={passed} failed={failed}")
            # Keep CI diagnostics bounded and avoid echoing arbitrary logs.
            lines = [line for line in output.splitlines() if line.strip()]
            for line in lines[-12:]:
                print(f"  {line}")
    print(
        f"METRIC test_files={len(files)} passed_files={passed_files} "
        f"tests_passed={total_passed} tests_failed={total_failed}"
    )
    return total_failed == 0 and passed_files == len(files), len(files), total_passed, total_failed


def health_check(url: str, expected_mode: str | None) -> bool:
    """Perform an optional read-only health check and print non-PII fields."""
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.load(response)
            status = response.status
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL health: {type(exc).__name__}")
        return False

    ok = status == 200 and payload.get("ok") is True
    if expected_mode is not None:
        ok &= payload.get("agent_mode") == expected_mode
    fields = {
        key: payload.get(key)
        for key in ("ok", "agent_mode", "customers_registry", "personal_pages", "voice_auth_vendor")
    }
    print(f"{'PASS' if ok else 'FAIL'} health: status={status} fields={json.dumps(fields, sort_keys=True)}")
    return bool(ok)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-tests", action="store_true", help="run static checks only")
    parser.add_argument("--health-url", help="optional read-only health endpoint URL")
    parser.add_argument("--expected-mode", help="require this mode when --health-url is used")
    args = parser.parse_args(argv)

    static_ok, _ = static_checks()
    tests_ok = True
    if args.skip_tests:
        print("INFO tests: skipped by --skip-tests")
    else:
        tests_ok, _, _, _ = run_tests()
    health_ok = True
    if args.health_url:
        health_ok = health_check(args.health_url, args.expected_mode)

    passed = static_ok and tests_ok and health_ok
    print(f"AI411_RELEASE_GATE={'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
