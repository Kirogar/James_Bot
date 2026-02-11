# James_Bot

Automation + reporting scripts used by the Clawdbot assistant "James".

## MEET weekly report

Runs a consistency check between:
- Child features in **AGI** under area **AGI\\MEET** (state: `In Progress`)
- Their parent features in **EEM Portfolio**

Checks:
1) Child is `In Progress` but parent is **not** `In Progress`
2) Child Target Date (`Microsoft.VSTS.Scheduling.TargetDate`) is after parent Implementation End Date (`Custom.ImplementationEndDate`)

### Run

```bash
python3 weekly_meet_report.py
```

## MEET report: EEM Portfolio tagged MEET but missing AGI\\MEET child feature

Finds Features in **EEM Portfolio** that have tag **MEET** but do **not** have any child Feature in **AGI** under area **AGI\\MEET**.

### Run

```bash
python3 meet_missing_child_report.py
```

### Auth

Uses Azure DevOps PAT from either:
- `AZURE_DEVOPS_EXT_PAT` env var, or
- `~/.clawdbot/secrets/azure_devops_pat`
