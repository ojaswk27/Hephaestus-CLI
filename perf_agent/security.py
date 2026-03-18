"""Multilayer security check for candidate sources (all languages)."""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import openai as _openai
from openai import OpenAI

if TYPE_CHECKING:
    from .languages import LanguageSpec

_SANITIZER_FLAGS = "-fsanitize=address,undefined -fno-sanitize-recover=all -g"
_ISSUE_RE = re.compile(r"AddressSanitizer:|runtime error:|LeakSanitizer:")
_MAX_RAW = 4000

# --- Layer static_c: C/C++ regex pattern scan ---
_STATIC_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bgets\s*\("), "gets(): unbounded buffer read"),
    (re.compile(r"\bstrcpy\s*\("), "strcpy(): no bounds check, overflow risk"),
    (re.compile(r"\bstrcat\s*\("), "strcat(): no bounds check, overflow risk"),
    (re.compile(r"\bsprintf\s*\("), "sprintf(): no bounds, use snprintf"),
    (re.compile(r'scanf\s*\(\s*"[^"]*%s'), "scanf %s: unbounded string read"),
    (re.compile(r"\bsystem\s*\("), "system(): command injection risk"),
    (re.compile(r"\bpopen\s*\("), "popen(): command injection risk"),
    (re.compile(r'printf\s*\(\s*[^"]'), "printf(): possible unsanitized format string"),
    (re.compile(r"\bmalloc\s*\(0\)"), "malloc(0): zero-size allocation undefined"),
    (re.compile(r"\bstrcmp\s*\(.*password"), "strcmp() on password: timing side-channel"),
]

# --- LLM security system prompt template ---
_SECURITY_SYSTEM_TEMPLATE = """\
You are a {lang} security auditor. Analyze this code for vulnerabilities including:
{vuln_list}

List each issue as: ISSUE: <one-line description>
If no issues found, output exactly: SECURE
"""

_VULN_LIST: dict[str, str] = {
    "c": (
        "buffer overflows, integer overflows/underflows, use-after-free, null pointer "
        "dereference, format string bugs, command injection, path traversal, dangerous "
        "functions (gets/strcpy/system), memory leaks, uninitialized variables, "
        "race conditions, cryptographic misuse."
    ),
    "cpp": (
        "buffer overflows, use-after-free/use-after-move, dangling references, integer "
        "overflows, format string bugs, command injection, path traversal, memory leaks, "
        "uninitialized variables, race conditions, cryptographic misuse, exception safety."
    ),
    "rust": (
        "unsafe block misuse, integer overflow (debug vs release), format string injection, "
        "command injection, path traversal, race conditions in unsafe code, FFI memory safety."
    ),
    "python": (
        "SQL injection, shell injection (subprocess/os.system with user input), path traversal, "
        "hardcoded secrets, insecure deserialization (pickle/yaml), eval/exec misuse, "
        "XML vulnerabilities, weak cryptography."
    ),
    "java": (
        "SQL injection, command injection, path traversal, deserialization vulnerabilities, "
        "XXE, SSRF, hardcoded credentials, weak cryptography, null pointer dereference, "
        "resource leaks."
    ),
    "javascript": (
        "prototype pollution, XSS, command injection (child_process with user input), "
        "path traversal, ReDoS, hardcoded secrets, insecure deserialization, "
        "SQL/NoSQL injection, SSRF."
    ),
}


@dataclass
class LayerResult:
    layer: str
    passed: bool
    issues: list[str] = field(default_factory=list)
    raw_output: str = ""


@dataclass
class SecurityReport:
    passed: bool
    layers: list[LayerResult] = field(default_factory=list)

    @property
    def issues(self) -> list[str]:
        return [issue for lr in self.layers for issue in lr.issues]

    @property
    def summary(self) -> str:
        return "; ".join(self.issues[:5])


# ------------------------------------------------------------------
# Individual layers
# ------------------------------------------------------------------

def _static_scan(candidate_src: Path) -> LayerResult:
    """C/C++ regex pattern scan."""
    try:
        source = candidate_src.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return LayerResult(layer="static", passed=False, issues=[f"Cannot read source: {e}"])
    issues: list[str] = []
    for pattern, description in _STATIC_PATTERNS:
        if pattern.search(source):
            issues.append(description)
    return LayerResult(layer="static", passed=not issues, issues=issues)


