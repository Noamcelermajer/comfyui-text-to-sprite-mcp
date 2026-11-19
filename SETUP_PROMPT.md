# Setup prompt

Paste this into any coding agent (Claude Code, Codex, or another agent that can run
commands) on the machine with the GPU. It needs no further input from you.

```text
Install Sprite Forge from https://github.com/Noamcelermajer/comfyui-text-to-sprite-mcp on this machine.

Read AGENTS.md in that repository
(https://raw.githubusercontent.com/Noamcelermajer/comfyui-text-to-sprite-mcp/main/AGENTS.md)
and follow its "Install (unattended)" section from start to finish: clone, run
`python forge.py setup` and act on its JSON answer until it reports "ok", verify with
`python forge.py doctor`, register the MCP server and skill with `python forge.py connect`,
and generate the test sprite. Obey its "Rules while installing". Do not stop to ask me
questions; when you are done, give me the report it describes, including anything that
is still missing.
```

What the agent will do, in short: reuse an existing ComfyUI if it finds one (installing it
only if absent), download about 18 GB of model weights (no custom node packs are needed),
register the MCP server and skill with Claude and Codex, and generate one test sprite to
prove the pipeline works. The full, exact procedure is in [AGENTS.md](AGENTS.md).

To see what would be installed without changing anything, run
`python forge.py setup --dry-run` yourself first.
