"""Download the Xray-core binary (standalone: used by the Dockerfile and by the dashboard).

Usage:  python app/xray_get.py /usr/local/bin [zip-url]
"""
from __future__ import annotations

import io
import os
import platform
import sys
import urllib.request
import zipfile

RELEASES = "https://github.com/XTLS/Xray-core/releases/latest/download"
ARCH = {"x86_64": "64", "amd64": "64", "aarch64": "arm64-v8a", "arm64": "arm64-v8a", "armv7l": "arm32-v7a",
        "i386": "32", "i686": "32"}


def default_url() -> str:
    arch = ARCH.get(platform.machine().lower(), "64")
    return f"{RELEASES}/Xray-linux-{arch}.zip"


def install(dest: str, url: str = "", proxy: str = "", timeout: float = 120) -> str:
    """Download the release zip and extract xray (+ geoip/geosite) into `dest`. Returns the binary path."""
    url = url or default_url()
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
    with urllib.request.build_opener(*handlers).open(url, timeout=timeout) as r:
        data = r.read()
    os.makedirs(dest, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in ("xray", "geoip.dat", "geosite.dat"):
            if name in z.namelist():
                with open(os.path.join(dest, name), "wb") as f:
                    f.write(z.read(name))
    path = os.path.join(dest, "xray")
    os.chmod(path, 0o755)
    return path


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "/usr/local/bin"
    try:
        print("xray installed:", install(target, sys.argv[2] if len(sys.argv) > 2 else ""))
    except Exception as e:  # noqa: BLE001 - the build must not fail; it can be installed later from the dashboard
        print(f"!! xray download failed ({e}). Install it later from the dashboard settings page.")
