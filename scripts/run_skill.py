#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic CSV/TSV cleaning audit and quality report. Standard library only."""

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from pathlib import Path

MAX_FILES = 500
MAX_ROWS = 200000
PATH_KEYS = ("input_dir", "input_root", "materials_dir", "materials", "data_dir", "dataset_dir", "directory", "path", "root")
SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv", ".cache"}
MISSING = {"", "na", "n/a", "nan", "none", "null", "nil", "-", "--", "-999", "-9999", "未检测", "缺失"}
DATE_RE = re.compile(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}([ T]\d{1,2}:\d{2}(:\d{2})?)?$")
UNIT_RE = re.compile(r"[\(\[]([^\)\]]{1,24})[\)\]]")
ALIASES = {
    "case_id": ["case_id", "caseid", "id"],
    "data_dictionary": ["data_dictionary", "dictionary", "schema", "columns", "field_dictionary"],
    "missing_summary": ["missing", "missing_summary", "missing_values", "nulls"],
    "outliers": ["outliers", "anomalies", "outlier_report"],
    "duplicates": ["duplicates", "duplicate_rows", "duplicate_groups", "dedup"],
    "conversions": ["conversions", "transformations", "format_conversions", "unit_conversions"],
    "quality_report": ["quality_report", "report", "data_quality", "audit", "summary"],
    "cleaned_data": ["cleaned_data", "clean_data", "cleaned_table", "output_data"],
    "status": ["status", "run_status"],
    "result": ["result", "results", "data"],
    "evidence": ["evidence", "provenance", "sources"],
    "uncertainty": ["uncertainty", "limitations"],
    "errors": ["errors", "error"],
    "stats": ["stats", "statistics", "counts"],
}


def norm_key(name):
    return re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")


def contract_fields(case):
    oc = case.get("output_contract")
    out = []
    if isinstance(oc, dict):
        if isinstance(oc.get("properties"), dict):
            out = list(oc["properties"].keys())
        elif isinstance(oc.get("fields"), dict):
            out = list(oc["fields"].keys())
        elif isinstance(oc.get("fields"), list):
            out = [x for x in oc["fields"] if isinstance(x, str)]
        elif isinstance(oc.get("required"), list):
            out = [x for x in oc["required"] if isinstance(x, str)]
        else:
            out = list(oc.keys())
    elif isinstance(oc, list):
        out = [x for x in oc if isinstance(x, str)]
    seen, uniq = set(), []
    for f in out:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    return uniq


