"""tests/macro_index_data.py — a small OpenSAK-Macros index (#1342) and a fake
fetcher serving its files, shared by the macro repository tests."""

import hashlib
import json

from opensak.macro import repository as repo

RAW = "https://raw.githubusercontent.com/OpenSAK-Org/OpenSAK-Macros"

V1 = b"-- OpenSAK Macro\r\n-- Name: Roadsheet\r\n-- Version: 1.0\r\nprint(1)\r\n"
V2 = b"-- OpenSAK Macro\r\n-- Name: Roadsheet\r\n-- Version: 1.1\r\nprint(2)\r\n"
README = b"# Roadsheet\n"
PNG = b"\x89PNG fake"
GONE = b"-- OpenSAK Macro\n-- Name: Old one\nprint('old')\n"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file(path: str, data: bytes, commit: str = "c2") -> dict:
    return {"path": path, "size": len(data), "sha256": _sha(data),
            "url": f"{RAW}/{commit}/{path}"}


def _version(version: str, data: bytes, api: int = 4, commit: str = "c") -> dict:
    return {"version": version, "published": "2026-11-17T00:00:00Z", "commit": commit,
            "requires_api": api, "permissions": ["none"], "network": [],
            "size": len(data), "sha256": _sha(data),
            "url": f"{RAW}/{commit}/macros/roadsheet/roadsheet.lua"}


def _index_data(latest_api: int = 4, files=None) -> dict:
    return {
        "schema": 1, "commit": "c2", "generated": "2026-11-19T00:00:00Z",
        "macros": [{
            "id": "roadsheet", "folder": "macros/roadsheet", "name": "Roadsheet",
            "author": "Someone", "version": "1.1", "description": "A road sheet.",
            "requires_api": latest_api, "permissions": ["write"], "network": ["example.com"],
            "file": "macros/roadsheet/roadsheet.lua", "readme": "macros/roadsheet/README.md",
            "page": "https://github.com/OpenSAK-Org/OpenSAK-Macros/tree/main/macros/roadsheet",
            "files": files if files is not None else [
                _file("macros/roadsheet/README.md", README),
                _file("macros/roadsheet/roadsheet.lua", V2),
                _file("macros/roadsheet/img/shot.png", PNG),
            ],
            "versions": [_version("1.1", V2, latest_api, "c2"), _version("1.0", V1, 4, "c1")],
            "changelog": [{"version": "1.1", "text": "prints 2"},
                          {"version": "1.0", "text": "first version"}],
        }],
        "withdrawn": [{"id": "old-one", "folder": "macros/old-one", "name": "Old one",
                       "last_version": "0.9", "withdrawn": "2026-11-18T00:00:00Z",
                       "versions": [{"version": "0.9", "sha256": _sha(GONE)}]}],
    }


class FakeFetch:
    """Serves the index *data* and its files by URL; counts the requests."""

    def __init__(self, data: dict, overrides: dict | None = None):
        self.data = data
        self.files = {f["url"]: content for f, content in zip(
            data["macros"][0]["files"], (README, V2, PNG))}
        self.files.update(overrides or {})
        self.urls: list[str] = []

    def __call__(self, url: str, limit: int) -> bytes:
        self.urls.append(url)
        if url == repo.INDEX_URL:
            return json.dumps(self.data).encode()
        return self.files[url]
