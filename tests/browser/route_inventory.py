#!/usr/bin/env python3
"""Export the live Flask GET route inventory for the manual browser test.

The browser runner uses static HTML candidates as crawl seeds and keeps dynamic
rules in the report so route growth is visible even when a concrete object does
not exist in the test fixture.
"""

from __future__ import annotations

import json
import re

from app import app


PROTOCOL_PREFIXES = (
    "/api/",
    "/caldav",
    "/carddav",
    "/webdav",
    "/dav/",
    "/federation/",
    "/s3",
    "/.well-known/",
)
NON_PAGE_TOKENS = (
    "/blob",
    "/chunk",
    "/download",
    "/export",
    "/raw",
    "/stream",
)
AUTH_EXACT = {
    "/auth/login",
    "/auth/logout",
    "/auth/register",
}
AUTH_PREFIXES = (
    "/auth/google",
)
STATIC_RE = re.compile(r"<[^>]+>")
RULE_ARGUMENT_RE = re.compile(r"<(?:(?P<converter>[^:<>]+):)?[^<>]+>")


def browser_candidate(rule: str, endpoint: str) -> tuple[bool, str]:
    if rule == "/favicon.ico" or rule.startswith("/static/"):
        return False, "static-asset"
    if rule in AUTH_EXACT or any(rule.startswith(prefix) for prefix in AUTH_PREFIXES):
        return False, "authentication-flow"
    if any(rule.startswith(prefix) for prefix in PROTOCOL_PREFIXES):
        return False, "protocol-or-api"
    lowered = rule.casefold()
    endpoint_lowered = endpoint.casefold()
    if any(token in lowered for token in NON_PAGE_TOKENS):
        return False, "binary-or-download"
    if any(
        token in endpoint_lowered
        for token in ("download", "export", "blob", "chunk", "stream", "attachment")
    ):
        return False, "binary-or-download"
    return True, ""


def rule_pattern(rule: str) -> str:
    parts: list[str] = []
    offset = 0
    for match in RULE_ARGUMENT_RE.finditer(rule):
        parts.append(re.escape(rule[offset:match.start()]))
        converter = (match.group("converter") or "").casefold()
        parts.append(".+" if converter == "path" else "[^/]+")
        offset = match.end()
    parts.append(re.escape(rule[offset:]))
    return "^" + "".join(parts) + "$"


def main() -> None:
    routes: list[dict[str, object]] = []
    for rule in sorted(app.url_map.iter_rules(), key=lambda item: (item.rule, item.endpoint)):
        methods = sorted(method for method in rule.methods if method not in {"HEAD", "OPTIONS"})
        if "GET" not in methods:
            continue
        candidate, reason = browser_candidate(rule.rule, rule.endpoint)
        routes.append(
            {
                "rule": rule.rule,
                "endpoint": rule.endpoint,
                "methods": methods,
                "dynamic": bool(rule.arguments) or bool(STATIC_RE.search(rule.rule)),
                "arguments": sorted(rule.arguments),
                "path_regex": rule_pattern(rule.rule),
                "browser_candidate": candidate,
                "skip_reason": reason,
            }
        )

    payload = {
        "route_count": len(routes),
        "browser_candidate_count": sum(1 for item in routes if item["browser_candidate"]),
        "static_browser_candidate_count": sum(
            1
            for item in routes
            if item["browser_candidate"] and not item["dynamic"]
        ),
        "dynamic_browser_candidate_count": sum(
            1
            for item in routes
            if item["browser_candidate"] and item["dynamic"]
        ),
        "routes": routes,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
