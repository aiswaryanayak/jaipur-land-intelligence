"""Bounded, read-only connectivity probes for the deployed web service."""
from __future__ import annotations

import logging
import os
import socket
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import requests

LOGGER = logging.getLogger(__name__)

OFFICIAL_ENDPOINTS = (
    ("udh_auction_index", "https://udhonline.rajasthan.gov.in/Portal/AuctionListNew"),
    ("jda_post_auction", "https://jdaservice.rajasthan.gov.in/jdamicroportalproject/NoticeBoard/PostAuction"),
    ("riico_eauction", "https://www.riicoerp.industries.rajasthan.gov.in/eauction"),
    (
        "riico_direct_allotment",
        "https://www.riicoerp.industries.rajasthan.gov.in/DirectLand/DirectAllotmentSearchGrid",
    ),
)


def _failure(exc: Exception) -> dict[str, str]:
    if isinstance(exc, socket.gaierror):
        category = "dns"
    elif isinstance(exc, (socket.timeout, requests.ConnectTimeout, requests.ReadTimeout)):
        category = "timeout"
    elif isinstance(exc, requests.exceptions.SSLError):
        category = "tls"
    elif isinstance(exc, requests.exceptions.ProxyError):
        category = "proxy_or_egress"
    elif isinstance(exc, (requests.ConnectionError, OSError)):
        category = "connection"
    else:
        category = "error"
    return {"status": "failed", "category": category, "error_type": type(exc).__name__}


def _dns_probe(host: str) -> dict[str, Any]:
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except (OSError, socket.gaierror) as exc:
        return _failure(exc)
    return {"status": "ok", "category": "ok", "address_count": len({item[4][0] for item in addresses})}


def _tcp_probe(host: str, port: int, timeout: float) -> dict[str, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {"status": "ok", "category": "ok"}
    except (OSError, socket.timeout) as exc:
        return _failure(exc)


def _https_probe(session: requests.Session, url: str, timeout: tuple[float, float]) -> dict[str, Any]:
    try:
        response = session.get(
            url,
            headers={"User-Agent": "Jaipur-Land-Intelligence-connectivity-check/1.0"},
            timeout=timeout,
            allow_redirects=False,
            stream=True,
        )
        try:
            return {
                "status": "ok",
                "category": "response",
                "http_status": response.status_code,
                "content_type": response.headers.get("Content-Type", ""),
            }
        finally:
            response.close()
    except requests.RequestException as exc:
        return _failure(exc)


def run_connectivity_check(
    *,
    connect_timeout: float = 3.0,
    read_timeout: float = 5.0,
) -> dict[str, Any]:
    """Probe only endpoint reachability; never parse or persist response bodies."""
    checked_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    proxy_status = {
        name: "configured" if os.environ.get(name) else "not_configured"
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
    }
    results = []
    with requests.Session() as session:
        for endpoint_key, url in OFFICIAL_ENDPOINTS:
            host = urlparse(url).hostname or ""
            dns = _dns_probe(host)
            tcp = _tcp_probe(host, 443, connect_timeout) if dns["status"] == "ok" else {
                "status": "not_run",
                "category": "dns",
            }
            https = _https_probe(session, url, (connect_timeout, read_timeout))
            results.append({
                "endpoint": endpoint_key,
                "host": host,
                "url": url,
                "dns": dns,
                "tcp": tcp,
                "https": https,
            })
    LOGGER.info("Connectivity check completed for %d official endpoints", len(results))
    return {
        "checked_at": checked_at,
        "proxy_environment": proxy_status,
        "body_downloaded": False,
        "endpoints": results,
    }
