"""
ULID generation utility for universal identity.
Generates 26-character lexicographically sortable identifiers.
"""
from ulid import ULID


def generate_ulid() -> str:
    """Returns a new 26-character ULID string."""
    return str(ULID())
