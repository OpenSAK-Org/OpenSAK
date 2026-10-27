# MSIX packaging prototype (issue #786)

> **Status (27 Aug 2026): CI→Partner Center→Store pipeline fully
> validated, end to end.** A CI-built package (`build-msix.yml`, no
> Windows machine involved in the build itself) was submitted to the
> private/unlisted Partner Center listing for product `9P4NBMM84H2D`,
> **passed Store certification**, and was confirmed installable via the
> Microsoft Store app on Windows. This is the second, independent pass —
> the first (25 Aug) validated the *local* prototype build; this one
> validates the *CI* build, closing the loop on step 3.
>
> Two real bugs were caught and fixed along the way, both worth knowing
> about if this ever needs touching again:
> - `generate_assets.py` imports Pillow, which is only in `pyproject.toml`'s
>   `dev` extra — the CI job's `pip install` now installs it explicitly.
> - **The Store rejects any package with a non-zero Revision** (4th
>   version component) — `scripts/derive_msix_version.py` originally put
>   the beta number there (`1.18.0-beta.1` → `1.18.0.1`) and Partner
>   Center rejected it outright ("Apps are not allowed to have a Version
>   with a revision number other than zero"). Fixed by folding patch +
>   beta number into Build instead, Revision pinned at 0 — see that
>   script's docstring for the exact scheme. `1.18.0-beta.1` now derives
>   to `1.18.1.0`.
>
> Decision made 27 Aug 2026: the Store listing will go **fully public**
> (not a restricted Package Flights audience), with the app's display
> name automatically switching to **"OpenSAK Beta"** for beta versions
> — see "Naming beta vs. stable releases" below. Two more things came
> up wiring this in and are now documented under "Known Partner Center
> quirks": `DisplayName` values must be pre-reserved under Manage app
> names before a package using them will validate, and reusing a
> version number for changed content (even metadata-only) is rejected —
> outright, even against an already-certified prior submission — and
> needs a version bump.
>
> QtWebEngine/Chromium inside the MSIX sandbox is confirmed not a
> Store-certification blocker — that was the core #786 risk and it's
> cleared. **Remaining work — genuinely not done yet:** the listing is
> still private/unlisted; going public means finding and flipping the
> visibility/audience setting under Pricing and availability (not yet
> located precisely in this account's UI — check there first next
> time), which may itself need a separate approval pass and can take up
> to ~24h to propagate through Store search once flipped. Also still
> pending: wiring `build-msix.yml` to run automatically on version tags
> instead of manual `workflow_dispatch`. See "Where each step runs"
> Windows-only.

This directory is the **prototype** work for #786's step 1–2: package
OpenSAK as MSIX and confirm Microsoft Store certification doesn't reject
it because of the bundled QtWebEngine/Chromium runtime, before investing
in CI integration (step 3) or the full Store listing (step 4).

Nothing here changes the existing Windows `.exe`/ZIP build — `opensak.spec`
and `build.yml` are untouched. This wraps the same PyInstaller output in an
MSIX container as a side channel.

## Where each step runs

MSIX packaging tools (`makeappx.exe`, `signtool.exe`, `Add-AppxPackage`)
only exist on Windows — there's no practical Linux/macOS equivalent, so
this split is permanent, not just a prototype-stage limitation:

| Step | Where | Why |
|---|---|---|
| Editing this README / the manifest template / the PowerShell script | **Linux or Windows** | Plain text editing, no MSIX tooling involved |
| Building, signing, sideload-installing the `.msix` | **Windows only** | `build_msix_local.ps1` calls `makeappx.exe`/`signtool.exe`/`Add-AppxPackage` — Windows SDK tools with no Linux port |
| Testing the installed app (map/DB/GPX/update-checker) | **Windows only** | Needs the actual sandboxed install to test against |
| Partner Center submission (Properties, Store listings, Submit for certification) | **Linux or Windows, browser only** | Confirmed working from a Linux browser session during the 25 Aug submission |
| Installing/testing via the real Store (private link) | **Windows only** (for now) | Needs a Windows machine signed into the Store app with the right Microsoft account; not yet tested on other platforms |
| CI integration (step 3) — `.github/workflows/build-msix.yml` | **Neither — automated** | A `windows-latest` GitHub Actions runner builds/packages/self-signs the `.msix` on manual `workflow_dispatch` and uploads it as a CI artifact. **Manual trigger only for now** (Actions → "Build MSIX (manual)" → Run workflow) — it does not run on version tags and the artifact isn't attached to public GitHub Releases, since the Store listing is still private/unlisted. Partner Center submission itself (upload, listing fields, Submit for certification) stays a manual browser step either way. |

**Bottom line for now:** keep the Windows machine in the loop for every
build/sign/sideload-test cycle. Partner Center's browser-based submission
step can be done from either machine — Linux is fine for that part.

## Prerequisites (one-time, on the Windows VM)

1. Install the **Windows 10/11 SDK** (or the standalone "MSIX Packaging
   Tool" from the Microsoft Store) — this provides `makeappx.exe` and
   `signtool.exe`. SDK: https://developer.microsoft.com/windows/downloads/windows-sdk/
2. Make sure `dist\OpenSAK\opensak.exe` can be built the normal way first
   (i.e. your existing local Windows build setup already works).
3. Pull the branch you're working from (this is now merged into `beta`;
   use `feature/msix-packaging` only if you're picking up new,
   not-yet-merged MSIX work):
   ```powershell
   git fetch origin beta
   git checkout beta
   ```

## Step 1 — local packaging + sideload test

Run from the repo root in PowerShell:

```powershell
.\packaging\msix\build_msix_local.ps1 -InstallAfterBuild
```

What it does:

1. Builds `dist\OpenSAK\` via PyInstaller (same as CI) — skip with
   `-SkipBuild` if you already have a fresh build.
2. Generates the MSIX tile/logo images from `assets/icons/opensak_512.png`
   (`packaging/msix/assets/*.png` — gitignored output, regenerated each
   run, same pattern as the boundary baseline data).
3. Fills in `AppxManifest.xml.template` with **dev-test placeholder**
   values (`OpenSAKDevTest.OpenSAK` identity, self-signed cert) — these
   are NOT the real Store identity, see step 2.
4. Packages it with `makeappx.exe` into
   `dist\OpenSAK-<version>-prototype.msix`.
5. Signs it with a local self-signed certificate (created automatically
   on first run).
6. With `-InstallAfterBuild`: installs it via `Add-AppxPackage`.

### 1c. Trusting the self-signed test certificate

`Add-AppxPackage` will refuse to install an MSIX unless the signing
certificate is trusted locally. After the script creates the test cert
the first time:

```powershell
$cert = Get-ChildItem Cert:\CurrentUser\My | Where-Object { $_.Subject -eq "CN=OpenSAK Dev Test" }
Export-Certificate -Cert $cert -FilePath "$env:TEMP\opensak-dev-test.cer"
Import-Certificate -FilePath "$env:TEMP\opensak-dev-test.cer" -CertStoreLocation Cert:\LocalMachine\TrustedPeople
```
(Needs an elevated/Admin PowerShell for the `Import-Certificate` line.)

### What to actually check once it's installed

This is the point of the whole prototype — don't just confirm it installs:

- [ ] OpenSAK launches from the Start Menu tile
- [ ] **Map tab loads** (Leaflet/OSM via QtWebEngine) — this is the specific
      risk #786 flags; if QtWebEngine fails inside the MSIX sandbox this is
      where it'll show up
- [ ] Database create/open works, import a small GPX to sanity-check file
      I/O
- [ ] Update checker doesn't error out (outbound HTTPS — confirms the
      `internetClient` capability is sufficient)
- [ ] Uninstall cleanly via Settings → Apps

If any of those fail in ways that look sandbox-related (not ordinary
bugs), that's the signal to stop and reconsider — see "If this doesn't
work" below.

## Step 2 — Partner Center test submission

This step needs a Microsoft Partner Center **individual developer
account** (free as of the 2026 fee removal — see #786's step 4, but the
account itself needs to exist before you can submit anything, even a
private test). If you don't have one yet:

1. https://partner.microsoft.com/dashboard/registration — register as
   Individual.
2. Once approved, **App name reservation**: reserve "OpenSAK" (or
   whatever's available) under your account. This is where the *real*
   `Identity Name` and `Publisher` (CN) values come from — Partner Center
   shows them on the app's **Product identity** page after reservation.
3. Re-run the local build with the real identity values instead of the
   dev-test placeholders:
   ```powershell
   .\packaging\msix\build_msix_local.ps1 `
       -IdentityName "<value from Partner Center>" `
       -PublisherCN  "<value from Partner Center>" `
       -PublisherDisplayName "<value from Partner Center>" `
       -Version "1.17.2.0"
   ```
   Note: `-PublisherDisplayName` must match the `PublisherDisplayName` shown
   on Partner Center's Product identity page exactly - the Store validates
   it against your account's registered publisher display name and rejects
   submissions where it doesn't match (even though the package installs
   fine locally either way).
   Note: for an actual Store submission the package must be signed with
   your Partner Center-associated certificate flow, not the local
   self-signed one — Partner Center will tell you what's needed
   (typically it accepts an unsigned or dev-signed package for
   submission and re-signs it during certification).
4. In Partner Center: **Submissions → new submission**, mark it as
   private/unlisted (not public), upload the `.msix`, fill the minimum
   required Store listing fields (can be placeholder text — this is a
   certification test, not a real launch).
5. Submit and wait for certification results (per #786: can take hours
   to days).

### Reading the certification result

- **Pass** → QtWebEngine risk is cleared. Move to #786 step 3 (CI
  integration) and step 4 (real Store listing). #564 (paid Windows
  cert) can likely stay on hold. **This is the outcome we got on
  25 Aug 2026** — confirmed pass, and confirmed installable via the
  Store app itself (not just sideload): open
  `https://apps.microsoft.com/detail/restricted/<product ID>` while
  signed into the Store app with a Microsoft account that's a member
  of the private audience group, click Get/Install, launch normally.
- **Fail, specifically citing the embedded Chromium/WebView component**
  → this is the risk #786 called out. Options: request a manual review
  exception from Microsoft, investigate `WebView2` as a lighter-weight
  alternative to QtWebEngine for the Store build specifically, or fall
  back to reactivating #564.
- **Fail, for unrelated reasons** (manifest issues, missing listing
  fields, icon requirements) → fix and resubmit; not a signal to
  abandon MSIX.

## Known Partner Center quirks (from the 25 Aug and 27 Aug 2026 submissions)

Partner Center's UI has several silent-failure modes — no error shown,
just a section that won't turn "Complete" until you happen to fix the
right thing. Worth checking these first if a submission gets stuck:

- **Switching identity regenerates the signing cert.** If you rebuild
  with a different `-PublisherCN` than a previous run, the script
  creates a **new**, separate self-signed certificate in
  `Cert:\CurrentUser\My` — it does not reuse or rename the old one.
  Trusting an old cert (e.g. a leftover dev-test one) does nothing for
  a package signed with the new one. Check `Get-ChildItem
  Cert:\CurrentUser\My` for multiple certs and make sure you're
  trusting the one whose Subject matches your **current**
  `-PublisherCN`.
- **`Add-AppxPackage` needs the cert trusted in *two* stores.**
  `Cert:\LocalMachine\TrustedPeople` alone gave error `0x800B0109`
  ("root certificate not trusted") — it also needs importing into
  `Cert:\LocalMachine\Root`:
  ```powershell
  Import-Certificate -FilePath "$env:TEMP\<cert>.cer" -CertStoreLocation Cert:\LocalMachine\TrustedPeople
  Import-Certificate -FilePath "$env:TEMP\<cert>.cer" -CertStoreLocation Cert:\LocalMachine\Root
  ```
- **Free-text fields have undocumented character limits.** The
  "Why do you need runFullTrust" justification on the Submission
  options page silently truncated/rejected a ~700-character answer
  with no visible error — the field just wouldn't validate. Keep any
  Partner Center free-text field under roughly 300 characters to be
  safe. Same failure pattern is reported elsewhere in Partner Center
  (e.g. a documented 200-char limit on the Copyright field).
- **An empty *optional* section can block the whole page.** Store
  listings stayed stuck on "Incomplete" with every required field
  (Product name, Description, at least one correctly-sized screenshot)
  filled in correctly — confirmed not a cache issue across hard
  refresh, full logout/login, a different browser, and a different OS.
  The actual cause was the **Store logos** section (9:16 Poster art /
  1:1 Box art / Store display images) — all marked optional in the UI,
  but something in a partial/stale upload state there was silently
  blocking the section status. Fix: clear/delete anything in Store
  logos and save; Store defaults to the icon baked into the MSIX
  package itself. General lesson: if a section won't go "Complete" and
  every visibly-required field is right, systematically clear the
  *optional* fields/uploads one at a time — that's where this bug
  tends to hide.
- **`build_msix_local.ps1` downloaded via browser needs unblocking.**
  If PowerShell refuses to run the script with "not digitally signed",
  it's Windows' Mark-of-the-Web flag from the download, not an actual
  execution-policy problem:
  ```powershell
  Unblock-File -Path .\packaging\msix\build_msix_local.ps1
  ```
- **Any manifest `DisplayName` must be pre-reserved, exact string
  match.** Uploading a package whose `Package/Properties/DisplayName`
  is e.g. `"OpenSAK Beta"` fails acceptance validation
  ("...uses a display name that you have not reserved: OpenSAK Beta")
  unless that exact name has been reserved under **Product management →
  Manage app names** first. Reserving it is a one-time action per name —
  once `"OpenSAK Beta"` is reserved, it stays valid for future
  submissions too.
- **A version number can only be submitted once, ever — even for a
  metadata-only change.** Re-uploading a package with the *same* 4-part
  MSIX version but different manifest content (e.g. only `DisplayName`
  changed, nothing in the actual app) is rejected: "All .msix and .appx
  packages... must be uniquely identified by their full names. You have
  provided two packages with the full name
  `AgreeDK.OpenSAK_<version>_X64_` which have different contents."
  This applies even against a package from a *previous, already-
  certified* submission — Partner Center checks across submission
  history, not just the current draft. Fix: bump the version (the
  `build-msix.yml` `version` workflow_dispatch input can override the
  auto-derived one for exactly this case) and re-upload. If the old
  version's package still shows an error in the current draft's
  Packages list after this, delete it explicitly (not "Retry" — that
  just re-validates the same rejected file).

## Naming beta vs. stable releases

The Store listing (and Start Menu tile) shows one name for whatever's
currently published — there's no separate "beta channel" product, same
as the `.exe`/AppImage/dmg releases: beta and stable are just different
tagged versions of the same download, not separate apps. To make it
obvious to Store users when they're getting a beta, `AppxManifest.xml.template`'s
`DisplayName` is templated (`__DISPLAY_NAME__`, both the `<Properties>`
one and the `uap:VisualElements` one that controls the Start Menu tile),
and `build-msix.yml` fills it in automatically:

- `__version__` contains `-beta.` → `"OpenSAK Beta"`
- otherwise → `"OpenSAK"`

Override it explicitly with the `display_name` workflow_dispatch input,
or `-DisplayName` if building with `build_msix_local.ps1` directly
(default `"OpenSAK"` there, for backward-compatible local prototype
runs). No manual step needed for the common case — publishing a beta
tag names it "OpenSAK Beta" automatically; publishing a stable tag names
it plain "OpenSAK" again.

## Command line (App Execution Alias)

Issue #937: scripts and the Task Scheduler call OpenSAK from the command
line (`opensak import gpx …`, `opensak --run-macro …`). Without help, an
MSIX app can't be started that way: its folder under `C:\Program
Files\WindowsApps` is not readable, and starting `opensak.exe` from there
directly would run it without package identity (other AppData). The
manifest therefore has a second `<Application>`, `OpenSAKCLI`:

- It runs `opensak-cli.exe`, which `opensak.spec` builds next to
  `opensak.exe`: the same program as a **console** program. A shell waits
  for it, shows its output and gets its exit code. An alias to the
  windowed `opensak.exe` would only start it.
- `AppListEntry="none"`: no second Start Menu entry.
- A `uap5:AppExecutionAlias` with `desktop4:Subsystem="console"` puts the
  command in `%LOCALAPPDATA%\Microsoft\WindowsApps`, which is on the
  user's `PATH`. Users can turn it off under Settings → Apps → Advanced
  app settings → App execution aliases.
- **Alias name:** `opensak.exe` for a stable release, `opensak-beta.exe`
  for a beta, so a beta and a stable install don't claim the same command.
  `build-msix.yml` derives it from `__version__` in the same way as the
  display name; `build_msix_local.ps1 -CliAlias` sets it explicitly
  (default: from `-DisplayName`).
- A process started through the alias has package identity, so it sees
  the same virtualized AppData (settings, databases, macro approvals) as
  the Store GUI. The zip install keeps separate data; `--db <path>` reaches
  a database file in either.
- A bare `opensak` (or `opensak <file>`) is a call for the GUI: the
  shell shouldn't wait for it. `opensak-cli.exe` then starts `opensak.exe`
  next to it with the same arguments, detached (no console, its own
  Ctrl+C group), and exits 0 at once (#1180). Commands (`import …`),
  `--help` and `--run-macro` stay in `opensak-cli.exe`, so the shell keeps
  waiting for their output and exit code. A process started from the
  package's folder runs in the package too, so this GUI has the same
  AppData as the one from the Start Menu.
- In the Task Scheduler, the program is
  `%LOCALAPPDATA%\Microsoft\WindowsApps\opensak.exe` (or
  `opensak-beta.exe`).

### Sideload checklist for the alias

Run together with the Lupa / MSIX check for stable (agreed in #937), on a
sideloaded build (`build_msix_local.ps1 -InstallAfterBuild`):

- [ ] `opensak --run-macro x.lua` is found through the alias
- [ ] From cmd / PowerShell: the shell waits, the exit code is correct
      (`echo %ERRORLEVEL%` / `$LASTEXITCODE`), and output appears
- [ ] A bare `opensak` returns the prompt at once and opens the window;
      that GUI uses the same AppData (databases, settings) as the one
      from the Start Menu
- [ ] `opensak import gpx <folder> --db <name>` imports, with the summary
      on stdout and in the log file
- [ ] From an editor (VS Code task, NppExec): output and the exit code come
      through when the output is redirected
- [ ] The CLI started through the alias reaches the macro pipe of the Store
      GUI (`--run-macro` while the GUI is open)
- [ ] It sees the same databases and macro approvals as the GUI
- [ ] Lupa loads from the package
- [ ] Beta and stable installed side by side: their aliases don't collide
- [ ] Task Scheduler with the alias path: "Run only when user is logged
      on" works; check whether "Run whether user is logged on or not"
      works with a packaged app, and document it as unsupported if not

## Version numbering

MSIX requires a strict 4-part numeric `Package/Identity/@Version`
(`Major.Minor.Build.Revision`) — it does **not** accept OpenSAK's
`-beta.N` suffix, and (confirmed by a real Partner Center rejection on
27 Aug 2026) **Revision must always be 0** — the Store rejects any
package where it isn't. `build_msix_local.ps1` takes an explicit
`-Version` (default `1.17.2.0` for local prototype runs).
`scripts/derive_msix_version.py` derives a Store-valid version
automatically from `src/opensak/__init__.py`'s `__version__` and is what
`build-msix.yml` uses by default. It folds the patch number and beta
number into Build instead of Revision: `Build = patch * 1000 +
(beta_number, or 999 for a stable/non-beta version)`, so e.g.
`1.18.0-beta.1` → `1.18.1.0`, `1.18.0-beta.12` → `1.18.12.0`, `1.18.0`
(stable) → `1.18.999.0`, `1.17.2-beta.3` → `1.17.2003.0`. This keeps
every beta of a given patch sorting below that patch's eventual stable
release, and keeps different patches from colliding — see the script's
docstring for the full reasoning. If you're building manually with
`build_msix_local.ps1 -Version`, run
`python scripts/derive_msix_version.py` first rather than guessing a
4-part number by hand.

## Runbook: publishing a new version (post-prototype)

Two ways to get a `.msix` for a new version — pick whichever's convenient:

**Option A — CI (`build-msix.yml`), recommended:**

1. Actions tab → **"Build MSIX (manual)"** → Run workflow. Leave the
   `version` input blank to derive it automatically from
   `src/opensak/__init__.py` (via `scripts/derive_msix_version.py`), or
   type an explicit 4-part version to override it.
2. Download the `.msix` from the run's artifacts once it finishes.
3. **Optional but recommended:** sideload-install it on the Windows
   machine and re-run the checklist from "What to actually check once
   it's installed" above, especially after any change touching
   packaging, dependencies, or the Qt/QtWebEngine setup.
4. **In Partner Center (Linux or Windows browser):** open the OpenSAK
   product → Packages → remove the old `.msix` → upload the new one →
   confirm it shows "Validated"/"Complete".
5. Review the other submission sections in case anything needs
   updating for this release (e.g. "What's new in this version" release
   notes) — most sections should carry over unchanged from the last
   submission.
6. **Submit for certification.** Wait for the pass/fail email.

**Option B — local (Windows machine), if CI isn't available:**

1. **On Windows:** pull the latest branch/tag, then rebuild with the
   real (not dev-test) identity values:
   ```powershell
   git fetch origin <branch>
   git checkout <branch>
   git pull
   .\packaging\msix\build_msix_local.ps1 `
       -IdentityName "AgreeDK.OpenSAK" `
       -PublisherCN  "CN=12DE14F4-8896-42D3-BB58-EA4A95758F5C" `
       -PublisherDisplayName "AgreeDK" `
       -Version "<new 4-part version>"
   ```
   (Don't add `-InstallAfterBuild` unless you specifically want to
   sideload-test this build — the file you need for Partner Center is
   the same either way.)
2. Then continue from step 3 of Option A above (sideload check →
   Partner Center upload → submit).

Either way, Partner Center submission itself (upload, listing fields,
Submit for certification) stays a manual browser step — no plan to
adopt the Store Submission API for that yet. `build-msix.yml` is
manual-`workflow_dispatch`-only and doesn't run on version tags, so it
won't attach a `.msix` to public GitHub Releases; that stays out of
scope until the Store listing goes public.

## Files in this directory

| File | Purpose |
|---|---|
| `AppxManifest.xml.template` | Manifest template — see comments inside for each placeholder |
| `build_msix_local.ps1` | One-shot local build → package → sign → (optionally) install |
| `generate_assets.py` | Generates the required tile/logo PNGs from `assets/icons/opensak_512.png` |
| `assets/` | Generated image output (gitignored — regenerated by `generate_assets.py`) |

Also relevant, outside this directory:

| File | Purpose |
|---|---|
| `../../scripts/derive_msix_version.py` | Converts `__version__` to the 4-part numeric MSIX version — used by `build-msix.yml` |
| `../../.github/workflows/build-msix.yml` | CI job (manual `workflow_dispatch` only) that builds/packages/self-signs the `.msix` and uploads it as a CI artifact |
