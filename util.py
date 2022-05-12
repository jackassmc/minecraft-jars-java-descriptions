from typing import Any, Callable, TypeVar


class StrAlias:
    minecraft_version = str
    jar_key = str


def progress(i: int, max: int) -> str:
    width = len(str(max))
    percent = f"{(i/max)*100:.2f}%".rjust(7, "_")
    return f"{percent} {str(i).rjust(width, '_')}/{str(max).rjust(width, '_')}"


K = TypeVar("K")
V = TypeVar("V")


def sort_dict(
    dict_in: dict[K, V],
    key: Callable[[tuple[K, V]], Any] | None = None,
    reverse: bool = False,
) -> dict[K, V]:
    return {k: v for k, v in sorted(dict_in.items(), key=key, reverse=reverse)}
