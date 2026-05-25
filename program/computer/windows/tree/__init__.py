from typing import Any


def __getattr__(name: str) -> Any:
    if name == "Tree":
        from .service import Tree

        return Tree
    raise AttributeError(name)


__all__ = ["Tree"]
