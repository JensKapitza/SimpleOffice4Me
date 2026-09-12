def normalize_country(value):
    value = str(value or "").strip().upper()
    if len(value) != 2 or not value.isalpha():
        raise ValueError("country must be ISO-3166 alpha-2")
    return value
