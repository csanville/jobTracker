from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime
from pathlib import Path

from common import DASHBOARD_PATH

PLOTLY_PATH = Path(__file__).with_name("plotly.min.js")
NO_APPLICATION = "Recruiter Outreach (No Application)"
CLOSED = {
    "Offer",
    "Rejected",
    "Ghosted (Post-Engagement)",
    "No Response (6mo+, Application Stage)",
    "Withdrawn",
    NO_APPLICATION,
}
STAGE_BY_STATUS = {
    "Phone Screen Requested": "Recruiter/Phone",
    "Phone Screen Completed": "Recruiter/Phone",
    "Code/Technical Screen Requested": "Technical",
    "Code/Technical Screen Completed": "Technical",
    "Onsite/Final Interview Requested": "Onsite/Final",
    "Onsite/Final Interview Completed": "Onsite/Final",
    "Offer": "Offer",
}


def _application_payload(apps: list[dict]) -> list[dict]:
    today = date.today()
    return [
        {
            "app_id": app["app_id"],
            "company": app.get("company") or "",
            "role": app.get("role") or "",
            "track": app.get("track") or "",
            "source": app.get("source") or "",
            "status": app.get("status") or "",
            "date_applied": (
                app["date_applied"].isoformat() if app.get("date_applied") else None
            ),
            "last_activity": (
                app["last_activity_date"].isoformat()
                if app.get("last_activity_date") else None
            ),
            "days": (
                (today - app["last_activity_date"]).days
                if app.get("last_activity_date") else None
            ),
            "attempt": app.get("attempt"),
            "notes": app.get("notes") or "",
        }
        for app in apps
    ]


def _event_payload(apps: list[dict]) -> list[dict]:
    """Create the stage snapshot needed by the interactive dashboard.

    This project stores current workbook state rather than a separate event
    database, so every application gets an Applied event plus its current
    reached stage when that stage is represented by the status.
    """
    events = []
    for app in apps:
        app_id = app["app_id"]
        applied = app.get("date_applied")
        last = app.get("last_activity_date") or applied
        status = app.get("status") or ""
        events.append({
            "app_id": app_id,
            "stage": "Applied",
            "event_date": applied.isoformat() if applied else None,
            "provenance": "workbook",
            "status": status,
        })
        stage = STAGE_BY_STATUS.get(status)
        if stage:
            events.append({
                "app_id": app_id,
                "stage": stage,
                "event_date": last.isoformat() if last else None,
                "provenance": "workbook",
                "status": status,
            })
    return events


def build(apps: list[dict], out_path=None) -> tuple[Path, dict]:
    output = Path(out_path) if out_path else DASHBOARD_PATH
    generated = datetime.now().isoformat(timespec="seconds")
    payload = {
        "generated": generated,
        "apps": _application_payload(apps),
        "events": _event_payload(apps),
        "reviews": [],
        "last_run": {"status": "complete", "completed_at": generated},
    }
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    plotly = PLOTLY_PATH.read_text(encoding="utf-8")
    document = _template(data, plotly)
    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        prefix=".dashboard.", suffix=".tmp.html", dir=output.parent
    )
    os.close(handle)
    temp = Path(temp_name)
    try:
        temp.write_text(document, encoding="utf-8")
        os.replace(temp, output)
        output.chmod(0o644)
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    submitted = [a for a in apps if a.get("status") != NO_APPLICATION]
    metrics = {
        "total": len(submitted),
        "active": sum(1 for a in submitted if a.get("status") not in CLOSED),
        "rejection_rate": (
            100 * sum(1 for a in submitted if a.get("status") == "Rejected")
            / len(submitted) if submitted else 0
        ),
    }
    return output, metrics


