"""Just enough protobuf reading for Central's client stream, so no protobuf package is needed.

fields(data) gives {field number: [values]}: ints for varints, bytes for everything else.
Strings and nested messages are bytes; decode or call fields() on them.
"""

from __future__ import annotations


def _varint(b: bytes, i: int) -> tuple[int, int]:
    shift = result = 0
    while True:
        if i >= len(b) or shift > 63:
            raise ValueError("truncated varint")
        x = b[i]
        i += 1
        result |= (x & 0x7F) << shift
        if not x & 0x80:
            return result, i
        shift += 7


def fields(b: bytes) -> dict[int, list[int | bytes]]:
    out: dict[int, list[int | bytes]] = {}
    i = 0
    while i < len(b):
        key, i = _varint(b, i)
        num, wire = key >> 3, key & 7
        if wire == 0:
            value, i = _varint(b, i)
        elif wire in (1, 5):
            size = 8 if wire == 1 else 4
            value, i = b[i : i + size], i + size
        elif wire == 2:
            size, i = _varint(b, i)
            if i + size > len(b):
                raise ValueError("truncated field")
            value, i = b[i : i + size], i + size
        else:
            raise ValueError(f"unsupported wire type {wire}")
        out.setdefault(num, []).append(value)
    return out


def text(f: dict[int, list[int | bytes]], num: int) -> str | None:
    v = f.get(num, [None])[-1]
    return v.decode("utf-8", "replace") or None if isinstance(v, bytes) else None


def number(f: dict[int, list[int | bytes]], num: int) -> int | None:
    v = f.get(num, [None])[-1]
    if not isinstance(v, int):
        return None
    return v - (1 << 64) if v >= 1 << 63 else v  # negative int32 / int64


def message(f: dict[int, list[int | bytes]], num: int) -> dict[int, list[int | bytes]]:
    v = f.get(num, [None])[-1]
    return fields(v) if isinstance(v, bytes) else {}
