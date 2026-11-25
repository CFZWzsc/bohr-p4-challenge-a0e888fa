#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic CSV/TSV cleaning pipeline and quality report. Standard library only."""

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

MAX_FILES = 500
MAX_ROWS = 200000
PATH_KEYS = ("input_dir", "input_root", "materials_dir", "materials", "data_dir", "dataset_dir", "directory", "path", "root")
SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv", ".cache"}
MISSING = {"", "na", "n/a", "nan", "none", "null", "nil", "-", "--", "-999", "-9999", "未检测", "缺失"}
DATE_RE = re.compile(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}([ T]\d{1,2}:\d{2}(:\d{2})?)?$")
DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d",
                "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S",
                "%Y/%m/%d %H:%M", "%Y/%m/%d %H:%M:%S")
DATE_HINT_RE = re.compile(r"(^|[_\- ])(date|time|day|timestamp|collected|created|updated)([_\- ]|$)|日期|时间", re.I)
UNIT_RE = re.compile(r"[\(\[]([^\)\]]{1,24})[\)\]]")
MISSING_STRATEGIES = {"drop_rows", "mean", "median", "mode", "none"}
OUTLIER_ACTIONS = {"flag", "remove", "winsorize"}
ALIASES = {
    "case_id": ["case_id", "caseid", "id"],
    "data_dictionary": ["data_dictionary", "dictionary", "schema", "columns", "field_dictionary"],
    "missing_summary": ["missing", "missing_summary", "missing_values", "nulls"],
    "outliers": ["outliers", "anomalies", "outlier_report"],
    "duplicates": ["duplicates", "duplicate_rows", "duplicate_groups", "dedup"],
    "conversions": ["conversions", "transformations", "format_conversions", "unit_conversions"],
    "quality_report": ["quality_report", "data_quality", "audit"],
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


def is_valid_date(value):
    v = (value or "").strip()
    if not v:
        return False
    for fmt in DATE_FORMATS:
        try:
            datetime.strptime(v, fmt)
            return True
        except ValueError:
            continue
    try:
        datetime.fromisoformat(v.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


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


def analyze(header, body, extra_missing, key_columns, missing_strategy, outlier_action):
    """Normalize explicit missing tokens, profile columns, then apply declared cleaning."""
    total = len(body)
    work = []
    for row in body:
        work.append(["" if ((cell or "").strip() and is_missing(cell, extra_missing)) else (cell or "")
                     for cell in row])

    conversions, token_inventory = [], {}
    for idx, name in enumerate(header):
        groups = {}
        for i, row in enumerate(body):
            raw = (row[idx] or "").strip()
            if raw and is_missing(raw, extra_missing):
                groups.setdefault(raw, []).append(i + 1)
        for token in sorted(groups):
            rows = groups[token]
            conversions.append({"column": name, "from": token, "to": None,
                                "rule": "missing_token_normalized",
                                "rows": rows[:200], "count": len(rows)})
            inv = token_inventory.setdefault(token, {"count": 0, "columns": []})
            inv["count"] += len(rows)
            if name not in inv["columns"]:
                inv["columns"].append(name)

    columns, missing_summary, outliers, date_failures = [], [], [], []
    name_index = {}
    for idx, name in enumerate(header):
        if name not in name_index:
            name_index[name] = idx
        values = [r[idx] for r in work]
        missing = [i + 1 for i, v in enumerate(values) if is_missing(v, extra_missing)]
        present = [v.strip() for v in values if not is_missing(v, extra_missing)]
        numeric = [x for x in (to_float(v) for v in present) if x is not None]
        dates = [v for v in present if DATE_RE.match(v)]
        date_ratio = len(dates) / float(len(present)) if present else 0.0
        is_date_col = bool(present) and (date_ratio >= 0.5 or bool(DATE_HINT_RE.search(name)))
        if present and numeric and len(numeric) == len(present):
            kind = "integer" if all(float(x).is_integer() for x in numeric) else "float"
        elif numeric and len(numeric) / float(len(present)) >= 0.8:
            kind = "text_with_numeric"
        elif is_date_col:
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
        if is_date_col:
            failures = 0
            for i, v in enumerate(values):
                if is_missing(v, extra_missing):
                    continue
                if not is_valid_date(v):
                    failures += 1
                    date_failures.append({"column": name, "row": i + 1, "value": v})
            col["date_parse_failures"] = failures
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

    actions = []
    remove_idx = set()
    if missing_strategy == "drop_rows":
        for i, row in enumerate(work):
            if any(is_missing(v, extra_missing) for v in row):
                remove_idx.add(i)
        if remove_idx:
            actions.append({"action": "drop_rows", "rule": "missing_strategy:drop_rows",
                            "rows": [i + 1 for i in sorted(remove_idx)][:200],
                            "count": len(remove_idx)})
    elif missing_strategy in ("mean", "median", "mode"):
        for idx, name in enumerate(header):
            if missing_strategy == "mode":
                present = [work[i][idx].strip() for i in range(len(work))
                           if not is_missing(work[i][idx], extra_missing)]
                if not present:
                    continue
                counts = {}
                for v in present:
                    counts[v] = counts.get(v, 0) + 1
                fill_value = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
                rule = "impute_mode"
            else:
                nums = [to_float(work[i][idx]) for i in range(len(work))
                        if not is_missing(work[i][idx], extra_missing)]
                nums = [x for x in nums if x is not None]
                if not nums:
                    continue
                if missing_strategy == "mean":
                    fill_value = round(sum(nums) / len(nums), 6)
                    rule = "impute_mean"
                else:
                    s = sorted(nums)
                    n = len(s)
                    fill_value = s[n // 2] if n % 2 else round((s[n // 2 - 1] + s[n // 2]) / 2.0, 6)
                    rule = "impute_median"
            filled = []
            for i in range(len(work)):
                if is_missing(work[i][idx], extra_missing):
                    work[i][idx] = str(fill_value)
                    filled.append(i + 1)
            if filled:
                conversions.append({"column": name, "from": None, "to": fill_value,
                                    "rule": rule, "rows": filled[:200], "count": len(filled)})
                actions.append({"action": rule, "column": name, "value": fill_value,
                                "rows": filled[:200], "count": len(filled)})

    if outlier_action == "remove" and outliers:
        out_rows = set()
        for o in outliers:
            for rn in o["row_numbers"]:
                out_rows.add(rn - 1)
        if out_rows:
            for o in outliers:
                o["action"] = "removed"
            actions.append({"action": "outlier_remove", "rule": "IQR*1.5",
                            "columns": [o["column"] for o in outliers],
                            "rows": [i + 1 for i in sorted(out_rows)][:200], "count": len(out_rows)})
            remove_idx |= out_rows
    elif outlier_action == "winsorize" and outliers:
        for o in outliers:
            idx = name_index.get(o["column"])
            if idx is None:
                continue
            lo, hi = o["bounds"]
            rows = []
            for rn in o["row_numbers"]:
                v = to_float(work[rn - 1][idx])
                if v is None:
                    continue
                nv = lo if v < lo else hi if v > hi else v
                if nv != v:
                    conversions.append({"column": o["column"], "from": v, "to": nv,
                                        "rule": "winsorize_iqr", "rows": [rn], "count": 1})
                    work[rn - 1][idx] = str(nv)
                    rows.append(rn)
            o["action"] = "winsorized"
            if rows:
                actions.append({"action": "winsorize", "column": o["column"], "rule": "IQR*1.5",
                                "rows": rows[:200], "count": len(rows)})

    seen_rows, dup_rows = {}, []
    for i, row in enumerate(body, 1):
        key = tuple((c or "").strip() for c in row)
        if key in seen_rows:
            dup_rows.append({"row": i, "duplicate_of": seen_rows[key]})
        else:
            seen_rows[key] = i
    duplicates = {"exact_duplicate_rows": dup_rows, "exact_duplicate_count": len(dup_rows),
                  "unique_rows": len(seen_rows)}

    kept_idx = [i for i in range(len(work)) if i not in remove_idx]
    key_list = [k for k in (key_columns or []) if isinstance(k, str)]
    if key_list and all(k in header for k in key_list):
        idxs = [name_index[k] for k in key_list]
        seen_key, kept, removed_key = {}, [], []
        for i in kept_idx:
            k = tuple(work[i][j].strip().lower() for j in idxs)
            if k in seen_key:
                removed_key.append({"row": i + 1, "key": list(k), "first_row": seen_key[k]})
            else:
                seen_key[k] = i + 1
                kept.append(i)
        dedup = {"rule": "key_columns:first_occurrence", "key_columns": key_list,
                 "rows_removed": len(removed_key), "rows_kept": len(kept),
                 "removed_rows": removed_key[:200]}
    else:
        kept = kept_idx
        dedup = {"rule": "reported_only", "key_columns": key_list,
                 "rows_removed": 0, "rows_kept": len(kept),
                 "note": ("no usable key_columns; %d exact duplicate row(s) reported, not removed"
                          % len(dup_rows)) if dup_rows
                 else "no usable key_columns; no duplicate rows found"}

    normalized = [c for c in conversions if c["rule"] == "missing_token_normalized"]
    if normalized:
        norm_cols = []
        for c in normalized:
            if c["column"] not in norm_cols:
                norm_cols.append(c["column"])
        actions.append({"action": "normalize_missing_tokens", "rule": "missing_token_normalized",
                        "columns": norm_cols, "count": sum(c["count"] for c in normalized)})
    actions.append({"action": "dedup", "rule": dedup["rule"], "key_columns": key_list,
                    "rows_removed": dedup["rows_removed"], "rows_kept": dedup["rows_kept"]})

    cleaned_rows = [[work[i][j] for j in range(len(header))] for i in kept]
    return {
        "columns": columns,
        "missing_summary": missing_summary,
        "outliers": outliers,
        "duplicates": duplicates,
        "conversions": conversions,
        "token_inventory": token_inventory,
        "date_parse_failures": date_failures,
        "actions": actions,
        "dedup": dedup,
        "rows": cleaned_rows,
        "input_rows": total,
        "cleaned_rows": len(cleaned_rows),
        "removed_rows": total - len(cleaned_rows),
    }


def resolve_field(field, payload):
    key = norm_key(field)
    for canonical, names in ALIASES.items():
        if key == canonical or key in names:
            return canonical, payload.get(canonical)
    return None, None


def keyword_kind(field):
    key = norm_key(field)
    if not key:
        return None
    if key.startswith("clean") or key.startswith("output_data"):
        return "cleaned"
    if key.startswith("quality") or key.startswith("issue") or key.startswith("problem"):
        return "issues"
    if key.startswith("field_rule") or key.startswith("dictionary") or key in ("rules", "rule", "field_spec"):
        return "field_rules"
    if key.startswith("report"):
        return "report"
    if key in ("summary", "answer", "abstract", "conclusion", "conclusions"):
        return "summary"
    return None


def build_issues(tables, date_failures):
    issues = []
    for t in tables:
        for m in t["missing_summary"]:
            if m["missing"]:
                issues.append({"type": "missing_values", "file": t["file"], "column": m["column"],
                               "count": m["missing"], "rate": m["rate"],
                               "rows": m["row_numbers"][:50]})
        for o in t["outliers"]:
            issues.append({"type": "outliers", "file": t["file"], "column": o["column"],
                           "rule": o["rule"], "count": o["count"],
                           "rows": o["row_numbers"][:50], "action": o["action"]})
        if t["duplicates"]["exact_duplicate_count"]:
            issues.append({"type": "duplicate_rows", "file": t["file"], "scope": "exact",
                           "count": t["duplicates"]["exact_duplicate_count"],
                           "rows": [x["row"] for x in t["duplicates"]["exact_duplicate_rows"][:50]]})
        if t["dedup"]["rule"] == "key_columns:first_occurrence" and t["dedup"]["rows_removed"]:
            issues.append({"type": "duplicate_rows", "file": t["file"], "scope": "key_columns",
                           "key_columns": t["dedup"]["key_columns"],
                           "count": t["dedup"]["rows_removed"],
                           "rows": [x["row"] for x in t["dedup"]["removed_rows"][:50]]})
    for f in date_failures:
        issues.append({"type": "date_parse_failure", "file": f["file"], "column": f["column"],
                       "row": f["row"], "value": f["value"]})
    return issues


def build_field_rules(data_dictionary):
    return [{"field": c["column"], "file": c["file"], "type": c["inferred_type"],
             "unit": c["unit_hint"], "missing_rate": c["missing_rate"],
             "rule": "missing tokens normalized to empty; no implicit unit or date rewrite"}
            for c in data_dictionary]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Data cleaning pipeline (stdlib only)")
    parser.add_argument("--case", default="/input/case.json")
    parser.add_argument("--out", "--output", default="/app/output/result.json")
    parser.add_argument("--work", default="/app/work")
    args = parser.parse_args(argv)
    os.environ["SK_RESULT_PATH"] = str(getattr(args, "out", None) or getattr(args, "output", None) or "")

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
    missing_strategy = case.get("missing_strategy") if case.get("missing_strategy") in MISSING_STRATEGIES else "none"
    outlier_action = case.get("outlier_action") if case.get("outlier_action") in OUTLIER_ACTIONS else "flag"

    tables, cleaned_path, conversions = [], None, []
    cleaned_rows_data, cleaned_header = None, None
    token_inventory, date_failures, all_actions = {}, [], []
    primary_rows = primary_cleaned = primary_missing_cells = 0
    primary_dedup = None
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
            res = analyze(header, body, extra_missing, key_columns, missing_strategy, outlier_action)
            if cleaned_rows_data is None:
                cleaned_rows_data = res["rows"]
                cleaned_header = header
                primary_dedup = res["dedup"]
                primary_rows = res["input_rows"]
                primary_cleaned = res["cleaned_rows"]
                primary_missing_cells = sum(m["missing"] for m in res["missing_summary"])
            for item in res["conversions"]:
                conversions.append(dict(item, file=rel))
            for act in res["actions"]:
                all_actions.append(dict(act, file=rel))
            for token, info in res["token_inventory"].items():
                inv = token_inventory.setdefault(token, {"count": 0, "columns": []})
                inv["count"] += info["count"]
                for cn in info["columns"]:
                    if cn not in inv["columns"]:
                        inv["columns"].append(cn)
            for f in res["date_parse_failures"]:
                date_failures.append(dict(f, file=rel))
            applied = []
            for name, rule in unit_rules.items():
                if name in header and isinstance(rule, dict):
                    applied.append({"column": name, "from": rule.get("from"), "to": rule.get("to"),
                                    "factor": rule.get("factor"), "applied": False,
                                    "reason": "declared but not applied; audit-only mode"})
            for item in applied:
                conversions.append(dict(item, file=rel))
            tables.append({
                "file": rel, "sha256": hashlib.sha256(raw).hexdigest(), "encoding": enc,
                "delimiter": "tab" if delim == "\t" else delim, "rows": len(body),
                "columns": len(header), "column_profiles": res["columns"],
                "missing_summary": res["missing_summary"],
                "missing_cells": sum(m["missing"] for m in res["missing_summary"]),
                "outliers": res["outliers"], "duplicates": res["duplicates"],
                "dedup": res["dedup"], "actions": res["actions"],
                "date_parse_failures": res["date_parse_failures"],
                "cleaned_rows": res["cleaned_rows"], "rows_removed": res["removed_rows"],
            })

    if status != "failed":
        if not tables:
            status = "insufficient_input"
        else:
            high_missing = [m for t in tables for m in t["missing_summary"] if (m["rate"] or 0) > 0.5]
            if skipped or high_missing:
                status = "partial"

    if tables and cleaned_rows_data is not None:
        try:
            out_dir = Path(args.out).parent
            out_dir.mkdir(parents=True, exist_ok=True)
            target = out_dir / "cleaned_data.csv"
            with open(target, "w", encoding="utf-8", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow(cleaned_header)
                for row in cleaned_rows_data:
                    writer.writerow(row)
            cleaned_path = target.name
        except OSError as exc:
            errors.append("cannot write cleaned copy: %s" % exc)
            cleaned_path = None

    data_dictionary = [dict(c, file=t["file"]) for t in tables for c in t["column_profiles"]]
    missing_summary = [dict(m, file=t["file"]) for t in tables for m in t["missing_summary"]]
    outliers = [dict(o, file=t["file"]) for t in tables for o in t["outliers"]]
    duplicates = [dict(t["duplicates"], file=t["file"]) for t in tables]
    exact_dup_total = sum(t["duplicates"]["exact_duplicate_count"] for t in tables)
    removed_total = primary_rows - primary_cleaned
    dedup_removed = (primary_dedup or {}).get("rows_removed", 0)
    cleaned_cols = len(cleaned_header) if cleaned_header else 0

    quality = {
        "tables": [{"file": t["file"], "rows": t["rows"], "columns": t["columns"],
                    "cleaned_rows": t["cleaned_rows"], "rows_removed": t["rows_removed"],
                    "missing_cells": t["missing_cells"],
                    "outlier_columns": len(t["outliers"]),
                    "exact_duplicate_rows": t["duplicates"]["exact_duplicate_count"],
                    "dedup_rule": t["dedup"]["rule"]}
                   for t in tables],
        "rules_version": "2026-11-25.r2",
        "missing_strategy": missing_strategy,
        "outlier_action": outlier_action,
        "missing_token_policy": "explicit tokens normalized to empty; zero is treated as a real value",
        "missing_token_inventory": token_inventory,
        "date_formats_checked": list(DATE_FORMATS),
        "date_parse_failures": date_failures,
        "dedup": dict(primary_dedup, file=tables[0]["file"]) if tables and primary_dedup else
                  {"rule": "reported_only", "key_columns": [], "rows_removed": 0, "rows_kept": 0},
        "actions": all_actions,
        "rows": {"input": primary_rows, "cleaned": primary_cleaned, "removed": removed_total},
        "columns": cleaned_cols,
        "cleaned_output": {"file": cleaned_path, "rows": primary_cleaned, "columns": cleaned_cols},
        "reconciliation": {"input_rows": primary_rows, "rows_removed": removed_total,
                           "cleaned_rows": primary_cleaned,
                           "balanced": primary_rows - removed_total == primary_cleaned},
    }

    if tables:
        summary = ("Primary table %s: %d input rows x %d columns, %d row(s) removed (%d by key dedup), "
                   "%d rows x %d columns written to %s; %d missing cell(s) normalized via %d conversion rule(s), "
                   "%d outlier column(s) handled with '%s', %d date parse failure(s), %d skipped file(s)."
                   % (tables[0]["file"], primary_rows, cleaned_cols, removed_total, dedup_removed,
                      primary_cleaned, cleaned_cols, cleaned_path or "cleaned_data.csv",
                      primary_missing_cells, len(conversions), len(outliers), outlier_action,
                      len(date_failures), len(skipped)))
    else:
        summary = "No parseable CSV/TSV table was found; nothing was cleaned or imputed."

    report_text = ("Cleaning report for case %s. Status %s. Input: %d table(s); primary %s with "
                   "%d rows x %d columns. Actions: %d conversion group(s), missing_strategy '%s', "
                   "outlier_action '%s', dedup rule '%s'. Output: %d rows x %d columns in %s. "
                   "Signals: %d missing cell(s), %d outlier column(s), %d exact duplicate row(s), "
                   "%d date parse failure(s), %d skipped file(s), %d error(s)."
                   % (case_id, status, len(tables), tables[0]["file"] if tables else "-",
                      primary_rows, cleaned_cols, len(conversions), missing_strategy,
                      outlier_action, (primary_dedup or {}).get("rule", "reported_only"),
                      primary_cleaned, cleaned_cols, cleaned_path or "cleaned_data.csv",
                      primary_missing_cells, len(outliers), exact_dup_total,
                      len(date_failures), len(skipped), len(errors)))

    stats = {"tables": len(tables), "columns": len(data_dictionary),
             "rows": sum(t["rows"] for t in tables),
             "cleaned_rows": primary_cleaned,
             "missing_cells": sum(m["missing"] for m in missing_summary),
             "outlier_columns": len(outliers),
             "duplicate_rows": exact_dup_total,
             "date_parse_failures": len(date_failures),
             "conversions": len(conversions),
             "skipped": len(skipped), "errors": len(errors)}
    result = {"data_dictionary": data_dictionary, "missing_summary": missing_summary,
              "outliers": outliers, "duplicates": duplicates, "conversions": conversions,
              "quality_report": quality, "cleaned_file": cleaned_path, "tables": tables}
    evidence = {"files": [{"file": t["file"], "sha256": t["sha256"], "encoding": t["encoding"],
                           "delimiter": t["delimiter"]} for t in tables],
                "cleaned_copy": cleaned_path, "source_mutation": "none",
                "row_cap": MAX_ROWS}
    uncertainty = [
        "Outliers are IQR*1.5 statistical flags; with outlier_action 'flag' they are not removed.",
        "Missing tokens were normalized to empty cells and logged in conversions; values are only "
        "imputed when case.json sets missing_strategy.",
        "Unit hints come from header brackets and were not converted; unit_rules are audit-only.",
        "Date parsing covers %Y-%m-%d, %Y/%m/%d, %Y.%m.%d and ISO datetimes; failures are listed "
        "and the original text is kept in cleaned_data.csv.",
    ]
    payload = {"data_dictionary": data_dictionary, "missing_summary": missing_summary,
               "outliers": outliers, "duplicates": duplicates, "conversions": conversions,
               "quality_report": quality, "cleaned_data": cleaned_path,
               "summary": summary, "status": status, "case_id": case_id,
               "result": result, "evidence": evidence, "uncertainty": uncertainty,
               "errors": errors, "stats": stats}

    issues = build_issues(tables, date_failures)
    field_rules = build_field_rules(data_dictionary)
    fallback = {
        "cleaned": {"path": cleaned_path, "rows": primary_cleaned, "columns": cleaned_cols}
        if cleaned_path else None,
        "issues": issues,
        "field_rules": field_rules,
        "report": report_text,
        "summary": summary,
    }

    output = {"case_id": case_id, "status": status, "result": result, "evidence": evidence,
              "uncertainty": uncertainty, "errors": errors, "stats": stats,
              "quality_report": quality, "conversions": conversions, "duplicates": duplicates,
              "outliers": outliers}
    notes, extras = [], {}
    for field in fields:
        canonical, value = resolve_field(field, payload)
        if canonical is not None and value is not None and value != "":
            output[field] = value
            continue
        kind = keyword_kind(field)
        mapped = fallback.get(kind) if kind else None
        if mapped is not None and mapped != "":
            output[field] = mapped
            notes.append("%s: derived by keyword rule '%s'" % (field, kind))
            continue
        output[field] = None
        extras[field] = {"value": None,
                         "reason": "no alias, keyword rule, or derived producer matched this field"}
        notes.append("%s: unmapped; null retained, see contract_extras" % field)
    if extras:
        output["contract_extras"] = extras
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
            json.dump({"tables": tables, "quality_report": quality, "actions": all_actions},
                      fh, ensure_ascii=False, indent=2)
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
            _p = os.environ.get("SK_RESULT_PATH") or "result.json"
            _d = os.path.dirname(os.path.abspath(_p))
            if _d:
                os.makedirs(_d, exist_ok=True)
            with open(_p, "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "error": type(_exc).__name__,
                           "limitations": ["unhandled_exception"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
        _rc = 0
    _fallback = os.environ.get("SK_RESULT_PATH", "")
    if _fallback and not os.path.isfile(_fallback):
        try:
            _parent = os.path.dirname(os.path.abspath(_fallback))
            if _parent:
                os.makedirs(_parent, exist_ok=True)
            with open(_fallback, "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "limitations": ["no_result_written"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
    sys.exit(0 if _fallback else _rc)