def _template(data: str, plotly: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Job Search Dashboard</title>
<style>
:root {{
  --bg:#f5f7fb;--panel:#fff;--ink:#172033;--muted:#667085;--line:#e4e8f0;
  --blue:#276ef1;--green:#15845b;--amber:#b86e00;--red:#c93b43;--shadow:0 8px 28px #1f2f4d12;
}}
@media(prefers-color-scheme:dark){{:root{{
  --bg:#101522;--panel:#171e2d;--ink:#edf2ff;--muted:#a5afc3;--line:#29334a;
  --blue:#6ea1ff;--green:#56c79a;--amber:#f0b35b;--red:#ff7e86;--shadow:0 8px 28px #0005;
}}}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 Inter,Segoe UI,system-ui,sans-serif}}
.shell{{max-width:1500px;margin:auto;padding:28px}} header{{display:flex;justify-content:space-between;align-items:end;gap:20px;margin-bottom:20px}}
h1{{font-size:28px;margin:0 0 3px}} .sub,.muted{{color:var(--muted)}} .status{{text-align:right}}
.filters{{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:12px;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px;box-shadow:var(--shadow)}}
label{{font-size:12px;color:var(--muted);display:block}} select,input{{width:100%;margin-top:4px;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)}}
.kpis{{display:grid;grid-template-columns:repeat(7,1fr);gap:12px;margin:16px 0}} .kpi{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:15px;box-shadow:var(--shadow)}}
.kpi .value{{font-size:27px;font-weight:700;margin-top:5px}} .grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px;box-shadow:var(--shadow);min-width:0}}
.card h2{{font-size:16px;margin:0 0 4px}} .chart{{height:330px}} .wide{{grid-column:1/-1}}
table{{width:100%;border-collapse:collapse}} th,td{{text-align:left;padding:9px 8px;border-bottom:1px solid var(--line);vertical-align:top}} th{{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.04em}}
.scroll{{overflow:auto;max-height:380px}} .pill{{display:inline-block;padding:2px 7px;border-radius:99px;background:color-mix(in srgb,var(--blue) 14%,transparent)}}
.warning{{color:var(--amber);font-weight:600}} footer{{color:var(--muted);padding:20px 2px}}
@media(max-width:1000px){{.kpis{{grid-template-columns:repeat(3,1fr)}}.grid{{grid-template-columns:1fr}}.wide{{grid-column:auto}}}}
@media(max-width:650px){{.shell{{padding:14px}}header{{display:block}}.status{{text-align:left;margin-top:8px}}.filters{{grid-template-columns:1fr 1fr}}.kpis{{grid-template-columns:1fr 1fr}}}}
</style>
<script>{plotly}</script>
</head>
<body><main class="shell">
<header><div><h1>Job Search Dashboard</h1><div class="sub">Current pipeline from the Applications worksheet</div></div>
<div class="status" id="run-status"></div></header>
<section class="filters">
  <label>Applied from<input type="date" id="date-from"></label>
  <label>Applied through<input type="date" id="date-to"></label>
  <label>Track<select id="track"><option value="">All tracks</option></select></label>
  <label>Source<select id="source"><option value="">All sources</option></select></label>
</section>
<section class="kpis" id="kpis"></section>
<section class="grid">
  <article class="card"><h2>Applications over time</h2><div class="muted">Monthly volume</div><div id="trend" class="chart"></div></article>
  <article class="card"><h2>Current status</h2><div class="muted">Current workbook state</div><div id="status" class="chart"></div></article>
  <article class="card"><h2>Funnel</h2><div class="muted">Current stage recorded in the workbook</div><div id="funnel" class="chart"></div></article>
  <article class="card"><h2>Active aging</h2><div class="muted">Days since last activity</div><div id="aging" class="chart"></div></article>
  <article class="card"><h2>Source performance</h2><div class="muted">Applications and employer response rate</div><div id="sources" class="chart"></div></article>
  <article class="card"><h2>Track performance</h2><div class="muted">Applications and positive engagement</div><div id="tracks" class="chart"></div></article>
  <article class="card"><h2>Repeat companies</h2><div class="muted">Companies with multiple attempts</div><div id="companies" class="chart"></div></article>
  <article class="card"><h2>Data quality</h2><div class="muted">Stage records derived from the workbook</div><div id="quality" class="chart"></div></article>
  <article class="card wide"><h2>Active follow-up priorities</h2><div class="scroll"><table id="followups"></table></div></article>
  <article class="card wide"><h2>Recent activity</h2><div class="scroll"><table id="recent"></table></div></article>
  <article class="card wide"><h2>Review queue <span class="warning" id="review-count"></span></h2><div class="muted">No separate review queue is used in workbook mode</div><div class="scroll"><table id="reviews"></table></div></article>
