"""Offline Thai postcode lookup from ตำบล + อำเภอ + จังหวัด.

Data: kongvut/thai-province-data (MIT), bundled in ``data/thai_postcodes.csv``.
No network access is ever made.
"""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from apps.registry.address import BANGKOK_NAMES, strip_prefix

DATA_FILE = Path(__file__).parent / "data" / "thai_postcodes.csv"


def _key(text: str) -> str:
    return re.sub(r"[\s\.]", "", text or "")


def norm_province(value: str) -> str:
    name = _key(strip_prefix("province", value))
    if name in {_key(n) for n in BANGKOK_NAMES}:
        return "กรุงเทพมหานคร"
    return name


def norm_district(value: str, province: str) -> str:
    name = _key(strip_prefix("district", value))
    if name in {"เมือง", "อำเภอเมือง"} and province:
        return "เมือง" + province
    return name


def norm_subdistrict(value: str) -> str:
    return _key(strip_prefix("subdistrict", value))


class PostcodeLookup:
    def __init__(self, path: Path = DATA_FILE):
        self.by_full: dict[tuple, set] = defaultdict(set)
        self.by_district: dict[tuple, set] = defaultdict(set)
        self.by_province_sub: dict[tuple, set] = defaultdict(set)
        with open(path, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                p = norm_province(row["province"])
                d = norm_district(row["district"], p)
                s = norm_subdistrict(row["subdistrict"])
                code = row["postcode"]
                self.by_full[(p, d, s)].add(code)
                self.by_district[(p, d)].add(code)
                self.by_province_sub[(p, s)].add(code)

    @staticmethod
    def _single(codes) -> str:
        return next(iter(codes)) if codes and len(codes) == 1 else ""

    def lookup(self, subdistrict: str, district: str, province: str) -> str:
        """Return a postcode, or '' when it cannot be determined unambiguously."""
        p = norm_province(province)
        d = norm_district(district, p)
        s = norm_subdistrict(subdistrict)
        if not p:
            return ""
        if s and d:
            code = self._single(self.by_full.get((p, d, s)))
            if code:
                return code
        if d:
            code = self._single(self.by_district.get((p, d)))
            if code:
                return code
        if s:
            code = self._single(self.by_province_sub.get((p, s)))
            if code:
                return code
        return ""


@lru_cache(maxsize=1)
def get_lookup() -> PostcodeLookup:
    return PostcodeLookup()


def lookup_postcode(subdistrict: str, district: str, province: str) -> str:
    return get_lookup().lookup(subdistrict, district, province)
