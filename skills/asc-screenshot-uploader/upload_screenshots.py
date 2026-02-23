#!/usr/bin/env python3
"""
App Store Connect Screenshot Uploader

Uploads screenshots to App Store Connect via the REST API v2.
Supports multiple devices, languages, and apps.

Directory structure expected:
  {base_dir}/screenshots/{device_type}/{locale}/screenshot-*.png
  {base_dir}/screenshots/{device_type}/screenshot-*.png  (shared across all locales)

Usage:
  python3 upload_screenshots.py \
    --key-id YOUR_KEY_ID \
    --issuer-id YOUR_ISSUER_ID \
    --key-file /path/to/AuthKey_XXXXX.p8 \
    --app-id 123456789 \
    --version "1.0" \
    --screenshot-dir /path/to/store_metadata/1.0/screenshots \
    [--locales en-US,zh-Hans,ja] \
    [--devices iphone-6.9,ipad] \
    [--dry-run]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# ── JWT Generation (no external dependencies) ─────────────────────────

def _b64url(data: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _int_to_bytes(n: int, length: int) -> bytes:
    return n.to_bytes(length, byteorder="big")


def _decode_pem_ec_key(pem_text: str):
    """Extract raw EC private key (d, x, y) from PKCS#8 PEM for P-256."""
    import base64
    lines = [l.strip() for l in pem_text.strip().splitlines()
             if not l.strip().startswith("-----")]
    der = base64.b64decode("".join(lines))
    # For ES256 (P-256) PKCS#8: the private key octet string is 32 bytes
    # We need to find the OID for P-256 and extract d
    # Simple approach: find the 32-byte private key value
    # In PKCS#8, after the algorithm identifier, there's an OCTET STRING
    # containing the ECPrivateKey structure
    # Look for the pattern: 04 20 (32 bytes of private key)
    idx = der.find(b'\x04\x20', 10)
    if idx == -1:
        # Try alternate: sometimes it's 04 21 00 + 32 bytes
        idx = der.find(b'\x04\x21\x00', 10)
        if idx != -1:
            d = int.from_bytes(der[idx+3:idx+35], "big")
        else:
            raise ValueError("Cannot parse EC private key from PEM")
    else:
        d = int.from_bytes(der[idx+2:idx+34], "big")
    return d


# P-256 curve parameters
_P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
_A = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFC
_B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
_Gx = 0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296
_Gy = 0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5
_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551


def _modinv(a, m):
    if a < 0:
        a = a % m
    g, x, _ = _extended_gcd(a, m)
    if g != 1:
        raise ValueError("No inverse")
    return x % m