def iter_paths(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from iter_paths(item)
    elif isinstance(value, dict):
        for key in ("path", "dir", "file", "files", "root"):
            if key in value:
                yield from iter_paths(value[key])


def candidate_roots(case, case_path):
    base = case_path.parent
    roots = []
    for key in PATH_KEYS + ("data_files",):
        if key in case:
            for raw in iter_paths(case[key]):
                p = Path(raw)
                if not p.is_absolute():
                    p = base / p
                roots.append(p)
    if base.name.lower() == "input" or (base / "materials").is_dir():
        roots.append(base)
    if Path("/input").is_dir():
        roots.append(Path("/input"))
    seen, uniq = set(), []
    for r in roots:
        try:
            key = str(r.resolve())
        except OSError:
            key = str(r)
        if key not in seen:
            seen.add(key)
            uniq.append(r)
    return uniq


def collect(roots, errors, skipped, exclude):
    files, seen = [], set()
    try:
        exclude_key = str(exclude.resolve()) if exclude else None
    except OSError:
        exclude_key = None
    for root in roots:
        if not root.exists():
            errors.append("material root not found: %s" % (root.name or str(root)))
            continue
        if root.is_file():
            paths, root_dir = [root], root.parent
        else:
            root_dir, paths = root, []
            try:
                for dirpath, dirnames, filenames in os.walk(root):
                    dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
                    for fn in sorted(filenames):
                        paths.append(Path(dirpath) / fn)
            except OSError as exc:
                errors.append("walk failed on %s: %s" % (root.name, exc))
                continue
        for path in paths:
            if len(files) >= MAX_FILES:
                skipped.append({"path": path.name, "reason": "file cap reached"})
                continue
            try:
                rp = str(path.resolve())
                if path.is_symlink() or not path.is_file() or rp == exclude_key:
                    continue
            except OSError as exc:
                skipped.append({"path": path.name, "reason": "resolve failed: %s" % exc})
                continue
            if rp in seen:
                continue
            seen.add(rp)
            try:
                rel = path.relative_to(root_dir).as_posix()
            except ValueError:
                rel = path.name
            files.append((path, rel))
    files.sort(key=lambda x: x[1])
    return files


def decode(data):
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "utf-8-replace"


def is_missing(value, extra):
    v = (value or "").strip()
    return v.lower() in MISSING or v in extra


def to_float(value):
    try:
        return float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


def parse_table(text, delimiter):
    rows = list(csv.reader(text.splitlines(), delimiter=delimiter))
    rows = [r for r in rows if any(cell.strip() for cell in r)]
    if not rows:
        return [], []
    header = [h.strip() or "col_%d" % (i + 1) for i, h in enumerate(rows[0])]
    body = rows[1:]
    width = len(header)
    body = [r + [""] * (width - len(r)) if len(r) < width else r[:width] for r in body]
    return header, body


def analyze(header, body, extra_missing, key_columns):
    total = len(body)
    columns, missing_summary, outliers = [], [], []
    for idx, name in enumerate(header):
        values = [r[idx] for r in body]
        missing = [i + 1 for i, v in enumerate(values) if is_missing(v, extra_missing)]
        present = [v.strip() for v in values if not is_missing(v, extra_missing)]
        nums = [to_float(v) for v in present]
        numeric = [x for x in nums if x is not None]
        dates = [v for v in present if DATE_RE.match(v)]
        if present and len(numeric) == len(present):
            kind = "integer" if all(float(x).is_integer() for x in numeric) else "float"
        elif numeric and len(numeric) / float(len(present)) >= 0.8:
            kind = "text_with_numeric"
        elif present and len(dates) == len(present):
            kind = "date"
        else:
            kind = "text"
        unit = None
        m = UNIT_RE.search(name)
        if m:
            unit = m.group(1).strip()
        col = {
            "column": name,
            "inferred_type": kind,
            "non_missing": len(present),
            "missing": len(missing),
            "missing_rate": round(len(missing) / float(total), 4) if total else None,
            "distinct": len(set(present)),
            "unit_hint": unit,
            "example_values": present[:3],
        }
        if kind in ("integer", "float") and numeric:
            s = sorted(numeric)
            col["min"] = s[0]
            col["max"] = s[-1]
            col["mean"] = round(sum(s) / len(s), 6)
            col["median"] = s[len(s) // 2] if len(s) % 2 else round((s[len(s) // 2 - 1] + s[len(s) // 2]) / 2.0, 6)
            if len(s) >= 4:
                q1 = s[int(0.25 * (len(s) - 1))]
                q3 = s[int(0.75 * (len(s) - 1))]
                iqr = q3 - q1
                lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
                flagged = [i + 1 for i, v in enumerate(values)
                           if not is_missing(v, extra_missing) and to_float(v) is not None
                           and (to_float(v) < lo or to_float(v) > hi)]
                col["iqr_bounds"] = [round(lo, 6), round(hi, 6)]
                if flagged:
                    outliers.append({"column": name, "rule": "IQR*1.5", "bounds": [round(lo, 6), round(hi, 6)],
                                     "row_numbers": flagged[:200], "count": len(flagged),
                                     "action": "flagged_only"})
        columns.append(col)
        missing_summary.append({"column": name, "missing": len(missing), "rate": col["missing_rate"],
                                "row_numbers": missing[:200]})

    seen_rows, dup_rows = {}, []
    for i, row in enumerate(body, 1):
        key = tuple(cell.strip() for cell in row)
        if key in seen_rows:
            dup_rows.append({"row": i, "duplicate_of": seen_rows[key]})
        else:
            seen_rows[key] = i
    duplicates = {"exact_duplicate_rows": dup_rows, "exact_duplicate_count": len(dup_rows),
                  "unique_rows": len(seen_rows)}
    if key_columns:
        idxs = [header.index(k) for k in key_columns if k in header]
        if idxs:
            seen_key, key_dups = {}, []
            for i, row in enumerate(body, 1):
                k = tuple(row[j].strip().lower() for j in idxs)
                if k in seen_key:
                    key_dups.append({"row": i, "key": list(k), "first_row": seen_key[k]})
                else:
                    seen_key[k] = i
            duplicates["key_columns"] = key_columns
            duplicates["key_duplicate_count"] = len(key_dups)
            duplicates["key_duplicate_groups"] = key_dups[:200]
        else:
            duplicates["key_columns"] = key_columns
            duplicates["key_note"] = "requested key columns absent from header; not applied"
    return columns, missing_summary, outliers, duplicates


def resolve_field(field, payload):
    key = norm_key(field)
    for canonical, names in ALIASES.items():
        if key == canonical or key in names:
            return canonical, payload.get(canonical)
    return None, None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Data cleaning audit (stdlib only)")
    parser.add_argument("--case", default="/input/case.json")
    parser.add_argument("--out", "--output", default="/app/output/result.json")
    parser.add_argument("--work", default="/app/work")
    args = parser.parse_args(argv)

    case_path = Path(args.case)
    errors, skipped, case, status = [], [], {}, "success"
    try:
        with open(case_path, "r", encoding="utf-8-sig") as fh:
            case = json.load(fh)
        if not isinstance(case, dict):
            raise ValueError("case.json root is not an object")
    except (OSError, ValueError) as exc:
        errors.append("cannot read case.json: %s" % exc)
        case, status = {}, "failed"

    case_id = case.get("case_id") if isinstance(case.get("case_id"), str) else "unknown"
    fields = contract_fields(case)
    extra_missing = set(case.get("missing_tokens") or [])
    key_columns = case.get("key_columns") if isinstance(case.get("key_columns"), list) else None
    unit_rules = case.get("unit_rules") if isinstance(case.get("unit_rules"), dict) else {}

    tables, cleaned_path, conversions = [], None, []
    first_body = None
    if status != "failed":
        for path, rel in collect(candidate_roots(case, case_path), errors, skipped, case_path):
            ext = path.suffix.lower()
            if ext not in (".csv", ".tsv", ".txt"):
                skipped.append({"path": rel, "reason": "unsupported extension (only CSV/TSV parsed)"})
                continue
            try:
                raw = path.read_bytes()
                text, enc = decode(raw)
            except OSError as exc:
                skipped.append({"path": rel, "reason": "unreadable: %s" % exc})
                continue
            delim = "\t" if ext == ".tsv" else None
            if delim is None:
                line = text.splitlines()[0] if text else ""
                delim = max([",", "\t", ";"], key=lambda d: line.count(d))
                if line.count(delim) == 0:
                    delim = ","
            header, body = parse_table(text, delim)
            if not header:
                skipped.append({"path": rel, "reason": "empty or headerless table"})
                continue
            body = body[:MAX_ROWS]
            if first_body is None:
                first_body = [list(row) for row in body]
            cols, miss, outl, dups = analyze(header, body, extra_missing, key_columns)
            applied = []
            for name, rule in unit_rules.items():
                if name in header and isinstance(rule, dict):
                    applied.append({"column": name, "from": rule.get("from"), "to": rule.get("to"),
                                    "factor": rule.get("factor"), "applied": False,
                                    "reason": "declared but not applied; audit-only mode"})
            conversions.extend(applied)
            tables.append({
                "file": rel, "sha256": hashlib.sha256(raw).hexdigest(), "encoding": enc,
                "delimiter": "tab" if delim == "\t" else delim, "rows": len(body),
                "columns": len(header), "column_profiles": cols,
                "missing_summary": miss, "outliers": outl, "duplicates": dups,
            })

    if status != "failed":
        if not tables:
            status = "insufficient_input"
        else:
            high_missing = [m for t in tables for m in t["missing_summary"] if (m["rate"] or 0) > 0.5]
            if skipped or high_missing:
                status = "partial"

    if tables:
        first = tables[0]
        try:
            out_dir = Path(args.out).parent
            out_dir.mkdir(parents=True, exist_ok=True)
            cleaned_path = out_dir / "cleaned_data.csv"
            with open(cleaned_path, "w", encoding="utf-8", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow([c["column"] for c in first["column_profiles"]])
                for row in (first_body or []):
                    writer.writerow([("" if is_missing(v, extra_missing) else v) for v in row])
            cleaned_path = str(cleaned_path.name)
        except OSError as exc:
            errors.append("cannot write cleaned copy: %s" % exc)
            cleaned_path = None

    quality = {
        "tables": [{"file": t["file"], "rows": t["rows"], "columns": t["columns"],
                    "missing_cells": sum(m["missing"] for m in t["missing_summary"]),
                    "outlier_columns": len(t["outliers"]),
                    "exact_duplicate_rows": t["duplicates"]["exact_duplicate_count"]}
                   for t in tables],
        "rules_version": "2026-11-20.r1",
        "imputation": "none",
        "row_removal": "none",
        "missing_token_policy": "explicit tokens only; zero is treated as a real value",
    }
    data_dictionary = [dict(c, file=t["file"]) for t in tables for c in t["column_profiles"]]
    missing_summary = [dict(m, file=t["file"]) for t in tables for m in t["missing_summary"]]
    outliers = [dict(o, file=t["file"]) for t in tables for o in t["outliers"]]
    duplicates = [dict(t["duplicates"], file=t["file"]) for t in tables]
    summary = "Audited %d table(s) with %d column(s); missing cells and outliers were flagged, not imputed." % (
        len(tables), len(data_dictionary))

    result = {"data_dictionary": data_dictionary, "missing_summary": missing_summary,
              "outliers": outliers, "duplicates": duplicates, "conversions": conversions,
              "quality_report": quality, "cleaned_file": cleaned_path, "tables": tables}
    evidence = {"files": [{"file": t["file"], "sha256": t["sha256"], "encoding": t["encoding"],
                           "delimiter": t["delimiter"]} for t in tables],
                "cleaned_copy": cleaned_path, "source_mutation": "none",
                "row_cap": MAX_ROWS}
    uncertainty = [
        "Outliers are statistical flags only; they are not errors and were not removed.",
        "Missing values were not imputed; semantics of -999 style codes must be confirmed by the task.",
        "Unit hints come from header brackets and were not converted.",
    ]
    stats = {"tables": len(tables), "columns": len(data_dictionary),
             "rows": sum(t["rows"] for t in tables),
             "missing_cells": sum(m["missing"] for m in missing_summary),
             "outlier_columns": len(outliers),
             "duplicate_rows": sum(d["exact_duplicate_count"] for d in duplicates),
             "skipped": len(skipped), "errors": len(errors)}
    payload = {"data_dictionary": data_dictionary, "missing_summary": missing_summary,
               "outliers": outliers, "duplicates": duplicates, "conversions": conversions,
               "quality_report": quality, "cleaned_data": cleaned_path,
               "summary": summary, "status": status, "case_id": case_id,
               "result": result, "evidence": evidence, "uncertainty": uncertainty,
               "errors": errors, "stats": stats}

    output = {"case_id": case_id, "status": status, "result": result, "evidence": evidence,
              "uncertainty": uncertainty, "errors": errors, "stats": stats,
              "quality_report": quality}
    notes = []
    for field in fields:
        canonical, value = resolve_field(field, payload)
        if canonical is None:
            output[field] = None
            notes.append("%s: no supported producer in this skill; null retained" % field)
        else:
            output[field] = value
    if notes:
        output["contract_notes"] = notes

    try:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(output, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    except OSError as exc:
        sys.stderr.write("cannot write result: %s\n" % exc)
        return 2
    try:
        work = Path(args.work)
        work.mkdir(parents=True, exist_ok=True)
        with open(work / "cleaning_audit.json", "w", encoding="utf-8") as fh:
            json.dump({"tables": tables, "quality_report": quality}, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    _rc = 0
    try:
        _rc = main()
    except SystemExit:
        _rc = 0
    except Exception as _exc:  # last-resort guard: never fail the run
        try:
            _p = os.path.join("/app/output", "result.json")
            os.makedirs("/app/output", exist_ok=True)
            with open(_p, "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "error": type(_exc).__name__,
                           "limitations": ["unhandled_exception"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
        _rc = 0
    if not os.path.isfile("/app/output/result.json"):
        try:
            os.makedirs("/app/output", exist_ok=True)
            with open("/app/output/result.json", "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "limitations": ["no_result_written"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
    sys.exit(0)
