"""Pick-list quantity parsing and warehouse location sort order."""

from __future__ import annotations

import re
from typing import Iterable

_SMALL_PARTS = re.compile(
    r"^([A-Za-z])-(\d{2})-(\d{2})(?:-([A-Za-z]))?$",
    re.IGNORECASE,
)
_CUBBY = re.compile(r"^([A-Za-z])-([1-9]\d*)$")
_ENDCAP = re.compile(r"^([A-Za-z])-(0\d+)$")
_CPU_RACK = re.compile(r"^CR-([A-Za-z0-9]+)", re.IGNORECASE)
_CABINET = re.compile(r"^CAB", re.IGNORECASE)

GROUP_CPU = 0
GROUP_CUBBY = 1
GROUP_ENDCAP = 2
GROUP_SMALL = 3
GROUP_CAB = 4
GROUP_MISC = 5

GROUP_TITLES = {
    GROUP_CPU: "CPU Rack",
    GROUP_CUBBY: "Cubbies",
    GROUP_ENDCAP: "Endcaps",
    GROUP_SMALL: "Small parts",
    GROUP_CAB: "Cabinet",
    GROUP_MISC: "Misc",
}


def parse_quantity(text: str) -> int:
    """Keep the ordered qty; drop on-hand text in parentheses."""
    cleaned = re.sub(r"\s+", " ", (text or "")).strip()
    before = cleaned.split("(", 1)[0].strip()
    match = re.search(r"\d+", before)
    if not match:
        return 1
    return max(1, int(match.group(0)))


def location_group_and_key(location: str) -> tuple:
    """Return (group_index, sort_tuple) for warehouse walk order."""
    loc = re.sub(r"\s+", " ", (location or "")).strip()
    upper = loc.upper()

    cpu = _CPU_RACK.match(loc)
    if cpu:
        return (GROUP_CPU, (cpu.group(1).upper(), upper))

    cubby = _CUBBY.match(loc)
    if cubby:
        return (GROUP_CUBBY, (cubby.group(1).upper(), int(cubby.group(2)), upper))

    endcap = _ENDCAP.match(loc)
    if endcap:
        return (
            GROUP_ENDCAP,
            (endcap.group(1).upper(), int(endcap.group(2)), upper),
        )

    small = _SMALL_PARTS.match(loc)
    if small:
        isle = small.group(1).upper()
        column = int(small.group(2))
        shelf = int(small.group(3))
        bin_code = (small.group(4) or "").upper()
        return (GROUP_SMALL, (isle, column, shelf, bin_code, upper))

    if _CABINET.match(loc):
        return (GROUP_CAB, (upper,))

    return (GROUP_MISC, (upper,))


def build_pick_list(
    items: Iterable[tuple[str, str, int]],
) -> str:
    """
    items: (sku, location, quantity) already combined per SKU.
    Output grouped sections, SKUs sorted by walk order then SKU.
    """
    rows = []
    for sku, location, qty in items:
        if not sku or qty < 1:
            continue
        group, key = location_group_and_key(location)
        rows.append((group, key, str(sku), str(location), int(qty)))

    rows.sort(key=lambda r: (r[0], r[1], r[2]))

    sections: list[str] = []
    current_group: int | None = None
    lines: list[str] = []
    for group, _key, sku, location, qty in rows:
        if group != current_group:
            if lines:
                title = GROUP_TITLES.get(current_group, "Misc")
                sections.append(f"{title}\n" + "\n".join(lines))
            current_group = group
            lines = []
        lines.append(f"{qty}x - {sku} - {location}")
    if lines:
        title = GROUP_TITLES.get(current_group, "Misc")
        sections.append(f"{title}\n" + "\n".join(lines))

    return "\n\n".join(sections)