def _extended_gcd(a, b):
    if a == 0:
        return b, 0, 1
    g, x, y = _extended_gcd(b % a, a)
    return g, y - (b // a) * x, x


def _ec_add(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2 and y1 == y2:
        lam = (3 * x1 * x1 + _A) * _modinv(2 * y1, _P) % _P
    elif x1 == x2:
        return None
    else:
        lam = (y2 - y1) * _modinv(x2 - x1, _P) % _P
    x3 = (lam * lam - x1 - x2) % _P
    y3 = (lam * (x1 - x3) - y1) % _P
    return (x3, y3)


def _ec_mul(k, point):
    result = None
    addend = point
    while k:
        if k & 1:
            result = _ec_add(result, addend)
        addend = _ec_add(addend, addend)
        k >>= 1
    return result


def _sign_es256(private_key_d: int, message: bytes) -> bytes:
    """Sign with ES256 (ECDSA P-256 + SHA-256), pure Python."""
    digest = hashlib.sha256(message).digest()
    z = int.from_bytes(digest, "big")
    # Use deterministic k (RFC 6979 simplified)
    import hmac
    h = digest
    x_bytes = _int_to_bytes(private_key_d, 32)
    # RFC 6979
    v = b'\x01' * 32
    k_hmac = b'\x00' * 32
    k_hmac = hmac.new(k_hmac, v + b'\x00' + x_bytes + h, hashlib.sha256).digest()
    v = hmac.new(k_hmac, v, hashlib.sha256).digest()
    k_hmac = hmac.new(k_hmac, v + b'\x01' + x_bytes + h, hashlib.sha256).digest()
    v = hmac.new(k_hmac, v, hashlib.sha256).digest()

    while True:
        v = hmac.new(k_hmac, v, hashlib.sha256).digest()
        k = int.from_bytes(v, "big")
        if 1 <= k < _N:
            point = _ec_mul(k, (_Gx, _Gy))
            r = point[0] % _N
            if r == 0:
                continue
            s = (_modinv(k, _N) * (z + r * private_key_d)) % _N
            if s == 0:
                continue
            # Normalize s to low-S form
            if s > _N // 2:
                s = _N - s
            return _int_to_bytes(r, 32) + _int_to_bytes(s, 32)
        k_hmac = hmac.new(k_hmac, v + b'\x00', hashlib.sha256).digest()
        v = hmac.new(k_hmac, v, hashlib.sha256).digest()


def create_jwt(key_id: str, issuer_id: str, key_file: str) -> str:
    """Create a JWT for App Store Connect API authentication."""
    with open(key_file, "r") as f:
        pem = f.read()
    d = _decode_pem_ec_key(pem)

    now = int(time.time())
    header = {"alg": "ES256", "kid": key_id, "typ": "JWT"}
    payload = {
        "iss": issuer_id,
        "iat": now,
        "exp": now + 1200,  # 20 minutes
        "aud": "appstoreconnect-v1",
    }
    segments = _b64url(json.dumps(header).encode()) + "." + _b64url(json.dumps(payload).encode())
    sig = _sign_es256(d, segments.encode())
    return segments + "." + _b64url(sig)


# ── App Store Connect API Client ──────────────────────────────────────

BASE_URL = "https://api.appstoreconnect.apple.com/v1"


class ASCClient:
    def __init__(self, token: str, dry_run: bool = False):
        self.token = token
        self.dry_run = dry_run

    def _request(self, method: str, url: str, body=None, content_type="application/json") -> dict:
        if not url.startswith("http"):
            url = BASE_URL + url
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": content_type,
        }
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            error_body = e.read().decode()
            print(f"  API Error {e.code}: {error_body}", file=sys.stderr)
            raise

    def get(self, path: str, params: dict = None) -> dict:
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            path = f"{path}?{qs}"
        return self._request("GET", path)

    def post(self, path: str, body: dict) -> dict:
        return self._request("POST", path, body)

    def patch(self, path: str, body: dict) -> dict:
        return self._request("PATCH", path, body)

    def delete(self, path: str) -> dict:
        return self._request("DELETE", path)

    def upload_binary(self, url: str, data: bytes, content_type: str = "image/png"):
        """Upload binary data to the given URL."""
        headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(data)),
        }
        req = urllib.request.Request(url, data=data, headers=headers, method="PUT")
        with urllib.request.urlopen(req) as resp:
            return resp.read()

    # ── High-level API methods ────────────────────────────────────────

    def get_app(self, app_id: str) -> dict:
        return self.get(f"/apps/{app_id}")

    def get_app_store_version(self, app_id: str, version_string: str) -> str | None:
        """Find an appStoreVersion by version string. Returns version ID or None."""
        resp = self.get(f"/apps/{app_id}/appStoreVersions", {
            "filter[versionString]": version_string,
            "filter[platform]": "IOS",
        })
        versions = resp.get("data", [])
        if versions:
            return versions[0]["id"]
        return None

    def get_localizations(self, version_id: str) -> dict:
        """Get all localizations for a version. Returns {locale: localization_id}."""
        result = {}
        url = f"/appStoreVersions/{version_id}/appStoreVersionLocalizations"
        while url:
            resp = self.get(url)
            for loc in resp.get("data", []):
                locale = loc["attributes"]["locale"]
                result[locale] = loc["id"]
            url = resp.get("links", {}).get("next")
        return result

    def create_localization(self, version_id: str, locale: str) -> str:
        """Create a localization for a version. Returns localization ID."""
        body = {
            "data": {
                "type": "appStoreVersionLocalizations",
                "attributes": {"locale": locale},
                "relationships": {
                    "appStoreVersion": {
                        "data": {"type": "appStoreVersions", "id": version_id}
                    }
                }
            }
        }
        resp = self.post("/appStoreVersionLocalizations", body)
        return resp["data"]["id"]

    def get_screenshot_sets(self, localization_id: str) -> dict:
        """Get screenshot sets for a localization. Returns {display_type: set_id}."""
        result = {}
        url = f"/appStoreVersionLocalizations/{localization_id}/appScreenshotSets"
        while url:
            resp = self.get(url)
            for ss in resp.get("data", []):
                dt = ss["attributes"]["screenshotDisplayType"]
                result[dt] = ss["id"]
            url = resp.get("links", {}).get("next")
        return result

    def create_screenshot_set(self, localization_id: str, display_type: str) -> str:
        """Create a screenshot set. Returns set ID."""
        body = {
            "data": {
                "type": "appScreenshotSets",
                "attributes": {"screenshotDisplayType": display_type},
                "relationships": {
                    "appStoreVersionLocalization": {
                        "data": {"type": "appStoreVersionLocalizations", "id": localization_id}
                    }
                }
            }
        }
        resp = self.post("/appScreenshotSets", body)
        return resp["data"]["id"]

    def get_screenshots_in_set(self, set_id: str) -> list:
        """Get existing screenshots in a set."""
        resp = self.get(f"/appScreenshotSets/{set_id}/appScreenshots")
        return resp.get("data", [])

    def delete_screenshot(self, screenshot_id: str):
        """Delete a screenshot."""
        self.delete(f"/appScreenshots/{screenshot_id}")

    def reserve_screenshot(self, set_id: str, filename: str, file_size: int) -> dict:
        """Reserve a screenshot upload slot. Returns screenshot data with upload operations."""
        body = {
            "data": {
                "type": "appScreenshots",
                "attributes": {
                    "fileName": filename,
                    "fileSize": file_size,
                },
                "relationships": {
                    "appScreenshotSet": {
                        "data": {"type": "appScreenshotSets", "id": set_id}
                    }
                }
            }
        }
        return self.post("/appScreenshots", body)

    def commit_screenshot(self, screenshot_id: str, checksum: str):
        """Commit a screenshot after upload."""
        body = {
            "data": {
                "type": "appScreenshots",
                "id": screenshot_id,
                "attributes": {
                    "uploaded": True,
                    "sourceFileChecksum": checksum,
                }
            }
        }
        return self.patch(f"/appScreenshots/{screenshot_id}", body)

    def upload_screenshot(self, set_id: str, filepath: Path) -> str:
        """Full screenshot upload flow: reserve → upload parts → commit."""
        file_size = filepath.stat().st_size
        filename = filepath.name

        # 1. Reserve
        print(f"    Reserving upload slot for {filename}...")
        reserve_resp = self.reserve_screenshot(set_id, filename, file_size)
        screenshot_data = reserve_resp["data"]
        screenshot_id = screenshot_data["id"]
        upload_ops = screenshot_data["attributes"]["uploadOperations"]

        # 2. Upload binary parts
        file_data = filepath.read_bytes()
        for op in upload_ops:
            url = op["url"]
            offset = op["offset"]
            length = op["length"]
            headers_list = op["requestHeaders"]
            chunk = file_data[offset:offset + length]

            req = urllib.request.Request(url, data=chunk, method=op["method"])
            for h in headers_list:
                req.add_header(h["name"], h["value"])
            print(f"    Uploading part (offset={offset}, length={length})...")
            with urllib.request.urlopen(req) as resp:
                resp.read()

        # 3. Commit
        checksum = hashlib.md5(file_data).hexdigest()
        print(f"    Committing {filename} (md5={checksum})...")
        self.commit_screenshot(screenshot_id, checksum)

        # 4. Wait for processing
        for attempt in range(30):
            time.sleep(2)
            resp = self.get(f"/appScreenshots/{screenshot_id}")
            state = resp["data"]["attributes"]["assetDeliveryState"]["state"]
            if state == "COMPLETE":
                print(f"    ✓ {filename} uploaded successfully")
                return screenshot_id
            elif state == "FAILED":
                errors = resp["data"]["attributes"]["assetDeliveryState"].get("errors", [])
                print(f"    ✗ {filename} processing failed: {errors}", file=sys.stderr)
                return screenshot_id
            print(f"    Waiting for processing... ({state})")

        print(f"    ⚠ {filename} processing timed out", file=sys.stderr)
        return screenshot_id


