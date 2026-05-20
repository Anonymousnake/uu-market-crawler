# Security Notes

This project needs authenticated UU Youpin request headers for private use.
Never commit real browser headers, cookies, tokens, device identifiers, local
SQLite databases, caches, or service environment files.

Keep private values in one of these places:

- environment variables
- `.secrets/*.json`
- a local `.env` file
- systemd `EnvironmentFile=` paths outside Git

Before publishing or opening a pull request, run:

```powershell
git grep -n -I -E "sk_live_|abk_|authorization|Cookie|deviceUk|deviceId|acw_tc|X-API-Key|ASTRBOT_API_KEY"
git status --short
```

If a real secret was ever committed, rotate it first. If the repository is
already public, also rewrite Git history or create a fresh public repository.
