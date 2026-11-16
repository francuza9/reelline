# Security policy

## Reporting a vulnerability

Please report security problems **privately** through GitHub's security advisories: on the repository's *Security* tab choose *Report a vulnerability*. Do not open a public issue or pull request for it.

Include what you found, how to reproduce it, the version or commit, and what an attacker could do. You will get an acknowledgement; this is a small project, so please be patient, and keep the details private until a fix is available.

## What is in scope

reelline handles credentials (model keys, platform tokens, a review chat's bot token) and publishes content to accounts. Reports about, for example, credentials written to logs or the config output, a path that escapes a channel's folders, unsafe handling of fetched content, or a way to approve or publish without the review are in scope.

## Good practice for users

- Credentials live only in the channel's `.env`; never commit it. `reelline config show` masks credential values, and the config only names the variables.
- Give each platform token the smallest scope it needs, and use a separate review chat that only operators can write to.
- Run the services as an unprivileged user with resource limits ([deployment](docs/deployment.md)).