# ── Device Type Mapping ───────────────────────────────────────────────

DEVICE_DISPLAY_TYPES = {
    # iPhone
    "iphone-6.9": "APP_IPHONE_67",       # iPhone 16 Pro Max (6.9")
    "iphone-6.7": "APP_IPHONE_67",       # iPhone 15 Pro Max / 16 Plus (6.7")
    "iphone-6.5": "APP_IPHONE_65",       # iPhone 11 Pro Max / XS Max
    "iphone-6.1": "APP_IPHONE_61",       # iPhone 14 / 15
    "iphone-5.8": "APP_IPHONE_58",       # iPhone X / XS / 11 Pro
    "iphone-5.5": "APP_IPHONE_55",       # iPhone 8 Plus / 7 Plus / 6s Plus
    # iPad
    "ipad-13": "APP_IPAD_PRO_3GEN_129",  # iPad Pro 13" / 12.9" 3rd gen+
    "ipad": "APP_IPAD_PRO_3GEN_129",     # Default iPad mapping
    "ipad-pro-129": "APP_IPAD_PRO_3GEN_129",
    "ipad-pro-11": "APP_IPAD_PRO_3GEN_11",
    "ipad-105": "APP_IPAD_105",
    "ipad-97": "APP_IPAD_97",
    # Apple Watch
    "watch-ultra": "APP_WATCH_ULTRA",
    "watch-series7": "APP_WATCH_SERIES_7",
    "watch-series4": "APP_WATCH_SERIES_4",
    # Apple TV
    "appletv": "APP_APPLE_TV",
    # Mac
    "mac": "APP_DESKTOP",
}

