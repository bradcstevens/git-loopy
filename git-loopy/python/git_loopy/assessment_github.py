"""Fixed GitHub REST reads. The transport is the only network substitution.

Endpoint-to-App-read-permission matrix for this tracer:
    GET /repos/{repository}                       Metadata (default branch)
    GET /repos/{repository}/commits/{ref}         Contents (pinned head)
    GET /repos/{repository}/contents/VERSION     Contents (ref = pinned SHA)
    GET /repos/{repository}/milestones           Issues (complete catalog)
    GET /repos/{repository}/actions/runs         Actions (head_sha = pinned SHA)

Pagination is state=all, per_page=100 and a monotonic page number. It never
follows a caller-provided or foreign URL. The only API version is 2022-11-28.
"""

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
        if suffix not in ("", "/contents/VERSION", "/milestones", "/actions/runs") and not (
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

    async def workflow_runs(self, sha: str) -> list[dict]:
        read, source = "Counted checks", f"{self.web}/commit/{sha}/checks"
        found = []
        total = None
        # GitHub caps filtered workflow-run searches at 1,000 results.
        for page in range(1, 11):
            response = await self._get(
                "/actions/runs", {"head_sha": sha, "per_page": 100, "page": page},
                read, source,
            )
            data = response.data
            if (
                not isinstance(data, dict)
                or not isinstance(data.get("total_count"), int)
                or isinstance(data["total_count"], bool) or data["total_count"] < 0
                or not isinstance(data.get("workflow_runs"), list)
                or not all(
                    isinstance(run, dict)
                    and isinstance(run.get("id"), int) and not isinstance(run["id"], bool)
                    and run["id"] > 0
                    and isinstance(run.get("name"), str) and 0 < len(run["name"]) <= 200
                    and run.get("head_sha") == sha
                    and isinstance(run.get("status"), str) and 0 < len(run["status"]) <= 100
                    and "conclusion" in run and (
                        run["conclusion"] is None or (
                            isinstance(run["conclusion"], str)
                            and 0 < len(run["conclusion"]) <= 100
                        )
                    )
                    for run in data["workflow_runs"]
                )
            ):
                raise Unreadable(read, "unrecognized evidence shape", source)
            if total is None:
                total = data["total_count"]
            found.extend(data["workflow_runs"])
            if (
                total > 1000 or total != data["total_count"] or len(found) > total
                or len(data["workflow_runs"]) > 100
                or len({run["id"] for run in found}) != len(found)
            ):
                raise Unreadable(read, "pagination did not complete", source)
            link = next(
                (v for k, v in response.headers.items() if k.lower() == "link"), "",
            )
            parsed = [
                re.fullmatch(r'\s*<([^>]+)>;\s*rel="([^"]+)"\s*', part)
                for part in link.split(",")
            ] if link else []
            if any(match is None for match in parsed):
                raise Unreadable(read, "pagination did not complete", source)
            next_links = [
                match.group(1) for match in parsed if match and match.group(2) == "next"
            ]
            if not next_links:
                if len(found) == total:
                    return found
                raise Unreadable(read, "pagination did not complete", source)
            target = urlsplit(next_links[0])
            if (
                len(next_links) != 1 or not data["workflow_runs"] or len(found) >= total
                or target.scheme != "https" or target.netloc != "api.github.com"
                or target.path != self.api + "/actions/runs" or target.fragment
                or parse_qs(target.query) != {
                    "head_sha": [sha], "per_page": ["100"], "page": [str(page + 1)],
                }
            ):
                raise Unreadable(read, "pagination did not complete", source)
        raise Unreadable(read, "pagination did not complete", source)
