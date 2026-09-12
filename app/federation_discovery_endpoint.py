from urllib.parse import urlparse


def normalize_endpoint(value):
    value = str(value or "").strip()
    if "://" not in value:
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("invalid peer endpoint")
    return value.rstrip("/")
