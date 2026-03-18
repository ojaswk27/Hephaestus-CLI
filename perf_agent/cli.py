"""argparse + top-level orchestration for perf-agent."""

import argparse
import difflib
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import __version__
from . import compiler as _compiler
from . import display, llm, optimizer, parser, runner
from . import security as _security
from .compiler import CompileResult
from .errors import (
    BinaryNotELFError,
    BinaryNotFoundError,
    DockerContainerError,
    DockerImageBuildError,
    DockerNotFoundError,
    LLMConnectionError,
    LLMModelNotFoundError,
    PerfNotFoundError,
    PerfPermissionError,
    PerfTimeoutError,
)
from .languages import LANGUAGES, LanguageSpec, detect_language, get_language

_FORBIDDEN_FLAGS = {"-o", "--output", "-MF", "-MT", "-MQ", "-save-temps"}


def _validate_compile_flags(flags: str) -> None:
    try:
        tokens = shlex.split(flags)
    except ValueError as e:
        raise SystemExit(f"Invalid --compile-flags: {e}") from e
    for tok in tokens:
        if tok in _FORBIDDEN_FLAGS:
            raise SystemExit(
                f"--compile-flags contains disallowed flag {tok!r} "
                "(output path is controlled by perf-agent)"
            )


def _redact(text: str, api_key: str | None) -> str:
    if api_key:
        return text.replace(api_key, "sk-***REDACTED***")
    return text


class _ListTargetsAction(argparse.Action):
    def __call__(self, parser_obj, namespace, values, option_string=None):
        from rich.table import Table
        from .targets import CATALOG

        table = Table(title="Available Targets", show_lines=True)
        table.add_column("Name", style="cyan", no_wrap=True)
        table.add_column("Platform", style="dim")
        table.add_column("Compiler")
        table.add_column("Flags")
        table.add_column("Description")

        for name, spec in sorted(CATALOG.items()):
            table.add_row(
                name,
                spec.platform,
                spec.compiler,
                spec.compile_flags,
                spec.description,
            )

        display.CONSOLE.print(table)
        parser_obj.exit(0)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="perf-agent",
        description="AI-powered Linux perf profiler — profile any program and get LLM analysis.",
    )
    p.add_argument(
        "binary",
        nargs="?",
        default=None,
        help="Path to the ELF binary or interpreted script to profile",
    )
    p.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        help="Arguments to pass to the binary/script",
    )
    p.add_argument("--model", default="gpt-4o",
                   help="LLM model name (default: gpt-4o). "
                        "Use claude-* names for Anthropic (e.g. claude-sonnet-4-6). "
                        "Provider is auto-detected from the model name.")
    p.add_argument("--base-url", default=None,
                   help="OpenAI-compatible API base URL (e.g. http://localhost:11434/v1 for Ollama). "
                        "Not used for Anthropic models.")
    p.add_argument("--api-key", default=None,
                   help="API key override (default: OPENAI_API_KEY or ANTHROPIC_API_KEY env var)")
    p.add_argument("--timeout", type=int, default=120,
                   help="perf execution timeout in seconds (default: 120)")
    p.add_argument("--version", action="version",
                   version=f"perf-agent {__version__}")

    opt_group = p.add_argument_group("self-optimization")
    opt_group.add_argument("--loops", type=int, default=0, metavar="N",
                           help="Optimization iterations (0 = analysis only)")
    opt_group.add_argument("--source", type=Path, default=None, metavar="PATH",
                           help="Source file to optimize (required for --loops > 0 or --target)")
    opt_group.add_argument("--lang", default=None,
                           choices=list(LANGUAGES.keys()),
                           metavar="LANG",
                           help=f"Source language — auto-detected from extension if omitted. "
                                f"Choices: {', '.join(LANGUAGES)}")
    opt_group.add_argument("--compiler", default=None, metavar="CMD",
                           help="Compiler command override (default: language default)")
    opt_group.add_argument("--compile-flags", default=None, metavar="FLAGS",
                           help="Compile flags override (default: language default)")
    opt_group.add_argument("--output-dir", type=Path, default=None, metavar="DIR",
                           help="Directory to write optimized source "
                                "(default: optimized/ next to source)")

    docker_group = p.add_argument_group("docker targets")
    docker_group.add_argument("--target", default=None, metavar="NAME",
                               help="Build and profile inside a Docker container")
    docker_group.add_argument("--list-targets", nargs=0, action=_ListTargetsAction,
                               help="List available Docker targets and exit")
    docker_group.add_argument("--no-build", action="store_true", default=False,
                               help="Skip docker build — fail if image not present")

    p.add_argument("--no-think", action="store_true", default=False,
                   help="Disable LLM chain-of-thought (faster, less thorough)")
    p.add_argument("--no-security", action="store_true", default=False,
                   help="Skip the multilayer security gate on each candidate")
    p.add_argument("--no-remediate", action="store_true", default=False,
                   help="Skip the LLM security remediation pre-pass")
    p.add_argument("--user-approved", action="store_true", default=False,
                   help="Pause after each LLM proposal and ask for approval")
    p.add_argument("--check-cmd", default=None, metavar="CMD",
                   help="Shell command to verify correctness after each optimization "
                        "(exit 0 = pass). The candidate binary path is in $PERF_AGENT_BINARY. "
                        "If omitted, auto-detects pytest/go-test or falls back to stdout comparison.")

    return p


