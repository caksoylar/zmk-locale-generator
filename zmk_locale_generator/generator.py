from collections import defaultdict
from datetime import date
import logging
from pathlib import Path
import string
from typing import TextIO
import yaml

from . import locales
from .keys import HidUsage, KeyAlias, Modifier, parse_keys, get_zmk_name
from .util import unique

CODE_POINTS_PATH = Path(__file__).parent / "codepoints.yaml"
MODULE_NAME = __name__.split(".")[0]


class LocaleGenerator:
    """
    Generates locale headers.
    """

    keys: dict[str, HidUsage | KeyAlias]
    codepoint_names: dict[str, str | list[str]]

    def __init__(self, keys_h_path: Path | None):
        self.keys = parse_keys(keys_h_path)
        with CODE_POINTS_PATH.open(encoding="utf-8") as f:
            self.codepoint_names = yaml.safe_load(f)

    def write_header(self, io: TextIO, locale: str, layout_name: str = None):
        """
        Write a local header.

        :param io: Output stream
        :param locale: Locale code (used as a prefix for key names)
        :param layout_name: kbdlayout.info layout name (if it differs from locale)
        """
        layout = locales.get_layout(layout_name or locale)
        defs = self._get_key_definitions(layout)

        io.write(
            f"""\
/*
 * Localized Keys for {', '.join(layout.names)}
 *
 * Copyright (c) {date.today().year} The ZMK Contributors
 *
 * SPDX-License-Identifier: MIT
 *
 * This file was generated by a script. Do not modify it directly.
 * Instead, modify {MODULE_NAME} and re-generate the file.
 */
#pragma once

#include <dt-bindings/zmk/hid_usage.h>
#include <dt-bindings/zmk/hid_usage_pages.h>
#include <dt-bindings/zmk/modifiers.h>
"""
        )

        for usage, value in defs:
            if names := self._get_key_names(locale, value):
                main = names[0]
                aliases = names[1:]

                io.write("\n")
                io.write(f"#define {main} ({usage})\n")
                for alias in aliases:
                    io.write(f"#define {alias} ({main})\n")
            else:
                logging.debug(f"Skipped U+{ord(value):04X} ({value}) = {usage}")

    def _lookup_usage(self, name: str) -> HidUsage:
        match self.keys[name]:
            case KeyAlias(alias=alias):
                return self._lookup_usage(alias)

            case HidUsage() as value:
                return value

        raise ValueError(f'Invalid type for "{name}"')

    def _get_key_definitions(self, layout: locales.LocaleLayout):
        defs = list(self._get_raw_definitions(layout))

        defs = _dedupe_same_usage(defs)
        defs = _dedupe_uppercase(defs)
        defs = _dedupe_same_value(defs)

        defs.sort(key=lambda d: d[1].lower())
        return defs

    def _get_raw_definitions(self, layout: locales.LocaleLayout):
        for keymap in layout.keymaps:
            for key, value in keymap.keys.items():
                usage = self._lookup_usage(get_zmk_name(key))

                if keymap.modifiers:
                    usage = HidUsage(
                        usage.modifiers | keymap.modifiers, usage.page, usage.id
                    )

                yield usage, value

    def _get_key_names(self, locale: str, value: str):
        try:
            names = self.codepoint_names[value]
        except KeyError:
            return None

        if isinstance(names, str):
            names = [names]

        names += [
            k
            for k, v in self.keys.items()
            if isinstance(v, KeyAlias) and v.alias in names
        ]

        return [f"{locale.upper()}_{name}" for name in names]


def _has_shift(modifiers: frozenset[Modifier]):
    return Modifier.LShift in modifiers or Modifier.RShift in modifiers


def _remove_shift(modifiers: frozenset[Modifier]):
    return modifiers - {Modifier.LShift, Modifier.RShift}


def _dedupe_uppercase(defs: list[tuple[HidUsage, str]]):
    base_defs = [d for d in defs if not d[0].modifiers]
    mod_defs = [d for d in defs if d[0].modifiers]

    # If we have two definitions a and b such that:
    # a.value == b.value.upper() and a.usage == LS(b.usage)
    # then the uppercase definition (a) is redundant.

    def is_duplicate_uppercase(a: tuple[HidUsage, str]):
        if not _has_shift(a[0].modifiers):
            return False

        base_mods = _remove_shift(a[0].modifiers)
        return any(
            a[1].casefold() == b[1].casefold() and base_mods == b[0].modifiers
            for b in base_defs
        )

    mod_defs = [d for d in mod_defs if not is_duplicate_uppercase(d)]

    return base_defs + mod_defs


def _dedupe_same_usage(defs: list[tuple[HidUsage, str]]):
    return list(unique(defs, lambda d: d[0]))


def _dedupe_same_value(defs: list[tuple[HidUsage, str]]):
    # Keep the entry with the fewest modifiers
    d: defaultdict[str, list[HidUsage]] = defaultdict(list)
    for usage, value in defs:
        d[value].append(usage)

    def shortest_mods(seq: list[HidUsage]):
        return min(seq, key=lambda x: len(x.modifiers))

    return [(shortest_mods(v), k) for k, v in d.items()]