# Reverse: detect device type from image dimensions
DIMENSION_TO_DEVICE = {
    (1320, 2868): "APP_IPHONE_67",    # iPhone 16 Pro Max (6.9")
    (2868, 1320): "APP_IPHONE_67",
    (1290, 2796): "APP_IPHONE_67",    # iPhone 15 Pro Max (6.7")
    (2796, 1290): "APP_IPHONE_67",
    (1284, 2778): "APP_IPHONE_67",    # iPhone 14 Pro Max
    (2778, 1284): "APP_IPHONE_67",
    (1242, 2688): "APP_IPHONE_65",
    (2688, 1242): "APP_IPHONE_65",
    (1179, 2556): "APP_IPHONE_61",
    (2556, 1179): "APP_IPHONE_61",
    (1125, 2436): "APP_IPHONE_58",
    (2436, 1125): "APP_IPHONE_58",
    (1242, 2208): "APP_IPHONE_55",
    (2208, 1242): "APP_IPHONE_55",
    (2048, 2732): "APP_IPAD_PRO_3GEN_129",
    (2732, 2048): "APP_IPAD_PRO_3GEN_129",
    (2388, 1668): "APP_IPAD_PRO_3GEN_11",
    (1668, 2388): "APP_IPAD_PRO_3GEN_11",
}


def get_image_dimensions(filepath: Path) -> tuple[int, int]:
    """Read PNG dimensions from file header (no external deps needed)."""
    with open(filepath, "rb") as f:
        header = f.read(24)
        if header[:8] == b'\x89PNG\r\n\x1a\n':
            w, h = struct.unpack('>II', header[16:24])
            return (w, h)
        # JPEG
        f.seek(0)
        data = f.read()
        idx = 2
        while idx < len(data) - 1:
            if data[idx] != 0xFF:
                break
            marker = data[idx + 1]
            if marker in (0xC0, 0xC2):
                h = struct.unpack('>H', data[idx+5:idx+7])[0]
                w = struct.unpack('>H', data[idx+7:idx+9])[0]
                return (w, h)
            length = struct.unpack('>H', data[idx+2:idx+4])[0]
            idx += 2 + length
    raise ValueError(f"Cannot determine dimensions of {filepath}")


