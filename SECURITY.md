# Security policy

CADYX is an early-stage, Windows x64 development prototype. Security fixes are made against the current `main` branch; there are no supported release branches or promised response times.

## Reporting vulnerabilities

**Do not publish vulnerability details, exploit material, or secrets in a public issue.** If GitHub private vulnerability reporting is enabled for this repository, use the repository's **Security → Report a vulnerability** flow. Otherwise contact the maintainer through a private contact method listed on their GitHub profile. Include an impact summary, affected commit or version, and minimal safe reproduction steps. Never include live credentials or other users' CAD files.

## Deployment boundaries

- The host defaults to `127.0.0.1`; a non-loopback bind is rejected unless an authentication token of at least 32 characters is configured.
- LAN authentication supports Bearer and Basic credentials but **does not encrypt HTTP traffic**. Use only trusted private networks; use TLS termination or a trusted VPN before crossing untrusted networks.
- Do not directly expose CADYX's HTTP port to the public Internet.
- Agent commands can modify CAD projects and imported files. Restrict access to trusted operators and protect the `projects/` workspace and authentication token files.
- Never commit `.env` files, tokens, runtime downloads, generated projects, scanner captures, or logs. The `.gitignore` guards against accidental additions, but **does not remove files already tracked or secrets from Git history**.

See [README.md](README.md#authentication-and-lan-access) for the local authentication configuration.