def _detect_lang(ns: argparse.Namespace) -> LanguageSpec:
    """Determine LanguageSpec from --lang flag or source/binary extension."""
    if ns.lang:
        return get_language(ns.lang)
    # Try source first, then binary
    for path_attr in ("source", "binary"):
        val = getattr(ns, path_attr, None)
        if val is not None:
            spec = detect_language(Path(val))
            if spec is not None:
                return spec
    return LANGUAGES["c"]   # default


def _make_security_fn(lang, _compiler, _compile_flags, _timeout, _api_key, _base_url, _model):
    def _check(src: Path, args: list[str]):
        import tempfile as _tf
        with _tf.TemporaryDirectory(prefix="perf_sec_") as td:
            return _security.run_security_check_for_lang(
                src, args, _timeout, lang, _compiler, _compile_flags, Path(td),
                api_key=_api_key, base_url=_base_url, model=_model,
            )
    return _check


def _build_check_fn(
    lang_spec: "LanguageSpec",
    source_file: Path,
    initial_run_argv: list[str],
    check_cmd: str | None,
    timeout: int,
):
    """Return a ``check_fn(candidate_run_argv) -> (passed, reason)`` closure, or None.

    Priority:
    1. ``--check-cmd`` (explicit shell command)
    2. Auto-detect: pytest for Python, ``go test`` for Go
    3. Stdout comparison against the original program's output
    """
    import os as _os

    # 1. Explicit check command
    if check_cmd is not None:
        _tokens = shlex.split(check_cmd)

        def _custom(candidate_run_argv: list[str]) -> tuple[bool, str]:
            env = _os.environ.copy()
            if candidate_run_argv:
                env["PERF_AGENT_BINARY"] = candidate_run_argv[0]
            try:
                r = subprocess.run(
                    _tokens, capture_output=True, text=True,
                    timeout=timeout, env=env,
                )
            except FileNotFoundError as e:
                return False, str(e)
            except subprocess.TimeoutExpired:
                return False, f"check-cmd timed out after {timeout}s"
            if r.returncode != 0:
                return False, (r.stdout + r.stderr)[:300]
            return True, ""

        return _custom

    # 2a. Auto-detect: pytest
    if lang_spec.name == "python" and shutil.which("pytest"):
        test_files = (
            list(source_file.parent.glob("test_*.py"))
            + list(source_file.parent.glob("*_test.py"))
        )
        tests_dir = source_file.parent / "tests"
        if tests_dir.is_dir():
            test_files += list(tests_dir.glob("*.py"))
        if test_files:
            _pytest = shutil.which("pytest")
            _cwd = str(source_file.parent)

            def _run_pytest(candidate_run_argv: list[str]) -> tuple[bool, str]:
                r = subprocess.run(
                    [_pytest, "-q", "--tb=short"], capture_output=True,
                    text=True, timeout=timeout, cwd=_cwd,
                )
                if r.returncode != 0:
                    return False, (r.stdout + r.stderr)[:300]
                return True, ""

            return _run_pytest

    # 2b. Auto-detect: go test
    if lang_spec.name == "go" and shutil.which("go"):
        test_files = list(source_file.parent.glob("*_test.go"))
        if test_files:
            _cwd = str(source_file.parent)

            def _run_go_test(candidate_run_argv: list[str]) -> tuple[bool, str]:
                r = subprocess.run(
                    ["go", "test", "./..."], capture_output=True,
                    text=True, timeout=timeout, cwd=_cwd,
                )
                if r.returncode != 0:
                    return False, (r.stdout + r.stderr)[:300]
                return True, ""

            return _run_go_test

    # 3. Stdout comparison — capture baseline once
    ok, baseline_stdout, baseline_stderr = runner.run_for_output(initial_run_argv, timeout)
    if not ok:
        # Can't establish a baseline (program already fails) — skip correctness checks
        return None

    def _stdout_check(candidate_run_argv: list[str]) -> tuple[bool, str]:
        run_ok, stdout, stderr = runner.run_for_output(candidate_run_argv, timeout)
        if not run_ok:
            return False, f"Program exited with error: {stderr[:150]}"
        if stdout == baseline_stdout:
            return True, ""
        diff_lines = list(difflib.unified_diff(
            baseline_stdout.splitlines(keepends=True),
            stdout.splitlines(keepends=True),
            fromfile="baseline", tofile="candidate", n=2,
        ))
        return False, "Output mismatch:\n" + "".join(diff_lines[:25])

    return _stdout_check


