#!/usr/bin/env python3
import os, sys, json, re, datetime, urllib.parse
import requests

ORG = "eon-seed"
ADO = f"https://dev.azure.com/{ORG}"
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

CHILD_PROJECT = "AGI"
CHILD_AREA = "AGI\\MEET"
CHILD_STATE = "In Progress"

PARENT_PROJECT = "EEM Portfolio"

# Field mapping
CHILD_END = "Microsoft.VSTS.Scheduling.TargetDate"           # "Target Date" (child)
PARENT_END = "Custom.ImplementationEndDate"                 # "Implementation End Date" (parent)


def parse_iso(dt: str | None):
    if not dt:
        return None
    # ADO gives e.g. 2026-06-30T00:00:00Z
    return datetime.datetime.fromisoformat(dt.replace("Z", "+00:00"))


def wiql_children_in_progress():
    wiql = {
        "query": f"""
SELECT [System.Id]
FROM WorkItems
WHERE
  [System.TeamProject] = '{CHILD_PROJECT}'
  AND [System.WorkItemType] = 'Feature'
  AND [System.AreaPath] = '{CHILD_AREA}'
  AND [System.State] = '{CHILD_STATE}'
"""
    }
    url = f"{ADO}/{urllib.parse.quote(CHILD_PROJECT)}/_apis/wit/wiql?api-version={API}"
    r = requests.post(url, auth=AUTH, json=wiql)
    if r.status_code != 200:
        raise RuntimeError(f"WIQL failed HTTP={r.status_code}: {r.text[:400]}")
    return [w["id"] for w in r.json().get("workItems", [])]


def batch_get(ids, fields):
    if not ids:
        return []
    url = f"{ADO}/_apis/wit/workitemsbatch?api-version={API}"
    payload = {"ids": ids, "fields": fields}
    r = requests.post(url, auth=AUTH, json=payload)
    if r.status_code != 200:
        raise RuntimeError(f"workitemsbatch failed HTTP={r.status_code}: {r.text[:400]}")
    return r.json().get("value", [])


def get_parent_id(child_id: int):
    url = f"{ADO}/_apis/wit/workitems/{child_id}?$expand=relations&api-version={API}"
    r = requests.get(url, auth=AUTH)
    if r.status_code != 200:
        return None
    rels = r.json().get("relations") or []
    parent_urls = [x.get("url", "") for x in rels if x.get("rel") == "System.LinkTypes.Hierarchy-Reverse"]
    if not parent_urls:
        return None
    m = re.search(r"/workitems/(\d+)$", parent_urls[0])
    return int(m.group(1)) if m else None


def f(item, key):
    return (item.get("fields") or {}).get(key)


def main():
    ids = wiql_children_in_progress()
    print(f"Weekly MEET report — {datetime.datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %Z')}")
    print(f"Found {len(ids)} child features in {CHILD_PROJECT}/{CHILD_AREA} with state='{CHILD_STATE}'")

    if not ids:
        return 0

    children = batch_get(
        ids,
        [
            "System.Id",
            "System.Title",
            "System.State",
            "System.TeamProject",
            "System.AreaPath",
            CHILD_END,
        ],
    )

    # Resolve parents
    child_to_parent = {}
    for c in children:
        cid = c["id"]
        pid = get_parent_id(cid)
        if pid:
            child_to_parent[cid] = pid

    parent_ids = sorted(set(child_to_parent.values()))
    parents_by_id = {p["id"]: p for p in batch_get(parent_ids, [
        "System.Id",
        "System.Title",
        "System.State",
        "System.TeamProject",
        PARENT_END,
    ])}

    viol_1 = []
    viol_2 = []

    for c in children:
        cid = c["id"]
        pid = child_to_parent.get(cid)
        if not pid:
            continue
        p = parents_by_id.get(pid)
        if not p:
            continue
        if f(p, "System.TeamProject") != PARENT_PROJECT:
            continue

        c_state = f(c, "System.State")
        p_state = f(p, "System.State")

        # Check 1: Child in progress, parent not in progress
        if c_state == CHILD_STATE and p_state != CHILD_STATE:
            viol_1.append((cid, f(c, "System.Title"), pid, f(p, "System.Title"), p_state))

        # Check 2: child target date > parent impl end date
        c_end = parse_iso(f(c, CHILD_END))
        p_end = parse_iso(f(p, PARENT_END))
        if c_state == CHILD_STATE and c_end and p_end and c_end > p_end:
            viol_2.append((cid, f(c, "System.Title"), f(c, CHILD_END), pid, f(p, "System.Title"), f(p, PARENT_END)))

    print("\nCHECK 1: AGI\\MEET Feature is 'In Progress' but parent (EEM Portfolio) is NOT 'In Progress'")
    if not viol_1:
        print("  OK (no issues)")
    else:
        for cid, ctitle, pid, ptitle, pstate in viol_1:
            print(f"  Child {cid} | {ctitle}")
            print(f"    Parent {pid} | {ptitle} | state={pstate}")

    print("\nCHECK 2: AGI\\MEET Feature is 'In Progress' and its TargetDate > parent ImplementationEndDate")
    if not viol_2:
        print("  OK (no issues)")
    else:
        for cid, ctitle, cdate, pid, ptitle, pdate in viol_2:
            print(f"  Child {cid} | {ctitle} | TargetDate={cdate}")
            print(f"    Parent {pid} | {ptitle} | ImplementationEndDate={pdate}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
