"""Read-only audit of Kontur 01/21/93 marking codes. Does not call BarTender."""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

GS = "\x1d"
BATCH_FIELDS = (
    "ShortName", "ShortGTIN", "EXP_DATE", "PROD_DATE",
    "PART_NUM", "DM", "NUM",
)


def parse_dm93(raw: str, expected_gtin: str = "") -> tuple[str, str, str]:
    """Validate the shortened Kontur payload, without rewriting a single byte."""
    if not isinstance(raw, str) or len(raw) != 38:
        raise ValueError("length must be exactly 38")
    if raw[:2] != "01" or raw[16:18] != "21":
        raise ValueError("required 01/21 identifiers not found")
    if raw[31] != GS or raw[32:34] != "93":
        raise ValueError("required ASCII 29 / 93 boundary not found")
    gtin, serial, verifier = raw[2:16], raw[18:31], raw[34:38]
    if not gtin.isascii() or not gtin.isdigit():
        raise ValueError("GTIN must contain 14 ASCII digits")
    if any(ord(c) < 32 or ord(c) > 126 for c in serial + verifier):
        raise ValueError("non-printable or non-ASCII serial/verifier")
    if expected_gtin and gtin != expected_gtin:
        raise ValueError("GTIN does not match the source column")
    digits = [int(c) for c in gtin]
    total = sum(d * (3 if idx % 2 == 0 else 1) for idx, d in enumerate(digits[:13]))
    check = (10 - total % 10) % 10
    if check != digits[13]:
        raise ValueError("invalid GTIN check digit")
    return gtin, serial, verifier


def _is_calibration_row(header: list[str], row: list[str]) -> bool:
    """Accept only the exact calibration fixture emitted by _write_tmp_batch_csv.

    Just DM == "X" is not sufficient: malformed genuine marking codes
    must never be silently classified as calibration.
    """
    if not set(BATCH_FIELDS).issubset(header) or len(row) != len(header):
        return False
    values = dict(zip(header, row))
    return all(
        values[field] == (
            "1" if field == "NUM" else "000" if field == "ShortGTIN" else "X"
        )
        for field in BATCH_FIELDS
    )


def iter_source_rows(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream, delimiter="\t", quotechar='"')
        first = next(reader, None)
        if first is None:
            return
        if "DM" in first and ("ShortName" in first or "GTIN" in first):
            key = first.index("DM")
            gtin_idx = first.index("GTIN") if "GTIN" in first else None
            header = first
        else:
            key = 0
            gtin_idx = 1
            header = []
            yield 1, first[key], first[gtin_idx] if len(first) > 1 else "", False
        for line_number, row in enumerate(reader, start=2):
            if not row or all(not part for part in row):
                continue
            yield (
                line_number,
                row[key] if key < len(row) else "",
                row[gtin_idx] if gtin_idx is not None and gtin_idx < len(row) else "",
                _is_calibration_row(header, row),
            )


def audit(path: Path):
    total = 0
    valid = 0
    invalid = 0
    calibration = 0
    errors = []
    previews = []
    seen_dm = {}
    for line, dm, gtin, is_calibration in iter_source_rows(path):
        total += 1
        if is_calibration:
            if total <= 6 and calibration == total - 1:
                calibration += 1
            else:
                invalid += 1
                if len(errors) < 10:
                    errors.append((line, "unexpected calibration row outside the first six"))
            continue
        try:
            fields = parse_dm93(dm, gtin)
            if dm in seen_dm:
                raise ValueError(
                    f"duplicate DM (first seen at line {seen_dm[dm]})"
                )
            seen_dm[dm] = line
            valid += 1
            if len(previews) < 2:
                previews.append((line, fields[0]))
        except ValueError as exc:
            invalid += 1
            if len(errors) < 10:
                errors.append((line, str(exc)))
    if calibration not in (0, 6):
        invalid += calibration
        if len(errors) < 10:
            errors.append((2, f"incomplete calibration block ({calibration}/6)"))
    return total, valid, invalid, calibration, errors, previews


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only inspection of GS1 01/21/93 Kontur source data."
    )
    parser.add_argument("csv", type=Path)
    args = parser.parse_args()
    total, valid, invalid, calibration, errors, previews = audit(args.csv)
    print(
        f"rows={total}; valid_dm={valid}; calibration={calibration}; "
        f"invalid={invalid}; first_two_gtin={previews}; errors_shown={len(errors)}"
    )
    for line, reason in errors:
        print(f"line {line}: {reason}")
    if not total:
        print("No rows found")
        return 1
    if invalid:
        print("FAIL: at least one non-calibration marking code is invalid")
        return 1
    print("PASS: source data structure valid; GS1 symbol and printer NOT verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
