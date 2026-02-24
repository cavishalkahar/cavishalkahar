# GST Reconciliation Tool

This repository now includes a lightweight **GST Reco tool** to compare your Purchase Register against GSTR-2B and identify:

- Matched invoices
- Value/tax mismatches
- Invoices missing in GSTR-2B
- Invoices present in GSTR-2B but missing in Purchase Register

## Input format

Both CSV files (`purchase` and `gstr2b`) must have these columns:

- `supplier_gstin`
- `invoice_no`
- `invoice_date` (`YYYY-MM-DD`, `DD-MM-YYYY`, `DD/MM/YYYY`, `YYYY/MM/DD`)
- `taxable_value`
- `igst`
- `cgst`
- `sgst`
- `cess`

## Usage

```bash
python3 gst_reco.py \
  --purchase sample_data/purchase.csv \
  --gstr2b sample_data/gstr2b.csv \
  --output-dir output \
  --tolerance 1.0
```

## Output files

The tool generates these reports inside `--output-dir`:

- `matched_invoices.csv`
- `unmatched_purchase.csv`
- `unmatched_gstr2b.csv`

It also prints a summary in terminal.

## Quick test

```bash
python3 gst_reco.py --purchase sample_data/purchase.csv --gstr2b sample_data/gstr2b.csv
```
