"""Compile C sources for the optimizer loop."""

from __future__ import annotations

import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .languages import LanguageSpec


@dataclass
class CompileResult:
    success: bool
    output_binary: Path
    run_argv: list[str]       # base argv to execute the program (without user args)
    stdout: str
    stderr: str
    elapsed_seconds: float


def infer_compile_flags(binary: Path, lang: "LanguageSpec | None" = None) -> str:
    """Return default compile flags for *lang*, or guess from binary with readelf."""
    if lang is not None:
        return lang.default_flags
    try:
        subprocess.run(
            ["readelf", "-S", str(binary)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            text=True,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "-O2 -g -fno-omit-frame-pointer"


def compile_source(
    source: Path,
    output: Path,
    compiler: str = "gcc",
    flags: str = "-O2 -g -fno-omit-frame-pointer",
) -> CompileResult:
    """Compile *source* to *output* binary. Does NOT raise on build error."""
    cmd = [compiler, *shlex.split(flags), "-o", str(output), str(source)]
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            text=True,
        )
        elapsed = time.monotonic() - t0
        return CompileResult(
            success=proc.returncode == 0,
            output_binary=output,
            run_argv=[str(output)],
            stdout=proc.stdout,
            stderr=proc.stderr,
            elapsed_seconds=elapsed,
        )
    except FileNotFoundError as e:
        elapsed = time.monotonic() - t0
        return CompileResult(
            success=False,
            output_binary=output,
            run_argv=[],
            stdout="",
            stderr=f"Compiler not found: {e}",
            elapsed_seconds=elapsed,
        )
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - t0
        return CompileResult(
            success=False,
            output_binary=output,
            run_argv=[],
            stdout="",
            stderr="Compilation timed out after 120s",
            elapsed_seconds=elapsed,
        )


def build_source(
    source: Path,
    output: Path,
    lang: "LanguageSpec",
    compiler: str | None = None,
    flags: str | None = None,
) -> CompileResult:
    """Build/prepare *source* for execution according to *lang*.

    For interpreted languages (compiled=False) returns a no-op success result.
    For Java, *output* is used as the -d class output directory.
    For all other compiled languages delegates to compile_source().
    """
    if not lang.compiled:
        runtime = shutil.which(lang.runtime) or lang.runtime
        return CompileResult(
            success=True,
            output_binary=source,
            run_argv=[runtime, str(source)],
            stdout="",
            stderr="",
            elapsed_seconds=0.0,
        )

    cc = compiler or lang.default_compiler
    fl = flags if flags is not None else lang.default_flags

    if lang.name == "java":
        return _compile_java(source, output, flags=fl)

    if lang.name == "rust":
        return _compile_rust(source, output, compiler=cc, flags=fl)

    if lang.name == "go":
        return _compile_go(source, output, flags=fl)

    # C, C++ — compile_source handles gcc/g++/clang
    return compile_source(source, output, compiler=cc, flags=fl)


def _compile_rust(
    source: Path,
    output: Path,
    compiler: str = "rustc",
    flags: str = "-C opt-level=2 -C debuginfo=2",
) -> CompileResult:
    cmd = [compiler, *shlex.split(flags), "-o", str(output), str(source)]
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            text=True,
        )
        elapsed = time.monotonic() - t0
        return CompileResult(
            success=proc.returncode == 0,
            output_binary=output,
            run_argv=[str(output)],
            stdout=proc.stdout,
            stderr=proc.stderr,
            elapsed_seconds=elapsed,
        )
    except FileNotFoundError as e:
        elapsed = time.monotonic() - t0
        return CompileResult(
            success=False, output_binary=output, run_argv=[],
            stdout="", stderr=f"rustc not found: {e}",
            elapsed_seconds=elapsed,
        )
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - t0
        return CompileResult(
            success=False, output_binary=output, run_argv=[],
            stdout="", stderr="Rust compilation timed out after 120s",
            elapsed_seconds=elapsed,
        )


def _compile_java(
    source: Path,
    output_dir: Path,
    flags: str = "",
) -> CompileResult:
    """Compile a single Java source file. output_dir is the -d target for .class files."""
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["javac", *shlex.split(flags), "-d", str(output_dir), str(source)]
    class_name = source.stem
    java = shutil.which("java") or "java"
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            text=True,
        )
        elapsed = time.monotonic() - t0
        return CompileResult(
            success=proc.returncode == 0,
            output_binary=output_dir / f"{class_name}.class",
            run_argv=[java, "-cp", str(output_dir), class_name],
            stdout=proc.stdout,
            stderr=proc.stderr,
            elapsed_seconds=elapsed,
        )
    except FileNotFoundError as e:
        elapsed = time.monotonic() - t0
        return CompileResult(
            success=False,
            output_binary=output_dir / f"{class_name}.class",
            run_argv=[],
            stdout="", stderr=f"javac not found: {e}",
            elapsed_seconds=elapsed,
        )
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - t0
        return CompileResult(
            success=False,
            output_binary=output_dir / f"{class_name}.class",
            run_argv=[],
            stdout="", stderr="Java compilation timed out after 120s",
            elapsed_seconds=elapsed,
        )


def _compile_go(
    source: Path,
    output: Path,
    flags: str = "",
) -> CompileResult:
    """Compile a single-file Go program. flags are inserted after 'go build'."""
    cmd = ["go", "build"]
    if flags:
        cmd += shlex.split(flags)
    cmd += ["-o", str(output), str(source)]
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            text=True,
        )
        elapsed = time.monotonic() - t0
        return CompileResult(
            success=proc.returncode == 0,
            output_binary=output,
            run_argv=[str(output)],
            stdout=proc.stdout,
            stderr=proc.stderr,
            elapsed_seconds=elapsed,
        )
    except FileNotFoundError as e:
        elapsed = time.monotonic() - t0
        return CompileResult(
            success=False, output_binary=output, run_argv=[],
            stdout="", stderr=f"go not found: {e}",
            elapsed_seconds=elapsed,
        )
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - t0
        return CompileResult(
            success=False, output_binary=output, run_argv=[],
            stdout="", stderr="Go compilation timed out after 120s",
            elapsed_seconds=elapsed,
        )


def write_source(path: Path, source_code: str) -> None:
    """Atomically write source_code to path via tmp rename."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(source_code, encoding="utf-8")
    tmp.rename(path)
