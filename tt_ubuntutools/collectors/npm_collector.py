"""Gói npm cài toàn cục (npm install -g)."""

from __future__ import annotations

import datetime as dt
import json
import os

from ..desktop_index import DesktopIndex
from ..models import SoftwareItem, Source
from ..utils import dir_size, run, which
from .base import Collector

BUNDLED = {"npm", "corepack"}  # đi kèm Node.js, không gỡ riêng


def parse_npm_ls(text: str) -> dict[str, str]:
    try:
        data = json.loads(text or "{}")
    except ValueError:
        return {}
    deps = data.get("dependencies") or {}
    return {name: (info or {}).get("version", "") for name, info in deps.items()}


class NpmCollector(Collector):
    name = "npm"
    label = "npm"

    def available(self) -> bool:
        return which("npm") is not None

    def collect(self, index: DesktopIndex) -> list[SoftwareItem]:
        npm = which("npm") or "npm"
        npm_real = os.path.realpath(npm)
        node = os.path.join(os.path.dirname(npm), "node")
        if not os.access(node, os.X_OK):
            node = which("node") or ""
        prefix = run([npm, "prefix", "-g"], timeout=30).stdout.strip()
        root = run([npm, "root", "-g"], timeout=30).stdout.strip() or os.path.join(prefix, "lib/node_modules")
        ls = run([npm, "ls", "-g", "--json", "--depth=0"], timeout=60)
        try:
            json.loads(ls.stdout or "")
        except ValueError:
            raise RuntimeError("npm ls -g không trả về JSON hợp lệ: " + (ls.stderr or "").strip()[-200:])
        pkgs = parse_npm_ls(ls.stdout)
        unsupported = ""
        if npm_real.startswith("/snap/") or npm.startswith("/snap/"):
            unsupported = "npm cài bằng snap — hãy gỡ gói này bằng lệnh npm trong terminal."
        elif os.path.normpath(root) != os.path.join(prefix, "lib", "node_modules"):
            unsupported = "Cấu trúc thư mục npm toàn cục không chuẩn — công cụ không tự gỡ."
        writable = os.access(root, os.W_OK)
        items = []
        for name, version in sorted(pkgs.items()):
            path = os.path.join(root, name)
            try:
                date = dt.datetime.fromtimestamp(os.stat(path).st_mtime)
            except OSError:
                date = None
            summary = ""
            try:
                with open(os.path.join(path, "package.json"), encoding="utf-8") as fh:
                    summary = json.load(fh).get("description", "") or ""
            except (OSError, ValueError):
                pass
            items.append(SoftwareItem(
                source=Source.NPM,
                id=name,
                name=name,
                version=version,
                size=dir_size(path),
                install_date=date,
                origin=f"npm toàn cục ({root})",
                summary=summary,
                icon="text-x-script",
                manual=True,
                path=path,
                protected_reason="Đi kèm Node.js — gỡ cùng Node.js." if name in BUNDLED else unsupported,
                extra={"needs_root": not writable, "prefix": prefix, "npm": npm, "npm_real": npm_real,
                       "node": node},
            ))
        return items
