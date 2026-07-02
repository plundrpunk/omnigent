# Local CLI Agents

These Omnigent agent templates use Codex as the supervising harness and expose
one local provider CLI through a named terminal:

- `kimi_code` -> `kimi`
- `gemini_desktop` -> `gemini`
- `copilot_cli` -> `copilot`
- `grok_cli` -> `grok`

Register them against the local Omnigent server with:

```bash
python scripts/register_local_cli_agents.py
```

The Kimi template intentionally supplies `kimi-empty-mcp.json` so a broken
global Kimi MCP server does not prevent the CLI from starting inside Omnigent.
