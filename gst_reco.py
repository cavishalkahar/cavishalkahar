#!/usr/bin/env python3
"""GST Reconciliation utility.

Compares purchase register invoices against GSTR-2B data and produces
summary metrics and mismatch reports.
"""

from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d")
NUMERIC_FIELDS = ("taxable_value", "igst", "cgst", "sgst", "cess")


@dataclass
class Invoice:
    source: str
    row_number: int
    supplier_gstin: str
    invoice_no: str
    invoice_date: Optional[datetime]
    taxable_value: float
    igst: float
    cgst: float
    sgst: float
    cess: float

    @property
    def total_tax(self) -> float:
        return self.igst + self.cgst + self.sgst + self.cess

    @property
    def key(self) -> Tuple[str, str]:
        return (normalize_text(self.supplier_gstin), normalize_text(self.invoice_no))


@dataclass
class MatchResult:
    purchase: Invoice
    gstr2b: Invoice
    tax_delta: float
    taxable_delta: float
    date_match: bool


def normalize_text(value: str) -> str:
    return (value or "").strip().upper().replace(" ", "")


def parse_date(raw: str) -> Optional[datetime]:
    value = (raw or "").strip()
    if not value:
        return None

    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unsupported date format: '{raw}'")


def parse_float(raw: str) -> float:
    value = (raw or "").strip().replace(",", "")
    if not value:
        return 0.0
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"Invalid numeric value: '{raw}'") from exc


def load_invoices(path: Path, source: str) -> List[Invoice]:
    invoices: List[Invoice] = []
    with path.open(newline="", encoding="utf-8-sig") as csvfile:
        reader = csv.DictReader(csvfile)
        required = {"supplier_gstin", "invoice_no", "invoice_date", *NUMERIC_FIELDS}
        missing = sorted(required - set(reader.fieldnames or []))
        if missing:
            raise ValueError(f"{path}: missing required columns: {', '.join(missing)}")

        for idx, row in enumerate(reader, start=2):
            try:
                invoices.append(
                    Invoice(
                        source=source,
                        row_number=idx,
                        supplier_gstin=row["supplier_gstin"],
                        invoice_no=row["invoice_no"],
                        invoice_date=parse_date(row["invoice_date"]),
                        taxable_value=parse_float(row["taxable_value"]),
                        igst=parse_float(row["igst"]),
                        cgst=parse_float(row["cgst"]),
                        sgst=parse_float(row["sgst"]),
                        cess=parse_float(row["cess"]),
                    )
                )
            except Exception as exc:
                raise ValueError(f"{path}:{idx}: {exc}") from exc

    return invoices


def reconcile(
    purchase_invoices: Sequence[Invoice],
    gstr2b_invoices: Sequence[Invoice],
    tolerance: float,
) -> Tuple[List[MatchResult], List[Invoice], List[Invoice]]:
    gstr2b_index: Dict[Tuple[str, str], List[Invoice]] = {}
    for invoice in gstr2b_invoices:
        gstr2b_index.setdefault(invoice.key, []).append(invoice)

    matched: List[MatchResult] = []
    unmatched_purchase: List[Invoice] = []

    for purchase in purchase_invoices:
        candidates = gstr2b_index.get(purchase.key, [])
        if not candidates:
            unmatched_purchase.append(purchase)
            continue

        best_idx, best_score = find_best_candidate(purchase, candidates)
        candidate = candidates.pop(best_idx)
        if not candidates:
            gstr2b_index.pop(purchase.key, None)

        tax_delta = purchase.total_tax - candidate.total_tax
        taxable_delta = purchase.taxable_value - candidate.taxable_value
        date_match = purchase.invoice_date == candidate.invoice_date

        matched.append(
            MatchResult(
                purchase=purchase,
                gstr2b=candidate,
                tax_delta=tax_delta,
                taxable_delta=taxable_delta,
                date_match=date_match,
            )
        )

    unmatched_gstr2b = [inv for bucket in gstr2b_index.values() for inv in bucket]

    # Keep exact/near matches first in output.
    matched.sort(key=lambda m: (abs(m.tax_delta) > tolerance, abs(m.taxable_delta) > tolerance))

    return matched, unmatched_purchase, unmatched_gstr2b


def find_best_candidate(purchase: Invoice, candidates: Sequence[Invoice]) -> Tuple[int, float]:
    best_idx = 0
    best_score = math.inf
    for idx, candidate in enumerate(candidates):
        date_penalty = 0 if purchase.invoice_date == candidate.invoice_date else 1
        tax_delta = abs(purchase.total_tax - candidate.total_tax)
        taxable_delta = abs(purchase.taxable_value - candidate.taxable_value)
        score = date_penalty + tax_delta + taxable_delta
        if score < best_score:
            best_idx = idx
            best_score = score
    return best_idx, best_score