def main() -> None:
    import os
    from dotenv import load_dotenv
    load_dotenv()

    p = _build_parser()
    ns = p.parse_args()

    # If no explicit API key, fall back to provider-appropriate env var
    if ns.api_key is None:
        from .llm import detect_provider
        if detect_provider(ns.model) == "anthropic":
            ns.api_key = os.environ.get("ANTHROPIC_API_KEY")
        else:
            ns.api_key = os.environ.get("OPENAI_API_KEY")

    binary_args: list[str] = [a for a in (ns.args or []) if a != "--"]

    try:
        if ns.target is not None:
            _run_docker_path(p, ns, binary_args)
        else:
            _run_local_path(p, ns, binary_args)

    except PerfPermissionError as e:
        display.show_error(str(e))
        sys.exit(2)
    except PerfNotFoundError as e:
        display.show_error(str(e))
        sys.exit(3)
    except PerfTimeoutError as e:
        display.show_error(str(e))
        sys.exit(4)
    except LLMConnectionError as e:
        display.show_error(_redact(str(e), ns.api_key if hasattr(ns, "api_key") else None))
        sys.exit(5)
    except LLMModelNotFoundError as e:
        display.show_error(_redact(str(e), ns.api_key if hasattr(ns, "api_key") else None))
        sys.exit(6)
    except DockerNotFoundError as e:
        display.show_error(str(e))
        sys.exit(7)
    except DockerImageBuildError as e:
        msg = str(e)
        if e.build_log:
            msg += f"\n\nBuild log:\n{e.build_log[-2000:]}"
        display.show_error(msg)
        sys.exit(8)
    except DockerContainerError as e:
        display.show_error(str(e))
        sys.exit(9)
    except KeyboardInterrupt:
        display.CONSOLE.print("\n[yellow]Interrupted.[/]")
        sys.exit(130)


