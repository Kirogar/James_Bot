#!/usr/bin/env python3
import os, sys, re, datetime, urllib.parse
import requests

ORG = "eon-seed"
ADO = f"https://dev.azure.com/{ORG}"

# PAT: env var first, then local secret file (cron-safe)
PAT = os.environ.get("AZURE_DEVOPS_EXT_PAT")
if not PAT:
    try:
        with open(os.path.expanduser("~/.clawdbot/secrets/azure_devops_pat"), "r", encoding="utf-8") as fh:
            PAT = fh.read().strip()
    except FileNotFoundError:
        PAT = None
if not PAT:
    print(
        "ERROR: AZURE_DEVOPS_EXT_PAT not set and ~/.clawdbot/secrets/azure_devops_pat not found",
        file=sys.stderr,
    )
    sys.exit(2)

AUTH = ("", PAT)
API = "7.1"

PROJECT = "AGI"
AREA = "AGI\\MEET"
WIT = "Feature"
STATES = ["New", "In Progress"]
TARGET_DATE_FIELD = "Microsoft.VSTS.Scheduling.TargetDate"

# Progress fields: default guesses; if missing, we also try to discover by name.
PROGRESS_STATUS_FIELD_GUESS = "Custom.ProgressStatus"
PROGRESS_INFO_FIELD_GUESS = "Custom.ProgressInfo"
AMBER_VALUE = "2-Amber"

TZ_NAME = "Europe/Berlin"  # used for labeling only; we compute dates in local time


def parse_iso(dt: str | None):
    if not dt:
        return None
    return datetime.datetime.fromisoformat(dt.replace("Z", "+00:00"))


def batch_get(ids, fields):
    if not ids:
        return []
    url = f"{ADO}/_apis/wit/workitemsbatch?api-version={API}"
    payload = {"ids": ids, "fields": fields}
    r = requests.post(url, auth=AUTH, json=payload)
    if r.status_code != 200:
        raise RuntimeError(f"workitemsbatch failed HTTP={r.status_code}: {r.text[:400]}")
    return r.json().get("value", [])


def wiql_ids_for_state(state: str):
    wiql = {
        "query": f"""
SELECT [System.Id]
FROM WorkItems
WHERE
  [System.TeamProject] = '{PROJECT}'
  AND [System.WorkItemType] = '{WIT}'
  AND [System.AreaPath] = '{AREA}'
  AND [System.State] = '{state}'
"""
    }
    url = f"{ADO}/{urllib.parse.quote(PROJECT)}/_apis/wit/wiql?api-version={API}"
    r = requests.post(url, auth=AUTH, json=wiql)
    if r.status_code != 200:
        raise RuntimeError(f"WIQL failed HTTP={r.status_code}: {r.text[:400]}")
    return [w["id"] for w in r.json().get("workItems", [])]


def f(item, key):
    return (item.get("fields") or {}).get(key)


def discover_progress_fields(sample_item):
    fields = (sample_item.get("fields") or {})
    keys = list(fields.keys())

    # Prefer exact-ish names if present
    status_candidates = [k for k in keys if re.search(r"Progress.*Status", k, re.I)]
    info_candidates = [k for k in keys if re.search(r"Progress.*Info", k, re.I)]

    status_field = status_candidates[0] if status_candidates else None
    info_field = info_candidates[0] if info_candidates else None

    return status_field, info_field


def classify_target_date(target_dt: str | None, today: datetime.date, next_monday: datetime.date):
    if not target_dt:
        return "MISSING"

    dt = parse_iso(target_dt)
    if not dt:
        return "MISSING"

    d = dt.astimezone().date()

    if d < today:
        return "RED"

    # next calendar week: Monday..Sunday
    next_week_start = next_monday
    next_week_end = next_monday + datetime.timedelta(days=6)

    if next_week_start <= d <= next_week_end:
        return "YELLOW"

    if d >= today:
        return "GREEN"

    return "MISSING"