# ── Locale Mapping ────────────────────────────────────────────────────
# Map directory locale names to ASC locale codes when they differ.
# ASC uses specific codes; some common directory conventions differ.
LOCALE_DIR_TO_ASC = {
    "hi-IN": "hi",
    "hi_IN": "hi",
    "de": "de-DE",
    "es": "es-ES",
    "fr": "fr-FR",
    "pt": "pt-BR",
    "nl": "nl-NL",
}


def normalize_locale(dir_locale: str, existing_locales: dict) -> str:
    """Map a directory locale name to the ASC locale code."""
    # Direct match
    if dir_locale in existing_locales:
        return dir_locale
    # Explicit mapping
    if dir_locale in LOCALE_DIR_TO_ASC:
        return LOCALE_DIR_TO_ASC[dir_locale]
    return dir_locale


# ── Main Upload Logic ─────────────────────────────────────────────────

def discover_screenshots(screenshot_dir: Path) -> dict:
    """
    Scan directory and return structure:
    {device_folder: {locale: [sorted file paths]}}

    For device folders without locale subdirs, locale is "__shared__".
    """
    result = {}
    if not screenshot_dir.exists():
        print(f"Screenshot directory not found: {screenshot_dir}", file=sys.stderr)
        return result

    for device_dir in sorted(screenshot_dir.iterdir()):
        if not device_dir.is_dir():
            continue
        device_name = device_dir.name
        result[device_name] = {}

        # Check if there are locale subdirectories or direct files
        has_locale_dirs = any(d.is_dir() for d in device_dir.iterdir())
        has_direct_files = any(f.suffix.lower() in (".png", ".jpg", ".jpeg") for f in device_dir.iterdir() if f.is_file())

        if has_locale_dirs:
            for locale_dir in sorted(device_dir.iterdir()):
                if not locale_dir.is_dir():
                    continue
                files = sorted(
                    [f for f in locale_dir.iterdir() if f.suffix.lower() in (".png", ".jpg", ".jpeg")],
                    key=lambda f: _natural_sort_key(f.name)
                )
                if files:
                    result[device_name][locale_dir.name] = files

        if has_direct_files:
            files = sorted(
                [f for f in device_dir.iterdir() if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg")],
                key=lambda f: _natural_sort_key(f.name)
            )
            if files:
                result[device_name]["__shared__"] = files

    return result


def _natural_sort_key(name: str):
    """Sort screenshot-1.png, screenshot-2.png, ..., screenshot-10.png naturally."""
    import re
    parts = re.split(r'(\d+)', name)
    return [int(p) if p.isdigit() else p.lower() for p in parts]


def upload_all(
    client: ASCClient,
    app_id: str,
    version_string: str,
    screenshot_dir: Path,
    filter_locales: list[str] | None = None,
    filter_devices: list[str] | None = None,
    delete_existing: bool = False,
):
    """Main upload orchestrator."""
    # 1. Discover screenshots first (works offline)
    print(f"\nScanning screenshots in {screenshot_dir}...")
    discovered = discover_screenshots(screenshot_dir)

    if not discovered:
        print("No screenshots found!", file=sys.stderr)
        sys.exit(1)

    # Print summary
    total_files = 0
    for device, locales in discovered.items():
        for locale, files in locales.items():
            total_files += len(files)
            print(f"  {device}/{locale}: {len(files)} files")
    print(f"  Total: {total_files} screenshots\n")

    # In dry-run mode, just show what would be uploaded and exit
    if client.dry_run:
        for device_name, locales_map in discovered.items():
            if filter_devices and device_name not in filter_devices:
                print(f"[SKIP] Device: {device_name} (filtered out)")
                continue
            display_type = DEVICE_DISPLAY_TYPES.get(device_name, "AUTO_DETECT")
            print(f"── Device: {device_name} → {display_type} ──")
            for locale, files in locales_map.items():
                if locale != "__shared__" and filter_locales and locale not in filter_locales:
                    print(f"  [SKIP] {locale} (filtered out)")
                    continue
                label = "SHARED (all locales)" if locale == "__shared__" else locale
                print(f"  [{label}]")
                for filepath in files:
                    dims = get_image_dimensions(filepath)
                    print(f"    → {filepath.name} ({dims[0]}x{dims[1]})")
        print(f"\nDry run complete. {total_files} screenshots would be uploaded.")
        return

    # 2. Find the app store version
    print(f"Looking up version {version_string} for app {app_id}...")
    version_id = client.get_app_store_version(app_id, version_string)
    if not version_id:
        print(f"Error: Version '{version_string}' not found for app {app_id}.", file=sys.stderr)
        print("Make sure you have created this version in App Store Connect first.", file=sys.stderr)
        sys.exit(1)
    print(f"  Found version ID: {version_id}")

    # 3. Get existing localizations
    print("Fetching localizations...")
    localizations = client.get_localizations(version_id)
    print(f"  Found {len(localizations)} localizations: {', '.join(sorted(localizations.keys()))}")

    # 4. Process each device/locale combination
    stats = {"uploaded": 0, "skipped": 0, "failed": 0, "deleted": 0}

    for device_name, locales_map in discovered.items():
        # Filter devices
        if filter_devices and device_name not in filter_devices:
            print(f"Skipping device {device_name} (filtered out)")
            continue

        # Resolve display type
        display_type = DEVICE_DISPLAY_TYPES.get(device_name)
        if not display_type:
            # Try to detect from first image dimensions
            first_file = next(iter(next(iter(locales_map.values()))))
            dims = get_image_dimensions(first_file)
            display_type = DIMENSION_TO_DEVICE.get(dims)
            if not display_type:
                print(f"  Warning: Unknown device '{device_name}' with dimensions {dims}, skipping", file=sys.stderr)
                continue

        print(f"── Device: {device_name} → {display_type} ──")

        for locale, files in locales_map.items():
            # Determine target locales
            if locale == "__shared__":
                # Shared screenshots: upload to all localizations
                target_locales = list(localizations.keys())
                if filter_locales:
                    target_locales = [l for l in target_locales if l in filter_locales]
                print(f"  Shared screenshots → uploading to {len(target_locales)} locales")
            else:
                # Normalize locale name to ASC format
                asc_locale = normalize_locale(locale, localizations)
                if filter_locales and locale not in filter_locales and asc_locale not in filter_locales:
                    print(f"  Skipping locale {locale} (filtered out)")
                    continue
                if asc_locale != locale:
                    print(f"  Mapping locale: {locale} → {asc_locale}")
                target_locales = [asc_locale]

            for target_locale in target_locales:
                print(f"\n  [{device_name}/{target_locale}] {len(files)} screenshots")

                # Ensure localization exists
                if target_locale not in localizations:
                    print(f"    Creating localization for {target_locale}...")
                    try:
                        loc_id = client.create_localization(version_id, target_locale)
                        localizations[target_locale] = loc_id
                    except Exception as e:
                        print(f"    ✗ Failed to create localization {target_locale}: {e}", file=sys.stderr)
                        stats["failed"] += len(files)
                        continue

                loc_id = localizations[target_locale]

                # Get or create screenshot set
                sets = client.get_screenshot_sets(loc_id)
                set_id = sets.get(display_type)

                if set_id and delete_existing:
                    existing = client.get_screenshots_in_set(set_id)
                    for ss in existing:
                        print(f"    Deleting existing: {ss['attributes']['fileName']}")
                        client.delete_screenshot(ss["id"])
                        stats["deleted"] += 1
                        time.sleep(0.5)

                if not set_id:
                    print(f"    Creating screenshot set for {display_type}...")
                    set_id = client.create_screenshot_set(loc_id, display_type)

                # Upload each file
                for filepath in files:
                    try:
                        client.upload_screenshot(set_id, filepath)
                        stats["uploaded"] += 1
                        time.sleep(1)  # Rate limiting
                    except Exception as e:
                        print(f"    ✗ Failed to upload {filepath.name}: {e}", file=sys.stderr)
                        stats["failed"] += 1

    # Summary
    print(f"\n{'='*50}")
    print(f"Upload complete!")
    print(f"  Uploaded: {stats['uploaded']}")
    print(f"  Deleted:  {stats['deleted']}")
    print(f"  Skipped:  {stats['skipped']}")
    print(f"  Failed:   {stats['failed']}")


# ── CLI ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Upload screenshots to App Store Connect",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run to preview what would be uploaded
  python3 upload_screenshots.py \\
    --key-id ABC123 --issuer-id DEF456 --key-file AuthKey.p8 \\
    --app-id 123456789 --version 1.0 \\
    --screenshot-dir ./store_metadata/1.0/screenshots --dry-run

  # Upload only specific locales
  python3 upload_screenshots.py \\
    --key-id ABC123 --issuer-id DEF456 --key-file AuthKey.p8 \\
    --app-id 123456789 --version 1.0 \\
    --screenshot-dir ./store_metadata/1.0/screenshots \\
    --locales en-US,zh-Hans,ja

  # Upload and replace existing screenshots
  python3 upload_screenshots.py \\
    --key-id ABC123 --issuer-id DEF456 --key-file AuthKey.p8 \\
    --app-id 123456789 --version 1.0 \\
    --screenshot-dir ./store_metadata/1.0/screenshots \\
    --delete-existing

Environment variables (alternative to flags):
  ASC_KEY_ID, ASC_ISSUER_ID, ASC_KEY_FILE, ASC_APP_ID
        """,
    )
    parser.add_argument("--key-id", default=os.environ.get("ASC_KEY_ID"), help="API Key ID")
    parser.add_argument("--issuer-id", default=os.environ.get("ASC_ISSUER_ID"), help="Issuer ID")
    parser.add_argument("--key-file", default=os.environ.get("ASC_KEY_FILE"), help="Path to .p8 key file")
    parser.add_argument("--app-id", default=os.environ.get("ASC_APP_ID"), help="App Store app ID")
    parser.add_argument("--version", required=True, help="Version string (e.g. 1.0)")
    parser.add_argument("--screenshot-dir", required=True, help="Path to screenshots directory")
    parser.add_argument("--locales", help="Comma-separated list of locales to upload (default: all)")
    parser.add_argument("--devices", help="Comma-separated list of device types to upload (default: all)")
    parser.add_argument("--delete-existing", action="store_true", help="Delete existing screenshots before uploading")
    parser.add_argument("--dry-run", action="store_true", help="Preview what would be uploaded without making changes")

    args = parser.parse_args()

    # Validate required args
    if not args.dry_run:
        missing = []
        if not args.key_id:
            missing.append("--key-id or ASC_KEY_ID")
        if not args.issuer_id:
            missing.append("--issuer-id or ASC_ISSUER_ID")
        if not args.key_file:
            missing.append("--key-file or ASC_KEY_FILE")
        if not args.app_id:
            missing.append("--app-id or ASC_APP_ID")
        if missing:
            print(f"Error: Missing required parameters: {', '.join(missing)}", file=sys.stderr)
            print("Use --dry-run to preview without authentication.", file=sys.stderr)
            sys.exit(1)

    screenshot_dir = Path(args.screenshot_dir).expanduser().resolve()
    if not screenshot_dir.exists():
        print(f"Error: Screenshot directory not found: {screenshot_dir}", file=sys.stderr)
        sys.exit(1)

    filter_locales = args.locales.split(",") if args.locales else None
    filter_devices = args.devices.split(",") if args.devices else None

    if args.dry_run:
        print("=" * 50)
        print("DRY RUN MODE - No changes will be made")
        print("=" * 50)
        # Create a dummy client for dry run
        client = ASCClient("", dry_run=True)
    else:
        print("Authenticating with App Store Connect...")
        token = create_jwt(args.key_id, args.issuer_id, args.key_file)
        client = ASCClient(token)
        print("  ✓ JWT created")

    upload_all(
        client=client,
        app_id=args.app_id or "DRY_RUN",
        version_string=args.version,
        screenshot_dir=screenshot_dir,
        filter_locales=filter_locales,
        filter_devices=filter_devices,
        delete_existing=args.delete_existing,
    )


if __name__ == "__main__":
    main()
