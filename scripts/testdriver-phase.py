#!/usr/bin/env python3
"""Estate TestDriver phase runner.  Python standard library only."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# datetime.UTC was added in Python 3.11; installed runners still support Python 3.9.
UTC = dt.timezone.utc  # noqa: UP017

SCRIPT = Path(__file__).resolve()
ROOT = SCRIPT.parents[2] if SCRIPT.parent.name == "bin" else SCRIPT.parent.parent
DEFAULT_CONFIG = (
    ROOT / "autonomy/canonical/testdriver-repos.json"
    if (ROOT / "autonomy/canonical/testdriver-repos.json").exists()
    else ROOT / "configs/testdriver.json"
)
ALLOWED_MODES = {"advisory", "required"}
ALLOWED_WHEN = {"pull_request", "merge_group", "pre_manual_qa"}
ALLOWED_PROVIDERS = {"mock", "real"}
HEAL_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:heal(?:ed|ing)?|self[ -]?heal(?:ed|ing)?|adapted\s+selector|recovered)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])(?:sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{8,})(?![A-Za-z0-9])")
MASKED_KEY_RE = re.compile(r"(?im)(\bKey:\s*)\*+[A-Za-z0-9_-]+")
URL_CREDENTIAL_RE = re.compile(r"(?i)([?&](?:data|share|token)=)[^&\s]+")
PACKAGE_PIN = "testdriverai"
VERSION_PIN = "7.11.59"
VITEST_VERSION_PIN = "4.0.18"
INTEGRITY_PIN = "sha512-9k1Mb7/Db4Pr0fIwJXpiaXfgtsmXUcDapK+JHsDx0DoeOyw8oUqj+Wjg5dOc9DT4jtOeU8FgWGRhjHkQNK06zQ=="
CHECK_EXTERNAL_ID = "estate-testdriver-phase-v1"
EXPECTED_RENDERED_FROM_SHA256 = {
    "ClickFinity/GrokOmniAgentOS": "be254afea5ee5f4b9511413128b55f71981550b780d59b136f09d9c829621fbd",
    "ClickFinity/agent-estate": "63c5f2496e5bcef89492af01e562e03abbfc9673b67551a64d2c69fc18940506",
    "ClickFinity/omnirogue": "6bcafa205d6cabc5d8471278dd78240f6c22cfea9e706620d1d406fab9eaa3ed",
    "samblakeads/OmniAgentOS-Starter": "bbcf03de0b6ba0bb0efe8d108ff63fcc60e3d64def6761e7dbad55eb5391a191",
    "samblakeads/ThreeLoops": "33bd05f08cd8f72b6898520fd700c9687856bd9655a753a820de20caa6670028",
    "samblakeads/claude-md": "fae563218b025b38471b91c97d1e462ffc08414a14072cefe7382ce28186d395",
    "samblakeads/estate-escrow": "c2b0c0113c1bcf4d5c8f79d8674489e2d35504dded14f880986e94f7a6d5302e",
    "samblakeads/fleet-lb-dispatcher": "298fd0d848e4e7181b606bffdfd7518218e2d9820a868fcb6ff56cf9d9e2fd6c",
}

lib_dir = ROOT / "autonomy/lib"
if lib_dir.exists():
    sys.path.insert(0, str(lib_dir))
try:
    from untrusted import fence as _shared_fence  # type: ignore
    from untrusted import impl as _shared_fence_impl  # type: ignore
except ImportError:
    _shared_fence = None
    _shared_fence_impl = None


class Refusal(RuntimeError):
    """An operational precondition is unknown or unsafe."""


def _fence(label: str, content: str) -> str:
    """Fallback fence compatible with the estate untrusted-data convention."""
    escaped = content.replace("<<<", "<\u200b<<")
    delimiter = hashlib.sha256(content.encode()).hexdigest()[:24]
    return "\n".join(
        [
            f"<<<OMNIAGENTOS_DATA_NOT_INSTRUCTIONS label={label} delimiter={delimiter}>>>",
            escaped,
            f"<<<END_OMNIAGENTOS_DATA_NOT_INSTRUCTIONS delimiter={delimiter}>>>",
        ]
    )


def fence_vendor_output(content: str) -> str:
    if _shared_fence is None:
        return _fence("TESTDRIVER_VENDOR_OUTPUT", content)
    return _shared_fence(content, "TESTDRIVER_VENDOR_OUTPUT")


def fence_impl() -> str:
    if _shared_fence_impl is None:
        return "bundled"
    return _shared_fence_impl()


def redact_output(content: str, secrets: tuple[str, ...] = ()) -> str:
    redacted = content
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    redacted = TOKEN_RE.sub("[REDACTED]", redacted)
    redacted = MASKED_KEY_RE.sub(r"\1[REDACTED]", redacted)
    return URL_CREDENTIAL_RE.sub(r"\1[REDACTED]", redacted)


def stable_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def config_path(explicit: str | None = None) -> Path:
    return Path(explicit or os.environ.get("TESTDRIVER_CONFIG", DEFAULT_CONFIG)).expanduser()


def load_config(explicit: str | None = None) -> dict[str, Any]:
    path = config_path(explicit)
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise Refusal(f"cannot load config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise Refusal(f"config {path} must contain a JSON object")
    return data


def validate_config(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if data.get("version") != 1:
        errors.append("version must be 1")
    runner = data.get("runner")
    if not isinstance(runner, dict):
        errors.append("runner must be an object")
    else:
        for field in (
            "package",
            "version",
            "vitest_version",
            "integrity",
            "image_pin",
            "command_template",
        ):
            if not runner.get(field):
                errors.append(f"runner.{field} is required")
        if not isinstance(runner.get("invocation_verified"), bool):
            errors.append("runner.invocation_verified must be true or false")
        expected_pins = {
            "package": PACKAGE_PIN,
            "version": VERSION_PIN,
            "vitest_version": VITEST_VERSION_PIN,
            "integrity": INTEGRITY_PIN,
        }
        for field, expected in expected_pins.items():
            if runner.get(field) and runner[field] != expected:
                errors.append(f"runner.{field} must equal the approved pin {expected!r}")
        command_template = runner.get("command_template")
        if not isinstance(command_template, list) or not command_template:
            errors.append("runner.command_template must be a non-empty list")
        elif not all(isinstance(part, str) and part for part in command_template):
            errors.append("runner.command_template entries must be non-empty strings")
        elif not any("{flow_file}" in part for part in command_template):
            errors.append("runner.command_template must contain {flow_file}")
        else:
            try:
                [
                    part.format(
                        package=PACKAGE_PIN,
                        version=VERSION_PIN,
                        flow_file="flow.test.mjs",
                        flow_config="vitest.config.mjs",
                    )
                    for part in command_template
                ]
            except (KeyError, ValueError) as exc:
                errors.append(f"runner.command_template has an invalid placeholder: {exc}")
    rip_out = data.get("rip_out")
    if not isinstance(rip_out, dict):
        errors.append("rip_out must be an object")
    else:
        rip_out_types = {
            "fp_rate_max": (int, float),
            "healed_regression_max": (int,),
            "p95_latency_s": (int, float),
            "window_days": (int,),
        }
        for field, expected_types in rip_out_types.items():
            value = rip_out.get(field)
            if isinstance(value, bool) or not isinstance(value, expected_types):
                errors.append(f"rip_out.{field} has the wrong type")
    heal_policy = data.get("heal_policy")
    if not isinstance(heal_policy, dict):
        errors.append("heal_policy must be an object")
    elif not isinstance(heal_policy.get("guard_path_prefixes"), list) or not all(
        isinstance(item, str) and item for item in heal_policy.get("guard_path_prefixes", [])
    ):
        errors.append("heal_policy.guard_path_prefixes must be a list of non-empty strings")
    rendered_hash = data.get("rendered_from_sha256")
    if rendered_hash is not None and not re.fullmatch(r"[0-9a-f]{64}", str(rendered_hash)):
        errors.append("rendered_from_sha256 must be a lowercase SHA-256 digest")
    repos = data.get("repos")
    if not isinstance(repos, dict) or not repos:
        errors.append("repos must be a non-empty object")
        return errors
    for repo, row in repos.items():
        prefix = f"repos.{repo}"
        if not isinstance(row, dict):
            errors.append(f"{prefix} must be an object")
            continue
        if not isinstance(row.get("enabled"), bool):
            errors.append(f"{prefix}.enabled must be true or false")
        mode = row.get("mode")
        if mode not in ALLOWED_MODES:
            errors.append(f"{prefix}.mode unknown value {mode!r}; expected advisory|required")
        provider = row.get("provider")
        if provider not in ALLOWED_PROVIDERS:
            errors.append(f"{prefix}.provider unknown value {provider!r}; expected mock|real")
        when = row.get("when")
        if not isinstance(when, list):
            errors.append(f"{prefix}.when must be a list")
        else:
            for event in when:
                if event not in ALLOWED_WHEN:
                    errors.append(
                        f"{prefix}.when unknown value {event!r}; expected pull_request|merge_group|pre_manual_qa"
                    )
        if not isinstance(row.get("pre_manual_qa"), bool):
            errors.append(f"{prefix}.pre_manual_qa must be true or false")
        if row.get("enabled"):
            flows = row.get("flows")
            if not isinstance(flows, list) or not flows:
                errors.append(f"{prefix}.flows must be a non-empty list when enabled")
                continue
            for index, flow in enumerate(flows):
                fp = f"{prefix}.flows[{index}]"
                if not isinstance(flow, dict):
                    errors.append(f"{fp} must be an object")
                    continue
                for field in ("name", "file", "fixture_seed"):
                    if not flow.get(field):
                        errors.append(f"{fp}.{field} is required")
                if flow.get("stable_chrome") is not True:
                    errors.append(
                        f"{fp}.stable_chrome must be true: vision fingerprints over "
                        "non-deterministic data are flaky by construction"
                    )
        elif not row.get("reason"):
            errors.append(f"{prefix}.reason is required when disabled")
    return errors


def validate_rendered_config(data: dict[str, Any], expected: str | None) -> list[str]:
    if expected is None:
        return ["vendored runner has no rendered config hash expectation"]
    actual = data.get("rendered_from_sha256")
    if actual != expected:
        return [f"rendered config hash differs from vendored runner expectation {expected}"]
    return []


def rendered_reinstall_command() -> str:
    return f"testdriver-phase install --repo-dir {shlex.quote(str(ROOT))}"


def validate_rendered_workflow(workflow: str) -> list[str]:
    """Refuse workflow indirection around the hash-pinned vendored runner."""
    unfolded = re.sub(r"\\\r?\n[ \t]*", " ", workflow)
    invocation_re = re.compile(
        r"(?<![A-Za-z0-9_./-])python3[ \t]+"
        r"(?P<runner>[^ \t\r\n;&|()]+)[ \t]+"
        r"(?P<command>config|run)\b(?P<arguments>[^\r\n]*)"
    )
    invocations = [match.groupdict() for match in invocation_re.finditer(unfolded)]
    errors: list[str] = []
    if len(invocations) != 2:
        errors.append("rendered workflow must contain exactly one config and one run invocation")
        return errors
    if any(item["runner"] != "scripts/testdriver-phase.py" for item in invocations):
        errors.append("rendered workflow must invoke exactly scripts/testdriver-phase.py")
    commands = [item["command"] for item in invocations]
    config_args = invocations[0]["arguments"]
    if commands != ["config", "run"] or not re.search(
        r"(?:^|[ \t])--check-rendered(?:[ \t]|$)", config_args
    ):
        errors.append("rendered workflow must call config --check-rendered before run")
    return errors


def validate_rendered_artifacts(data: dict[str, Any]) -> list[str]:
    artifacts = (
        ("runner_sha256", SCRIPT),
        ("template_sha256", ROOT / ".github/workflows/testdriver-phase.yml"),
    )
    errors: list[str] = []
    for field, path in artifacts:
        expected = data.get(field)
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            errors.append(f"{field} is missing or invalid")
            continue
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            errors.append(f"cannot hash rendered artifact {path}: {exc}")
            continue
        if actual != expected:
            errors.append(f"{field} differs for {path}")
    workflow_path = ROOT / ".github/workflows/testdriver-phase.yml"
    try:
        workflow = workflow_path.read_text()
    except (OSError, UnicodeError) as exc:
        errors.append(f"cannot parse rendered workflow {workflow_path}: {exc}")
    else:
        errors.extend(validate_rendered_workflow(workflow))
    if errors:
        errors.append(f"re-install with: {rendered_reinstall_command()}")
    return errors


def command_config(args: argparse.Namespace) -> int:
    try:
        data = load_config(args.path)
    except Refusal as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    errors = validate_config(data)
    if args.check_rendered:
        repos = data.get("repos")
        repo = next(iter(repos)) if isinstance(repos, dict) and len(repos) == 1 else None
        errors.extend(validate_rendered_config(data, EXPECTED_RENDERED_FROM_SHA256.get(repo)))
        errors.extend(validate_rendered_artifacts(data))
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"valid: {config_path(args.path)}")
    return 0


def repo_row(config: dict[str, Any], repo: str) -> dict[str, Any]:
    row = config.get("repos", {}).get(repo)
    if not isinstance(row, dict):
        raise Refusal(f"repository {repo!r} is absent from config")
    defaults = config.get("defaults", {})
    return {**defaults, **row} if isinstance(defaults, dict) else dict(row)


def archive_root(config: dict[str, Any]) -> Path:
    raw = os.environ.get("TESTDRIVER_ARCHIVE_ROOT") or config.get("evidence_archive_root")
    if not raw:
        raise Refusal("evidence_archive_root is missing")
    return Path(str(raw)).expanduser()


def run_capture(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, text=True, capture_output=True, check=False, **kwargs)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Refusal(f"cannot run {shlex.join(command)}: {exc}") from exc


def seed_flow(flow: dict[str, Any], cwd: Path) -> tuple[bool, str]:
    command = str(flow["fixture_seed"])
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"fixture seed could not run: {exc}"
    output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
    if result.returncode:
        return False, f"fixture seed exited {result.returncode}: {output}"
    return True, output


BUILTIN_MOCKS: dict[str, dict[str, Any]] = {
    "pass": {"status": "pass", "duration_s": 4, "output": "mock flow passed"},
    "fail": {"status": "fail", "duration_s": 5, "output": "mock assertion failed"},
    "heal_nonguard": {
        "status": "pass",
        "duration_s": 6,
        "output": 'Self-Heal {"path":"ui/selectors/cockpit.json","before":"#old","after":"#new"}',
    },
    "heal_guard": {
        "status": "pass",
        "duration_s": 6,
        "output": 'healing {"path":"scripts/gates/ui-gate.py","before":"allow","after":"skip"}',
    },
    "timeout": {"status": "timeout", "duration_s": 1201, "output": "mock provider timeout"},
}


def load_mock_scenario(name: str) -> dict[str, Any]:
    fixture = ROOT / "autonomy/tests/fixtures/testdriver" / f"{name}.json"
    if fixture.exists():
        try:
            value = json.loads(fixture.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise Refusal(f"cannot load mock scenario {fixture}: {exc}") from exc
        if not isinstance(value, dict):
            raise Refusal(f"mock scenario {fixture} must be a JSON object")
        return value
    if name in BUILTIN_MOCKS:
        return BUILTIN_MOCKS[name]
    raise Refusal(f"mock scenario {name!r} not found at {fixture}")


def detect_heal_events(output: str, flow_name: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in output.splitlines():
        if not HEAL_RE.search(line):
            continue
        event: dict[str, Any] = {
            "flow": flow_name,
            "path": "<unknown>",
            "before": "<unavailable>",
            "after": line,
        }
        json_start = line.find("{")
        if json_start >= 0:
            try:
                detail = json.loads(line[json_start:])
            except json.JSONDecodeError:
                detail = None
            if isinstance(detail, dict):
                for field in ("path", "before", "after"):
                    if isinstance(detail.get(field), str):
                        event[field] = detail[field]
        if event["path"] == "<unknown>":
            match = re.search(
                r"(?:path|file)\s*[=:]\s*['\"]?([^\s'\"]+)", line, re.IGNORECASE
            )
            if match:
                event["path"] = match.group(1)
        events.append(event)
    return events


def execute_mock(flows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    name = os.environ.get("TESTDRIVER_MOCK_SCENARIO", "pass")
    scenario = load_mock_scenario(name)
    status = str(scenario.get("status", "fail"))
    if status not in {"pass", "fail", "timeout"}:
        raise Refusal(f"mock scenario has unknown status {status!r}")
    duration = float(scenario.get("duration_s", 0))
    output = redact_output(str(scenario.get("output", "")), (os.environ.get("TD_API_KEY", ""),))
    results = [
        {"name": flow["name"], "file": flow["file"], "status": status, "duration_s": duration}
        for flow in flows
    ]
    events = detect_heal_events(output, flows[0]["name"] if flows else "unknown")
    return results, events, output


def execute_real(
    flows: list[dict[str, Any]], runner: dict[str, Any], cwd: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    key = os.environ.get("TD_API_KEY")
    if not key:
        raise Refusal("real provider requires TD_API_KEY")
    package = runner.get("package")
    version = runner.get("version")
    if package != PACKAGE_PIN or version != VERSION_PIN or runner.get("integrity") != INTEGRITY_PIN:
        raise Refusal("real provider refuses an unapproved package/version pin")
    if runner.get("invocation_verified") is not True:
        raise Refusal("real provider command is unverified against a live key")
    expected_dependencies = {
        "testdriverai": VERSION_PIN,
        "vitest": VITEST_VERSION_PIN,
    }
    for dependency, expected_version in expected_dependencies.items():
        manifest = cwd / "node_modules" / dependency / "package.json"
        try:
            installed = json.loads(manifest.read_text()).get("version")
        except (OSError, json.JSONDecodeError, AttributeError) as exc:
            raise Refusal(f"cannot verify local {dependency}@{expected_version}: {exc}") from exc
        if installed != expected_version:
            raise Refusal(
                f"local {dependency} version {installed!r} does not match approved {expected_version!r}"
            )
    vitest_bin = cwd / "node_modules" / ".bin" / "vitest"
    if not vitest_bin.is_file() or not os.access(vitest_bin, os.X_OK):
        raise Refusal(f"local Vitest executable is unavailable: {vitest_bin}")
    results: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    output_parts: list[str] = []
    command_template = runner.get("command_template")
    if not isinstance(command_template, list):
        raise Refusal("runner.command_template is not configured")
    provider_env = {
        name: os.environ[name]
        for name in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "CI")
        if name in os.environ
    }
    provider_env.update({"TD_API_KEY": key, "NO_COLOR": os.environ.get("NO_COLOR", "1")})
    for flow in flows:
        flow_config = flow.get("config")
        if not isinstance(flow_config, str) or not flow_config:
            raise Refusal(f"real flow {flow.get('name')!r} has no Vitest config")
        try:
            command = [
                str(part).format(
                    package=package,
                    version=version,
                    flow_file=flow["file"],
                    flow_config=flow_config,
                )
                for part in command_template
            ]
        except (KeyError, ValueError) as exc:
            raise Refusal(f"cannot render runner.command_template: {exc}") from exc
        started = time.monotonic()
        result = run_capture(command, cwd=cwd, env=provider_env, timeout=1800)
        elapsed = max(time.monotonic() - started, 0.000001)
        combined = redact_output(
            "\n".join(part for part in (result.stdout, result.stderr) if part).strip(), (key,)
        )
        output_parts.append(f"[{flow['name']}]\n{combined}")
        status = "pass" if result.returncode == 0 else "fail"
        results.append(
            {
                "name": flow["name"],
                "file": flow["file"],
                "status": status,
                "duration_s": round(elapsed, 6),
            }
        )
        events.extend(detect_heal_events(combined, flow["name"]))
    return results, events, "\n".join(output_parts)


def normalize_repo_path(path: str, repo_root: Path | None = None) -> str:
    if path == "<unknown>":
        return path
    raw = path.replace("\\", "/")
    norm = os.path.normpath(raw).replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    if os.path.isabs(norm) and repo_root is not None:
        root = os.path.normpath(str(repo_root)).replace("\\", "/")
        try:
            relative = os.path.relpath(norm, root).replace("\\", "/")
        except ValueError:
            relative = norm
        if relative != ".." and not relative.startswith("../"):
            norm = relative
    return norm


def is_guard_path(path: str, prefixes: list[str], repo_root: Path | None = None) -> bool:
    norm = normalize_repo_path(path, repo_root)
    if norm == "<unknown>":
        return True
    for prefix in prefixes:
        directory_prefix = prefix.endswith(("/", "\\"))
        normalized_prefix = normalize_repo_path(prefix).rstrip("/")
        if directory_prefix:
            if norm == normalized_prefix or norm.startswith(normalized_prefix + "/"):
                return True
        elif norm == normalized_prefix:
            return True
    return False


def apply_heal_policy(events: list[dict[str, Any]], policy: dict[str, Any], repo_root: Path) -> None:
    prefixes = [str(item) for item in policy.get("guard_path_prefixes", [])]
    for event in events:
        event["path"] = normalize_repo_path(str(event.get("path", "<unknown>")), repo_root)
        event["guard_path"] = is_guard_path(event["path"], prefixes, repo_root)
        event["diff"] = "\n".join(
            [
                f"--- before/{event.get('path', '<unknown>')}",
                f"+++ after/{event.get('path', '<unknown>')}",
                f"-{event.get('before', '<unavailable>')}",
                f"+{event.get('after', '<unavailable>')}",
            ]
        )


def verdict_for(results: list[dict[str, Any]], events: list[dict[str, Any]], policy: dict[str, Any]) -> tuple[str, str]:
    if policy.get("block_on_guard_paths") and any(event.get("guard_path") for event in events):
        return "block", "guard-path-heal"
    if any(result["status"] == "timeout" for result in results):
        return "fail", "provider-timeout"
    if any(result["status"] == "fail" for result in results):
        return "fail", "flow-failed"
    return "pass", "flows-passed"


def conclusion_for(verdict: str, mode: str) -> str:
    if verdict in {"block", "refusal"}:
        return "failure"
    if verdict == "skipped":
        return "neutral"
    if verdict == "fail":
        return "neutral" if mode == "advisory" else "failure"
    if verdict == "pass":
        return "success"
    return "failure"


def archive_result(
    config: dict[str, Any], repo: str, head: str, result: dict[str, Any], output: str, cwd: Path
) -> Path:
    now = dt.datetime.now(UTC)
    target = archive_root(config) / repo / head / now.strftime("%Y%m%dT%H%M%S.%fZ")
    try:
        target.mkdir(parents=True, exist_ok=False)
        (target / "output.log").write_text(redact_output(output, (os.environ.get("TD_API_KEY", ""),)))
        (target / "heal-events.json").write_text(json.dumps(result["heal_events"], indent=2) + "\n")
        evidence_target = target / ".evidence"
        source_evidence = cwd / ".evidence"
        if source_evidence.is_dir():
            shutil.copytree(source_evidence, evidence_target)
        else:
            evidence_target.mkdir()
        result["created_at"] = now.isoformat()
        result["duration_s"] = sum(float(item.get("duration_s", 0)) for item in result["flows"])
        result["archive_dir"] = str(target)
        (target / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    except OSError as exc:
        raise Refusal(f"cannot archive evidence at {target}: {exc}") from exc
    return target


def record_heal_metrics(repo: str, head: str, events: list[dict[str, Any]]) -> None:
    raw = os.environ.get("GATE_METRICS_CMD")
    if not raw or not events:
        return
    command = shlex.split(raw) + [
        "heal",
        "--repo",
        repo,
        "--head",
        head,
        "--events-json",
        json.dumps(
            [
                {
                    "flow": event.get("flow"),
                    "path": event.get("path"),
                    "guard_path": bool(event.get("guard_path")),
                }
                for event in events
            ],
            separators=(",", ":"),
        ),
    ]
    result = run_capture(command)
    if result.returncode:
        raise Refusal(f"GATE_METRICS_CMD exited {result.returncode}: {result.stderr.strip()}")


def gh_json(arguments: list[str], *, input_data: str | None = None) -> dict[str, Any]:
    command = ["gh", "api", *arguments]
    result = run_capture(command, input=input_data)
    if result.returncode:
        raise Refusal(f"gh api failed ({result.returncode}): {result.stderr.strip()}")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise Refusal(f"gh api returned invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise Refusal("gh api returned a non-object response")
    return value


def gh_list(arguments: list[str]) -> list[dict[str, Any]]:
    command = ["gh", "api", *arguments]
    result = run_capture(command)
    if result.returncode:
        raise Refusal(f"gh api failed ({result.returncode}): {result.stderr.strip()}")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise Refusal(f"gh api returned invalid JSON: {exc}") from exc
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise Refusal("gh api returned a non-object-list response")
    return value


def post_check(repo: str, head: str, result: dict[str, Any]) -> None:
    conclusion = conclusion_for(result["verdict"], result["mode"])
    summary = (
        f"verdict={result['verdict']} heal_events={len(result['heal_events'])} "
        f"archive={result['archive_dir']}"
    )
    verdict = result["verdict"]
    if verdict == "skipped":
        title = f"testdriver (skipped: {result['reason']})"
    elif verdict == "refusal":
        title = f"testdriver (refused: {result['reason']})"
    elif result["provider"] == "mock":
        title = "testdriver (mock)"
    else:
        title = "testdriver"
    payload = {
        "name": "testdriver",
        "external_id": CHECK_EXTERNAL_ID,
        "head_sha": head,
        "status": "completed",
        "conclusion": conclusion,
        "output": {
            "title": title,
            "summary": summary,
        },
    }
    existing = gh_json([f"repos/{repo}/commits/{head}/check-runs"])
    existing_runs = existing.get("check_runs")
    if not isinstance(existing_runs, list) or not all(isinstance(run, dict) for run in existing_runs):
        raise Refusal("GitHub check-runs response is malformed")
    match = next(
        (
            run
            for run in existing_runs
            if run.get("name") == "testdriver" and run.get("external_id") == CHECK_EXTERNAL_ID
        ),
        None,
    )
    if match and match.get("id"):
        payload.pop("head_sha")
        gh_json(
            [f"repos/{repo}/check-runs/{match['id']}", "--method", "PATCH", "--input", "-"],
            input_data=json.dumps(payload),
        )
    else:
        gh_json(
            [f"repos/{repo}/check-runs", "--method", "POST", "--input", "-"],
            input_data=json.dumps(payload),
        )


def public_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "verdict": result.get("verdict"),
        "mode": result.get("mode"),
        "provider": result.get("provider"),
        "flow_count": len(result.get("flows", [])),
        "heal_count": len(result.get("heal_events", [])),
        "flows": [
            {"name": flow.get("name"), "status": flow.get("status")}
            for flow in result.get("flows", [])
        ],
        "heal_events": [
            {
                "flow": event.get("flow"),
                "path": event.get("path"),
                "guard_path": bool(event.get("guard_path")),
            }
            for event in result.get("heal_events", [])
        ],
        "archive_dir": result.get("archive_dir"),
        "reason": result.get("reason"),
        "fence_impl": fence_impl(),
    }


def print_result(result: dict[str, Any], *, as_json: bool, for_agent: bool, output: str = "") -> None:
    if as_json:
        print(json.dumps(public_result(result), separators=(",", ":"), sort_keys=True))
        return
    print(
        f"verdict={result['verdict']} mode={result['mode']} provider={result['provider']} "
        f"reason={result['reason']}"
    )
    if result.get("archive_dir"):
        print(f"archive_dir={result['archive_dir']}")
    if for_agent:
        diffs = "\n".join(event["diff"] for event in result.get("heal_events", []))
        agent_content = "\n".join(part for part in (output, diffs) if part)
        print(fence_vendor_output(agent_content))


def command_run(args: argparse.Namespace) -> int:
    base = {
        "verdict": "refusal",
        "mode": "required",
        "provider": args.provider or "unknown",
        "flows": [],
        "heal_events": [],
        "archive_dir": None,
        "reason": "runner-refused",
    }
    post_attempted = False
    try:
        config = load_config()
        errors = validate_config(config)
        if errors:
            raise Refusal("invalid config: " + "; ".join(errors))
        row = repo_row(config, args.repo)
        provider = args.provider or row["provider"]
        if provider not in ALLOWED_PROVIDERS:
            raise Refusal(f"unknown provider {provider!r}")
        base = {
            "verdict": "skipped",
            "mode": row["mode"],
            "provider": provider,
            "flows": [],
            "heal_events": [],
            "archive_dir": None,
            "reason": "",
        }
        if not row["enabled"]:
            base["reason"] = "repository-disabled"
            if args.post_check:
                post_attempted = True
                post_check(args.repo, args.head, base)
            print_result(base, as_json=args.json, for_agent=args.for_agent)
            return 0
        if args.event not in row["when"]:
            base["reason"] = "event-not-configured"
            if args.post_check:
                post_attempted = True
                post_check(args.repo, args.head, base)
            print_result(base, as_json=args.json, for_agent=args.for_agent)
            return 0
        flows = row["flows"]
        cwd = Path(os.environ.get("TESTDRIVER_REPO_DIR", os.getcwd())).resolve()
        seed_output: list[str] = []
        if not args.dry:
            for flow in flows:
                ok, output = seed_flow(flow, cwd)
                output = redact_output(output, (os.environ.get("TD_API_KEY", ""),))
                seed_output.append(f"[{flow['name']} seed]\n{output}")
                if not ok:
                    base.update(verdict="fail", reason="fixture-seed-failed")
                    base["flows"] = [
                        {"name": flow["name"], "file": flow["file"], "status": "fail", "duration_s": 0}
                    ]
                    archived = archive_result(config, args.repo, args.head, base, "\n".join(seed_output), cwd)
                    base["archive_dir"] = str(archived)
                    if args.post_check:
                        post_attempted = True
                        post_check(args.repo, args.head, base)
                    print_result(base, as_json=args.json, for_agent=args.for_agent, output="\n".join(seed_output))
                    return 0
        if provider == "mock":
            flow_results, events, vendor_output = execute_mock(flows)
        else:
            flow_results, events, vendor_output = execute_real(flows, config["runner"], cwd)
        policy = config["heal_policy"]
        apply_heal_policy(events, policy, cwd)
        verdict, reason = verdict_for(flow_results, events, policy)
        base.update(verdict=verdict, flows=flow_results, heal_events=events, reason=reason)
        full_output = "\n".join(seed_output + [vendor_output]).strip()
        archived = archive_result(config, args.repo, args.head, base, full_output, cwd)
        base["archive_dir"] = str(archived)
        record_heal_metrics(args.repo, args.head, events)
        if args.post_check:
            post_attempted = True
            post_check(args.repo, args.head, base)
        print_result(base, as_json=args.json, for_agent=args.for_agent, output=vendor_output)
        return 0
    except Refusal as exc:
        base.update(verdict="refusal", reason=str(exc))
        if args.post_check and not post_attempted:
            try:
                post_check(args.repo, args.head, base)
            except Refusal as post_exc:
                print(f"REFUSED: {exc}; additionally could not post check: {post_exc}", file=sys.stderr)
                return 2
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2


def load_gh_fixture(path: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise Refusal(f"cannot load GitHub fixture {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise Refusal("GitHub fixture must be an object")
    return value


def command_qa_gate(args: argparse.Namespace) -> int:
    try:
        config = load_config()
        errors = validate_config(config)
        if errors:
            raise Refusal("invalid config: " + "; ".join(errors))
        configured = config.get("repos", {}).get(args.repo)
        if not isinstance(configured, dict):
            value = {
                "status": "not-configured",
                "qa_ready": True,
                "verdict": "not-configured",
                "heal_count": 0,
            }
            print(json.dumps(value) if args.json else "verdict=not-configured heal_count=0")
            return 0
        row = repo_row(config, args.repo)
        if not row["enabled"]:
            value = {
                "status": "not-configured",
                "qa_ready": True,
                "verdict": "not-configured",
                "heal_count": 0,
            }
            print(json.dumps(value) if args.json else "verdict=not-configured heal_count=0")
            return 0
        if not row.get("pre_manual_qa"):
            value = {
                "status": "not-required",
                "qa_ready": True,
                "verdict": "not-required",
                "heal_count": 0,
            }
            print(json.dumps(value) if args.json else "verdict=not-required heal_count=0")
            return 0
        fixture = os.environ.get("TESTDRIVER_GH_FIXTURE")
        if fixture:
            data = load_gh_fixture(fixture)
            pull_request = data.get("pull_request", {})
            if not isinstance(pull_request, dict):
                raise Refusal("GitHub fixture pull_request must be an object")
            pr_head = pull_request.get("head", {})
            if not isinstance(pr_head, dict):
                raise Refusal("GitHub fixture pull_request.head must be an object")
            head = data.get("head_sha") or pr_head.get("sha")
            check_data = data
        else:
            pr_data = gh_json([f"repos/{args.repo}/pulls/{args.pr}"])
            head = pr_data.get("head", {}).get("sha")
            if not head:
                raise Refusal("GitHub PR response has no head SHA")
            check_data = gh_json([f"repos/{args.repo}/commits/{head}/check-runs"])
        if not head:
            raise Refusal("GitHub fixture has no PR head SHA")
        raw_checks = check_data.get("check_runs")
        if not isinstance(raw_checks, list) or not all(isinstance(item, dict) for item in raw_checks):
            raise Refusal("GitHub check-runs response is malformed")
        named_checks = [
            item
            for item in raw_checks
            if item.get("name") == "testdriver"
            and item.get("external_id") in {None, CHECK_EXTERNAL_ID}
        ]
        checks = [
            item
            for item in named_checks
            if item.get("head_sha", head) == head
        ]
        concluded = next(
            (
                item
                for item in checks
                if item.get("status") == "completed" and item.get("conclusion") is not None
            ),
            None,
        )
        if concluded is None:
            if checks:
                status = "pending"
            elif named_checks:
                status = "stale-head"
            else:
                status = "absent"
            value = {
                "status": "waiting-on-testdriver",
                "check_status": status,
                "qa_ready": False,
                "verdict": "pending",
                "heal_count": 0,
                "head": head,
            }
            print(
                json.dumps(value)
                if args.json
                else f"status=waiting-on-testdriver check_status={status} "
                "verdict=pending heal_count=0"
            )
            return 3
        check_output = concluded.get("output", {})
        if not isinstance(check_output, dict):
            raise Refusal("concluded TestDriver check summary is malformed")
        raw_summary = check_output.get("summary")
        if raw_summary is not None and not isinstance(raw_summary, str):
            raise Refusal("concluded TestDriver check summary is malformed")
        summary = raw_summary or ""
        verdict_match = re.search(r"verdict=([a-z-]+)", summary)
        heal_match = re.search(r"heal_events=(\d+)|heal_count=(\d+)", summary)
        if verdict_match is None or heal_match is None:
            if fixture:
                annotations = concluded.get("annotations", [])
                if not isinstance(annotations, list) or not all(
                    isinstance(item, dict) for item in annotations
                ):
                    raise Refusal("fixture TestDriver annotations are malformed")
            else:
                check_id = concluded.get("id")
                if not check_id:
                    raise Refusal("concluded TestDriver check has no id for annotation lookup")
                annotations = gh_list(
                    [f"repos/{args.repo}/check-runs/{check_id}/annotations?per_page=100"]
                )
            annotation_text = "\n".join(str(item.get("message", "")) for item in annotations)
            verdict_match = verdict_match or re.search(r"verdict=([a-z-]+)", annotation_text)
            heal_match = heal_match or re.search(
                r"heal_events=(\d+)|heal_count=(\d+)", annotation_text
            )
        if verdict_match is None or heal_match is None:
            raise Refusal("concluded TestDriver check has no verdict/heal-count witness")
        verdict = verdict_match.group(1)
        heal_count = int(next(group for group in heal_match.groups() if group))
        value = {
            "status": "concluded",
            "qa_ready": True,
            "verdict": verdict,
            "heal_count": heal_count,
            "head": head,
        }
        print(json.dumps(value) if args.json else f"verdict={verdict} heal_count={heal_count}")
        return 0
    except Refusal as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2


def infer_repo(repo_dir: Path) -> str:
    explicit = os.environ.get("TESTDRIVER_REPO")
    if explicit:
        return explicit
    result = run_capture(["git", "-C", str(repo_dir), "config", "--get", "remote.origin.url"])
    if result.returncode:
        raise Refusal("cannot infer target repository; set TESTDRIVER_REPO=owner/name")
    remote = result.stdout.strip().removesuffix(".git")
    match = re.search(r"(?:github\.com[:/])([^/]+/[^/]+)$", remote)
    if not match:
        raise Refusal(f"cannot infer owner/name from remote {remote!r}; set TESTDRIVER_REPO")
    return match.group(1)


def write_if_changed(path: Path, content: bytes, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_bytes() != content:
        path.write_bytes(content)
    if mode is not None:
        path.chmod(mode)


def command_install(args: argparse.Namespace) -> int:
    try:
        config = load_config()
        source_errors = validate_config(config)
        if source_errors:
            raise Refusal("invalid source config: " + "; ".join(source_errors))
        target = Path(args.repo_dir).expanduser().resolve()
        if not target.is_dir():
            raise Refusal(f"repo directory does not exist: {target}")
        repo = args.repo or infer_repo(target)
        row = repo_row(config, repo)
        template = ROOT / "templates/testdriver-phase.yml"
        if not template.is_file():
            raise Refusal(f"workflow template is missing: {template}")
        source_row = config.get("repos", {}).get(repo)
        if not isinstance(source_row, dict):
            raise Refusal(f"repository {repo!r} is absent from source config")
        rendered_from = stable_sha256(source_row)
        expected_rendered_from = EXPECTED_RENDERED_FROM_SHA256.get(repo)
        source_pin_errors = validate_rendered_config(
            {"rendered_from_sha256": rendered_from}, expected_rendered_from
        )
        if source_pin_errors:
            raise Refusal("source row is not pinned: " + "; ".join(source_pin_errors))
        rendered = {key: value for key, value in config.items() if key != "repos"}
        rendered["rendered_from_sha256"] = rendered_from
        rendered["repos"] = {repo: row}
        rendered_errors = validate_config(rendered) + validate_rendered_config(
            rendered, expected_rendered_from
        )
        if rendered_errors:
            raise Refusal("invalid rendered config: " + "; ".join(rendered_errors))
        runner_bytes = SCRIPT.read_bytes()
        template_bytes = template.read_bytes()
        rendered["runner_sha256"] = hashlib.sha256(runner_bytes).hexdigest()
        rendered["template_sha256"] = hashlib.sha256(template_bytes).hexdigest()
        rendered_errors = validate_config(rendered) + validate_rendered_config(
            rendered, expected_rendered_from
        )
        if rendered_errors:
            raise Refusal("invalid rendered config: " + "; ".join(rendered_errors))
        destinations = [
            (target / ".github/workflows/testdriver-phase.yml", template_bytes, None),
            (target / "scripts/testdriver-phase.py", runner_bytes, 0o755),
            (
                target / "configs/testdriver.json",
                (json.dumps(rendered, indent=2, sort_keys=True) + "\n").encode(),
                None,
            ),
        ]
        for path, content, mode in destinations:
            write_if_changed(path, content, mode)
            print(path)
        return 0
    except (OSError, Refusal) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2


def parse_since(value: str) -> dt.timedelta:
    match = re.fullmatch(r"(\d+)([dh])", value)
    if not match:
        raise Refusal("--since must look like 14d or 24h")
    amount = int(match.group(1))
    return dt.timedelta(days=amount) if match.group(2) == "d" else dt.timedelta(hours=amount)


def command_metrics(args: argparse.Namespace) -> int:
    try:
        config = load_config()
        errors = validate_config(config)
        if errors:
            raise Refusal("invalid config: " + "; ".join(errors))
        cutoff = dt.datetime.now(UTC) - parse_since(args.since)
        base = archive_root(config) / args.repo
        records: list[dict[str, Any]] = []
        if not base.is_dir():
            raise Refusal(f"archive directory is absent: {base}")
        for path in base.rglob("result.json"):
            try:
                record = json.loads(path.read_text())
                created = dt.datetime.fromisoformat(str(record["created_at"]).replace("Z", "+00:00"))
            except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
                raise Refusal(f"invalid metrics record {path}: {exc}") from exc
            if created >= cutoff:
                records.append(record)
        if not records:
            raise Refusal(f"no metrics records for {args.repo} since {args.since}")
        fp_count = sum(bool(record.get("false_positive")) for record in records)
        fp_rate = fp_count / len(records)
        healed_regressions = sum(
            bool(event.get("healed_regression"))
            for record in records
            for event in record.get("heal_events", [])
        )
        latencies = sorted(float(record.get("duration_s", 0)) for record in records)
        if not latencies:
            raise Refusal("cannot compute p95 without run durations")
        p95 = latencies[max(0, math.ceil(0.95 * len(latencies)) - 1)]
        thresholds = config["rip_out"]
        rip_out = (
            fp_rate > float(thresholds["fp_rate_max"])
            or healed_regressions > int(thresholds["healed_regression_max"])
            or p95 > float(thresholds["p95_latency_s"])
        )
        print(
            f"repo={args.repo} runs={len(records)} fp_rate={fp_rate:.3f} "
            f"healed_regressions={healed_regressions} p95_latency_s={p95:.1f}"
        )
        print(f"RIP-OUT: {'yes' if rip_out else 'no'}")
        return 0
    except (KeyError, TypeError, ValueError, Refusal) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    config = commands.add_parser("config")
    config.add_argument("--validate", action="store_true", required=True)
    config.add_argument("--check-rendered", action="store_true")
    config.add_argument("--path")
    config.set_defaults(func=command_config)

    run = commands.add_parser("run")
    run.add_argument("--repo", required=True)
    run.add_argument("--event", required=True, choices=sorted(ALLOWED_WHEN))
    run.add_argument("--head", required=True)
    run.add_argument("--provider", choices=sorted(ALLOWED_PROVIDERS))
    run.add_argument("--dry", action="store_true")
    output = run.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true")
    output.add_argument("--for-agent", action="store_true")
    run.add_argument("--post-check", action="store_true")
    run.set_defaults(func=command_run)

    qa = commands.add_parser("qa-gate")
    qa.add_argument("--repo", required=True)
    qa.add_argument("--pr", required=True)
    qa.add_argument("--json", action="store_true")
    qa.set_defaults(func=command_qa_gate)

    install = commands.add_parser("install")
    install.add_argument("--repo-dir", required=True)
    install.add_argument("--repo")
    install.set_defaults(func=command_install)

    metrics = commands.add_parser("metrics")
    metrics.add_argument("--since", default="14d")
    metrics.add_argument("--repo", required=True)
    metrics.set_defaults(func=command_metrics)
    return root


if __name__ == "__main__":
    arguments = parser().parse_args()
    raise SystemExit(arguments.func(arguments))