def write_csv(path: Path, headers: Iterable[str], rows: Iterable[Iterable[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(list(headers))
        for row in rows:
            writer.writerow(list(row))


def to_date(value: Optional[datetime]) -> str:
    return value.strftime("%Y-%m-%d") if value else ""


def generate_reports(
    matched: Sequence[MatchResult],
    unmatched_purchase: Sequence[Invoice],
    unmatched_gstr2b: Sequence[Invoice],
    output_dir: Path,
    tolerance: float,
) -> None:
    matched_rows = (
        (
            item.purchase.supplier_gstin,
            item.purchase.invoice_no,
            to_date(item.purchase.invoice_date),
            round(item.purchase.taxable_value, 2),
            round(item.gstr2b.taxable_value, 2),
            round(item.taxable_delta, 2),
            round(item.purchase.total_tax, 2),
            round(item.gstr2b.total_tax, 2),
            round(item.tax_delta, 2),
            "YES" if item.date_match else "NO",
            "MATCHED"
            if abs(item.tax_delta) <= tolerance and abs(item.taxable_delta) <= tolerance
            else "MISMATCH",
        )
        for item in matched
    )
    write_csv(
        output_dir / "matched_invoices.csv",
        [
            "supplier_gstin",
            "invoice_no",
            "invoice_date",
            "purchase_taxable",
            "gstr2b_taxable",
            "taxable_delta",
            "purchase_total_tax",
            "gstr2b_total_tax",
            "tax_delta",
            "date_match",
            "status",
        ],
        matched_rows,
    )

    unmatched_headers = [
        "source",
        "row_number",
        "supplier_gstin",
        "invoice_no",
        "invoice_date",
        "taxable_value",
        "igst",
        "cgst",
        "sgst",
        "cess",
        "total_tax",
    ]

    write_csv(
        output_dir / "unmatched_purchase.csv",
        unmatched_headers,
        (
            (
                inv.source,
                inv.row_number,
                inv.supplier_gstin,
                inv.invoice_no,
                to_date(inv.invoice_date),
                round(inv.taxable_value, 2),
                round(inv.igst, 2),
                round(inv.cgst, 2),
                round(inv.sgst, 2),
                round(inv.cess, 2),
                round(inv.total_tax, 2),
            )
            for inv in unmatched_purchase
        ),
    )

    write_csv(
        output_dir / "unmatched_gstr2b.csv",
        unmatched_headers,
        (
            (
                inv.source,
                inv.row_number,
                inv.supplier_gstin,
                inv.invoice_no,
                to_date(inv.invoice_date),
                round(inv.taxable_value, 2),
                round(inv.igst, 2),
                round(inv.cgst, 2),
                round(inv.sgst, 2),
                round(inv.cess, 2),
                round(inv.total_tax, 2),
            )
            for inv in unmatched_gstr2b
        ),
    )


def print_summary(
    matched: Sequence[MatchResult],
    unmatched_purchase: Sequence[Invoice],
    unmatched_gstr2b: Sequence[Invoice],
    tolerance: float,
) -> None:
    mismatch_count = sum(
        1
        for item in matched
        if abs(item.tax_delta) > tolerance or abs(item.taxable_delta) > tolerance
    )
    exact_count = len(matched) - mismatch_count

    print("GST Reconciliation Summary")
    print("-" * 32)
    print(f"Purchase invoices: {len(matched) + len(unmatched_purchase)}")
    print(f"GSTR-2B invoices:   {len(matched) + len(unmatched_gstr2b)}")
    print(f"Matched invoices:   {len(matched)}")
    print(f"  • Exact/near:     {exact_count}")
    print(f"  • Mismatch:       {mismatch_count}")
    print(f"Missing in GSTR-2B: {len(unmatched_purchase)}")
    print(f"Missing in Purchase:{len(unmatched_gstr2b)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GST purchase vs GSTR-2B reconciliation tool")
    parser.add_argument("--purchase", required=True, help="CSV path for purchase register")
    parser.add_argument("--gstr2b", required=True, help="CSV path for GSTR-2B data")
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory to save reconciliation reports (default: output)",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=1.0,
        help="Permitted delta for taxable/tax values to still count as matched",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    purchase_path = Path(args.purchase)
    gstr2b_path = Path(args.gstr2b)

    purchase_invoices = load_invoices(purchase_path, "PURCHASE")
    gstr2b_invoices = load_invoices(gstr2b_path, "GSTR2B")

    matched, unmatched_purchase, unmatched_gstr2b = reconcile(
        purchase_invoices, gstr2b_invoices, tolerance=args.tolerance
    )

    generate_reports(
        matched,
        unmatched_purchase,
        unmatched_gstr2b,
        output_dir=Path(args.output_dir),
        tolerance=args.tolerance,
    )

    print_summary(matched, unmatched_purchase, unmatched_gstr2b, tolerance=args.tolerance)


if __name__ == "__main__":
    main()
