"""Typed exception hierarchy for perf-agent."""


class PerfAgentError(RuntimeError):
    """Base class for all perf-agent errors."""


class BinaryNotFoundError(PerfAgentError):
    """Raised when the target binary does not exist."""


class BinaryNotELFError(PerfAgentError):
    """Raised when the target binary is not a valid ELF file."""


class PerfNotFoundError(PerfAgentError):
    """Raised when perf is not installed or not on PATH."""


class PerfPermissionError(PerfAgentError):
    """Raised when perf is denied due to perf_event_paranoid settings."""


class PerfTimeoutError(PerfAgentError):
    """Raised when perf subprocess exceeds the configured timeout."""


class PerfNoSymbolsWarning(UserWarning):
    """Warning emitted when the binary has no debug symbols."""


class LLMConnectionError(PerfAgentError):
    """Raised when the LLM API is unreachable or returns an unexpected error."""


class LLMModelNotFoundError(PerfAgentError):
    """Raised when the requested model is not available."""


class CompileError(PerfAgentError):
    """Raised when the compiler subprocess itself fails to run (not a build error)."""


class NoCodeBlockError(PerfAgentError):
    """Raised when the LLM response contains no ```c code block."""


class DockerNotFoundError(PerfAgentError):
    """docker CLI not on PATH."""


class DockerImageBuildError(PerfAgentError):
    """Raised when the Docker image build fails."""

    def __init__(self, message: str, build_log: str = "") -> None:
        super().__init__(message)
        self.build_log = build_log


class DockerContainerError(PerfAgentError):
    """Unexpected container/exec failure."""
