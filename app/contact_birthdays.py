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

    email, phone = _tokens(fields)
    if not (email or phone):
        return []
    result = []
    for other in visible:
        if other["contact_id"] == item["contact_id"]:
            continue
        f = other.get("fields", {})
        peer_mail, peer_phone = _tokens(f)
        if ((email and email == peer_mail) or (phone and phone == peer_phone)) and birthday_conflict(source_date, f.get("birthday")):
            result.append(other)
    return result


def _tokens(fields):
    email = str(fields.get("email", "")).strip().casefold()
    phone = re.sub(r"[^0-9]", "", str(fields.get("phone", "")))
    return email if "@" in email else "", phone if len(phone) >= 7 else ""


def birthday_warning_ids(visible):
    """Compute the same review hints in bounded linear passes for a large export."""
    groups = {}
    contacts = []
    for item in visible:
        fields = item["fields"]
        value = normal_birthday(fields.get("birthday"))
        if not value:
            continue
        keys = [(i, token) for i, token in enumerate(_tokens(fields)) if token]
        known = not value.startswith("--")
        contacts.append((item["contact_id"], keys, known))
        for key in keys:
            days, dated = groups.setdefault(key, (set(), set()))
            days.add(value[-5:])
            if known:
                dated.add(value)
    return {contact_id for contact_id, keys, known in contacts
            if any(len(groups[key][0]) > 1 or (known and len(groups[key][1]) > 1) for key in keys)}


def birthday_review_page(visible, page, review_only, page_size=50):
    """Build the authenticated contact list page without changing contact data."""
    warnings = birthday_warning_ids(visible)
    shown = [item for item in visible if not review_only or item["contact_id"] in warnings]
    last = max(1, (len(shown) + page_size - 1) // page_size)
    page = min(max(1, page), last)
    rows = [(item, conflicting_birthdays(item, visible)[:5] if item["contact_id"] in warnings else [])
            for item in shown[(page - 1) * page_size:page * page_size]]
    return rows, page, last, len(warnings)