def main():
    now = datetime.datetime.now().astimezone()
    today = now.date()

    # next calendar week (Mon-Sun), relative to today
    # weekday(): Mon=0..Sun=6
    days_until_next_monday = (7 - today.weekday()) % 7
    if days_until_next_monday == 0:
        days_until_next_monday = 7
    next_monday = today + datetime.timedelta(days=days_until_next_monday)

    print(f"MEET health report — {now.strftime('%Y-%m-%d %H:%M %Z')} ({TZ_NAME})")
    print(f"Scope: {PROJECT} / {AREA} / {WIT}")
    print(f"Calendar week window (YELLOW): next week {next_monday} .. {next_monday + datetime.timedelta(days=6)}")

    # Collect all items across states so we can do data quality checks once
    all_ids = []
    ids_by_state = {}
    for state in STATES:
        ids = wiql_ids_for_state(state)
        ids_by_state[state] = ids
        all_ids.extend(ids)

    all_ids = sorted(set(all_ids))
    print(f"Total features (New + In Progress): {len(all_ids)}")

    # Try to load progress fields + target date
    base_fields = [
        "System.Id",
        "System.Title",
        "System.State",
        "System.AreaPath",
        TARGET_DATE_FIELD,
        PROGRESS_STATUS_FIELD_GUESS,
        PROGRESS_INFO_FIELD_GUESS,
    ]

    items = {w["id"]: w for w in batch_get(all_ids, base_fields)}

    # If progress field guesses don't exist, attempt discovery using one sample item
    progress_status_field = PROGRESS_STATUS_FIELD_GUESS
    progress_info_field = PROGRESS_INFO_FIELD_GUESS

    if all_ids:
        sample = items.get(all_ids[0])
        if sample:
            fields = sample.get("fields") or {}
            if progress_status_field not in fields or progress_info_field not in fields:
                ds, di = discover_progress_fields(sample)
                if ds:
                    progress_status_field = ds
                if di:
                    progress_info_field = di

    def wi_url(wid: int):
        # Human-clickable ADO link
        return f"{ADO}/{urllib.parse.quote(PROJECT)}/_workitems/edit/{wid}"

    # Per-state traffic light (also keep buckets for executive summary)
    state_buckets = {}
    for state in STATES:
        ids = ids_by_state[state]
        rows = [items[i] for i in ids if i in items]

        buckets = {"GREEN": [], "YELLOW": [], "RED": [], "MISSING": []}
        for w in rows:
            color = classify_target_date(f(w, TARGET_DATE_FIELD), today, next_monday)
            buckets[color].append(w)

        state_buckets[state] = buckets

    # Executive summary (top of the report)
    print("\nSUMMARY (quick) — focus: In Progress risks")
    ip = state_buckets.get("In Progress", {"RED": [], "YELLOW": [], "GREEN": [], "MISSING": []})
    print(
        f"  In Progress: GREEN={len(ip['GREEN'])} | YELLOW(next week)={len(ip['YELLOW'])} | RED(past)={len(ip['RED'])} | MISSING TargetDate={len(ip['MISSING'])}"
    )

    # Show up to 3 most urgent items: missing TargetDate first, then RED
    urgent = (ip.get("MISSING") or []) + (ip.get("RED") or [])
    if not urgent:
        print("  Top issues: none")
    else:
        print("  Top issues (up to 3):")
        for w in urgent[:3]:
            td = f(w, TARGET_DATE_FIELD)
            td_txt = td if td else "MISSING"
            print(f"    - {w['id']} | {f(w,'System.Title')} | TargetDate={td_txt}")
            print(f"      {wi_url(w['id'])}")

    # Detailed per-state output
    for state in STATES:
        buckets = state_buckets[state]

        print(f"\nSTATE = {state}")
        print(
            f"  GREEN={len(buckets['GREEN'])} | YELLOW={len(buckets['YELLOW'])} | RED={len(buckets['RED'])} | MISSING TargetDate={len(buckets['MISSING'])}"
        )

        # List only the problematic ones by default
        if buckets["RED"]:
            print("  RED (TargetDate in the past):")
            for w in buckets["RED"]:
                print(f"    - {w['id']} | {f(w,'System.Title')} | TargetDate={f(w, TARGET_DATE_FIELD)}")
                print(f"      {wi_url(w['id'])}")
        if buckets["YELLOW"]:
            print("  YELLOW (TargetDate next calendar week):")
            for w in buckets["YELLOW"]:
                print(f"    - {w['id']} | {f(w,'System.Title')} | TargetDate={f(w, TARGET_DATE_FIELD)}")
                print(f"      {wi_url(w['id'])}")
        if buckets["MISSING"]:
            print("  MISSING TargetDate:")
            for w in buckets["MISSING"]:
                print(f"    - {w['id']} | {f(w,'System.Title')}")
                print(f"      {wi_url(w['id'])}")

    # Data quality checks
    print("\nDATA QUALITY")
    print(f"  Progress Status field used: {progress_status_field}")
    print(f"  Progress Info field used: {progress_info_field}")

    missing_progress_status = []
    amber_missing_info = []

    for wid in all_ids:
        w = items.get(wid)
        if not w:
            continue
        ps = f(w, progress_status_field)
        pi = f(w, progress_info_field)

        if ps is None or (isinstance(ps, str) and ps.strip() == ""):
            missing_progress_status.append(w)
            continue

        if isinstance(ps, str) and ps.strip() == AMBER_VALUE:
            if pi is None or (isinstance(pi, str) and pi.strip() == ""):
                amber_missing_info.append(w)

    if not missing_progress_status:
        print("  OK: All features have Progress Status")
    else:
        print(f"  Missing Progress Status: {len(missing_progress_status)}")
        for w in missing_progress_status:
            print(f"    - {w['id']} | {f(w,'System.State')} | {f(w,'System.Title')}")
            print(f"      {wi_url(w['id'])}")

    if not amber_missing_info:
        print(f"  OK: All '{AMBER_VALUE}' features have Progress Info")
    else:
        print(f"  '{AMBER_VALUE}' but missing Progress Info: {len(amber_missing_info)}")
        for w in amber_missing_info:
            print(f"    - {w['id']} | {f(w,'System.State')} | {f(w,'System.Title')}")
            print(f"      {wi_url(w['id'])}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
