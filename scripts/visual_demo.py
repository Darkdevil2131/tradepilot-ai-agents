"""Run from the repo root: python -m scripts.visual_demo output.html"""
from copy import deepcopy
from html import escape
import json
from pathlib import Path
import sys

from tradepilot.agents.orchestrator import LogisticsOrchestrator
from tradepilot.models import ShipmentRequest

root = Path(__file__).resolve().parents[1]
base = json.loads((root / "examples/impact_comparison.json").read_text())
base.update(dimensions_cm=[100, 80, 60], declared_value=500000, incoterm="CIF", documents=[
    dict(document_type="commercial_invoice",fields=dict(invoice_number="SIM-500",shipper="Demo Dubai",consignee="Demo Mumbai",currency="INR",total=500000)),
    dict(document_type="packing_list",fields=dict(package_count=10)),
    dict(document_type="bill_of_lading",fields=dict(consignee="Demo Mumbai"))])

def table(headers, rows):
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body = "".join("<tr>"+"".join(f"<td>{escape(str(c))}</td>" for c in row)+"</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

def money(value):
    return f"₹{value:,.0f}"

def details(title, data):
    return f"<details><summary>{escape(title)}</summary><pre>{escape(json.dumps(data,indent=2))}</pre></details>"

graph = LogisticsOrchestrator()
panels = []
tabs = []
for index, title in enumerate(["Urgent shipment","Ocean closure","Low delay cost","Invoice conflict","Hazard review"]):
    payload = deepcopy(base)
    if index == 1:
        payload["impact_scenario"]["closed_leg_ids"] = ["ocean"]
    if index == 2:
        payload["impact_scenario"]["daily_stoppage_inr"] = 1000
    if index == 3:
        payload["documents"][0]["fields"]["total"] = 700000
    if index == 4:
        payload["cargo_description"] = "lithium-ion batteries"
    run = graph.analyze(ShipmentRequest(**payload)).model_dump(mode="json")
    agents = run["agents"]
    coord = agents["logistics_orchestrator"]["data"]
    decision = coord["decision"]
    exp = coord["explanation"]
    plan = decision["candidate"] or {}
    routes = agents["route_optimization"]["data"]["impact_comparison"]
    tabs.append(f'<option value="{index}">{escape(title)}</option>')
    panel = [f'<section id="s{index}" '+("hidden" if index else "")+'>',
             f'<h1>{escape(title)}</h1><p class="sub">Dubai → Mumbai · 500 kg · 100 kg production-critical · target day 5</p>',
             '<div class="metrics">'+''.join(f'<article><small>{k}</small><strong>{v}</strong></article>' for k,v in [
                 ("Selected plan",plan.get("option","Withheld")),("Modeled total impact",money(plan["total_impact_inr"]) if plan else "—"),
                 ("Daily loss assumption",money(payload["impact_scenario"]["daily_stoppage_inr"]))])+'</div>',
             '<p class="callout">'+escape(exp["conclusion"])+'</p>',
             '<h2>Decision checks</h2>'+table(["Check","Result","Reason"],[(g["gate"],g["status"],g["reason"]) for g in decision["gates"]])]
    if plan:
        panel += ['<h2>Who moves each leg</h2>'+table(["Leg","Fictional provider","Load (kg)","Freight"],[(l["leg"],l["carrier_id"],l["weight_kg"],money(l["freight_inr"])) for l in plan["legs"]])]
    if exp["comparisons"]:
        panel += ['<h2>Alternative strategies</h2>'+table(["Option","Freight","Delay loss","Total impact"],[(o["option"],money(o["freight_inr"]),money(o["delay_loss_inr"]),money(o["total_impact_inr"])) for o in exp["comparisons"]])]
    panel += ['<h2>Evidence from all six agents</h2>']
    for name, result in agents.items():
        panel.append(details(name.replace("_"," ").title()+" · "+result["status"],result))
    panel += [details("Exact input",payload),details("Full workflow output",run),'</section>']
    panels.extend(panel)
    print(title, decision["status"], plan.get("option"), plan.get("total_impact_inr"))

html = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TradePilot | Shipment decisions</title><style>
*{box-sizing:border-box}body{background:#f5f4ef;color:#172c32;font:16px/1.5 system-ui;margin:0}header{background:#153c3d;color:white;padding:18px 6vw;display:flex;justify-content:space-between}main{max-width:1100px;margin:auto;padding:32px}h1{font-size:42px;letter-spacing:-2px;margin:20px 0 0}h2{font-size:20px;margin-top:28px}.sub,small{color:#476366}.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}article,details,table{background:white;border:1px solid #d8dfdb;border-radius:8px}article{padding:16px}strong,small{display:block}strong{font-size:27px}.callout{background:#dcece4;padding:15px;border-left:4px solid #267055}table{width:100%;border-collapse:collapse;text-align:left}td,th{border-bottom:1px solid #dae1dc;padding:8px}th{color:#44605d;font-size:13px}details,select{padding:10px;margin:6px 0}summary,select{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere}label{font-weight:650}select{font:inherit} @media(max-width:700px){main{padding:12px}.metrics{grid-template-columns:1fr}h1{font-size:32px}td,th{padding:4px;font-size:13px}}
</style><header><b>TradePilot AI</b><span>Synthetic data · Recorded local runs</span></header><main><label>Compare a scenario <select id="choice">"""
html += "".join(tabs)+'</select></label>'+"".join(panels)+"""</main><script>choice.onchange=()=>document.querySelectorAll("main section").forEach((s,i)=>s.hidden=i!==Number(choice.value));</script></html>"""
Path(sys.argv[1]).write_text(html, encoding="utf-8")