</section>
<footer>Generated locally from the Applications worksheet <span id="generated"></span>. No email bodies or Gmail identifiers are included.</footer>
</main>
<script>
const DB={data};
const CLOSED=new Set(["Offer","Rejected","Ghosted (Post-Engagement)","No Response (6mo+, Application Stage)","Withdrawn","Recruiter Outreach (No Application)"]);
const NOAPP="Recruiter Outreach (No Application)";
const STAGES=["Applied","Recruiter/Phone","Technical","Onsite/Final","Offer"];
const COLORS=["#276ef1","#4c8df6","#76a9fa","#9ec5ff","#15845b","#b86e00","#c93b43"];
const layout={{paper_bgcolor:"rgba(0,0,0,0)",plot_bgcolor:"rgba(0,0,0,0)",font:{{color:getComputedStyle(document.documentElement).getPropertyValue("--ink"),family:"Inter, Segoe UI, sans-serif"}},margin:{{l:55,r:18,t:20,b:50}}}};
function unique(k){{return [...new Set(DB.apps.map(x=>x[k]).filter(Boolean))].sort()}}
function fillSelect(id,values){{const el=document.getElementById(id);values.forEach(v=>el.add(new Option(v,v)))}}
fillSelect("track",unique("track"));fillSelect("source",unique("source"));
const dates=DB.apps.map(x=>x.date_applied).filter(Boolean).sort();if(dates.length){{document.getElementById("date-from").value=dates[0];document.getElementById("date-to").value=dates.at(-1)}}
["date-from","date-to","track","source"].forEach(id=>document.getElementById(id).addEventListener("change",render));
function filtered(){{const f=document.getElementById("date-from").value,t=document.getElementById("date-to").value,tr=document.getElementById("track").value,s=document.getElementById("source").value;return DB.apps.filter(a=>(!f||!a.date_applied||a.date_applied>=f)&&(!t||!a.date_applied||a.date_applied<=t)&&(!tr||a.track===tr)&&(!s||a.source===s))}}
function counts(rows,key){{const m={{}};rows.forEach(x=>m[x[key]||"Unspecified"]=(m[x[key]||"Unspecified"]||0)+1);return m}}
function farthest(appIds){{const m={{}};DB.events.filter(e=>appIds.has(e.app_id)).forEach(e=>m[e.app_id]=Math.max(m[e.app_id]??0,STAGES.indexOf(e.stage)));return m}}
function responded(a,stages){{return (stages[a.app_id]??0)>0 || !["Awaiting Response","No Response (6mo+, Application Stage)"].includes(a.status)}}
function plot(id,data,extra={{}}){{Plotly.react(id,data,{{...layout,...extra}},{{displayModeBar:false,responsive:true}})}}
function pct(n,d){{return d?`${{(100*n/d).toFixed(0)}}%`:"—"}}
function esc(v){{const e=document.createElement("span");e.textContent=v??"";return e.innerHTML}}
function table(id,headers,rows){{document.getElementById(id).innerHTML=`<thead><tr>${{headers.map(h=>`<th>${{esc(h)}}</th>`).join("")}}</tr></thead><tbody>${{rows.map(r=>`<tr>${{r.map(v=>`<td>${{v}}</td>`).join("")}}</tr>`).join("")}}</tbody>`}}
function render(){{
 const all=filtered(),rows=all.filter(a=>a.status!==NOAPP),ids=new Set(rows.map(a=>a.app_id)),stages=farthest(ids),active=rows.filter(a=>!CLOSED.has(a.status)),responses=rows.filter(a=>responded(a,stages)),positive=rows.filter(a=>(stages[a.app_id]??0)>0),recent30=rows.filter(a=>a.date_applied&&((Date.now()-Date.parse(a.date_applied))/86400000)<=30);
 const cards=[["Applications",rows.length],["Active",active.length],["Last 30 days",recent30.length],["Response rate",pct(responses.length,rows.length)],["Current advanced stage",pct(positive.length,rows.length)],["Offers",rows.filter(a=>a.status==="Offer").length],["Needs review",DB.reviews.length]];
 document.getElementById("kpis").innerHTML=cards.map(x=>`<div class="kpi"><div class="muted">${{x[0]}}</div><div class="value">${{x[1]}}</div></div>`).join("");
 const months=counts(rows.map(a=>({{month:a.date_applied?.slice(0,7)}})).filter(x=>x.month),"month"),mx=Object.keys(months).sort();plot("trend",[{{x:mx,y:mx.map(x=>months[x]),type:"bar",marker:{{color:COLORS[0]}},hovertemplate:"%{{x}}: %{{y}}<extra></extra>"}}]);
 const sc=counts(rows,"status");plot("status",[{{labels:Object.keys(sc),values:Object.values(sc),type:"pie",hole:.55,marker:{{colors:COLORS}},textinfo:"percent",hovertemplate:"%{{label}}: %{{value}}<extra></extra>"}}],{{showlegend:true,legend:{{orientation:"h"}}}});
 const funnel=STAGES.map((_,i)=>rows.filter(a=>(stages[a.app_id]??0)>=i).length);plot("funnel",[{{y:STAGES,x:funnel,type:"bar",orientation:"h",marker:{{color:COLORS.slice(0,5)}},text:funnel,textposition:"auto",hovertemplate:"%{{y}}: %{{x}}<extra></extra>"}}],{{yaxis:{{autorange:"reversed"}}}});
 const buckets={{"0–7":0,"8–14":0,"15–30":0,"31–60":0,"61–179":0,"180+":0,"Unknown":0}};active.forEach(a=>{{const d=a.days;const k=d==null?"Unknown":d<=7?"0–7":d<=14?"8–14":d<=30?"15–30":d<=60?"31–60":d<180?"61–179":"180+";buckets[k]++}});plot("aging",[{{x:Object.keys(buckets),y:Object.values(buckets),type:"bar",marker:{{color:COLORS[5]}}}}]);
 function performance(key,id){{const groups={{}};rows.forEach(a=>(groups[a[key]||"Unspecified"]??=[]).push(a));const names=Object.keys(groups);plot(id,[{{x:names,y:names.map(n=>groups[n].length),type:"bar",name:"Applications",marker:{{color:COLORS[0]}}}},{{x:names,y:names.map(n=>100*groups[n].filter(a=>responded(a,stages)).length/groups[n].length),type:"scatter",mode:"lines+markers",name:"Response %",yaxis:"y2",line:{{color:COLORS[1]}}}}],{{legend:{{orientation:"h"}},yaxis2:{{overlaying:"y",side:"right",range:[0,100],ticksuffix:"%"}}}})}} performance("source","sources");performance("track","tracks");
 const cc=counts(rows,"company"),companies=Object.entries(cc).filter(x=>x[1]>1).sort((a,b)=>b[1]-a[1]).slice(0,12).reverse();plot("companies",[{{y:companies.map(x=>x[0]),x:companies.map(x=>x[1]),type:"bar",orientation:"h",marker:{{color:COLORS[2]}}}}]);
 const quality=counts(DB.events.filter(e=>ids.has(e.app_id)),"provenance");plot("quality",[{{labels:Object.keys(quality),values:Object.values(quality),type:"pie",hole:.55,marker:{{colors:[COLORS[1],COLORS[5]]}}}}],{{legend:{{orientation:"h"}}}});
 const follow=active.slice().sort((a,b)=>(b.days??-1)-(a.days??-1)).slice(0,25);table("followups",["Company / role","Status","Last activity","Days","Track"],follow.map(a=>[`<strong>${{esc(a.company)}}</strong><br><span class="muted">${{esc(a.role)}}</span>`,`<span class="pill">${{esc(a.status)}}</span>`,esc(a.last_activity||"—"),esc(a.days??"—"),esc(a.track)]));
 const recent=rows.slice().sort((a,b)=>(b.last_activity||"").localeCompare(a.last_activity||"")).slice(0,25);table("recent",["Date","Company / role","Status","Source"],recent.map(a=>[esc(a.last_activity||"—"),`<strong>${{esc(a.company)}}</strong><br><span class="muted">${{esc(a.role)}}</span>`,esc(a.status),esc(a.source)]));
}}
table("reviews",["Created","Sender","Subject","Reason"],DB.reviews.map(r=>[esc(r.created_at.slice(0,16).replace("T"," ")),esc(r.sender),esc(r.subject),esc(r.reason)]));document.getElementById("review-count").textContent=DB.reviews.length?`(${{DB.reviews.length}} pending)`:"";
document.getElementById("generated").textContent=DB.generated.replace("T"," ");
const run=DB.last_run;document.getElementById("run-status").innerHTML=run?`Last run: <strong>${{esc(run.status)}}</strong><br><span class="muted">${{esc((run.completed_at||run.started_at).slice(0,16).replace("T"," "))}}</span>`:"No synchronization run recorded";
render();
</script></body></html>"""


def main() -> int:
    from common import find_workbook, setup_logging
    from workbook import open_workbook, read_applications

    logger = setup_logging()
    workbook = open_workbook(find_workbook())
    path, metrics = build(read_applications(workbook))
    logger.info(
        "dashboard: %s (%d applications, %d active)",
        path,
        metrics["total"],
        metrics["active"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
