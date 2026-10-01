import re


def channel_key(name: str) -> str:
    """Compare channel purposes independently of decorative emoji and separators."""
    return re.sub(r'^[^a-z0-9]+|[^a-z0-9]+$', '', name.casefold()).strip()
