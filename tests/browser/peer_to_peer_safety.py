"""Fail-closed guards for the browser smoke's local identity writes."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit


def is_numeric_loopback_origin(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            return False
        _ = parsed.port
        return ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False


def validate_local_browser_target(*, base_url: str, name: str = "BASE_URL") -> None:
    """Refuse browser smoke credentials and account creation off loopback."""
    if not is_numeric_loopback_origin(base_url):
        raise RuntimeError(f"{name} must be a numeric loopback origin for browser smoke")


def validate_same_origin_target(*, base_url: str, target_url: str) -> None:
    """Require an action/navigation target to stay on the explicitly configured origin."""
    try:
        expected = urlsplit(base_url)
        actual = urlsplit(target_url)
        if (
            expected.scheme.lower() not in {"http", "https"}
            or actual.scheme.lower() not in {"http", "https"}
            or not expected.hostname
            or not actual.hostname
            or expected.username is not None
            or expected.password is not None
            or actual.username is not None
            or actual.password is not None
        ):
            raise ValueError("invalid origin")
        expected_origin = (
            expected.scheme.lower(),
            expected.hostname.casefold(),
            expected.port or (443 if expected.scheme.lower() == "https" else 80),
        )
        actual_origin = (
            actual.scheme.lower(),
            actual.hostname.casefold(),
            actual.port or (443 if actual.scheme.lower() == "https" else 80),
        )
    except ValueError as exc:
        raise RuntimeError("Target did not remain on a valid configured origin") from exc
    if expected_origin != actual_origin:
        raise RuntimeError("Target did not remain on the configured origin")


def validate_identity_mutation_targets(
    *,
    base_url: str,
    peer_base_url: str,
    shared_token: str,
    explicitly_allowed: bool,
) -> None:
    """Require explicit test opt-in, a nonempty token, and numeric loopback targets."""
    if not explicitly_allowed:
        raise RuntimeError(
            "Peer identity writes require BROWSER_PEER_ALLOW_IDENTITY_MUTATION=1"
        )
    if not shared_token.strip():
        raise RuntimeError("BROWSER_PEER_SHARED_TOKEN must be explicitly configured")
    if not base_url or not peer_base_url:
        raise RuntimeError("Both local browser peer origins must be explicitly configured")
    for name, value in (("BASE_URL", base_url), ("PEER_BASE_URL", peer_base_url)):
        if not is_numeric_loopback_origin(value):
            raise RuntimeError(f"{name} must be a numeric loopback origin before identity writes")


def validate_identity_form_destination(
    *, base_url: str, final_url: str, was_redirected: bool,
) -> None:
    """Reject redirects and any final page outside the configured origin."""
    if was_redirected:
        raise RuntimeError("Refusing to submit peer identity after a redirect")
    validate_same_origin_target(base_url=base_url, target_url=final_url)


def validate_testnet_peer_targets(
    *, peer_a_url: str, peer_b_url: str, peer_a_ip: str, peer_b_ip: str, link_token: str,
) -> None:
    """Constrain app-to-peer URLs used with the test link token to Docker peers."""
    if not link_token.strip():
        raise RuntimeError("BROWSER_PEER_LINK_TOKEN must be explicitly configured")
    for name, value, expected_host in (
        ("BROWSER_PEER_A_DOCKER_URL", peer_a_url, "peer-a"),
        ("BROWSER_PEER_B_DOCKER_URL", peer_b_url, "peer-b"),
    ):
        try:
            parsed = urlsplit(value)
            valid = (
                parsed.scheme == "http"
                and parsed.hostname == expected_host
                and parsed.port == 8080
                and parsed.username is None
                and parsed.password is None
                and parsed.path in {"", "/"}
                and not parsed.query
                and not parsed.fragment
            )
        except ValueError:
            valid = False
        if not valid:
            raise RuntimeError(f"{name} must target the isolated {expected_host}:8080 service")
    for name, value in (("BROWSER_PEER_A_IP", peer_a_ip), ("BROWSER_PEER_B_IP", peer_b_ip)):
        if not value:
            continue
        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise RuntimeError(f"{name} must be a Docker bridge IPv4 address") from exc
        if address.version != 4 or address not in ipaddress.ip_network("172.16.0.0/12"):
            raise RuntimeError(f"{name} must be within the Docker bridge range 172.16.0.0/12")
    if peer_a_ip and peer_b_ip and peer_a_ip == peer_b_ip:
        raise RuntimeError("BROWSER_PEER_A_IP and BROWSER_PEER_B_IP must be distinct")