def _run_local_path(p: argparse.ArgumentParser, ns: argparse.Namespace, binary_args: list[str]) -> None:
    """Local (non-Docker) profiling and optimization path."""
    lang_spec = _detect_lang(ns)

    # Resolve what we're going to profile
    if lang_spec.compiled:
        # Compiled language — need a binary
        if ns.binary is None:
            if ns.source is not None:
                # Build from source right now so we have something to profile
                pass  # handled below in the "have source" branch
            else:
                p.error("binary argument is required for compiled languages")

        if ns.source is not None:
            # Build from source
            if not ns.source.exists():
                display.show_error(f"Source file not found: {ns.source}")
                sys.exit(1)
            compile_flags = ns.compile_flags or lang_spec.default_flags
            if ns.compile_flags:
                _validate_compile_flags(ns.compile_flags)
            cc = ns.compiler or lang_spec.default_compiler
            with display.spinner(f"Compiling {ns.source.name} with {cc}..."):
                work_tmpdir = Path(tempfile.mkdtemp(prefix="perf_local_"))
                out_bin = work_tmpdir / ns.source.stem
                compile_result = _compiler.build_source(
                    ns.source, out_bin, lang_spec, compiler=cc, flags=compile_flags
                )
            display.show_compile_result(compile_result)
            if not compile_result.success:
                display.show_error("Compilation failed — cannot continue.")
                sys.exit(1)
            binary = out_bin
            run_argv = compile_result.run_argv + binary_args
        else:
            binary = Path(ns.binary)
            try:
                runner.check_elf(binary)
            except BinaryNotFoundError as e:
                display.show_error(str(e))
                sys.exit(1)
            except BinaryNotELFError as e:
                display.show_error(str(e))
                sys.exit(1)
            run_argv = [str(binary)] + binary_args
            work_tmpdir = None
    else:
        # Interpreted language — binary IS the script, or use --source
        script = Path(ns.binary) if ns.binary else ns.source
        if script is None:
            p.error("binary or --source argument is required")
        if not script.exists():
            display.show_error(f"Script not found: {script}")
            sys.exit(1)
        runtime = shutil.which(lang_spec.runtime) or lang_spec.runtime
        run_argv = [runtime, str(script)] + binary_args
        binary = script
        work_tmpdir = None

    if shutil.which("perf") is None:
        display.show_error(
            "perf not found on PATH.\n"
            "  Arch:   sudo pacman -S perf\n"
            "  Debian: sudo apt install linux-perf"
        )
        sys.exit(1)

    display.show_banner(f"{binary} [{lang_spec.display_name}]")

    tmpdir = Path(tempfile.mkdtemp(prefix="perf_agent_"))
    try:
        # perf stat
        with display.spinner("Running perf stat..."):
            stat_result = runner.run_perf_stat(run_argv, ns.timeout)

        # perf record + report
        perf_data = tmpdir / "perf.data"
        with display.spinner("Recording call graph..."):
            runner.run_perf_record(run_argv, ns.timeout, perf_data)
        with display.spinner("Generating perf report..."):
            report_result = runner.run_perf_report(perf_data)

        metrics = parser.parse_stat(stat_result.stderr)
        functions = parser.parse_report(report_result.stdout)
        has_sym = parser.has_symbols(functions)

        display.show_metrics_table(metrics, functions)
        if not has_sym:
            display.show_warning_no_symbols()

        if ns.loops > 0:
            # Need a source file for optimization
            source_file = ns.source
            if source_file is None:
                if not lang_spec.compiled:
                    source_file = Path(ns.binary) if ns.binary else None
                if source_file is None:
                    p.error("--loops > 0 requires --source")
            if not source_file.exists():
                display.show_error(f"Source file not found: {source_file}")
                sys.exit(1)

            compile_flags = ns.compile_flags or lang_spec.default_flags
            if ns.compile_flags:
                _validate_compile_flags(ns.compile_flags)
            cc = ns.compiler or lang_spec.default_compiler
            output_dir = ns.output_dir or (source_file.parent / "optimized")

            if lang_spec.compiled and shutil.which(cc) is None:
                display.show_error(
                    f"Compiler not found on PATH: {cc}\n"
                    "Install it or specify a different compiler with --compiler."
                )
                sys.exit(1)

            if lang_spec.compiled:
                def _compile_fn(src: Path, out: Path) -> CompileResult:
                    return _compiler.build_source(
                        src, out, lang_spec, compiler=cc, flags=compile_flags
                    )
            else:
                _runtime = shutil.which(lang_spec.runtime) or lang_spec.runtime
                def _compile_fn(src: Path, out: Path) -> CompileResult:
                    return CompileResult(
                        success=True, output_binary=src,
                        run_argv=[_runtime, str(src)],
                        stdout="", stderr="", elapsed_seconds=0.0,
                    )

            display.CONSOLE.print(
                display.Rule(
                    f"[bold cyan]Starting optimization loop — up to {ns.loops} iteration(s) "
                    f"[{lang_spec.display_name}][/]",
                    style="cyan",
                )
            )

            _check_fn = _build_check_fn(
                lang_spec, source_file, run_argv, ns.check_cmd, ns.timeout
            )

            config = optimizer.OptimizeConfig(
                source=source_file,
                lang=lang_spec,
                initial_run_argv=run_argv,
                binary=binary,
                binary_args=binary_args,
                compiler=cc,
                compile_flags=compile_flags,
                max_iterations=ns.loops,
                timeout=ns.timeout,
                model=ns.model,
                base_url=ns.base_url,
                api_key=ns.api_key,
                think=not ns.no_think,
                output_dir=output_dir,
                initial_metrics=metrics,
                initial_functions=functions,
                compile_fn=_compile_fn,
                on_iteration_start=display.show_iteration_header,
                on_llm_start=lambda n, m: display.spinner(
                    f"Asking LLM for optimization {n}/{m}..."
                ),
                on_compile_result=display.show_compile_result,
                on_profile_start=display.spinner,
                on_profile_done=display.show_metrics_table,
                on_llm_response=lambda thinking, response, n: (
                    display.show_llm_thinking(thinking, n),
                    display.show_llm_optimization_response(response, n),
                ),
                on_iteration_done=display.show_iteration_result,
                on_source_written=display.show_source_diff,
                on_user_approval=(
                    (lambda cur, new: display.prompt_user_approval(cur, new, source_file.name))
                    if ns.user_approved else None
                ),
                on_near_best=display.show_near_theoretical_best,
                security_fn=(
                    None if ns.no_security
                    else _make_security_fn(
                        lang_spec, cc, compile_flags, ns.timeout,
                        ns.api_key, ns.base_url, ns.model
                    )
                ),
                on_security_result=display.show_security_report,
                security_remediation=not ns.no_remediate and not ns.no_security,
                on_security_remediation=display.show_security_remediation,
                check_fn=_check_fn,
                on_check_result=display.show_check_result,
            )

            history, output_path = optimizer.run_optimize_loop(config)
            display.show_optimization_summary(history, output_path)

        else:
            chunks = llm.stream_analysis(
                metrics=metrics,
                functions=functions,
                binary=str(binary),
                model=ns.model,
                base_url=ns.base_url,
                api_key=ns.api_key,
                think=not ns.no_think,
                lang=lang_spec,
            )
            display.stream_llm_panel(chunks)

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        if work_tmpdir is not None:
            shutil.rmtree(work_tmpdir, ignore_errors=True)


