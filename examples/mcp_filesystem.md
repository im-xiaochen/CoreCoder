# MCP example: a filesystem server over stdio

The smallest useful `mcp.json`: the MCP org's reference filesystem server,
scoped to the directory you launch CoreCoder from. The agent gains
`mcp__fs__*` tools (read/write/list inside that one directory) while
CoreCoder's own consent gate and hooks keep applying to every call.

## Use it

Merge the entry into `~/.corecoder/mcp.json` (create the file if it does not
exist; if you already have servers, add `"fs"` next to them):

```json
{
  "mcpServers": {
    "fs": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]
    }
  }
}
```

Requires Node.js on PATH (`npx`). The first launch downloads the server
package, which can outlast the 15-second handshake budget. A server that
fails initial startup is skipped and is not registered in `/mcp`, so
`/mcp reconnect fs` cannot recover that initial failure. Run
`npx -y @modelcontextprotocol/server-filesystem .` separately, let the package
finish downloading, stop that server with Ctrl+C, then restart CoreCoder.
Once a server has successfully registered in the current session, `/mcp`
shows later failures as dead and `/mcp reconnect fs` starts it again.

The `"."` argument is resolved by the server itself, so launch CoreCoder
from the directory you want exposed. To expose a fixed tree instead, put an
absolute path there. Anything outside the listed directories is refused by
the server, not by CoreCoder.

## What you should see

`/mcp` lists `fs` as alive with its tool count, and asking the agent to
"show me what's in this directory" now routes through `mcp__fs__list_directory`
with the usual consent prompt, since MCP tools never join the read-only set.

## Remove it

Delete the `"fs"` entry from `~/.corecoder/mcp.json` and restart. No state
is kept anywhere else.
