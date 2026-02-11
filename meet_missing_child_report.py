#!/usr/bin/env python3
import os
import sys
import re
import time
import datetime
import urllib.parse
import requests

# Report: EEM Portfolio Features tagged MEET but missing AGI\\MEET child Feature

ORG = "eon-seed"
ADO = f"https://dev.azure.com/{ORG}"
API = "7.1"

PARENT_PROJECT = "EEM Portfolio"
CHILD_PROJECT = "AGI"
CHILD_AREA = "AGI\\MEET"

# Tag to look for in System.Tags
MEET_TAG = "MEET"

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
    # Human clickable link
    return f"{ADO}/{urllib.parse.quote(project)}/_workitems/edit/{wid}"


def wiql_parent_meet_features() -> list[int]:
    # Note: Tags are semi-colon separated string. CONTAINS works.
    wiql = {
        "query": f"""
SELECT [System.Id]
FROM WorkItems
WHERE
  [System.TeamProject] = '{PARENT_PROJECT}'
  AND [System.WorkItemType] = 'Feature'
  AND [System.Tags] CONTAINS '{MEET_TAG}'
"""
    }
    url = f"{ADO}/{urllib.parse.quote(PARENT_PROJECT)}/_apis/wit/wiql?api-version={API}"
    r = requests.post(url, auth=AUTH, json=wiql, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"WIQL failed HTTP={r.status_code}: {r.text[:400]}")
    return [w["id"] for w in r.json().get("workItems", [])]


def batch_get(ids: list[int], fields: list[str]) -> list[dict]:
    if not ids:
        return []
    url = f"{ADO}/_apis/wit/workitemsbatch?api-version={API}"
    payload = {"ids": ids, "fields": fields}
    r = requests.post(url, auth=AUTH, json=payload, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"workitemsbatch failed HTTP={r.status_code}: {r.text[:400]}")
    return r.json().get("value", [])


def get_child_ids(parent_id: int) -> list[int]:
    # Parent has forward links to children.
    url = f"{ADO}/_apis/wit/workitems/{parent_id}?$expand=relations&api-version={API}"
    r = requests.get(url, auth=AUTH, timeout=30)
    if r.status_code != 200:
        return []

    rels = r.json().get("relations") or []
    child_urls = [
        x.get("url", "")
        for x in rels
        if x.get("rel") == "System.LinkTypes.Hierarchy-Forward" and x.get("url")
    ]

    ids = []
    for u in child_urls:
        m = re.search(r"/workitems/(\d+)$", u)
        if m:
            ids.append(int(m.group(1)))
    return ids


def f(item: dict, key: str):
    return (item.get("fields") or {}).get(key)


def main() -> int:
    now = datetime.datetime.now().astimezone()
    print(f"MEET missing child report — {now.strftime('%Y-%m-%d %H:%M %Z')}")

    parent_ids = wiql_parent_meet_features()
    print(f"Found {len(parent_ids)} EEM Portfolio Features tagged '{MEET_TAG}'")

    parents = batch_get(
        parent_ids,
        [
            "System.Id",
            "System.Title",
            "System.State",
            "System.TeamProject",
            "System.Tags",
        ],
    )

    missing = []

    # Loop parents and inspect child links.
    # Throttle slightly to be kind to ADO.
    total = len(parents)
    for idx, p in enumerate(parents, start=1):
        pid = p.get("id")
        if not pid:
            continue

        if idx == 1 or idx % 25 == 0:
            print(f"Checking parents: {idx}/{total}...", flush=True)

        child_ids = get_child_ids(pid)

        has_meet_child = False
        if child_ids:
            children = batch_get(
                child_ids,
                [
                    "System.Id",
                    "System.TeamProject",
                    "System.AreaPath",
                    "System.WorkItemType",
                    "System.State",
                    "System.Title",
                ],
            )
            for c in children:
                if f(c, "System.TeamProject") == CHILD_PROJECT and f(c, "System.AreaPath") == CHILD_AREA and f(c, "System.WorkItemType") == "Feature":
                    has_meet_child = True
                    break

        if not has_meet_child:
            missing.append(p)

        time.sleep(0.05)

    print(f"\nMissing child features in {CHILD_PROJECT}/{CHILD_AREA}: {len(missing)}")
    if not missing:
        print("OK (keine fehlenden Child Features gefunden)")
        return 0

    print("\nLISTE:")
    for p in sorted(missing, key=lambda x: x.get("id", 0)):
        pid = p["id"]
        title = f(p, "System.Title")
        state = f(p, "System.State")
        print(f"- {pid} | {state} | {title}")
        print(f"  {wi_url(PARENT_PROJECT, pid)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
