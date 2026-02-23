---
name: asc-screenshot-uploader
description: Upload screenshots to App Store Connect by language and device size. Supports any iOS/iPadOS app with multi-locale screenshot directories.
---

# App Store Connect Screenshot Uploader

Upload app screenshots to App Store Connect via the REST API v2. Handles multiple device types, languages, and automatically manages screenshot sets.

## Prerequisites

1. **App Store Connect API Key**: Create one at https://appstoreconnect.apple.com/access/integrations/api
   - Role: Admin or App Manager
   - Download the `.p8` file and note the Key ID and Issuer ID
2. **App version must already exist** in App Store Connect (draft or editable state)
3. Python 3.10+ (uses stdlib only, no pip install needed)

## Screenshot Directory Structure

The uploader expects this layout:

```
{base}/screenshots/
├── {device-type}/
│   ├── {locale}/
│   │   ├── screenshot-1.png
│   │   ├── screenshot-2.png
│   │   └── screenshot-3.png
│   └── {locale}/
│       └── ...
└── {device-type}/          # No locale subdir = shared across all locales
    ├── screenshot-1.png
    └── screenshot-2.png
```

### Supported Device Types

| Directory Name    | ASC Display Type           | Description                    |
|-------------------|----------------------------|--------------------------------|
| `iphone-6.9`     | APP_IPHONE_67              | iPhone 16 Pro Max              |
| `iphone-6.7`     | APP_IPHONE_67              | iPhone 15 Pro Max / 16 Plus    |
| `iphone-6.5`     | APP_IPHONE_65              | iPhone 11 Pro Max / XS Max     |
| `iphone-5.5`     | APP_IPHONE_55              | iPhone 8 Plus / 7 Plus         |
| `ipad`           | APP_IPAD_PRO_3GEN_129      | iPad Pro 12.9" (3rd gen+)      |
| `ipad-13`        | APP_IPAD_PRO_3GEN_129      | iPad Pro 13"                   |
| `ipad-pro-11`    | APP_IPAD_PRO_3GEN_11       | iPad Pro 11"                   |
| `watch-ultra`    | APP_WATCH_ULTRA             | Apple Watch Ultra               |
| `appletv`        | APP_APPLE_TV                | Apple TV                        |
| `mac`            | APP_DESKTOP                 | macOS                           |

### Supported Locales

Use standard Apple locale codes: `en-US`, `zh-Hans`, `zh-Hant`, `ja`, `ko`, `fr-FR`, `de-DE`, `es-ES`, `pt-BR`, `hi-IN`, `id`, etc.

## Configuration

Set these environment variables or pass as CLI flags:

```bash
export ASC_KEY_ID="YOUR_KEY_ID"
export ASC_ISSUER_ID="YOUR_ISSUER_ID"
export ASC_KEY_FILE="/path/to/AuthKey_XXXXX.p8"
export ASC_APP_ID="123456789"  # Your app's Apple ID
```

## Usage

### How to invoke

When the user asks to upload screenshots to App Store Connect, run the Python script located at `~/.claude/skills/asc-screenshot-uploader/upload_screenshots.py`.

### Step 1: Dry Run (always do this first)

```bash
python3 ~/.claude/skills/asc-screenshot-uploader/upload_screenshots.py \
  --version "1.0" \
  --screenshot-dir /path/to/store_metadata/1.0/screenshots \
  --dry-run
```

This previews what would be uploaded without making any API calls.

### Step 2: Upload

```bash
python3 ~/.claude/skills/asc-screenshot-uploader/upload_screenshots.py \
  --key-id "$ASC_KEY_ID" \
  --issuer-id "$ASC_ISSUER_ID" \
  --key-file "$ASC_KEY_FILE" \
  --app-id "$ASC_APP_ID" \
  --version "1.0" \
  --screenshot-dir /path/to/store_metadata/1.0/screenshots
```

### Common Options

```bash
# Upload only specific locales
--locales en-US,zh-Hans,ja

# Upload only specific devices
--devices iphone-6.9,ipad

# Delete existing screenshots before uploading new ones
--delete-existing

# Combine options
--locales en-US --devices iphone-6.9 --delete-existing
```

## Workflow

When the user asks to upload screenshots:

1. **Confirm the screenshot directory** and scan it with `--dry-run` to show what will be uploaded
2. **Check environment variables** are set (`ASC_KEY_ID`, `ASC_ISSUER_ID`, `ASC_KEY_FILE`, `ASC_APP_ID`)
3. **Ask if they want to delete existing** screenshots first (`--delete-existing`)
4. **Ask if they want to filter** by specific locales or devices
5. **Run the upload** and report results

## Troubleshooting

- **"Version not found"**: The version must exist in App Store Connect in an editable state (Prepare for Submission)
- **"401 Unauthorized"**: Check API key permissions (needs Admin or App Manager role)
- **"409 Conflict"**: Screenshot set already has maximum screenshots (10). Use `--delete-existing` to replace
- **Processing timeout**: Large images may take longer. The script waits up to 60 seconds per image
- **Wrong display type**: Check image dimensions match the expected device size

## Script Location

`~/.claude/skills/asc-screenshot-uploader/upload_screenshots.py`

- Pure Python 3.10+ (no external dependencies)
- JWT signing implemented with stdlib (no PyJWT needed)
- Supports PNG and JPEG formats