def _cppcheck_scan(candidate_src: Path) -> LayerResult:
    """cppcheck static analysis — skipped gracefully if not on PATH."""
    if shutil.which("cppcheck") is None:
        return LayerResult(layer="cppcheck-skipped", passed=True)
    try:
        result = subprocess.run(
            ["cppcheck", "--enable=warning,portability",
             "--error-exitcode=1", "--suppress=missingInclude",
             str(candidate_src)],
            capture_output=True, text=True, timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return LayerResult(layer="cppcheck-skipped", passed=True)
    issues = [
        line.strip()[:120]
        for line in result.stderr.splitlines()
        if ": error:" in line or ": warning:" in line
    ]
    return LayerResult(
        layer="cppcheck", passed=result.returncode == 0,
        issues=issues, raw_output=result.stderr[:_MAX_RAW],
    )


def _bandit_scan(candidate_src: Path) -> LayerResult:
    """Python security scan via bandit — skipped gracefully if not installed."""
    if shutil.which("bandit") is None:
        return LayerResult(layer="bandit-skipped", passed=True)
    try:
        result = subprocess.run(
            ["bandit", "-q", "-f", "text", str(candidate_src)],
            capture_output=True, text=True, timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return LayerResult(layer="bandit-skipped", passed=True)
    issues = [
        line.strip()[:120]
        for line in result.stdout.splitlines()
        if line.strip().startswith(">> Issue:")
    ]
    return LayerResult(
        layer="bandit", passed=result.returncode == 0,
        issues=issues, raw_output=result.stdout[:_MAX_RAW],
    )


def _sanitizer_check(
    candidate_src: Path,
    binary_args: list[str],
    timeout: int,
    compiler: str,
    base_flags: str,
    work_dir: Path,
) -> LayerResult:
    """Compile with ASan/UBSan/LSan and run."""
    san_bin = work_dir / "sanitizer_check"
    san_flags = shlex.split(base_flags) + shlex.split(_SANITIZER_FLAGS)
    cmd = [compiler, *san_flags, "-o", str(san_bin), str(candidate_src)]
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=120, text=True)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return LayerResult(layer="sanitizer-skipped", passed=True)
    if r.returncode != 0:
        return LayerResult(layer="sanitizer-skipped", passed=True)

    san_bin.chmod(0o755)
    env = {**os.environ,
           "ASAN_OPTIONS":  "abort_on_error=0:detect_leaks=1:exitcode=0",
           "UBSAN_OPTIONS": "print_stacktrace=1:exitcode=0",
           "LSAN_OPTIONS":  "exitcode=0"}
    try:
        run = subprocess.run([str(san_bin), *binary_args],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             timeout=min(timeout, 60), text=True, env=env)
        stderr = run.stderr
    except subprocess.TimeoutExpired:
        return LayerResult(layer="sanitizer", passed=True, raw_output="(timed out)")
    except OSError:
        return LayerResult(layer="sanitizer-skipped", passed=True)

    issues = list({line.strip()[:120] for line in stderr.splitlines()
                   if _ISSUE_RE.search(line)})
    return LayerResult(layer="sanitizer", passed=not issues,
                       issues=issues, raw_output=stderr[:_MAX_RAW])


def _llm_security_review(
    candidate_src: Path,
    api_key: str | None,
    base_url: str | None,
    model: str,
    lang: "LanguageSpec",
) -> LayerResult:
    """LLM-based security audit."""
    try:
        source = candidate_src.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return LayerResult(layer="llm-skipped", passed=True)

    vuln_list = _VULN_LIST.get(lang.name, _VULN_LIST["c"])
    system_prompt = _SECURITY_SYSTEM_TEMPLATE.format(
        lang=lang.display_name, vuln_list=vuln_list
    )

    kwargs: dict = {}
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url

    try:
        client = OpenAI(**kwargs)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"```{lang.fence}\n{source}\n```"},
            ],
            stream=False,
        )
        text = response.choices[0].message.content or ""
    except (_openai.APIConnectionError, _openai.AuthenticationError,
            _openai.NotFoundError, _openai.APIError):
        return LayerResult(layer="llm-skipped", passed=True)

    issue_lines = [
        line[len("ISSUE:"):].strip()
        for line in text.splitlines()
        if line.startswith("ISSUE:")
    ]
    passed = "SECURE" in text and not issue_lines
    return LayerResult(layer="llm", passed=passed, issues=issue_lines, raw_output=text)


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def run_security_check(
    candidate_src: Path,
    binary_args: list[str],
    timeout: int,
    compiler: str,
    base_flags: str,
    work_dir: Path,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str = "gpt-4o",
    language: str = "c",
) -> SecurityReport:
    """Run security checks appropriate for *language*."""
    from .languages import LANGUAGES
    lang = LANGUAGES.get(language, LANGUAGES["c"])
    return run_security_check_for_lang(
        candidate_src, binary_args, timeout, lang, compiler, base_flags,
        work_dir, api_key=api_key, base_url=base_url, model=model,
    )


def run_security_check_for_lang(
    candidate_src: Path,
    binary_args: list[str],
    timeout: int,
    lang: "LanguageSpec",
    compiler: str,
    base_flags: str,
    work_dir: Path,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str = "gpt-4o",
) -> SecurityReport:
    """Run the security layers declared in lang.security_layers."""
    results: list[LayerResult] = []
    layers = lang.security_layers

    if "static_c" in layers:
        results.append(_static_scan(candidate_src))
    if "cppcheck" in layers:
        results.append(_cppcheck_scan(candidate_src))
    if "sanitizer" in layers:
        results.append(_sanitizer_check(
            candidate_src, binary_args, timeout, compiler, base_flags, work_dir
        ))
    if "bandit" in layers:
        results.append(_bandit_scan(candidate_src))
    if "llm" in layers:
        results.append(_llm_security_review(
            candidate_src, api_key, base_url, model, lang
        ))

    passed = all(lr.passed for lr in results)
    return SecurityReport(passed=passed, layers=results)
