# Submitting EVE Alert to winget

**Status: manifests are templates, not yet submitted.** No release has
been through this process yet — the steps below are what to do once
you're ready to make `winget install bluhayz.EVEAlert` work.

This process opens a **public pull request against Microsoft's
[microsoft/winget-pkgs](https://github.com/microsoft/winget-pkgs)
repository** under your own GitHub identity. Because that's a real,
visible, third-party action (not something scoped to this repo), it's
deliberately **not wired into the release workflow to run
automatically on every tag** — do it by hand (or from a manually
triggered `workflow_dispatch` job you add later, if you want to
semi-automate the repetitive parts) after you've decided a given
release is ready for it.

## Prerequisites

- A **published, signed** release is strongly preferred (see
  [`SIGNING.md`](SIGNING.md)) — winget submissions of unsigned
  installers get flagged more heavily in review.
- The release must include `EVE-Alert-Setup-<version>.exe` (from
  [`installer.iss`](installer.iss)) and `checksums.txt` as release
  assets (the existing release workflow already produces
  `checksums.txt` for the portable `.exe`; extend the same "Generate
  checksums" step to cover the installer output once it's part of the
  build).

## One-time setup

```powershell
winget install wingetcreate
```

## Per-release steps

1. **Fill in the templates.** Copy the three files in
   [`winget/`](winget/) and replace every `__VERSION__` and
   `__INSTALLER_SHA256__` placeholder:
   - `__VERSION__` → the release version without the `v` prefix (e.g.
     tag `v8.0.0` → `8.0.0`).
   - `__INSTALLER_SHA256__` → the sha256 for
     `EVE-Alert-Setup-<version>.exe` from that release's
     `checksums.txt` asset.

   Or let `wingetcreate` do this interactively instead of hand-editing:

   ```powershell
   wingetcreate update bluhayz.EVEAlert `
     --version 8.0.0 `
     --urls "https://github.com/bluhayz/EVE-Alert/releases/download/v8.0.0/EVE-Alert-Setup-8.0.0.exe" `
     --submit
   ```

   (`wingetcreate update` downloads the installer itself, computes the
   sha256, and — with `--submit` — opens the PR directly. Omit
   `--submit` to review the generated manifest locally first.)

2. **Validate locally** before submitting, if you edited the YAML by
   hand rather than using `wingetcreate update`:

   ```powershell
   winget validate --manifest packaging\winget\
   ```

3. **First submission only:** `wingetcreate` needs a GitHub token with
   permission to fork `microsoft/winget-pkgs` and open a PR — it'll
   prompt for a device-code OAuth login on first run.

4. **After the PR merges** (Microsoft's automated pipeline runs
   installer validation + a manual moderator review, typically a few
   days), `winget install bluhayz.EVEAlert` works for everyone.

## Updating for a new release

Repeat the "per-release steps" above with the new version — each
release needs its own manifest update/PR. `wingetcreate update` is
built for exactly this repeated-per-release flow.

## Chocolatey (optional, lower priority)

A minimal `.nuspec` starting point is in [`choco/`](choco/) if you
later want `choco install eve-alert` too — Chocolatey's community
package review has a similar per-release update flow via
`choco push`. Not pursued further here since winget (bundled with
Windows 10 2004+/11) covers more users with less packaging-format
duplication to maintain.
