#!/usr/bin/env python3
import os
import sys
import re
import time
import datetime
import urllib.parse
import requests

# Report: EEM Portfolio Features (Ready For Delivery + tag MEET) missing a child Feature in AGI\MEET

ORG = "eon-seed"
ADO = f"https://dev.azure.com/{ORG}"
API = "7.1"

PARENT_PROJECT = "EEM Portfolio"
PARENT_STATE = "Ready For Delivery"
MEET_TAG = "MEET"

CHILD_PROJECT = "AGI"
CHILD_AREA = "AGI\\MEET"  # incl. sub-areas

# Auth: env var first, then local secret file (cron-safe)
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
    raise SystemExit(2)

AUTH = ("", PAT)


def wi_url(project: str, wid: int) -> str:
    return f"{ADO}/{urllib.parse.quote(project)}/_workitems/edit/{wid}"


def batch_get(ids: list[int], fields: list[str]) -> list[dict]:
    if not ids:
        return []
    url = f"{ADO}/_apis/wit/workitemsbatch?api-version={API}"
    payload = {"ids": ids, "fields": fields}
    r = requests.post(url, auth=AUTH, json=payload, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"workitemsbatch failed HTTP={r.status_code}: {r.text[:400]}")
    return r.json().get("value", [])


def wiql_parent_meet_features() -> list[int]:
    # Note: Tags are semi-colon separated string. CONTAINS works.
    wiql = {
        "query": f"""
SELECT [System.Id]
FROM WorkItems
WHERE
  [System.TeamProject] = '{PARENT_PROJECT}'
  AND [System.WorkItemType] = 'Feature'
  AND [System.State] = '{PARENT_STATE}'
  AND [System.Tags] CONTAINS '{MEET_TAG}'
"""
    }
    url = f"{ADO}/{urllib.parse.quote(PARENT_PROJECT)}/_apis/wit/wiql?api-version={API}"
    r = requests.post(url, auth=AUTH, json=wiql, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"WIQL failed HTTP={r.status_code}: {r.text[:400]}")
    return [w["id"] for w in r.json().get("workItems", [])]


def wiql_children_in_meet_area() -> list[int]:
    # UNDER includes the area itself + all sub-areas.
    wiql = {
        "query": f"""
SELECT [System.Id]
FROM WorkItems
WHERE
  [System.TeamProject] = '{CHILD_PROJECT}'
  AND [System.WorkItemType] = 'Feature'
  AND [System.AreaPath] UNDER '{CHILD_AREA}'
"""
    }
    url = f"{ADO}/{urllib.parse.quote(CHILD_PROJECT)}/_apis/wit/wiql?api-version={API}"
    r = requests.post(url, auth=AUTH, json=wiql, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"WIQL failed HTTP={r.status_code}: {r.text[:400]}")
    return [w["id"] for w in r.json().get("workItems", [])]


def get_parent_id(child_id: int) -> int | None:
    # Child has reverse link to parent.
    url = f"{ADO}/_apis/wit/workitems/{child_id}?$expand=relations&api-version={API}"
    r = requests.get(url, auth=AUTH, timeout=30)
    if r.status_code != 200:
        return None
    rels = r.json().get("relations") or []
    parent_urls = [x.get("url", "") for x in rels if x.get("rel") == "System.LinkTypes.Hierarchy-Reverse"]
    if not parent_urls:
        return None
    # ADO may return .../workItems/<id> (capital I)
    m = re.search(r"/workitems/(\d+)$", parent_urls[0], re.IGNORECASE)
    return int(m.group(1)) if m else None


def f(item: dict, key: str):
    return (item.get("fields") or {}).get(key)


def main() -> int:
    now = datetime.datetime.now().astimezone()
    print(f"MEET missing child report — {now.strftime('%Y-%m-%d %H:%M %Z')}")
    print(f"Parent filter: {PARENT_PROJECT} | State='{PARENT_STATE}' | Tag contains '{MEET_TAG}'")
    print(f"Child filter: {CHILD_PROJECT} | AreaPath UNDER '{CHILD_AREA}'")

    parent_ids = wiql_parent_meet_features()
    print(f"Found {len(parent_ids)} parent Features")

    # Determine which parents DO have a child Feature in AGI\MEET.
    child_ids = wiql_children_in_meet_area()
    print(f"Found {len(child_ids)} child Features in {CHILD_PROJECT}/{CHILD_AREA} (all states)")

    parents_with_child = set()

    # Resolve parent ids from children (robust even if parent does not expose forward links)
    for idx, cid in enumerate(child_ids, start=1):
        if idx == 1 or idx % 200 == 0:
            print(f"Resolving child->parent links: {idx}/{len(child_ids)}...", flush=True)
        pid = get_parent_id(cid)
        if pid:
            parents_with_child.add(pid)
        time.sleep(0.01)

    missing_ids = [pid for pid in parent_ids if pid not in parents_with_child]

    print(f"\nMissing child Features in {CHILD_PROJECT}/{CHILD_AREA}: {len(missing_ids)}")
    if not missing_ids:
        print("OK (keine fehlenden Child Features gefunden)")
        return 0

    parents = batch_get(
        missing_ids,
        [
            "System.Id",
            "System.Title",
            "System.State",
            "System.TeamProject",
            "System.Tags",
        ],
    )

    print("\nLISTE:")
    for p in sorted(parents, key=lambda x: x.get("id", 0)):
        pid = p["id"]
        title = f(p, "System.Title")
        state = f(p, "System.State")
        print(f"- {pid} | {state} | {title}")
        print(f"  {wi_url(PARENT_PROJECT, pid)}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0)
