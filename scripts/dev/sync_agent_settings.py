"""Generate Codex/Claude agent shell files from one root settings file."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = ROOT / "settings" / "agent-control.json"


def load_settings() -> dict:
    return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))


def toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def toml_array(values: list[str]) -> str:
    return json.dumps(values, ensure_ascii=True)


def render_codex_config(settings: dict) -> str:
    roles = settings["roles"]
    codex = settings["codex"]
    contract_path = settings["contract_path"]
    settings_path = settings["settings_path"]

    instruction_lines = [
        f"Generated from {settings_path}.",
        f"Operate under {contract_path}.",
        f"Current executor agent: {roles['executor_agent']}.",
        f"Current audit agent: {roles['audit_agent']}.",
        f"Executor scope: {roles['executor_scope']}.",
        f"Audit scope: {roles['audit_scope']}.",
        *codex["instructions"],
        "Preferred commands:",
        *[f"- {command}" for command in codex["preferred_commands"]],
        "Preferred MCP tools:",
        *[f"- {tool}" for tool in codex["preferred_mcp_tools"]],
    ]
    developer_instructions = "\n".join(instruction_lines)
    lines = [
        f'model = {toml_string(codex["model"])}',
        f'model_reasoning_effort = {toml_string(codex["model_reasoning_effort"])}',
        f'plan_mode_reasoning_effort = {toml_string(codex["plan_mode_reasoning_effort"])}',
        f'personality = {toml_string(codex["personality"])}',
        "",
        'developer_instructions = """',
        developer_instructions,
        '"""',
        "",
        '[mcp_servers."formal-claim"]',
        f'command = {toml_string(codex["mcp_server_command"])}',
        f'args = {toml_array(codex["mcp_server_args"])}',
        "enabled = true",
        "",
        '[mcp_servers."formal-claim".env]',
        f'TRACER_DATA_DIR = {toml_string(codex["data_dir"])}',
        'PYTHONIOENCODING = "utf-8"',
        'PYTHONUTF8 = "1"',
        "",
    ]
    return "\n".join(lines)


def render_claude_settings(settings: dict) -> str:
    claude = settings["claude"]
    payload = {
        "permissions": {
            "allow": list(claude["permissions"]),
        }
    }
    return json.dumps(payload, indent=2, ensure_ascii=True) + "\n"


def render_agent_markdown(settings: dict, *, agent_name: str, skill_paths: list[str]) -> str:
    roles = settings["roles"]
    contract_path = settings["contract_path"]
    settings_path = settings["settings_path"]
    title = f"Formal Claim Workbench {agent_name} Instructions"
    lines = [
        f"# {title}",
        "",
        f"This file is generated from [{settings_path}]({ROOT / settings_path}).",
        "",
        f"Read [{contract_path}]({ROOT / contract_path}) before changing engine, MCP, CLI, runner, or release code.",
        "",
        "Role routing:",
        "",
        f"- Executor agent: `{roles['executor_agent']}`",
        f"- Audit agent: `{roles['audit_agent']}`",
        "",
        "Use repo-local skills when the task matches:",
        "",
        *[f"- `{path}`" for path in skill_paths],
        "",
        "Keep `services/engine` as the system of record, keep `services/mcp-server` and `apps/cli` thin, and keep proof job control here as a thin `FWP` / `proof-assistant` pass-through.",
        "",
    ]
    return "\n".join(lines)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> None:
    settings = load_settings()
    write_text(ROOT / ".codex" / "config.toml", render_codex_config(settings))
    write_text(ROOT / ".claude" / "settings.local.json", render_claude_settings(settings))
    write_text(
        ROOT / "AGENTS.md",
        render_agent_markdown(
            settings,
            agent_name="Agent",
            skill_paths=list(settings["codex"]["skill_paths"]),
        ),
    )
    write_text(
        ROOT / "CLAUDE.md",
        render_agent_markdown(
            settings,
            agent_name="Claude",
            skill_paths=list(settings["claude"]["skill_paths"]),
        ),
    )


if __name__ == "__main__":
    main()
