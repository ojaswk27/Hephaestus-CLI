.PHONY: lint security audit all

all: lint security audit

lint:
	ruff check perf_agent/
	mypy perf_agent/ --ignore-missing-imports

security:
	bandit -r perf_agent/ -ll
	@if command -v hadolint >/dev/null 2>&1; then \
	    hadolint dockerfiles/Dockerfile.*; \
	else \
	    echo "hadolint not found — skipping Dockerfile lint"; \
	fi

audit:
	pip-audit
