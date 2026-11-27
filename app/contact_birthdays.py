"""Birthdays remain dates; a missing year is not a guessed one."""
from datetime import date
import re


def normal_birthday(value):
    if not isinstance(value, str) or not value:
        return ""
    try:
        if re.fullmatch(r"--[0-9]{2}-[0-9]{2}", value):
            date.fromisoformat("2000-" + value[2:])
        elif re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            date.fromisoformat(value)
        else:
            return ""
        return value
    except ValueError:
        return ""


def birthday_conflict(left, right):
    left, right = normal_birthday(left), normal_birthday(right)
    return bool(left and right and (left[-5:] != right[-5:]
                or (not left.startswith("--") and not right.startswith("--") and left != right)))


def conflicting_birthdays(item, visible):
    """Visible review hints only, never proof of identity or a merge decision."""
    fields = item.get("fields", {})
    source_date = fields.get("birthday")
    if not normal_birthday(source_date):
        return []

    def tokens(f):
        email = str(f.get("email", "")).strip().casefold()
        phone = re.sub(r"[^0-9]", "", str(f.get("phone", "")))
        return email if "@" in email else "", phone if len(phone) >= 7 else ""

    email, phone = tokens(fields)
    if not (email or phone):
        return []
    result = []
    for other in visible:
        if other["contact_id"] == item["contact_id"]:
            continue
        f = other.get("fields", {})
        peer_mail, peer_phone = tokens(f)
        if ((email and email == peer_mail) or (phone and phone == peer_phone)) and birthday_conflict(source_date, f.get("birthday")):
            result.append(other)
    return result
