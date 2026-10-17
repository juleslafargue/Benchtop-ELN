# Security policy

Benchtop holds research records, so security reports are welcome and taken seriously.

## Reporting a vulnerability

Please **don't open a public issue** for security problems. Instead, use GitHub's
*Report a vulnerability* button (Security tab → Advisories) on this repository, or email the
maintainer. Include what you found, how to reproduce it, and which version you tested
(`./benchtop status` shows it). You'll get an acknowledgement within a week.

## Supported versions

Only the latest release gets security fixes. Upgrading keeps your data
(`./benchtop upgrade NEW.zip` backs up first).

## Running it safely

- On a single Mac, plain `./benchtop` only listens on that computer.
- `./benchtop lab` shares it on your local network over plain HTTP: fine on a trusted lab network,
  not on public Wi-Fi. For wider access use HTTPS (see docs/SERVER.md).
- Give everyone their own account; never share logins (signatures depend on it).
- Keep backups somewhere other than the computer running Benchtop (`BENCHTOP_BACKUP_DIR`).
