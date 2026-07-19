# Code signing EVE Alert releases

Unsigned Windows executables trigger SmartScreen's "Windows protected
your PC" warning on first run. Signing removes that warning (Azure
Trusted Signing) or reduces it to a milder publisher-name prompt that
fades after enough reputation accrues (a standard OV cert).

**Status: not yet configured.** The release workflow builds and
publishes unsigned `EVE-Alert.exe` / `EVE-Alert-Setup-*.exe` today. This
is a repo-owner action item — an individual/organization identity and a
recurring cost, neither of which an automated agent can provision.
Everything below documents what's needed; the workflow already checks
for the relevant secrets and signs automatically once they exist
(`.github/workflows/release.yml`, "Sign Windows binaries" step) —
nothing else needs to change when signing is turned on.

## Option A: Azure Trusted Signing (recommended)

Microsoft's newer, cheaper signing service — a flat monthly Azure
subscription cost rather than per-certificate pricing, and it works for
individuals (not just registered businesses), with Extended Validation-
equivalent trust.

1. Create an Azure account and a **Trusted Signing** resource
   ([docs](https://learn.microsoft.com/azure/trusted-signing/quickstart)).
   Requires identity verification (a few business days for individuals).
2. Create a signing **Identity Validation** and a **Certificate
   Profile** (type: Public Trust) under that resource.
3. Create a Microsoft Entra app registration with permission to use the
   Trusted Signing resource, and a client secret for it.
4. Add these as GitHub Actions repository secrets (Settings → Secrets
   and variables → Actions):
   - `SIGN_AZURE_TENANT_ID`
   - `SIGN_AZURE_CLIENT_ID`
   - `SIGN_AZURE_CLIENT_SECRET`
   - `SIGN_AZURE_ENDPOINT` (region-specific, e.g. `https://eus.codesigning.azure.net`)
   - `SIGN_AZURE_ACCOUNT_NAME`
   - `SIGN_AZURE_CERT_PROFILE_NAME`
5. The workflow uses the [`azure/trusted-signing-action`](https://github.com/Azure/trusted-signing-action)
   to sign both `dist/EVE-Alert.exe` and the installer output, gated on
   `SIGN_AZURE_TENANT_ID` being set.

## Option B: traditional OV code-signing certificate

The older path — buy a certificate (DigiCert, Sectigo, SSL.com, etc.,
roughly $70-400/year depending on vendor and whether it's
hardware-token-backed, which several CAs now require for OV certs).

1. Purchase an OV (Organization Validation) or EV (Extended Validation)
   code-signing certificate. EV clears SmartScreen faster but usually
   requires a registered business entity and a hardware token, which
   complicates CI signing (a hardware token can't sit in a GitHub-hosted
   runner) — OV via a cloud HSM (e.g. DigiCert KeyLocker /
   SSL.com eSigner) is the more CI-friendly option if going this route.
2. Export the certificate + private key as a base64-encoded `.pfx` (or
   configure your CA's cloud HSM API credentials).
3. Add as GitHub Actions secrets:
   - `SIGN_PFX_BASE64` + `SIGN_PFX_PASSWORD`, **or**
   - the specific credential set your CA's cloud-HSM signing tool needs
     (e.g. `SIGN_ESIGNER_USERNAME`/`SIGN_ESIGNER_PASSWORD`/`SIGN_ESIGNER_TOTP_SECRET`
     for SSL.com eSigner).
4. The workflow decodes the secret and runs `signtool sign /fd SHA256
   /tr http://timestamp.digicert.com /td SHA256 dist\EVE-Alert.exe`
   (signtool ships with the Windows SDK, already present on
   `windows-latest`/most self-hosted Windows runners).

## What happens with neither configured

Nothing breaks. The release workflow's signing step is conditional on
`SIGN_AZURE_TENANT_ID` (Option A) or `SIGN_PFX_BASE64` (Option B) being
present; when neither secret exists it's skipped with a log line, and
the unsigned `.exe`/installer publish exactly as they do today. Users
installing an unsigned build see SmartScreen's warning and need
"More info → Run anyway" — inconvenient, not broken.

## winget and signing

`winget install` doesn't require a signed binary, but winget's own
manifest validation and Microsoft's community-repo review both flag
unsigned installers more heavily, and users get the same SmartScreen
prompt during a winget-driven install. Signing before submitting to
`microsoft/winget-pkgs` (see [`../docs`](../docs) → `WINGET.md`) is
strongly recommended, not required.
