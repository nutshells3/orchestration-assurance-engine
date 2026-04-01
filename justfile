set shell := ["sh", "-cu"]
set windows-shell := ["powershell", "-NoLogo", "-NoProfile", "-Command"]

default:
  @just --list

bootstrap:
  python scripts/dev/bootstrap.py

contracts-generate:
  python scripts/dev/run_uv.py run --python 3.12 --group dev python scripts/contracts/generate_bindings.py

sync-agent-settings:
  python scripts/dev/sync_agent_settings.py

test:
  python scripts/dev/check_repo.py --mode test

lint:
  python scripts/dev/check_repo.py --mode lint

release-smoke:
  python scripts/release/smoke_release.py

release-build:
  python scripts/release/build_release_artifacts.py

cleanup-dev:
  powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File scripts/dev/cleanup_dev_processes.ps1
