import json,sys

p=json.load(sys.stdin)
f=p.get("fields",{}) or {}

title=f.get("System.Title","")
desc=f.get("System.Description","")
start=f.get("Microsoft.VSTS.Scheduling.StartDate")
target=f.get("Microsoft.VSTS.Scheduling.TargetDate")

ops=[]
def add(name,val):
    if val is None: 
        return
    ops.append({"op":"add","path":f"/fields/{name}","value":val})

add("System.Title", title)
add("System.AreaPath", "AGI\\MEET")
add("System.State", "New")
add("System.Description", desc)
add("Microsoft.VSTS.Scheduling.StartDate", start)
add("Microsoft.VSTS.Scheduling.TargetDate", target)

parent_id=p.get("id")
org="https://dev.azure.com/eon-seed"
ops.append({
  "op":"add",
  "path":"/relations/-",
  "value":{
    "rel":"System.LinkTypes.Hierarchy-Reverse",
    "url":f"{org}/_apis/wit/workitems/{parent_id}",
    "attributes":{"comment":"Auto-linked from EEM Portfolio (MEET) -> AGI\\MEET"}
  }
})

print(json.dumps(ops))
