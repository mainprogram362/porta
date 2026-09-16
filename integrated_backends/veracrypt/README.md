# VeraCrypt AppImage backend

`VeraCrypt.AppImage` is a local, user-installed backend for PORTA.  It is not
committed to this repository.  PORTA checks that it is executable and that its
text-mode CLI supports `--non-interactive` and `--stdin` before it enables any
VeraCrypt operation.

Installed release: VeraCrypt 1.26.29, Linux x86_64 AppImage.

- Official download page: https://veracrypt.io/en/Downloads.html
- Official signing-key fingerprint: `5069 A233 D55A 0EEB 174A 5FC3 821A CD02 680D 16DE`
- Verified SHA-256: `5a9b96f937b94de42f196c04eb9b9154f944d049ddeaeb9961851332d994c92c`

The adjacent `.sig` file was verified against that official key at installation.
Replace the AppImage and its signature together when updating, then reopen the
VeraCrypt screen and use 「環境を再確認」.
