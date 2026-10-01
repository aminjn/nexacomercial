"""Publisher registry — import a module here to register its adapter."""
from __future__ import annotations

from typing import Any

from .base import Publisher, PublishError, PublishResult  # noqa: F401

REGISTRY: dict[str, type[Publisher]] = {}


def register(cls: type[Publisher]) -> type[Publisher]:
    REGISTRY[cls.kind] = cls
    return cls


def make(kind: str, options: dict[str, Any]) -> Publisher:
    try:
        return REGISTRY[kind](options)
    except KeyError:
        raise PublishError(f"unknown publisher kind '{kind}'. Known: {', '.join(sorted(REGISTRY))}") from None


# Register adapters.
from . import (  # noqa: E402,F401
    articles,
    socials,
)
