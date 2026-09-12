import hashlib


def email_hash(email):
    normalized = str(email or "").strip().casefold()
    if "@" not in normalized:
        raise ValueError("invalid email")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
