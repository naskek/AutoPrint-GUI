from __future__ import annotations

import re
from typing import Iterable, Mapping

from catalog_store import CatalogProduct, normalize_gtin
from data_processing import _legacy_template_from_pack, _parse_shelf_life


def _value(record: CatalogProduct | Mapping, field: str) -> str:
    if isinstance(record, Mapping):
        value = record.get(field, "")
    else:
        value = getattr(record, field, "")
    return "" if value is None else str(value).strip()


def build_product_map(
    records: Iterable[CatalogProduct | Mapping], *, has_short_col: bool
) -> dict:
    """Build the legacy read_product_map() contract from catalog records."""
    mapping = {}
    for record in records:
        gtin = normalize_gtin(_value(record, "gtin"))
        packaging = _value(record, "packaging")
        template_id = re.sub(
            r"\s+", " ", _value(record, "print_template").strip()
        )
        if not template_id:
            template_id = _legacy_template_from_pack(packaging)
        mapping[gtin] = {
            "TEMPLATE_ID": template_id,
            "FORMAT": template_id,
            "SHELF": _parse_shelf_life(_value(record, "shelf_life")),
            "PART_TEMPLATE": _value(record, "batch_template"),
            "SHORTNAME": _value(record, "short_name"),
            "BRAND": _value(record, "brand"),
            "CLIENT": _value(record, "client"),
            "PACKAGING": _value(record, "display_packaging") or packaging,
            "WEIGHT": _value(record, "weight"),
        }
    mapping["_HAS_SHORT_COL"] = bool(has_short_col)
    return mapping
