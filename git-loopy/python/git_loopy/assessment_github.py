"""Fixed GitHub REST reads. The transport is the only network substitution."""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import parse_qs, quote, urlsplit


API_VERSION = "2022-11-28"
SHA_PATTERN = r"[0-9a-f]{40}"


@dataclass(frozen=True)
class Response:
    status: int
    data: Any
    headers: Mapping[str, str] = field(default_factory=dict)


class Transport(Protocol):
    async def request(
        self, method: str, path: str, *,
        params: Mapping[str, str | int], headers: Mapping[str, str],
    ) -> Response: ...


class Unreadable(ValueError):
    def __init__(self, read: str, reason: str, source: str):
        self.item = {"read": read, "reason": reason, "source": source}
        super().__init__(reason)


class GitHubReads:
    def __init__(self, repository: str, transport: Transport):
        self.api = f"/repos/{repository}"
        self.web = f"https://github.com/{repository}"
        self.transport = transport

    async def _get(self, suffix, params, read, source) -> Response:
        # No caller-provided URL is ever followed, including pagination links.
        if suffix not in ("", "/contents/VERSION", "/milestones") and not (
            re.fullmatch(r"/commits/[A-Za-z0-9%_.-]+", suffix)
        ):
            raise Unreadable(read, "endpoint is not allowed", source)
        try:
            response = await self.transport.request(
                "GET", self.api + suffix, params=params,
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": API_VERSION,
                },
            )
        except Exception as exc:
            # Transport exception text may carry credentials; never echo it.
            raise Unreadable(read, "read errored", source) from exc
        if response.status != 200:
            reason = {
                401: "denied", 403: "denied or rate-limited",
                404: "not-found", 429: "rate-limited",
            }.get(response.status, f"read errored (HTTP {response.status})")
            raise Unreadable(read, reason, source)
        return response

    async def pin_head(self) -> str:
        read, source = "Trunk head", self.web
        data = (await self._get("", {}, read, source)).data
        branch = data.get("default_branch") if isinstance(data, dict) else None
        if (
            not isinstance(branch, str) or not branch or len(branch) > 255
            or branch in (".", "..")
            or any(ord(c) < 32 for c in branch)
        ):
            raise Unreadable(read, "unrecognized evidence shape", source)
        data = (await self._get(
            f"/commits/{quote(branch, safe='')}", {}, read, source,
        )).data
        sha = data.get("sha") if isinstance(data, dict) else None
        if not isinstance(sha, str) or re.fullmatch(SHA_PATTERN, sha) is None:
            raise Unreadable(read, "unrecognized evidence shape", source)
        return sha

    async def version_text(self, sha: str) -> str:
        read, source = "VERSION at pinned SHA", f"{self.web}/blob/{sha}/VERSION"
        data = (await self._get(
            "/contents/VERSION", {"ref": sha}, read, source,
        )).data
        if not isinstance(data, dict) or (
            data.get("type") != "file" or data.get("encoding") != "base64"
            or not isinstance(data.get("content"), str)
        ):
            raise Unreadable(read, "unrecognized evidence shape", source)
        try:
            return base64.b64decode(
                data["content"].replace("\n", ""), validate=True,
            ).decode("utf-8")
        except (binascii.Error, UnicodeError, ValueError) as exc:
            raise Unreadable(read, "unrecognized evidence shape", source) from exc

    async def milestones(self) -> list[dict]:
        read, source = "Milestone catalog", f"{self.web}/milestones"
        found = []
        for page in range(1, 1001):
            response = await self._get(
                "/milestones", {"state": "all", "per_page": 100, "page": page},
                read, source,
            )
            if not isinstance(response.data, list) or not all(
                isinstance(m, dict) and isinstance(m.get("title"), str)
                and m.get("state") in ("open", "closed")
                and isinstance(m.get("number"), int) and not isinstance(m["number"], bool)
                and m["number"] > 0
                for m in response.data
            ):
                raise Unreadable(read, "unrecognized evidence shape", source)
            found.extend(response.data)
            link = next(
                (v for k, v in response.headers.items() if k.lower() == "link"), "",
            )
            if not link:
                return found
            parts = link.split(",")
            parsed = [re.fullmatch(r'\s*<([^>]+)>;\s*rel="([^"]+)"\s*', p)
                      for p in parts]
            if any(m is None for m in parsed):
                raise Unreadable(read, "pagination did not complete", source)
            next_links = [m.group(1) for m in parsed if m and m.group(2) == "next"]
            if not next_links:
                return found
            target = urlsplit(next_links[0])
            if (
                len(next_links) != 1 or not response.data
                or target.scheme != "https" or target.netloc != "api.github.com"
                or target.path != self.api + "/milestones" or target.fragment
                or parse_qs(target.query) != {
                    "state": ["all"], "per_page": ["100"], "page": [str(page + 1)],
                }
            ):
                raise Unreadable(read, "pagination did not complete", source)
        raise Unreadable(read, "pagination did not complete", source)
