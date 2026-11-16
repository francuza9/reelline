# tools/

Third-party binaries used by the pipeline. The binaries themselves are gitignored;
this file records exactly what to download so the setup is reproducible.

## Rhubarb Lip Sync

- Version: 1.14.0 (released 2025-04-03)
- Source: https://github.com/DanielSWolf/rhubarb-lip-sync
- Download: https://github.com/DanielSWolf/rhubarb-lip-sync/releases/download/v1.14.0/Rhubarb-Lip-Sync-1.14.0-Linux.zip
- SHA-256 of the zip: a9a9074862cff47b2d59b8bf399a678a3b0b74f9452ad6ad94cb292913dd8667
- Install location: `engine/tools/rhubarb/` (the release folder's contents; the `rhubarb`
  binary needs the `res/` directory next to it)

```sh
curl -L -o /tmp/rhubarb.zip \
  https://github.com/DanielSWolf/rhubarb-lip-sync/releases/download/v1.14.0/Rhubarb-Lip-Sync-1.14.0-Linux.zip
echo "a9a9074862cff47b2d59b8bf399a678a3b0b74f9452ad6ad94cb292913dd8667  /tmp/rhubarb.zip" | sha256sum -c
unzip -q /tmp/rhubarb.zip -d /tmp/rhubarb
mkdir -p engine/tools/rhubarb
cp -r /tmp/rhubarb/Rhubarb-Lip-Sync-1.14.0-Linux/. engine/tools/rhubarb/
chmod +x engine/tools/rhubarb/rhubarb
engine/tools/rhubarb/rhubarb --version   # Rhubarb Lip Sync version 1.14.0
```

## Fredoka (caption font, Latin fallback)

The channel's caption font (`channels/<name>/assets/fonts/`) has no Latin letters; the
Captions layer falls back to Fredoka for those. Unlike the binaries above, the TTF
is committed.

- Font: Fredoka variable (wdth 75-125, wght 300-700), from the Google Fonts repo
- Source: https://github.com/google/fonts/tree/main/ofl/fredoka
  (pinned to commit eda3fe521878f70de0e2d91fd6d800f93f8c4758)
- License: SIL Open Font License 1.1 (https://openfontlicense.org)
  (Copyright 2016 The Fredoka Project Authors). The OFL requires the license to
  travel with the font, so its text is committed next to it as
  `channels/<name>/assets/fonts/OFL-Fredoka.txt` (the `OFL.txt` from the same commit).
- Installed as: `channels/<name>/assets/fonts/Fredoka-Variable.ttf`
- SHA-256: 2ba02e68b152868aef9ba28e24b3648c7d457fe6f25c761f2c2c53fb61a73fc8

```sh
BASE=https://raw.githubusercontent.com/google/fonts/eda3fe521878f70de0e2d91fd6d800f93f8c4758/ofl/fredoka
curl -L -o channels/<name>/assets/fonts/Fredoka-Variable.ttf "$BASE/Fredoka%5Bwdth%2Cwght%5D.ttf"
curl -L -o channels/<name>/assets/fonts/OFL-Fredoka.txt "$BASE/OFL.txt"
echo "2ba02e68b152868aef9ba28e24b3648c7d457fe6f25c761f2c2c53fb61a73fc8  channels/<name>/assets/fonts/Fredoka-Variable.ttf" | sha256sum -c
```
