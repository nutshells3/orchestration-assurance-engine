# Agent Settings

Edit [agent-control.json](C:\Users\madab\Downloads\Project\formal-claim\settings\agent-control.json) to change:

- default executor vs audit agent routing
- Codex model and reasoning settings
- shared preferred commands and MCP tools
- Claude local command permissions
- skill paths for both agent shells

After editing, regenerate the derived files with:

```powershell
python scripts/dev/sync_agent_settings.py
```

Generated targets:

- `.codex/config.toml`
- `.claude/settings.local.json`
- `AGENTS.md`
- `CLAUDE.md`

The engine, MCP, CLI, desktop, and runner boundaries still live in [docs/product/agent-runtime-contract.md](C:\Users\madab\Downloads\Project\formal-claim\docs\product\agent-runtime-contract.md). `settings/agent-control.json` only controls the human/agent shell configuration and routing.
