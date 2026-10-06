"""Download the Xray-core binary (standalone: used by the Dockerfile and by the dashboard).

Usage:  python app/xray_get.py /usr/local/bin [zip-url]
"""
from __future__ import annotations

import io
import os
import platform
import sys
import time
import urllib.request
import zipfile

RELEASES = "https://github.com/XTLS/Xray-core/releases/latest/download"
ARCH = {"x86_64": "64", "amd64": "64", "aarch64": "arm64-v8a", "arm64": "arm64-v8a", "armv7l": "arm32-v7a",
        "i386": "32", "i686": "32"}


def default_url() -> str:
    arch = ARCH.get(platform.machine().lower(), "64")
    return f"{RELEASES}/Xray-linux-{arch}.zip"


def extract(data: bytes, dest: str) -> str:
    """Put xray (+ geoip/geosite) from a release zip — or a bare xray binary — into `dest`."""
    os.makedirs(dest, exist_ok=True)
    path = os.path.join(dest, "xray")
    if data.startswith(b"\x7fELF"):
        files = {"xray": data}
    else:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                files = {n: z.read(n) for n in ("xray", "geoip.dat", "geosite.dat") if n in z.namelist()}
        except zipfile.BadZipFile as e:
            raise ValueError("not a zip file or an xray binary") from e
        if "xray" not in files:
            raise ValueError("no xray binary inside the zip (use Xray-linux-64.zip)")
    for name, content in files.items():
        with open(os.path.join(dest, name), "wb") as f:
            f.write(content)
    os.chmod(path, 0o755)
    return path


def install(dest: str, url: str = "", proxy: str = "", timeout: float = 90) -> str:
    """Download the release zip into `dest`. Gives up after `timeout` seconds in total (slow links in Iran)."""
    url = url or default_url()
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
    deadline = time.monotonic() + timeout
    buf = io.BytesIO()
    with urllib.request.build_opener(*handlers).open(url, timeout=20) as r:
        while chunk := r.read1(64 * 1024):
            buf.write(chunk)
            if time.monotonic() > deadline:
                raise TimeoutError(f"download too slow ({buf.tell() // 1024} KB in {int(timeout)} s)")
    return extract(buf.getvalue(), dest)


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "/usr/local/bin"
    try:
        print("xray installed:", install(target, sys.argv[2] if len(sys.argv) > 2 else ""))
    except Exception as e:  # noqa: BLE001 - the build must not fail; it can be installed later from the dashboard
        print(f"!! xray download failed ({e}). Install it later from the dashboard settings page.")
