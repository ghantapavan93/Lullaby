"""The security headers every page and asset is served with, by both hosts:
Vercel's static bundle (deploy/vercel/build.py writes them into vercel.json)
and the review gateway (a middleware in gateway.py). One definition, so the
two can't drift.

The Content-Security-Policy allows no inline script except the few small ones
in the pages themselves, each by its SHA-256 hash. Styles keep
'unsafe-inline': the UI sets a handful of style attributes, and styles can't
run code. Nothing may frame the pages.
"""
from __future__ import annotations

import hashlib
import re
from base64 import b64encode
from pathlib import Path

DISCLOSURE = "Independent engineering prototype. Synthetic child records. Simulated payments. Not affiliated with Nanit."
INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S)


def script_hashes(pages: list[Path]) -> list[str]:
    """CSP source expressions for every inline <script> in these pages."""
    found = []
    for page in pages:
        if page.is_file():
            for source in INLINE_SCRIPT.findall(page.read_text(encoding="utf-8")):
                digest = "'sha256-" + b64encode(hashlib.sha256(source.encode()).digest()).decode() + "'"
                if digest not in found:
                    found.append(digest)
    return found


def security_headers(pages: list[Path]) -> dict[str, str]:
    csp = "; ".join([
        "default-src 'self'",
        "script-src " + " ".join(["'self'", *script_hashes(pages)]),
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src https://fonts.gstatic.com",
        "img-src 'self' data:",
        "media-src 'self'",
        "connect-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "object-src 'none'",
    ])
    return {
        "Content-Security-Policy": csp,
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "same-origin",
        "X-Robots-Tag": "noindex",
        "X-Prototype-Disclosure": DISCLOSURE,
    }
