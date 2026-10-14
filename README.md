# Filttr Supervise (Windows)

Staff tool that marks a **USB-connected iPhone** as **supervised** for organization **Filttr** and points it at **Filttr MDM enrollment**, without wiping user data.

Built with **Python 3**, **pymobiledevice3**, and **tkinter** only (open-source). It does **not** bundle or copy any third-party commercial supervision app. Only the Filttr **public** supervisor certificate is included under `assets/` — never ship a `.key`.

## What it does

1. Reads device info over USB (name, UDID, serial, iOS, pairing/trust).
2. Builds a `CloudConfigurationDetails.plist` matching the key structure of a real supervised phone (`OrganizationName=Filttr`, `IsSupervised=true`, SkipSetup list, supervisor cert DER, etc.).
3. Applies it by:
   - **MCInstall** `SetCloudConfiguration` when the device accepts it, or
   - restoring a **minimal MobileBackup2** package that contains only that plist under  
     `SysSharedContainerDomain-systemgroup.com.apple.configurationprofiles/Library/ConfigurationProfiles/`.
4. Optionally shows / installs the MDM enrollment URL after reboot.
5. POSTs device info to Filttr (`api_base` + `complete_path`) — network errors are non-fatal.


## Does not erase the phone

Filttr Supervise **never** factory-resets / obliterates the iPhone and **never** calls MCInstall `SetCloudConfiguration` (which can require erase to replace an existing cloud config).

**Exact method (same class of approach as USB Configurator-style sparse inject):**

1. Build a **minimal MobileBackup2** package containing only  
   `SysSharedContainerDomain-systemgroup.com.apple.configurationprofiles/Library/ConfigurationProfiles/CloudConfigurationDetails.plist`
2. Call `Mobilebackup2Service.restore` with:
   - `system=True` — apply system-domain files **present in our sparse package** (one plist), not an IPSW / full system image
   - `settings=True` — `RestorePreserveSettings` (keep user settings)
   - `remove=False` — do not delete other items on the device
   - `copy=False`, `reboot=False`, `skip_apps=True`
3. Soft-reboot via `DiagnosticsService.restart` (restart only — not erase)

Hard guards refuse to run if Find My is on or the device is not Activated. A runtime wrapper blocks any `erase_device` call.

## Windows prep

1. Install **Apple Devices** (Microsoft Store) or **iTunes** so USB drivers / Apple Mobile Device Support are present.
2. On the iPhone:
   - **Find My** → off  
   - **iCloud Backup** → off (recommended)  
   - Unlock the phone and tap **Trust** this computer  
3. Copy `config.json` next to `FilttrSupervise.exe` (or edit the project root copy) and fill in values below.
4. Run `FilttrSupervise.exe` (GUI) or see CLI notes.

## config.json (owner must fill)

| Key | Default | Notes |
|-----|---------|--------|
| `api_base` | `https://www.filttr.org` | Filttr API origin |
| `access_key` | `""` | **Required for Supervise** — portal user key (`k`); also enterable in the GUI |
| `enrollment_url_template` | `https://www.filttr.org/api/mdm-enroll?k={access_key}&cfg=1` | Expanded into `ConfigurationURL` (USB / supervised, non-removable MDM via `cfg=1`) |
| `enrollment_url` | `""` | Optional static override / display URL |
| `configuration_url` | `""` | Optional explicit `ConfigurationURL` (wins over template) |
| `configuration_url_template` | `""` | Optional alternate template (`{udid}` / `{access_key}`) |
| `organization_name` | `Filttr` | Cloud-config org name (profile branding on server is "Filttr Kosher Filters") |
| `is_mdm_unremovable` | `true` | Mirrored into the cloud-config plist |
| `api_token` | `""` | Optional Bearer token for complete / lookup APIs |
| `complete_path` | `/api/supervision/complete` | POST path after success |
| `supervisor_cert` | `assets/filttr-supervisor.der` | Public cert only |
| `method` | `backup_restore` | Sparse MobileBackup2 only (MCInstall/erase removed) |

MDM enrollment is served by `GET /api/mdm-enroll?k=<access_key>` (`.mobileconfig`). Append `&cfg=1` for supervised/USB installs so `PayloadRemovalDisallowed=true`. Without `cfg=1`, enrollment stays removable (OTA on unsupervised devices). Check-in URLs are tagged with `?eu=<k>` server-side.


## Unlock (no pasted tool tokens)

On launch the app tries a silent `POST /api/admin-2fa` `{action:"check"}` using the trusted-device file:

- Windows: `%APPDATA%/FilttrSupervise/device.json`
- Linux: `~/.config/FilttrSupervise/device.json`

Otherwise use **Unlock…**:

- **Admin code** → `{action:"checkcode", email, code}` → session `token` (~8h)
- **Email me a code** → `{action:"send", …, source:"Windows supervisor tool"}` then `{action:"verify", trust:true}` → session token + optional `device` saved locally (60 days)

Session token is kept in memory and sent as `x-tool-token` to `/api/search-users` and `/api/lookup`. Optional `tool_token` in `config.json` remains an override only. **Sign out / untrust this PC** calls `{action:"untrust"}` and deletes the local device file.

Default admin email prefill: `abbrachfeld@gmail.com` (editable).

## User search (Windows GUI)

1. Put an unlocked **tool token** in `config.json` as `tool_token` (or `api_token`).
2. In the GUI, type a **name** and click **Search**.
3. The app calls `GET {api_base}/api/search-users?q=…` with header `x-tool-token` (also `?token=`).
4. Response shape: `{ "ok": true, "users": [ { "userId", "name", "accessKey" } ] }` (max 25).
5. Selecting a row sets `access_key` from `accessKey`, shows **name + userId**, and builds  
   `https://www.filttr.org/api/mdm-enroll?k={accessKey}&cfg=1` as `ConfigurationURL`.
6. **Supervise** stays disabled until a user is selected or an access key is pasted (fallback).
7. After success, POSTs `/api/supervision/complete` (and best-effort `/api/supervision/assign`) with udid + user. MDM check-in URLs are also tagged `?eu=<k>` by `/api/mdm-enroll`.

If search returns **401**, unlock the tool in Filttr admin (Firestore `tooltokens`).

## Developer / CLI

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
python -m pytest -q
python -m filttr_supervise --dry-run .\out-backup
python -m filttr_supervise --info
python -m filttr_supervise --supervise
python -m filttr_supervise --gui
```

Dry-run builds the backup folder **without** a device (useful for CI / inspection).

## Remove supervision

The **Remove supervision** button writes a cloud-configuration payload with `IsSupervised=false` (same restore / MCInstall paths). This is **best-effort** and may not fully clear MDM enrollment profiles; verify on a real iPhone.

## Untested / needs a real iPhone

- End-to-end USB supervise and reboot  
- Whether MCInstall alone works on a given iOS version vs backup restore  
- Find My / passcode edge cases during restore  
- Automatic `.mobileconfig` install from `enrollment_url`  
- Remove-supervision effectiveness after MDM has already enrolled  
- POST `/api/supervision/complete` against production (endpoint may still need to be deployed)  
- Windows driver quirks with Apple Devices vs iTunes  

## Build (GitHub Actions)

Workflow `.github/workflows/build.yml` runs on `windows-latest`, executes pytest, and uploads `FilttrSupervise.exe` from PyInstaller (`FilttrSupervise.spec`).

## Safety

- Never commit or copy `*.key` files into this project or the exe.  
- Public cert files in `assets/` are intentional.  
- Do not reverse-engineer or redistribute commercial supervision binaries.
