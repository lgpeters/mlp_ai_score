# Millenium

## Layout

```
Millenium/
├── .venv/              # One virtual environment for the whole repo
├── .vscode/            # Interpreter + debug config
├── shared/             # Business logic shared across services
├── mcp-server/         # MCP server for Claude Desktop
├── backend/            # (empty for now)
├── pyproject.toml      # Makes `shared` importable everywhere
└── requirements.txt    # Third-party dependencies
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

The `pip install -e .` is what makes `from shared import ...` work from any
directory in the repo, without path juggling. Re-run it only if you change
`pyproject.toml` — new files under `shared/` are picked up automatically.

## MCP server

```bash
python mcp-server/server.py
```

It talks over **stdio**, not a network port, so running it directly just leaves
it waiting on stdin — that is expected, not a hang. Claude Desktop launches it as
a subprocess and communicates over that pipe. In VS Code, the "MCP Server" debug
configuration does the same thing with breakpoints attached.

### Connecting Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "my-case-study": {
      "command": "/Users/lukepeters/Desktop/Millenium/.venv/bin/python",
      "args": ["/Users/lukepeters/Desktop/Millenium/mcp-server/server.py"]
    }
  }
}
```

Both paths must be absolute, and `command` must be the venv's Python — the system
Python will not find `fastmcp`. Restart Claude Desktop fully after editing.

## Adding a tool

```python
@mcp.tool
def my_new_tool(param: str) -> str:
    """Description of what this tool does."""
    return f"Result: {param}"
```
