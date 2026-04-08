Run a full environment health check to validate all tooling before starting work.

Execute:
```bash
bash ~/my_claude_automations/healthcheck.sh --full
```

After the script completes, interpret the results and take action:

## Interpreting Results

- **Exit 0 — All clear:** All checks passed. Proceed with work.
- **Exit 1 — Warnings:** Some tools may be degraded. Surface the warnings to the user and ask if they want to proceed.
- **Exit 2 — Critical failures:** Stop. Present the failures and the "How to fix" section clearly. Offer to auto-fix anything within reach.

## Auto-fixes Claude can apply

| Failure | Auto-fix command |
|---------|-----------------|
| Chromium binary missing | `cd ~/my_claude_automations/playwright && npx playwright install chromium` |
| MCP_TOKEN not in current shell | `source ~/.bash_profile` |
| pyyaml missing from venv | `/opt/apps/.venv-3/bin/pip install pyyaml` |

## Manual fixes (requires user action)

| Failure | What to tell the user |
|---------|----------------------|
| SSO expired / auth.json stale | "Run refresh-auth.bat on your Windows machine while logged into the portal, then run `npm run extract-auth` on the Linux server to capture a fresh session" |
| MCP_TOKEN expired (JWT) | "Your developer token has expired. Visit https://servicedesk.ebiz.verizon.com/mcp/developer and generate a new token, then add it to `~/.bash_profile` and run `source ~/.bash_profile`" |
| VPN / network unreachable | "Check that your VPN is connected and you can reach servicedesk.ebiz.verizon.com" |

After auto-fixing, re-run the script to confirm all green before proceeding.
