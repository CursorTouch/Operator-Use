# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Operator-Use, please **do not open a public issue**. Instead, report it privately via [GitHub Security Advisories](https://github.com/CursorTouch/Operator-Use/security/advisories/new).

We aim to respond within **48 hours** and will work with you to understand and resolve the issue promptly.

---

## Security Recommendations

### API Key Management

- Never commit API keys or tokens to version control.
- Store credentials in your `.env` file and ensure it is listed in `.gitignore`.
- Use environment variables for production deployments.
- Restrict file permissions on your config: `chmod 600 .env`

### Channel Access Control

- Always configure `allow_from` lists for all channels (Telegram, Discord, Slack, etc.).
- An empty `allow_from` list means **no one is allowed** — use it intentionally.
- Do not expose your bot token publicly or share it in logs.

### Desktop Automation

- Operator can control your desktop, run terminal commands, browse the web, and read/write files. Only give access to trusted users.
- Never run Operator as a root or administrator account unless absolutely necessary.
- Use a dedicated user account with limited system permissions for production setups.

### Dependency Security

- Keep dependencies up to date.
- Audit dependencies periodically with:
  ```bash
  pip-audit
  ```

### Production Deployment

- Run Operator inside a container or VM for isolation.
- Enable logging and monitor for unexpected activity.
- Use a reverse proxy with HTTPS in front of any exposed webhook ports.
- Restrict inbound network access to only the ports Operator needs.

---

## Built-in Security Controls

- Channel allowlisting to restrict who can send messages to the agent.
- HTTPS enforced for all external API calls.
- Webhook signature verification where supported by the platform.

---

## Known Limitations

- No built-in rate limiting on incoming messages.
- Session history is stored in plaintext on disk.
- No automatic session expiry.
- Audit logging is minimal by default.

---

## Supported Versions

We actively maintain the latest release on PyPI. Older versions do not receive security patches.

| Version | Supported |
|---------|-----------|
| Latest  | ✅        |
| Older   | ❌        |
