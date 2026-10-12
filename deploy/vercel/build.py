"""Build the static frontend for Vercel.

    python deploy/vercel/build.py --backend https://203-0-113-7.sslip.io

Output (deploy/vercel/out/):
- /            the landing page (deploy/landing/index.html)
- /app         the prototype (proof-layer/static/index.html)
- /static/*    the app's own CSS, JS and icons, unchanged
- vercel.json  rewrites /v1/*, /architecture* and /healthz to the backend

The browser only ever talks to the Vercel origin, so the gateway's workspace
cookie, the media link cookie (path-scoped, SameSite=Strict) and video Range
requests work exactly as they do on one host. No framework, no build step for
the app itself.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STATIC = REPO / "proof-layer" / "static"
OUT = Path(__file__).with_name("out")


def build(backend: str, out: Path = OUT) -> Path:
    backend = backend.rstrip("/")
    if not backend.startswith("https://"):
        raise SystemExit("the backend must be an https:// origin")
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(STATIC, out / "static")
    shutil.copy(REPO / "deploy" / "landing" / "index.html", out / "index.html")
    shutil.copy(STATIC / "index.html", out / "app.html")
    for page in ("privacy.html", "404.html"):
        if (STATIC / page).exists():
            shutil.copy(STATIC / page, out / page)
    config = {
        "cleanUrls": True,
        "trailingSlash": False,
        "rewrites": [
            {"source": "/v1/:path*", "destination": f"{backend}/v1/:path*"},
            {"source": "/architecture", "destination": f"{backend}/architecture"},
            {"source": "/architecture/:path*", "destination": f"{backend}/architecture/:path*"},
            {"source": "/healthz", "destination": f"{backend}/healthz"},
        ],
        "headers": [
            {"source": "/(.*)", "headers": [
                {"key": "X-Robots-Tag", "value": "noindex"},
                {"key": "X-Content-Type-Options", "value": "nosniff"},
                {"key": "Referrer-Policy", "value": "same-origin"},
                {"key": "X-Prototype-Disclosure", "value": "Independent engineering prototype. Synthetic child "
                                                           "records. Simulated payments. Not affiliated with Nanit."}]},
            {"source": "/static/(.*)", "headers": [{"key": "Cache-Control", "value": "public, max-age=300"}]},
        ],
    }
    (out / "vercel.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", required=True)
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    print(build(a.backend, Path(a.out)))
