Run the Playwright + SSO pre-flight check to validate all tooling before executing tests.

Execute the script and display the results:

```bash
bash ~/my_claude_automations/playwright/preflight.sh
```

After the script completes, interpret the exit code and output:

- **Exit 0** — All clear. Safe to run tests.
- **Exit 1** — Warnings present. Tests may be flaky. Recommend addressing warnings before a full suite run, but short/focused runs may be acceptable.
- **Exit 2** — Critical failures. Do NOT run tests. Present the "How to fix" section clearly and wait for the user to resolve the issues.

If the script file doesn't exist yet, tell the user it's missing and suggest they check `~/my_claude_automations/playwright/`.

## Common Fixes

| Issue | Fix |
|-------|-----|
| SSO expired / auth.json stale | Run `refresh-auth.bat` on Windows while logged into the portal, then `npm run extract-auth` on the Linux server |
| Chromium binary missing | `cd ~/my_claude_automations/playwright && npx playwright install chromium` |
| Playwright CLI missing | `cd ~/my_claude_automations/playwright && npm install` |
| MCP_TOKEN not set | `source ~/.bash_profile` |
| Portal unreachable | Check VPN connection |
