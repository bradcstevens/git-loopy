"""Fixed GitHub REST reads. The transport is the only network substitution.

Endpoint-to-App-read-permission matrix for this tracer:
    GET /repos/{repository}/installation         App JWT (installation lookup)
    GET /app                                    App JWT (App identity)
    POST /app/installations/{id}/access_tokens   App JWT (repository/read scope)
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
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import parse_qs, quote, urlsplit

from .assessment_config import AssessmentConfigError, Policy, read_app_key


API_VERSION = "2022-11-28"
SHA_PATTERN = r"[0-9a-f]{40}"
READ_PERMISSIONS = {
    name: "read" for name in (
        "metadata", "issues", "pull_requests", "contents", "checks", "statuses", "actions",
    )
}


def _headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
        "Authorization": f"Bearer {token}",
    }


@dataclass(frozen=True)
class Response:
    status: int
    data: Any
    headers: Mapping[str, str] = field(default_factory=dict)


class Transport(Protocol):
    async def request(
        self, method: str, path: str, *,
        params: Mapping[str, str | int], headers: Mapping[str, str],
        json: Mapping[str, Any] | None = None,
    ) -> Response: ...


class HTTPTransport:
    """GitHub.com only; redirects and ambient HTTP credentials are disabled."""

    async def request(self, method, path, *, params, headers, json=None) -> Response:
        import httpx

        async with httpx.AsyncClient(
            base_url="https://api.github.com", timeout=30,
            follow_redirects=False, trust_env=False,
        ) as client:
            response = await client.request(
                method, path, params=params, headers=headers, json=json,
            )
            # Error bodies are not evidence and can contain credential material.
            data = response.json() if response.status_code in (200, 201) else None
            return Response(response.status_code, data, response.headers)


class GitHubApp:
    def __init__(self, policy: Policy, transport: Transport):
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

        self.policy, self.transport = policy, transport
        material = read_app_key(policy.key_file)
        try:
            key = load_pem_private_key(material, password=None)
            if not isinstance(key, RSAPrivateKey) or key.key_size < 2048:
                raise ValueError()
            self._key = key
        except (ValueError, TypeError):
            raise AssessmentConfigError("App key file is not a valid RSA private key") from None

    async def installation(self, repository: str) -> tuple[str, str]:
        import jwt

        now = int(time.time())
        try:
            app_token = jwt.encode(
                {"iat": now - 60, "exp": now + 540, "iss": self.policy.app_id},
                self._key, algorithm="RS256",
            )
        except Exception:
            raise ValueError("could not sign the GitHub App credential") from None
        headers = _headers(app_token)

        async def request(method, path, body=None):
            try:
                response = await self.transport.request(
                    method, path, params={}, headers=headers,
                    **({"json": body} if body is not None else {}),
                )
            except Exception:
                raise ValueError(
                    f"GitHub App authentication errored for {repository}"
                ) from None
            if response.status != (201 if method == "POST" else 200):
                if response.status == 404 and path.endswith("/installation"):
                    raise ValueError(f"GitHub App is not installed on {repository}")
                raise ValueError(
                    f"GitHub App authentication failed for {repository} "
                    f"(HTTP {response.status})"
                )
            if not isinstance(response.data, dict):
                raise ValueError(f"unrecognized GitHub App response for {repository}")
            return response.data

        installation = await request("GET", f"/repos/{repository}/installation")
        identifier = installation.get("id")
        if (
            not isinstance(identifier, int) or isinstance(identifier, bool)
            or str(identifier) != self.policy.installation_id
            or installation.get("suspended_at") is not None
        ):
            raise ValueError(f"GitHub App installation does not match policy for {repository}")
        app = await request("GET", "/app")
        name = app.get("name")
        if (
            not isinstance(app.get("id"), int) or isinstance(app["id"], bool)
            or str(app["id"]) != self.policy.app_id
            or not isinstance(name, str) or not 0 < len(name) <= 200
            or not all(c.isprintable() for c in name)
        ):
            raise ValueError(f"unrecognized GitHub App identity for {repository}")
        data = await request(
            "POST", f"/app/installations/{identifier}/access_tokens",
            {"repositories": [repository.split("/")[1]], "permissions": READ_PERMISSIONS},
        )
        token = data.get("token")
        if (
            not isinstance(token, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,1024}", token) is None
            or data.get("permissions") != READ_PERMISSIONS
        ):
            raise ValueError(f"invalid read-only GitHub App token response for {repository}")
        identity = f"GitHub App {name} (app {app['id']}, installation {identifier})"
        return token, identity


class Unreadable(ValueError):
    def __init__(self, read: str, reason: str, source: str):
        self.item = {"read": read, "reason": reason, "source": source}
        super().__init__(reason)


class GitHubReads:
    def __init__(self, repository: str, transport: Transport, *, token: str = ""):
        self.api = f"/repos/{repository}"
        self.web = f"https://github.com/{repository}"
        self.transport = transport
        self._token = token

    async def _get(self, suffix, params, read, source) -> Response:
        # No caller-provided URL is ever followed, including pagination links.
        if suffix not in ("", "/contents/VERSION", "/milestones", "/actions/runs") and not (
            re.fullmatch(r"/commits/[A-Za-z0-9%_.-]+", suffix)
        ):
            raise Unreadable(read, "endpoint is not allowed", source)
        try:
            response = await self.transport.request(
                "GET", self.api + suffix, params=params,
                headers=_headers(self._token),
            )
        except Exception:
            # Transport exception text may carry credentials; never echo it.
            raise Unreadable(read, "read errored", source) from None
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