def _run_docker_path(p: argparse.ArgumentParser, ns: argparse.Namespace, binary_args: list[str]) -> None:
    """Docker-based profiling and optimization path."""
    from . import docker_runner
    from .targets import get_target

    if shutil.which("docker") is None:
        raise DockerNotFoundError(
            "docker not found on PATH.\n"
            "Install Docker: https://docs.docker.com/engine/install/"
        )

    try:
        target_spec = get_target(ns.target)
    except ValueError as e:
        display.show_error(str(e))
        sys.exit(1)

    if ns.source is None:
        p.error("--target requires --source")
    if not ns.source.exists():
        display.show_error(f"Source file not found: {ns.source}")
        sys.exit(1)

    lang_spec = _detect_lang(ns)

    binary_display = ns.binary or ns.source.stem

    dockerfiles_dir = Path(__file__).parent.parent / "dockerfiles"
    if not dockerfiles_dir.exists():
        display.show_error(f"dockerfiles/ directory not found: {dockerfiles_dir}")
        sys.exit(1)

    output_dir = ns.output_dir or (ns.source.parent / "optimized")
    work_dir = Path(tempfile.mkdtemp(prefix="perf_agent_docker_"))

    try:
        src_in_work = work_dir / ns.source.name
        src_in_work.write_text(ns.source.read_text(encoding="utf-8"), encoding="utf-8")
        binary_in_work = work_dir / ns.source.stem

        display.show_banner(f"{binary_display} [{target_spec.name}] [{lang_spec.display_name}]")

        with docker_runner.DockerBackend(
            target=target_spec,
            work_dir=work_dir,
            dockerfiles_dir=dockerfiles_dir,
            no_build=ns.no_build,
        ) as backend:
            compile_flags = ns.compile_flags or target_spec.compile_flags
            cc = ns.compiler or target_spec.compiler

            with display.spinner(f"Compiling with {cc} ({compile_flags})..."):
                compile_result = backend.compile_source(
                    src_in_work, binary_in_work, compiler=cc, flags=compile_flags,
                    lang=lang_spec,
                )
            display.show_compile_result(compile_result)
            if not compile_result.success:
                display.show_error("Initial compilation failed — cannot continue.")
                sys.exit(1)

            if lang_spec.compiled and compile_result.run_argv:
                try:
                    binary_in_work.chmod(0o755)
                except OSError:
                    pass

            run_argv = compile_result.run_argv + binary_args

            initial_perf_data = work_dir / "initial.data"
            with display.spinner("Profiling initial binary..."):
                metrics, functions = docker_runner.profile_binary_in_docker(
                    backend, run_argv, ns.timeout, initial_perf_data
                )
            display.show_metrics_table(metrics, functions)

            if not parser.has_symbols(functions):
                display.show_warning_no_symbols()

            if ns.loops > 0:
                display.CONSOLE.print(
                    display.Rule(
                        f"[bold cyan]Starting optimization loop — up to {ns.loops} iteration(s) "
                        f"[{target_spec.name}][/]",
                        style="cyan",
                    )
                )

                def _docker_compile(src: Path, out: Path) -> CompileResult:
                    return backend.compile_source(
                        src, out, compiler=cc, flags=compile_flags, lang=lang_spec
                    )

                def _docker_profile(run_av: list[str], timeout: int, perf_data: Path):
                    return docker_runner.profile_binary_in_docker(
                        backend, run_av, timeout, perf_data
                    )

                # Docker: only --check-cmd is supported (candidates run in container,
                # not locally — stdout comparison is not available in this mode)
                _docker_check_fn = None
                if ns.check_cmd:
                    _docker_check_fn = _build_check_fn(
                        lang_spec, ns.source, run_argv, ns.check_cmd, ns.timeout
                    )

                config = optimizer.OptimizeConfig(
                    source=src_in_work,
                    lang=lang_spec,
                    initial_run_argv=run_argv,
                    binary=binary_in_work,
                    binary_args=binary_args,
                    compiler=cc,
                    compile_flags=compile_flags,
                    max_iterations=ns.loops,
                    timeout=ns.timeout,
                    model=ns.model,
                    base_url=ns.base_url,
                    api_key=ns.api_key,
                    think=not ns.no_think,
                    output_dir=output_dir,
                    initial_metrics=metrics,
                    initial_functions=functions,
                    compile_fn=_docker_compile,
                    profile_fn=_docker_profile,
                    target_context=target_spec.llm_context,
                    work_dir=work_dir,
                    on_iteration_start=display.show_iteration_header,
                    on_llm_start=lambda n, m: display.spinner(
                        f"Asking LLM for optimization {n}/{m}..."
                    ),
                    on_compile_result=display.show_compile_result,
                    on_profile_start=display.spinner,
                    on_profile_done=display.show_metrics_table,
                    on_llm_response=lambda thinking, response, n: (
                        display.show_llm_thinking(thinking, n),
                        display.show_llm_optimization_response(response, n),
                    ),
                    on_iteration_done=display.show_iteration_result,
                    on_source_written=display.show_source_diff,
                    on_user_approval=(
                        (lambda cur, new: display.prompt_user_approval(cur, new, ns.source.name))
                        if ns.user_approved else None
                    ),
                    on_near_best=display.show_near_theoretical_best,
                    security_fn=(
                        None if ns.no_security
                        else _make_security_fn(
                            lang_spec, cc, compile_flags, ns.timeout,
                            ns.api_key, ns.base_url, ns.model
                        )
                    ),
                    on_security_result=display.show_security_report,
                    security_remediation=not ns.no_remediate and not ns.no_security,
                    on_security_remediation=display.show_security_remediation,
                    check_fn=_docker_check_fn,
                    on_check_result=display.show_check_result if _docker_check_fn else None,
                )

                history, output_path = optimizer.run_optimize_loop(config)
                display.show_optimization_summary(history, output_path)

            else:
                chunks = llm.stream_analysis(
                    metrics=metrics,
                    functions=functions,
                    binary=binary_display,
                    model=ns.model,
                    base_url=ns.base_url,
                    api_key=ns.api_key,
                    think=not ns.no_think,
                    target_context=target_spec.llm_context,
                    lang=lang_spec,
                )
                display.stream_llm_panel(chunks)

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
