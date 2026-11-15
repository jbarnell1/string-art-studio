# Security

## Reporting a vulnerability

Please report security problems privately through GitHub: open the repository's
[Security tab](https://github.com/jbarnell1/string-art-studio/security) and choose
"Report a vulnerability" (GitHub private vulnerability reporting). Do not open a public issue for
something that could put other people's installs at risk.

Include what you found, how to reproduce it, and the version or commit you tested. This is a hobby
project with one maintainer, so expect a first answer within about a week. Fixes land on `main`;
only the latest version is supported.

## What to know about the design

- **There is no login.** String Art Studio has no accounts and no authentication. Anyone who can
  reach the port can see, change and delete every project. The compose file binds it to
  `127.0.0.1` for that reason. To use it from a phone, put it behind something that does access
  control: a VPN such as Tailscale, or a reverse proxy with authentication. Do not publish it on
  the open internet as it is.
- **Uploads are decoded on the server.** Photos (JPEG, PNG, HEIC) go through Pillow, pillow-heif
  and OpenCV, and project bundles are ZIP files read by the server. Only let people you trust
  upload to your instance, and keep the image up to date.
- **Outbound connections** are listed in the README under "What leaves your server".
- The container runs as an unprivileged user; everything it keeps is under `/data`.
