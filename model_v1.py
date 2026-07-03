import os, json, uuid, re, csv, io
from datetime import datetime, date, timedelta
from urllib.parse import quote
import streamlit as st
from streamlit_echarts import st_echarts
from werkzeug.security import generate_password_hash, check_password_hash
from supabase import create_client, Client

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG / CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════
SUPABASE_URL = "https://mvoxhdbcxmmozulenlvh.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im12b3hoZGJjeG1tb3p1bGVubHZoIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzg5MTczNDIsImV4cCI6MjA5NDQ5MzM0Mn0.6Fhrqo6sfMnO3KklN5dwLup0BVbp0_ga8k5hi3LgXQU"

@st.cache_resource
def get_supabase() -> Client:
    try:
        sb = create_client(SUPABASE_URL, SUPABASE_KEY)
        sb.table("users").select("email").limit(1).execute()
        return sb
    except Exception as e:
        st.error(f"❌ Supabase connection failed: {e}")
        st.stop()

STATUSES   = ["New Idea","Assigned","WIP","UAT","Completed","Hold/Park","Rejected"]
PROJECTS   = ["EFS CA-MRO","EFS BA-MRO","EFS BA-LCE","EFS CA-LCE","EFS Controls","EFS Technical Response","Others"]
CATEGORIES = ["Customer Requirement","Internal"]
AUTOMATION_CATS = ["Automation-Personal Productivity","Automation-Process Improvement","Automation-Defined Product and Sales","Automation-Quality Enhancement"]
AI_CATS         = ["AI-Personal Productivity","AI-Process Improvement","AI-Defined Product and Sales"]
AUTO_CATS  = AUTOMATION_CATS + AI_CATS   # kept for feasibility dropdown (flat list)
FREQ_MULT  = {"Daily":260,"Weekly":52,"Monthly":12,"Yearly":1}
REJ_REASONS= ["Technical Rejection","Business Rejection"]
ROLES_LIST = ["super user","normal user","automation engineer","automation pl","pl/spl"]
DEFAULT_PW = "admin123"

SUPPORT_NAME  = "Manoj JAGADEESH, Raja AMMAIAPPAN, Naveen KONNUR"
SUPPORT_EMAIL = "manoj.jagadeesh@alten-india.com"

ALTEN_LOGO_URL = "https://www.alten.com/wp-content/uploads/2019/01/favicon-alten.png"

CUSTOMERS = ["Rolls-Royce"]
REGIONS   = ["INDIA", "UK", "USA", "Germany"]

BLOCKED_DOMAINS = {
    "gmail.com","yahoo.com","hotmail.com","rediff.com","outlook.com",
    "live.com","icloud.com","aol.com","protonmail.com","yandex.com",
    "mail.com","zoho.com","gmx.com","inbox.com",
}

ROLE_PAGES = {
    "super user":         ["Dashboard","Submit Idea","PL Assignment","Feasibility","Approval","Admin","OTP List","Workflow"],
    "normal user":        ["Submit Idea"],
    "automation engineer":["Dashboard","Submit Idea","Feasibility"],
    "automation pl":      ["Dashboard","Submit Idea","PL Assignment","Feasibility","Approval"],
    "pl/spl":             ["Dashboard","Submit Idea","Approval"],
}
PW_ROLES = {"super user","automation engineer","automation pl","pl/spl"}

DEFAULT_USERS = [{"email":"ravi.manoharan@alten-india.com","role":"super user"}]

AUTO_CAT_COLORS = {
    "Automation-Personal Productivity":"#1a4fad",
    "Automation-Process Improvement":"#7c3aed",
    "Automation-Defined Product and Sales":"#059669",
    "Automation-Quality Enhancement":"#0d9488",
    "AI-Personal Productivity":"#0369a1",
    "AI-Process Improvement":"#9333ea",
    "AI-Defined Product and Sales":"#0891b2",
}
CAT_COLORS = {"Customer Requirement":"#0623E3","Internal":"#0ee95e"}

STATUS_COLORS = {
    "New Idea":"#1a4fad","Assigned":"#7c3aed","WIP":"#0d9488",
    "UAT":"#0ea5e9","Completed":"#059669","Hold/Park":"#b45309","Rejected":"#dc2626"
}

STATUS_ICONS = {
    "New Idea":"💡","Assigned":"📋","WIP":"⚙️",
    "UAT":"🧪","Completed":"✅","Hold/Park":"⏸","Rejected":"❌"
}

THEMES = {
    "ALTEN Red & Blue":   {"primary":"#E30613","secondary":"#00AEEF","bg":"#f5f6fa","sidebar":"#0a0a0a"},
    "Ocean Blue":         {"primary":"#1a4fad","secondary":"#0ea5e9","bg":"#f0f4ff","sidebar":"#0a0a0a"},
    "Forest Green":       {"primary":"#059669","secondary":"#0d9488","bg":"#f0fdf4","sidebar":"#0a0a0a"},
    "Purple Haze":        {"primary":"#7c3aed","secondary":"#a855f7","bg":"#faf5ff","sidebar":"#0a0a0a"},
    "Midnight Dark":      {"primary":"#e2e8f0","secondary":"#94a3b8","bg":"#0f172a","sidebar":"#0a0a0a"},
}

# ══════════════════════════════════════════════════════════════════════════════
#  DATABASE  (Supabase backend)
# ══════════════════════════════════════════════════════════════════════════════
def _run_sql(sb, sql):
    try:
        sb.rpc("pg_query", {"query": sql}).execute()
    except Exception:
        pass

def init_db():
    sb = get_supabase()
    _run_sql(sb, """
        CREATE TABLE IF NOT EXISTS users (
            email         text PRIMARY KEY,
            role          text,
            password_hash text
        );
    """)
    _run_sql(sb, """
        CREATE TABLE IF NOT EXISTS ideas (
            id                   text PRIMARY KEY,
            name                 text,
            submitter_email      text,
            idea_name            text,
            idea                 text,
            project              text,
            category             text,
            automation_category  text,
            pl_name              text,
            status               text,
            roi                  float8,
            assigned_engineer    text,
            feasibility_data     text,
            feasibility_comments text,
            decision             text,
            rejection_reason     text,
            approval_comment     text,
            priority_label       text,
            sprint_start         text,
            sprint_end           text,
            delivery_date        text,
            vsm_meeting_date     text,
            sprint_meeting_date  text,
            hold_reason          text,
            customer             text,
            region               text,
            parent_id            text,
            created_date         text,
            assigned_date        text,
            wip_date             text,
            uat_date             text,
            completion_date      text
        );
    """)
    idea_cols = [
        ("submitter_email","text"),("automation_category","text"),
        ("priority_label","text"),("sprint_start","text"),("sprint_end","text"),
        ("delivery_date","text"),("vsm_meeting_date","text"),
        ("sprint_meeting_date","text"),("hold_reason","text"),
        ("customer","text"),("region","text"),("parent_id","text"),
        ("otp","text"),("business_unit","text"),("pd_name","text"),("spl_pl","text"),
    ]
    for col, dtype in idea_cols:
        _run_sql(sb, f"ALTER TABLE ideas ADD COLUMN IF NOT EXISTS {col} {dtype};")
    _run_sql(sb, "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash text;")

    _run_sql(sb, """
        CREATE TABLE IF NOT EXISTS otp_list (
            otp            text PRIMARY KEY,
            project_name   text,
            business_unit  text,
            pd             text,
            spl_pl         text
        );
    """)

    dh = generate_password_hash(DEFAULT_PW)
    for u in DEFAULT_USERS:
        try:
            existing = sb.table("users").select("email,password_hash").eq("email", u["email"].lower()).execute()
            if not existing.data:
                sb.table("users").insert({"email":u["email"].lower(),"role":u["role"],"password_hash":dh}).execute()
            elif not existing.data[0].get("password_hash"):
                sb.table("users").update({"password_hash":dh}).eq("email",u["email"].lower()).execute()
        except Exception:
            pass

def get_all():
    sb   = get_supabase()
    resp = sb.table("ideas").select("*").order("created_date", desc=True).execute()
    rows = []
    for r in (resp.data or []):
        try: r["feasibility_data"] = json.loads(r.get("feasibility_data") or "{}")
        except: r["feasibility_data"] = {}
        rows.append(r)
    return rows

def add_idea(idea):
    row = {
        "id":idea["id"],"name":idea.get("name",""),"submitter_email":idea.get("submitter_email",""),
        "idea_name":idea.get("idea_name",""),"idea":idea.get("idea",""),"project":idea.get("project",""),
        "category":idea.get("category",""),"automation_category":idea.get("automation_category",""),
        "pl_name":idea.get("pl_name",""),"status":idea.get("status","New Idea"),"roi":idea.get("roi",0),
        "assigned_engineer":idea.get("assigned_engineer",""),
        "feasibility_data":json.dumps(idea.get("feasibility_data",{})),
        "feasibility_comments":idea.get("feasibility_comments",""),"decision":idea.get("decision",""),
        "rejection_reason":idea.get("rejection_reason",""),"approval_comment":idea.get("approval_comment",""),
        "priority_label":idea.get("priority_label",""),"sprint_start":idea.get("sprint_start",""),
        "sprint_end":idea.get("sprint_end",""),"delivery_date":idea.get("delivery_date",""),
        "vsm_meeting_date":idea.get("vsm_meeting_date",""),"sprint_meeting_date":idea.get("sprint_meeting_date",""),
        "hold_reason":idea.get("hold_reason",""),"created_date":datetime.now().strftime("%Y-%m-%d %H:%M"),
        "assigned_date":"","wip_date":"","uat_date":"","completion_date":"",
        "customer":idea.get("customer",""),"region":idea.get("region",""),
        "parent_id":idea.get("parent_id",""),
        "otp":idea.get("otp",""),"business_unit":idea.get("business_unit",""),
        "pd_name":idea.get("pd_name",""),"spl_pl":idea.get("spl_pl",""),
    }
    get_supabase().table("ideas").insert(row).execute()

def update_idea(iid, fields):
    payload = {}
    for k,v in fields.items():
        payload[k] = json.dumps(v) if k=="feasibility_data" else v
    get_supabase().table("ideas").update(payload).eq("id",iid).execute()

def get_users():
    resp = get_supabase().table("users").select("*").order("email").execute()
    return resp.data or []

def add_user(email, role):
    sb  = get_supabase()
    dh  = generate_password_hash(DEFAULT_PW)
    existing = sb.table("users").select("password_hash").eq("email",email.lower()).execute()
    if existing.data:
        sb.table("users").update({"role":role}).eq("email",email.lower()).execute()
    else:
        sb.table("users").insert({"email":email.lower(),"role":role,"password_hash":dh}).execute()

def delete_user(email):
    get_supabase().table("users").delete().eq("email",email.lower()).execute()

def update_role(email, role):
    get_supabase().table("users").update({"role":role}).eq("email",email.lower()).execute()

def set_password(email, new_pw):
    get_supabase().table("users").update({"password_hash":generate_password_hash(new_pw)}).eq("email",email.lower()).execute()

def reset_password(email):
    set_password(email, DEFAULT_PW)

# ══════════════════════════════════════════════════════════════════════════════
#  OTP LOOKUP TABLE  (master list feeding Submit Idea autofill)
# ══════════════════════════════════════════════════════════════════════════════
def get_otp_list():
    resp = get_supabase().table("otp_list").select("*").order("otp").execute()
    return resp.data or []

def upsert_otp_row(otp, project_name, business_unit, pd_name, spl_pl):
    sb = get_supabase()
    payload = {
        "otp":str(otp).strip(),
        "project_name":project_name or "",
        "business_unit":business_unit or "",
        "pd":pd_name or "",
        "spl_pl":spl_pl or "",
    }
    existing = sb.table("otp_list").select("otp").eq("otp", payload["otp"]).execute()
    if existing.data:
        sb.table("otp_list").update(payload).eq("otp", payload["otp"]).execute()
    else:
        sb.table("otp_list").insert(payload).execute()

def delete_otp_row(otp):
    get_supabase().table("otp_list").delete().eq("otp", otp).execute()

# ══════════════════════════════════════════════════════════════════════════════
#  DATE / SPRINT ENGINE
# ══════════════════════════════════════════════════════════════════════════════
def next_workday(d):
    while d.weekday() >= 5: d += timedelta(days=1)
    return d

def add_workdays(d, n):
    d = next_workday(d); count=0
    while count < n:
        d += timedelta(days=1)
        if d.weekday() < 5: count+=1
    return d

def sprint_dates(start):
    s = next_workday(start)
    return s, add_workdays(s, 9)

def fmt_d(d): return d.strftime("%Y-%m-%d") if d else ""
def parse_d(s):
    for f in ("%Y-%m-%d %H:%M","%Y-%m-%d"):
        try: return datetime.strptime(str(s).strip(), f).date()
        except: pass
    return None

# ══════════════════════════════════════════════════════════════════════════════
#  PRIORITY ENGINE
# ══════════════════════════════════════════════════════════════════════════════
CAT_RANK = {"Customer Requirement":0,"Internal":1}

def priority_key(i):
    return (CAT_RANK.get(i.get("category",""),2), -float(i.get("roi",0) or 0), i.get("created_date",""))

def rank_ideas(ideas):
    ordered = sorted(ideas, key=priority_key)
    for idx,i in enumerate(ordered,1):
        cat = i.get("category","")
        roi = float(i.get("roi",0) or 0)
        label = ("🔴 P1-Customer" if cat=="Customer Requirement" else "🟡 P2-Internal")
        if roi: label += f" (ROI {roi:.1f})"
        label += f" #{idx}"
        i["priority_rank"] = idx; i["priority_label"] = label
    return ordered

def compute_delivery(all_ideas, eng_email, new_idea):
    active = [i for i in all_ideas
              if i.get("assigned_engineer")==eng_email
              and i.get("status") in {"Assigned","WIP","UAT","Hold/Park"}
              and i.get("id")!=new_idea.get("id")]
    combined = rank_ideas(active + [new_idea])
    today = next_workday(date.today())
    cursor = today; result = None
    for i in combined:
        s,e = sprint_dates(cursor)
        if i.get("id")==new_idea.get("id"):
            result = {"sprint_start":s,"sprint_end":e,
                      "priority_rank":i["priority_rank"],"priority_label":i["priority_label"]}
        cursor = e
    return result

def engineer_queue(all_ideas, eng_email):
    active = rank_ideas([i for i in all_ideas
                         if i.get("assigned_engineer")==eng_email
                         and i.get("status") in {"Assigned","WIP","UAT","Hold/Park"}])
    cursor = next_workday(date.today()); out=[]
    for i in active:
        s,e = sprint_dates(cursor)
        out.append({**i,"sprint_start":s,"sprint_end":e}); cursor=e
    return out

# ══════════════════════════════════════════════════════════════════════════════
#  OUTLOOK COMPOSE LINK
# ══════════════════════════════════════════════════════════════════════════════
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
def is_email(e): return bool(e) and bool(EMAIL_RE.match(str(e).strip()))

def outlook_link(to_list, subject, body):
    to = ",".join(e for e in to_list if is_email(e))
    return (f"https://outlook.office.com/mail/deeplink/compose?"
            f"to={quote(to)}&subject={quote(subject)}&body={quote(body)}")

def build_assign_outlook(idea, eng, queue_info):
    body = f"""An idea has been assigned to you for Feasibility Study.

Idea Name   : {idea.get('idea_name','')}
Project     : {idea.get('project','')}
Category    : {idea.get('category','')}
Description : {idea.get('idea','')}
PL / SPL    : {idea.get('pl_name','')}
Priority    : {queue_info.get('priority_label','') if queue_info else ''}"""
    if queue_info:
        body += f"\nSprint Start: {fmt_d(queue_info['sprint_start'])}\nDelivery    : {fmt_d(queue_info['sprint_end'])}"
    body += "\n\nPlease begin Feasibility Study and update status in Turbo Drive."
    return outlook_link([eng], f"[Turbo Drive] New Idea Assigned: {idea.get('idea_name','')}", body)

def build_feasibility_outlook(idea, roi, vsm_date, queue_info):
    body = f"""Feasibility Study is complete — please review and provide GO / NO-GO decision.

Idea Name   : {idea.get('idea_name','')}
Project     : {idea.get('project','')}
Category    : {idea.get('category','')}
Engineer    : {idea.get('assigned_engineer','')}
ROI         : {round(roi,2)}
Priority    : {idea.get('priority_label','')}
VSM Date    : {fmt_d(vsm_date)} at 11:00 AM"""
    if queue_info:
        body += f"\nDelivery    : {fmt_d(queue_info['sprint_end'])}"
    body += "\n\nPlease log in to Turbo Drive to approve or reject."
    return outlook_link([idea.get("pl_name","")], f"[Turbo Drive] Feasibility Complete — Action Needed: {idea.get('idea_name','')}", body)

def build_meeting_outlook(mtype, idea, mdate, recipients):
    times  = {"vsm":"11:00 AM","sprint":"10:00 AM","delivery":"03:00 PM"}
    titles = {"vsm":"VSM Session","sprint":"Sprint Planning","delivery":"Delivery/Demo Review"}
    descs  = {
        "vsm":      "A Value Stream Mapping session to review current vs future-state process before GO/NO-GO.",
        "sprint":   "Sprint Planning for the 2-week delivery cycle.",
        "delivery": "Delivery / Demo review — please review the output and confirm acceptance.",
    }
    body = f"""{descs.get(mtype,'')}

Idea Name   : {idea.get('idea_name','')}
Project     : {idea.get('project','')}
Category    : {idea.get('category','')}
Date        : {fmt_d(mdate)} at {times.get(mtype,'')}
Engineer    : {idea.get('assigned_engineer','-')}
PL / SPL    : {idea.get('pl_name','-')}"""
    return outlook_link(recipients, f"[Turbo Drive] {titles.get(mtype,'Meeting')}: {idea.get('idea_name','')} — {fmt_d(mdate)}", body)

# ══════════════════════════════════════════════════════════════════════════════
#  SHARED UI COMPONENTS
# ══════════════════════════════════════════════════════════════════════════════
def render_support_bar():
    st.markdown(
        f'<p style="font-size:11px;color:#94a3b8;margin-top:-10px;margin-bottom:10px;">'
        f'For any queries, reach out to <b>{SUPPORT_NAME}</b> — '
        f'<a href="mailto:{SUPPORT_EMAIL}" style="color:#00AEEF;">{SUPPORT_EMAIL}</a></p>',
        unsafe_allow_html=True
    )

def render_copyright():
    st.markdown("---")
    st.markdown(
        '<p style="text-align:center;font-size:11px;color:#94a3b8;padding:4px 0 8px;">'
        '© 2025 ALTEN Engineering Services. All rights reserved. '
        'Turbo Drive is an internal tool developed and maintained by ALTEN India.'
        '</p>',
        unsafe_allow_html=True
    )

def page_header(title: str):
    st.markdown(f"""
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:4px;">
      <img src="{ALTEN_LOGO_URL}" style="height:45px;object-fit:contain;" alt="ALTEN"/>
      <span style="font-size:24px;font-weight:800;color:#E30613;letter-spacing:0.5px;">
         {title}
      </span>
    </div>""", unsafe_allow_html=True)
    render_support_bar()

# ══════════════════════════════════════════════════════════════════════════════
#  THEME / CSS INJECTION  (no forced page-bg override on any page)
# ══════════════════════════════════════════════════════════════════════════════
def apply_theme(theme_name):
    t = THEMES.get(theme_name, THEMES["ALTEN Red & Blue"])
    dark_bg = theme_name == "Midnight Dark"
    text_color = "#e2e8f0" if dark_bg else "#020202"
    surface = "#1e293b" if dark_bg else "#ffffff"
    st.markdown(f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
    html,body,[data-testid="stApp"]{{
        font-family:'Inter',sans-serif;
        background:{t['bg']} !important;
        color:{text_color};
        font-size:clamp(12px,1.1vw,15px);
    }}
    [data-testid="stSidebar"]{{background:{t['sidebar']} !important;}}
    [data-testid="stSidebar"] *{{color:#e2e8f0 !important;}}
    [data-testid="stSidebar"] .stRadio label{{
        font-size:clamp(11px,1vw,14px);padding:6px 10px;border-radius:8px;
        transition:background .15s;cursor:pointer;
    }}
    [data-testid="stSidebar"] .stRadio label:hover{{background:rgba(255,255,255,.1);}}
    h1{{
        background:linear-gradient(135deg,{t['primary']},{t['secondary']});
        -webkit-background-clip:text;background-clip:text;color:transparent;
        font-size:clamp(20px,2vw,28px);font-weight:800;margin-bottom:4px;
    }}
    h2{{color:{t['primary']};font-size:clamp(15px,1.5vw,20px);font-weight:700;}}
    h3{{color:{t['secondary']};font-size:clamp(13px,1.2vw,16px);font-weight:600;}}
    .kpi-card{{
        background:{surface};border-radius:14px;padding:14px 16px;
        border-left:5px solid {t['primary']};
        box-shadow:0 2px 12px rgba(0,0,0,.08);margin-bottom:6px;
    }}
    .kpi-val{{font-size:clamp(20px,2vw,28px);font-weight:800;}}
    .kpi-lbl{{font-size:clamp(9px,0.85vw,11px);color:#64748b;font-weight:500;margin-top:2px;}}
    .kpi-sub{{font-size:clamp(8px,0.75vw,10px);color:#94a3b8;margin-top:3px;}}
    .idea-card{{
        background:{surface};border-radius:12px;padding:14px 16px;
        border:1px solid #e2e8f0;box-shadow:0 2px 8px rgba(0,0,0,.06);margin-bottom:10px;
    }}
    .outlook-btn{{
        display:inline-block;background:{t['primary']};color:#fff;
        padding:8px 18px;border-radius:8px;text-decoration:none;
        font-weight:600;font-size:clamp(11px,1vw,13px);margin-top:8px;
    }}
    .stButton>button{{
        background:linear-gradient(135deg,{t['primary']},{t['secondary']});
        color:#fff;border:none;border-radius:8px;font-weight:600;padding:8px 20px;
    }}
    .stButton>button:hover{{opacity:.88;}}
    div[data-testid="stForm"]{{background:{surface};border-radius:12px;padding:12px;}}
    .login-box{{
        max-width:440px;margin:40px auto;background:{surface};border-radius:16px;
        padding:32px 36px;box-shadow:0 8px 32px rgba(0,0,0,.12);
        border:1px solid rgba(255,255,255,.08);
    }}
    /* filter bar */
    .filter-bar{{
        background:{surface};border-radius:10px;padding:10px 14px;
        border:1px solid #e2e8f0;margin-bottom:10px;
        box-shadow:0 1px 4px rgba(0,0,0,.05);
    }}

    /* ── Premium illustrated KPI card (50/50 split) ─────────────────────── */
    .kpi-card-v2{{
        display:flex;align-items:center;gap:10px;
        background:{surface};border-radius:16px;padding:12px 14px;
        box-shadow:0 3px 14px rgba(0,0,0,.08);
        transition:transform .18s ease, box-shadow .18s ease;
        margin-bottom:8px;min-height:88px;
    }}
    .kpi-card-v2:hover{{
        transform:translateY(-3px);
        box-shadow:0 10px 28px rgba(0,0,0,.14);
    }}
    .kpi-v2-illust{{
        flex:0 0 42%;max-width:60px;aspect-ratio:1/1;
        display:flex;align-items:center;justify-content:center;
        opacity:.92;
    }}
    .kpi-v2-content{{flex:1;min-width:0;}}
    .kpi-v2-value{{font-size:clamp(22px,2.2vw,30px);font-weight:800;line-height:1.1;}}
    .kpi-v2-label{{font-size:clamp(9px,0.85vw,11px);color:#64748b;font-weight:600;margin-top:2px;}}
    .kpi-v2-sub{{font-size:clamp(8px,0.7vw,9.5px);color:#94a3b8;margin-top:3px;line-height:1.4;}}

    /* ── Glassmorphism category panel (Automation / AI breakdown) ──────── */
    .glass-panel{{
        background:rgba(255,255,255,.55);
        backdrop-filter:blur(14px) saturate(160%);
        -webkit-backdrop-filter:blur(14px) saturate(160%);
        border-radius:20px;border:1px solid rgba(255,255,255,.4);
        padding:18px 16px;box-shadow:0 8px 30px rgba(0,0,0,.10);
        position:relative;overflow:hidden;
    }}
    .glass-panel::before{{
        content:"";position:absolute;inset:0;border-radius:20px;padding:1.5px;
        background:linear-gradient(135deg,{t['primary']},{t['secondary']},transparent 70%);
        -webkit-mask:linear-gradient(#fff 0 0) content-box,linear-gradient(#fff 0 0);
        -webkit-mask-composite:xor;mask-composite:exclude;
        opacity:.55;pointer-events:none;
    }}
    .glass-panel-title{{
        font-size:clamp(14px,1.3vw,18px);font-weight:800;margin-bottom:4px;
        display:flex;align-items:center;gap:8px;
    }}
    .cat-pill{{
        display:block;width:100%;text-align:left;
        background:rgba(255,255,255,.5);border:1.5px solid rgba(255,255,255,.6);
        border-radius:12px;padding:9px 12px;margin-bottom:7px;cursor:pointer;
        font-size:clamp(10px,0.95vw,12px);font-weight:600;
        transition:all .2s ease;position:relative;
    }}
    .cat-pill:hover{{
        background:rgba(255,255,255,.8);transform:translateX(3px);
        box-shadow:0 4px 14px rgba(0,0,0,.10);
    }}
    .cat-pill.selected{{
        background:linear-gradient(135deg,var(--cat-color,#1a4fad),transparent);
        color:#fff;border-color:var(--cat-color,#1a4fad);
        box-shadow:0 0 0 3px color-mix(in srgb, var(--cat-color,#1a4fad) 25%, transparent),
                   0 6px 18px color-mix(in srgb, var(--cat-color,#1a4fad) 45%, transparent);
    }}
    .cat-insight-card{{
        background:rgba(255,255,255,.65);border-radius:14px;padding:14px 16px;
        margin-top:8px;box-shadow:inset 0 0 0 1.5px rgba(255,255,255,.6),0 4px 16px rgba(0,0,0,.08);
    }}
    .cat-insight-grid{{
        display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:8px;
    }}
    .cat-insight-stat{{text-align:center;}}
    .cat-insight-stat .v{{font-size:clamp(17px,1.7vw,22px);font-weight:800;}}
    .cat-insight-stat .l{{font-size:clamp(7.5px,0.7vw,9px);color:#64748b;font-weight:600;margin-top:1px;}}
    </style>""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
#  SESSION / AUTH HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def ss(key, default=None):
    return st.session_state.get(key, default)

# ══════════════════════════════════════════════════════════════════════════════
#  SESSION TIMEOUT  (inactivity = time since last widget interaction / rerun)
#  True idle/mouse detection isn't possible in pure Streamlit — every widget
#  interaction (button, dropdown, form submit, navigation, kanban move, etc.)
#  triggers a rerun, and that rerun is what resets the timer here.
# ══════════════════════════════════════════════════════════════════════════════
SESSION_TIMEOUT_SECONDS = 600   # 5 minutes
SESSION_WARNING_AT      = 340   # show warning after 4 minutes (60s before logout)

def touch_activity():
    st.session_state["_last_activity"] = datetime.now()

def seconds_since_activity():
    last = st.session_state.get("_last_activity")
    if not last:
        return 0
    return (datetime.now() - last).total_seconds()

def enforce_session_timeout():
    """Call once per page render while logged in. Shows a warning dialog at
    4 minutes idle, and force-logs-out + clears session at 5 minutes idle."""
    if "_last_activity" not in st.session_state:
        touch_activity()
        return

    idle = seconds_since_activity()

    if idle >= SESSION_TIMEOUT_SECONDS:
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.session_state["_session_expired"] = True
        st.rerun()

    elif idle >= SESSION_WARNING_AT:
        remaining = int(SESSION_TIMEOUT_SECONDS - idle)
        with st.container():
            st.warning(f"⚠️ Your session will expire in **{remaining} seconds** due to inactivity.")
            wc1, wc2 = st.columns(2)
            with wc1:
                if st.button("✅ Continue Session", use_container_width=True, key="_continue_session"):
                    touch_activity()
                    st.rerun()
            with wc2:
                if st.button("🚪 Logout Now", use_container_width=True, key="_logout_now"):
                    for k in list(st.session_state.keys()):
                        del st.session_state[k]
                    st.rerun()

def render_session_countdown():
    """Live countdown shown in the sidebar — re-renders on every interaction
    (covers clicks, dropdowns, form submits, navigation, Kanban moves, etc.)."""
    idle      = seconds_since_activity()
    remaining = max(0, int(SESSION_TIMEOUT_SECONDS - idle))
    mins, secs = divmod(remaining, 60)
    color = "#dc2626" if remaining <= 60 else ("#b45309" if remaining <= 120 else "#94a3b8")
    st.markdown(
        f'<div style="text-align:center;margin-top:6px;">'
        f'<span style="font-size:9px;color:#64748b;letter-spacing:.5px;">SESSION TIMEOUT</span><br>'
        f'<span style="font-size:16px;font-weight:800;color:{color};font-family:monospace;">'
        f'{mins:02d}:{secs:02d}</span></div>',
        unsafe_allow_html=True
    )

def logged_in(): return bool(ss("email"))
def user_role(): return ss("role","")
def user_pages(): return ROLE_PAGES.get(user_role(),[])
def can(page): return page in user_pages()

def is_org_email(email: str) -> bool:
    if "@" not in email: return False
    domain = email.strip().lower().split("@")[-1]
    return domain not in BLOCKED_DOMAINS

def check_login(email, password):
    resp = get_supabase().table("users").select("*").eq("email",email.lower()).execute()
    if not resp.data: return None, "Email not found in system."
    row  = resp.data[0]
    role = row.get("role","")
    if role in PW_ROLES:
        if not password: return None, "Password required for your role."
        if not row.get("password_hash") or not check_password_hash(row["password_hash"], password):
            return None, "Incorrect password."
    return row, None

# ══════════════════════════════════════════════════════════════════════════════
#  DASHBOARD HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def idea_hours(i):
    fd = i.get("feasibility_data",{}) or {}
    try:
        return float(fd.get("manual",0) or 0)*float(fd.get("fte",0) or 0)*FREQ_MULT.get(fd.get("freq","Daily"),1)
    except: return 0

def kpi_card(value, label, color, sub="", icon=""):
    """Legacy small KPI card — kept for Automation Category Breakdown mini-cards."""
    st.markdown(f"""
    <div class="kpi-card" style="border-left-color:{color}">
      <div style="font-size:clamp(14px,1.4vw,18px);margin-bottom:2px;">{icon}</div>
      <div class="kpi-val" style="color:{color}">{value}</div>
      <div class="kpi-lbl">{label}</div>
      {"<div class='kpi-sub'>"+sub+"</div>" if sub else ""}
    </div>""", unsafe_allow_html=True)

# ── Premium illustrated KPI card (50/50 split: big illustration | value+trend) ──
KPI_ILLUSTRATIONS = {
    "total_ideas": """<svg viewBox="0 0 64 64" width="100%" height="100%">
        <circle cx="32" cy="26" r="16" fill="none" stroke="currentColor" stroke-width="3" opacity=".25"/>
        <path d="M32 10a16 16 0 0 1 9 29c-1.5 1-2 2.5-2 4v3H25v-3c0-1.5-.5-3-2-4a16 16 0 0 1 9-29z"
              fill="currentColor" opacity=".9"/>
        <rect x="25" y="48" width="14" height="4" rx="2" fill="currentColor"/>
        <rect x="27" y="54" width="10" height="3" rx="1.5" fill="currentColor" opacity=".7"/>
        <line x1="32" y1="2" x2="32" y2="7" stroke="currentColor" stroke-width="3" stroke-linecap="round"/>
        <line x1="12" y1="10" x2="16" y2="14" stroke="currentColor" stroke-width="3" stroke-linecap="round" opacity=".6"/>
        <line x1="52" y1="10" x2="48" y2="14" stroke="currentColor" stroke-width="3" stroke-linecap="round" opacity=".6"/>
    </svg>""",
    "trophy": """<svg viewBox="0 0 64 64" width="100%" height="100%">
        <path d="M18 10h28v14a14 14 0 0 1-28 0V10z" fill="currentColor" opacity=".9"/>
        <path d="M18 14h-6a8 8 0 0 0 8 8" fill="none" stroke="currentColor" stroke-width="3"/>
        <path d="M46 14h6a8 8 0 0 1-8 8" fill="none" stroke="currentColor" stroke-width="3"/>
        <rect x="29" y="38" width="6" height="10" fill="currentColor"/>
        <rect x="20" y="48" width="24" height="6" rx="2" fill="currentColor" opacity=".85"/>
        <circle cx="32" cy="20" r="5" fill="#fff" opacity=".5"/>
    </svg>""",
    "clock": """<svg viewBox="0 0 64 64" width="100%" height="100%">
        <circle cx="32" cy="34" r="22" fill="none" stroke="currentColor" stroke-width="3.5" opacity=".9"/>
        <line x1="32" y1="34" x2="32" y2="20" stroke="currentColor" stroke-width="3.5" stroke-linecap="round"/>
        <line x1="32" y1="34" x2="42" y2="38" stroke="currentColor" stroke-width="3.5" stroke-linecap="round"/>
        <rect x="26" y="6" width="12" height="5" rx="2.5" fill="currentColor"/>
        <circle cx="32" cy="34" r="2.5" fill="currentColor"/>
    </svg>""",
    "growth": """<svg viewBox="0 0 64 64" width="100%" height="100%">
        <polyline points="8,48 22,34 32,42 56,14" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
        <polygon points="56,14 56,24 46,14" fill="currentColor"/>
        <rect x="8" y="50" width="48" height="3" rx="1.5" fill="currentColor" opacity=".3"/>
        <circle cx="22" cy="34" r="3" fill="currentColor"/>
        <circle cx="32" cy="42" r="3" fill="currentColor"/>
    </svg>""",
    "robot_arm": """<svg viewBox="0 0 64 64" width="100%" height="100%">
        <rect x="10" y="46" width="44" height="8" rx="2" fill="currentColor" opacity=".85"/>
        <rect x="28" y="30" width="8" height="18" rx="2" fill="currentColor"/>
        <circle cx="32" cy="26" r="7" fill="currentColor" opacity=".9"/>
        <rect x="32" y="16" width="18" height="6" rx="3" fill="currentColor" transform="rotate(-25 32 19)"/>
        <circle cx="48" cy="10" r="4" fill="currentColor" opacity=".75"/>
    </svg>""",
    "ai_robot": """<svg viewBox="0 0 64 64" width="100%" height="100%">
        <rect x="18" y="18" width="28" height="24" rx="6" fill="currentColor" opacity=".9"/>
        <circle cx="27" cy="29" r="3" fill="#fff"/>
        <circle cx="37" cy="29" r="3" fill="#fff"/>
        <rect x="26" y="36" width="12" height="2.5" rx="1.25" fill="#fff" opacity=".8"/>
        <line x1="32" y1="18" x2="32" y2="10" stroke="currentColor" stroke-width="3"/>
        <circle cx="32" cy="7" r="3.5" fill="currentColor"/>
        <rect x="10" y="44" width="44" height="6" rx="3" fill="currentColor" opacity=".6"/>
        <rect x="6" y="24" width="5" height="12" rx="2.5" fill="currentColor" opacity=".7"/>
        <rect x="53" y="24" width="5" height="12" rx="2.5" fill="currentColor" opacity=".7"/>
    </svg>""",
    "folder": """<svg viewBox="0 0 64 64" width="100%" height="100%">
        <path d="M8 18a4 4 0 0 1 4-4h12l5 6h23a4 4 0 0 1 4 4v24a4 4 0 0 1-4 4H12a4 4 0 0 1-4-4V18z"
              fill="currentColor" opacity=".9"/>
        <path d="M8 24h48v22a4 4 0 0 1-4 4H12a4 4 0 0 1-4-4V24z" fill="currentColor"/>
    </svg>""",
}

def premium_kpi_card(value, label, color, sub="", illustration="total_ideas", trend=None):
    """50/50 illustration | value+trend KPI card."""
    trend_html = ""
    if trend is not None:
        up = trend >= 0
        tcol = "#059669" if up else "#dc2626"
        arrow = "▲" if up else "▼"
        trend_html = f'<div style="font-size:11px;font-weight:700;color:{tcol};margin-top:2px;">{arrow} {abs(trend):.1f}%</div>'
    svg = KPI_ILLUSTRATIONS.get(illustration, KPI_ILLUSTRATIONS["total_ideas"])
    st.markdown(f"""
    <div class="kpi-card-v2" style="border-top:4px solid {color};">
      <div class="kpi-v2-illust" style="color:{color};">{svg}</div>
      <div class="kpi-v2-content">
        <div class="kpi-v2-value" style="color:{color};">{value}</div>
        <div class="kpi-v2-label">{label}</div>
        {f'<div class="kpi-v2-sub">{sub}</div>' if sub else ''}
        {trend_html}
      </div>
    </div>""", unsafe_allow_html=True)

# ── Robotic arm SVG (Automation panel) — arm rotates/points per category idx
def cnt_cat_status(cat, st_, ideas):
    return len([i for i in ideas if i.get("category")==cat and i.get("status")==st_])

def cnt_cat_wip(ideas, cat):
    return len([i for i in ideas if i.get("category")==cat and i.get("status") in ("WIP","UAT")])

CATEGORY_ICONS = {
    "Automation-Personal Productivity":     "⚙️",
    "Automation-Process Improvement":       "🔧",
    "Automation-Defined Product and Sales": "📦",
    "Automation-Quality Enhancement":       "✅",
    "AI-Personal Productivity":             "🧠",
    "AI-Process Improvement":               "🔄",
    "AI-Defined Product and Sales":         "📊",
}

def render_category_panel(panel_title, panel_icon, categories, ideas, state_key, accent_color):
    """Simple, easy-to-read panel: small icon top-left, category list with
    counts, and an insight card (Total/Completed/WIP/UAT/Hours/ROI/Success%)
    for whichever category is selected. No animated character — plain and
    clear."""
    if state_key not in st.session_state:
        st.session_state[state_key] = categories[0]
    selected = st.session_state[state_key]
    if selected not in categories:
        selected = categories[0]
        st.session_state[state_key] = selected

    st.markdown(f"""
    <div class="glass-panel">
      <div class="glass-panel-title" style="color:{accent_color};">{panel_icon} {panel_title}</div>
    """, unsafe_allow_html=True)

    for cat in categories:
        is_sel = (cat == selected)
        color  = AUTO_CAT_COLORS.get(cat, accent_color)
        cat_icon  = CATEGORY_ICONS.get(cat, "•")
        cnt_total = len([i for i in ideas if i.get("automation_category")==cat])
        if st.button(
            f"{cat_icon}  {cat.split('-',1)[-1]}   ({cnt_total}) {'●' if is_sel else ''}",
            key=f"{state_key}_btn_{cat}",
            use_container_width=True,
        ):
            st.session_state[state_key] = cat
            touch_activity()
            st.rerun()

    # ── Insight card for selected category — icon left, details right ─────
    cat = selected
    color = AUTO_CAT_COLORS.get(cat, accent_color)
    cat_icon    = CATEGORY_ICONS.get(cat, "•")
    total_c     = len([i for i in ideas if i.get("automation_category")==cat])
    completed_c = len([i for i in ideas if i.get("automation_category")==cat and i.get("status")=="Completed"])
    wip_c       = len([i for i in ideas if i.get("automation_category")==cat and i.get("status")=="WIP"])
    uat_c       = len([i for i in ideas if i.get("automation_category")==cat and i.get("status")=="UAT"])
    hours_c     = round(sum(idea_hours(i) for i in ideas if i.get("automation_category")==cat), 1)
    roi_c       = round(sum(float(i.get("roi",0) or 0) for i in ideas if i.get("automation_category")==cat), 1)
    success_pct = round(completed_c/total_c*100, 1) if total_c else 0.0

    st.markdown(f"""
      <div class="cat-insight-card" style="border-left:4px solid {color};display:flex;gap:14px;align-items:flex-start;">
        <div style="flex:0 0 auto;font-size:34px;line-height:1;padding-top:2px;">{cat_icon}</div>
        <div style="flex:1;min-width:0;">
          <div style="font-size:clamp(11px,1vw,13px);font-weight:700;color:{color};margin-bottom:6px;">
            {cat}
          </div>
          <div class="cat-insight-grid">
            <div class="cat-insight-stat"><div class="v" style="color:{color};">{total_c}</div><div class="l">TOTAL</div></div>
            <div class="cat-insight-stat"><div class="v" style="color:#059669;">{completed_c}</div><div class="l">COMPLETED</div></div>
            <div class="cat-insight-stat"><div class="v" style="color:#0d9488;">{wip_c}</div><div class="l">WIP</div></div>
            <div class="cat-insight-stat"><div class="v" style="color:#0ea5e9;">{uat_c}</div><div class="l">UAT</div></div>
          </div>
          <div class="cat-insight-grid" style="margin-top:10px;">
            <div class="cat-insight-stat"><div class="v" style="color:#7c3aed;">{hours_c:,.0f}</div><div class="l">HRS SAVED</div></div>
            <div class="cat-insight-stat"><div class="v" style="color:#b45309;">{roi_c}</div><div class="l">ROI</div></div>
            <div class="cat-insight-stat" style="grid-column:span 2;"><div class="v" style="color:{color};">{success_pct}%</div><div class="l">SUCCESS RATE</div></div>
          </div>
        </div>
      </div>
    </div>""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
#  KANBAN: NATIVE STREAMLIT COLUMN + EXPANDER BOARD  (with Parent/Child links)
#  Note: true HTML5 drag-and-drop card-to-card linking is not supported by
#  Streamlit's widget model without a custom JS component. Parent/Child
#  relationships are created via a "Set Parent" dropdown on each card —
#  same end result (hierarchy, expand/collapse, aggregated metrics) without
#  a drag gesture.
# ══════════════════════════════════════════════════════════════════════════════
def _children_of(parent_id, all_ideas):
    return [i for i in all_ideas if i.get("parent_id") == parent_id]

def _parent_summary(parent, all_ideas):
    kids = _children_of(parent["id"], all_ideas)
    if not kids:
        return None
    n = len(kids)
    done = len([k for k in kids if k.get("status")=="Completed"])
    pct  = round(done/n*100, 1) if n else 0
    roi  = round(sum(float(k.get("roi",0) or 0) for k in kids), 1)
    hrs  = round(sum(idea_hours(k) for k in kids), 1)
    return {"children": n, "completion_pct": pct, "roi": roi, "hours": hrs}

def render_kanban_board(ideas):
    cols = st.columns(len(STATUSES))
    for col, status in zip(cols, STATUSES):
        color = STATUS_COLORS.get(status,"#888")
        icon  = STATUS_ICONS.get(status,"")
        count = len([i for i in ideas if i.get("status")==status])
        col.markdown(
            f'<div style="background:{color};color:#fff;border-radius:8px;'
            f'padding:6px 10px;text-align:center;font-size:clamp(10px,0.9vw,12px);font-weight:700;'
            f'margin-bottom:6px;">{icon} {status} &nbsp;'
            f'<span style="background:rgba(255,255,255,0.25);border-radius:10px;'
            f'padding:1px 7px;">{count}</span></div>',
            unsafe_allow_html=True,
        )

    id_to_idea = {i["id"]: i for i in ideas}

    cols = st.columns(len(STATUSES))
    for col, status in zip(cols, STATUSES):
        color  = STATUS_COLORS.get(status,"#888")
        bucket = [i for i in ideas if i.get("status")==status]
        with col:
            if not bucket:
                st.caption("_Empty_")
            for idea in bucket:
                _render_kanban_card(idea, status, color, ideas, id_to_idea, depth=0)

def _render_kanban_card(idea, status, color, all_ideas, id_to_idea, depth=0):
    eng      = idea.get("assigned_engineer") or ""
    proj     = idea.get("project") or "-"
    delivery = idea.get("delivery_date") or ""
    hold     = idea.get("hold_reason") or ""
    name     = idea.get("name") or "-"
    eng_name = eng.split("@")[0] if "@" in eng else (eng or "—")
    # Simple label: "Child Card" prefix for nested cards, card icon for top-level
    label_prefix = "📦 Child Card — " if depth > 0 else "📄 "
    label = label_prefix + (idea.get("idea_name") or "No Name")[:26]

    with st.expander(label, expanded=False):
        st.markdown(
            f'<div style="border-left:3px solid {color};padding-left:8px;margin-bottom:6px;">'
            f'<span style="font-size:clamp(9px,0.85vw,11px);color:#64748b;">📌 {proj}</span><br>'
            f'<span style="font-size:clamp(9px,0.85vw,11px);color:#64748b;">👤 {name}</span><br>'
            f'<span style="font-size:clamp(9px,0.85vw,11px);color:#64748b;">👷 {eng_name}</span>'
            +(f'<br><span style="font-size:clamp(8px,0.75vw,10px);color:#0369a1;">📅 {delivery}</span>' if delivery else "")
            +(f'<br><span style="font-size:clamp(8px,0.75vw,10px);color:#b45309;">⏸ {hold[:30]}</span>' if hold else "")
            +f'</div>', unsafe_allow_html=True,
        )

        # ── Status move ────────────────────────────────────────────────────
        new_status = st.selectbox("Move to", STATUSES,
                                  index=STATUSES.index(status),
                                  key=f"kanban_sel_{idea['id']}",
                                  label_visibility="collapsed")
        hold_input = ""
        if new_status == "Hold/Park":
            hold_input = st.text_input("Reason *", key=f"kanban_hold_{idea['id']}", placeholder="Hold reason…")
        if st.button("Update", key=f"kanban_btn_{idea['id']}", use_container_width=True):
            if new_status == "Hold/Park" and not hold_input:
                st.error("Enter a hold reason.")
            else:
                upd = {"status": new_status}
                if new_status == "Completed":
                    upd["completion_date"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                upd["hold_reason"] = hold_input if new_status == "Hold/Park" else ""
                update_idea(idea["id"], upd)
                touch_activity()
                st.rerun()

        st.divider()

# ══════════════════════════════════════════════════════════════════════════════
#  PAGE: LOGIN
# ══════════════════════════════════════════════════════════════════════════════
def page_login():
    t = THEMES.get(ss("theme","ALTEN Red & Blue"), THEMES["ALTEN Red & Blue"])
    dark_bg = ss("theme","") == "Midnight Dark"
    surface = "#2a61b8" if dark_bg else "#CACDE3"

    if ss("_session_expired"):
        st.warning("⚠️ Session expired due to 5 minutes of inactivity. Please login again.")
        st.session_state.pop("_session_expired", None)

    _, mid, _ = st.columns([1, 1.6, 1])
    with mid:
        st.markdown(f"""
        <div style="background:{surface};border-radius:16px;padding:32px 36px;
             box-shadow:0 8px 32px rgba(0,0,0,.13);border:1px solid rgba(200,200,200,.2);
             margin-top:20px;">
          <div style="display:flex;align-items:center;justify-content:center;gap:10px;margin-bottom:6px;">
              <img src="{ALTEN_LOGO_URL}" style="height:72px;object-fit:contain;" alt="ALTEN"/>
              <span style="font-size:clamp(20px,2vw,26px);font-weight:900; color:#000; letter-spacing:1px;"> TURBO DRIVE</span>
          </div>
          <div style="text-align:center;color:#64748b;font-size:clamp(11px,1vw,13px);margin-bottom:2px;">
            Ideation &amp; Automation Workflow Manager
          </div>
        </div>""", unsafe_allow_html=True)

        with st.form("login_form", clear_on_submit=False):
            email    = st.text_input("📧 Email (Username)", placeholder="you@company.com")
            password = st.text_input("🔒 Password", type="password",
                                     placeholder="Required for Admin / PL / Engineer roles")
            submitted = st.form_submit_button("🚀 Login", use_container_width=True)
            if submitted:
                if not email:
                    st.error("Please enter your email.")
                else:
                    row, err = check_login(email.strip(), password.strip())
                    if err:
                        st.error(f"🚫 {err}")
                    elif not ROLE_PAGES.get(row["role"]):
                        st.error("No pages configured for your role.")
                    else:
                        st.session_state["email"] = row["email"]
                        st.session_state["role"]  = row["role"]
                        st.session_state["name"]  = row["email"].split("@")[0].replace("."," ").title()
                        st.success("Welcome!")
                        st.rerun()

        col1, col2, col3 = st.columns(3)
        with col2:
            if st.button("🔑 Change Password"):
                st.session_state["_page_override"] = "change_password"; st.rerun()
        with col3:
            if st.button("📝 Register"):
                st.session_state["_page_override"] = "register"; st.rerun()

        st.markdown(
            f'<p style="font-size:clamp(9px,0.8vw,10px);text-align:center;color:#94a3b8;margin-top:6px;">'
            f'For queries: <a href="mailto:{SUPPORT_EMAIL}" style="color:#00AEEF;">'
            f'{SUPPORT_NAME} — {SUPPORT_EMAIL}</a></p>',
            unsafe_allow_html=True
        )
    render_copyright()

# ══════════════════════════════════════════════════════════════════════════════
#  PAGE: REGISTER
# ══════════════════════════════════════════════════════════════════════════════
def page_register():
    page_header("Create Account")
    st.caption("Only **organisational email addresses** are accepted (free providers like Gmail, Yahoo, Hotmail, Rediff are not allowed). Self-registered users get **Submit Idea** access; an Admin can upgrade your role.")
    with st.form("reg_form"):
        name  = st.text_input("Full Name")
        email = st.text_input("Work Email", placeholder="you@company.com")
        pw    = st.text_input("Password", type="password")
        cpw   = st.text_input("Confirm Password", type="password")
        if st.form_submit_button("🚀 Create Account", use_container_width=True):
            if not all([name,email,pw,cpw]):
                st.error("All fields are required.")
            elif not is_org_email(email.strip()):
                st.error("🚫 Free-mail providers (Gmail, Yahoo, Hotmail, Rediff, etc.) are not allowed. Please use your organisational email.")
            elif pw != cpw:
                st.error("Passwords do not match.")
            elif len(pw) < 4:
                st.error("Password must be at least 4 characters.")
            else:
                resp = get_supabase().table("users").select("email").eq("email",email.lower()).execute()
                if resp.data:
                    st.error("This email is already registered — please log in.")
                else:
                    get_supabase().table("users").insert({
                        "email":email.lower(),"role":"normal user","password_hash":generate_password_hash(pw)
                    }).execute()
                    st.success("✅ Registered! You can now log in.")
    if st.button("← Back to Login"):
        st.session_state.pop("_page_override",None); st.rerun()
    render_copyright()

# ══════════════════════════════════════════════════════════════════════════════
#  PAGE: CHANGE PASSWORD
# ══════════════════════════════════════════════════════════════════════════════
def page_change_password():
    page_header("Change Password")
    prefill = ss("email","")
    with st.form("cpw_form"):
        email  = st.text_input("Email", value=prefill)
        cur_pw = st.text_input("Current Password", type="password")
        new_pw = st.text_input("New Password", type="password")
        cnf_pw = st.text_input("Confirm New Password", type="password")
        if st.form_submit_button("Update Password", use_container_width=True):
            if not all([email,cur_pw,new_pw,cnf_pw]):
                st.error("All fields required.")
            elif new_pw != cnf_pw:
                st.error("New passwords do not match.")
            elif len(new_pw) < 4:
                st.error("Minimum 4 characters.")
            else:
                resp = get_supabase().table("users").select("*").eq("email",email.lower()).execute()
                if not resp.data:
                    st.error("Email not found.")
                elif not check_password_hash(resp.data[0].get("password_hash") or "", cur_pw):
                    st.error("🚫 Current password is incorrect.")
                else:
                    set_password(email.lower(), new_pw)
                    st.success("✅ Password changed successfully.")
    if st.button("← Back"):
        st.session_state.pop("_page_override",None); st.rerun()
    render_copyright()

# ══════════════════════════════════════════════════════════════════════════════
#  PAGE: SUBMIT IDEA
# ══════════════════════════════════════════════════════════════════════════════
def page_submit():
    page_header("Submit New Idea 💡")
    users     = get_users()
    pl_emails = [u["email"] for u in users if u["role"] in ("pl/spl","automation pl","super user")]

    otp_rows    = get_otp_list()
    otp_lookup  = {r.get("otp",""): r for r in otp_rows if r.get("otp")}
    otp_options = [""] + sorted(otp_lookup.keys())

    if not otp_lookup:
        st.warning("⚠️ No OTP entries configured yet — ask an Admin to add them under **OTP List**.")

    name      = st.text_input("Your Full Name", value=ss("name",""))
    sub_email = st.text_input("Your Email", value=ss("email",""))
    idea_name = st.text_input("Idea Name *", placeholder="Short title for the idea")
    idea_desc = st.text_area("Idea Description *", placeholder="Describe the automation idea in detail")

    SHAREPOINT_ATTACH_URL = ("https://altengroup.sharepoint.com/:f:/r/sites/Rolls-RoyceGlobalEfS/Production/25.%20EFS%20Continuous%20Improvements/Turbo%20Drive?csf=1&web=1&e=a63ybE")
    st.markdown("**Attachments (optional) please rename your file with the idea name**")
    st.markdown(f"""
    <a href="{SHAREPOINT_ATTACH_URL}" target="_blank" rel="noopener noreferrer"
       style="display:inline-flex;align-items:center;gap:8px;border:1px solid #94a3b8;
              border-radius:8px;padding:8px 16px;text-decoration:none;color:#1e293b;
              font-size:13px;font-weight:600;background:rgba(148,163,184,.06);">
        📎 Browse SharePoint Folder
    </a>
    """, unsafe_allow_html=True)

    sel_otp = st.selectbox(
        "OTP *", otp_options, key="submit_otp_select",
        help="Select the OTP to auto-fill Project Name, Business Unit, PD and SPL/PL below.",
    )
    otp_row = otp_lookup.get(sel_otp, {})

    with st.form("submit_form", clear_on_submit=True):
        ac1, ac2 = st.columns(2)
        with ac1:
            project_name  = st.text_input("Project name", value=otp_row.get("project_name",""), disabled=True)
            business_unit = st.text_input("Business unit", value=otp_row.get("business_unit",""), disabled=True)
        with ac2:
            pd_name = st.text_input("PD", value=otp_row.get("pd",""), disabled=True)
            spl_pl  = st.text_input("SPL/PL (from OTP)", value=otp_row.get("spl_pl",""), disabled=True)

        col1, col2 = st.columns(2)
        with col1:
            category = st.selectbox("Idea Category", CATEGORIES)
            customer = st.selectbox("Customer *", CUSTOMERS)
        with col2:
            region  = st.selectbox("Region *", REGIONS)
            pl_name = st.selectbox("Assign PL / SPL", pl_emails) if pl_emails else st.text_input("PL/SPL Email")
        if st.form_submit_button("🚀 Submit Idea", use_container_width=True):
            if not sel_otp:
                st.error("Please select an OTP.")
            elif not idea_name or not idea_desc:
                st.error("Idea Name and Description are required.")
            else:
                new_id = str(uuid.uuid4())
                add_idea({"id":new_id,"name":name,"submitter_email":sub_email,
                          "idea_name":idea_name,"idea":idea_desc,"project":project_name,
                          "otp":sel_otp,"business_unit":business_unit,"pd_name":pd_name,"spl_pl":spl_pl,
                          "category":category,"pl_name":pl_name,"status":"New Idea",
                          "customer":customer,"region":region})
                st.success("✅ Idea Submitted Successfully")
    render_copyright()

# ══════════════════════════════════════════════════════════════════════════════
#  PAGE: PL ASSIGNMENT  (+ engineer load bar chart top-right)
# ══════════════════════════════════════════════════════════════════════════════
def page_pl_assignment():
    page_header("PL Assignment 🧑‍💼")

    all_ideas = get_all()
    users     = get_users()
    engineers = [u["email"] for u in users if u["role"]=="automation engineer"]

    # ── Engineer load bar chart — top right ──────────────────────────────
    left_col, right_col = st.columns([2, 1])

    with right_col:
        st.markdown("<span style='font-size:13px;font-weight:600;'>📊 Engineer Task Load</span>", unsafe_allow_html=True)
        if engineers:
            MAX_TASKS = 10   # max expected tasks — gauge full at 10
            for eng in engineers:
                active = len([
                    i for i in all_ideas
                    if i.get("assigned_engineer")==eng
                    and i.get("status") in {"Assigned","WIP","UAT","Hold/Park"}
                ])
                eng_label = eng.split("@")[0].replace("."," ").title()
                load_pct  = min(active / MAX_TASKS, 1.0) * 100
                # colour: green→amber→red based on load
                needle_color = ("#059669" if load_pct < 40
                                else "#b45309" if load_pct < 75
                                else "#dc2626")
                gauge_opt = {
                    "series":[{
                        "type":"gauge",
                        "radius":"85%",
                        "startAngle":200,"endAngle":-20,
                        "min":0,"max":MAX_TASKS,
                        "splitNumber":5,
                        "axisLine":{
                            "lineStyle":{
                                "width":10,
                                "color":[[0.4,"#059669"],[0.75,"#b45309"],[1,"#dc2626"]]
                            }
                        },
                        "pointer":{"itemStyle":{"color":"auto"},"length":"60%","width":4},
                        "axisTick":{"distance":-15,"length":6,"lineStyle":{"color":"#fff","width":1}},
                        "splitLine":{"distance":-20,"length":12,"lineStyle":{"color":"#fff","width":2}},
                        "axisLabel":{"color":"inherit","distance":18,"fontSize":8},
                        "detail":{
                            "valueAnimation":True,
                            "formatter":f"{active} tasks",
                            "color":"inherit","fontSize":11,"offsetCenter":[0,"60%"]
                        },
                        "title":{"offsetCenter":[0,"85%"],"fontSize":9,"color":"#64748b"},
                        "data":[{"value":active,"name":eng_label}],
                    }]
                }
                st_echarts(gauge_opt, height="160px", key=f"gauge_{eng}")
        else:
            st.info("No automation engineers configured.")

    with left_col:
        st.caption("⭐ Auto-priority: **Customer Requirement → ROI (high first) → FIFO**")
        new_ideas = rank_ideas([i for i in all_ideas if i.get("status")=="New Idea"])

        if not new_ideas:
            st.info("No new ideas pending assignment.")
        else:
            if ss("_assign_outlook_url"):
                url = ss("_assign_outlook_url"); lbl = ss("_assign_outlook_label","")
                st.markdown(f"""
                <div class="idea-card" style="border-left:4px solid #0ea5e9;">
                  📤 <b>Send assignment notification via Outlook:</b><br>
                  <span style="color:#64748b;font-size:12px;">{lbl}</span><br>
                  <a href="{url}" target="_blank" class="outlook-btn">📤 Open in Outlook</a>
                </div>""", unsafe_allow_html=True)
                st.session_state.pop("_assign_outlook_url",None)
                st.session_state.pop("_assign_outlook_label",None)

            for idea in new_ideas:
                with st.expander(f"💡 {idea.get('idea_name','(no name)')}  —  {idea.get('priority_label','')}"):
                    c1,c2 = st.columns(2)
                    with c1:
                        st.markdown(f"**Submitted by:** {idea.get('name','-')}")
                        st.markdown(f"**Project:** {idea.get('project','-')} | **Category:** {idea.get('category','-')}")
                        st.markdown(f"**Customer:** {idea.get('customer','-')} | **Region:** {idea.get('region','-')}")
                        st.markdown(f"**Description:** {idea.get('idea','-')}")
                    with c2:
                        st.markdown(f"**PL/SPL:** {idea.get('pl_name','-')}")
                        st.markdown(f"**Submitted:** {idea.get('created_date','-')[:10]}")

                    if not engineers:
                        st.warning("No automation engineers in the system — add them in Admin first.")
                        continue

                    with st.form(f"assign_{idea['id']}"):
                        eng = st.selectbox("Assign Engineer", engineers, key=f"eng_{idea['id']}")
                        if st.form_submit_button("✅ Assign"):
                            qi = compute_delivery(all_ideas, eng, idea)
                            update_idea(idea["id"],{
                                "assigned_engineer":eng,"status":"Assigned",
                                "assigned_date":datetime.now().strftime("%Y-%m-%d %H:%M"),
                                "priority_label":qi["priority_label"] if qi else "",
                                "sprint_start":fmt_d(qi["sprint_start"]) if qi else "",
                                "sprint_end":fmt_d(qi["sprint_end"]) if qi else "",
                                "delivery_date":fmt_d(qi["sprint_end"]) if qi else "",
                            })
                            fresh = next((i for i in get_all() if i["id"]==idea["id"]),idea)
                            url = build_assign_outlook(fresh, eng, qi)
                            st.session_state["_assign_outlook_url"]   = url
                            st.session_state["_assign_outlook_label"] = f"Notify {eng} — {idea.get('idea_name','')}"
                            st.success(f"Assigned to {eng}. Click 📤 Open in Outlook above to notify them.")
                            st.rerun()

    # ── Engineer Workload & Sprint Schedule ───────────────────────────────
    st.divider()
    st.markdown("#### 📅 Engineer Workload & Sprint Schedule")
    st.caption("Active tasks per engineer ordered by auto-priority (Customer → ROI → FIFO), with rolling 2-week sprint dates.")
    all_eng = [u["email"] for u in users if u["role"]=="automation engineer"]
    if not all_eng:
        st.info("No automation engineers configured — add them in Admin.")
    else:
        sel_engs = st.multiselect("Select Engineer(s) to view", all_eng,
                                  default=None, placeholder="Choose one or more engineers…")
        import pandas as pd
        for eng in (sel_engs or []):
            queue = engineer_queue(all_ideas, eng)
            with st.expander(f"👷 {eng}  —  {len(queue)} active task(s)", expanded=True):
                if not queue:
                    st.caption("No active tasks — fully available.")
                else:
                    df = pd.DataFrame([{
                        "Queue #":i["priority_rank"],"Idea":i.get("idea_name",""),
                        "Category":i.get("category",""),"Priority":i.get("priority_label",""),
                        "Sprint Start":fmt_d(i["sprint_start"]),"Delivery (Sprint End)":fmt_d(i["sprint_end"]),
                    } for i in queue])
                    st.dataframe(df, use_container_width=True, hide_index=True)

    render_copyright()

# ══════════════════════════════════════════════════════════════════════════════
#  PAGE: FEASIBILITY STUDY
# ══════════════════════════════════════════════════════════════════════════════
def page_feasibility():
    page_header("Feasibility Study 🔍")
    all_ideas = get_all()
    assigned  = rank_ideas([i for i in all_ideas if i.get("status")=="Assigned"])

    if not assigned:
        st.info("No ideas pending feasibility study.")
        render_copyright(); return

    if ss("_feas_outlook_url"):
        url = ss("_feas_outlook_url"); lbl = ss("_feas_outlook_label","")
        st.markdown(f"""
        <div class="idea-card" style="border-left:4px solid #059669;">
          📤 <b>Notify PL/SPL via Outlook:</b><br>
          <span style="color:#64748b;font-size:12px;">{lbl}</span><br>
          <a href="{url}" target="_blank" class="outlook-btn" style="background:#059669;">📤 Open in Outlook</a>
        </div>""", unsafe_allow_html=True)
        st.session_state.pop("_feas_outlook_url",None)
        st.session_state.pop("_feas_outlook_label",None)

    for idea in assigned:
        with st.expander(f"💡 {idea.get('idea_name','(no name)')}  —  {idea.get('priority_label','')}"):
            st.markdown(f"**Engineer:** {idea.get('assigned_engineer','-')}  |  "
                        f"**PL/SPL:** {idea.get('pl_name','-')}  |  **Category:** {idea.get('category','-')}")
            if idea.get("delivery_date"):
                st.caption(f"📅 Provisional delivery: {idea['delivery_date']}")

            with st.form(f"feas_{idea['id']}"):
                st.markdown("##### ROI Calculator")
                col1,col2,col3 = st.columns(3)
                with col1: manual = st.number_input("Manual Effort (hrs)", min_value=0.0, step=0.5, key=f"m_{idea['id']}")
                with col2: fte    = st.number_input("FTE Count", min_value=0.0, step=0.1, key=f"f_{idea['id']}")
                with col3: eng_ef = st.number_input("Automation Effort (hrs)", min_value=0.01, step=0.5, value=1.0, key=f"e_{idea['id']}")
                col4,col5 = st.columns(2)
                with col4: freq     = st.selectbox("Frequency", list(FREQ_MULT.keys()), key=f"fr_{idea['id']}")
                with col5: auto_cat = st.selectbox("Automation Category *", AUTO_CATS, key=f"ac_{idea['id']}")
                comments = st.text_area("Comments / Observations", key=f"co_{idea['id']}")
                roi = round((manual*fte*FREQ_MULT[freq])/eng_ef, 2)
                st.info(f"📈 Computed ROI: **{roi}**")
                if st.form_submit_button("✅ Submit Feasibility & Notify PL via Outlook"):
                    vsm_date = next_workday(date.today()+timedelta(days=1))
                    qi = compute_delivery(all_ideas, idea.get("assigned_engineer",""), {**idea,"roi":roi})
                    update_idea(idea["id"],{
                        "status":"WIP","roi":roi,"automation_category":auto_cat,
                        "feasibility_comments":comments,
                        "feasibility_data":{"manual":manual,"fte":fte,"eng":eng_ef,"freq":freq},
                        "wip_date":datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "vsm_meeting_date":fmt_d(vsm_date),
                        **({"priority_label":qi["priority_label"],
                            "sprint_start":fmt_d(qi["sprint_start"]),
                            "sprint_end":fmt_d(qi["sprint_end"]),
                            "delivery_date":fmt_d(qi["sprint_end"])} if qi else {}),
                    })
                    fresh = next((i for i in get_all() if i["id"]==idea["id"]),idea)
                    url = build_feasibility_outlook(fresh, roi, vsm_date, qi)
                    st.session_state["_feas_outlook_url"]   = url
                    st.session_state["_feas_outlook_label"] = (
                        f"Notify PL/SPL ({idea.get('pl_name','')}) — Feasibility complete for {idea.get('idea_name','')}")
                    st.success(f"✅ Submitted. ROI: {roi} | VSM: {fmt_d(vsm_date)}. Click Outlook above to notify PL/SPL.")
                    st.rerun()
    render_copyright()

# ══════════════════════════════════════════════════════════════════════════════
#  PAGE: APPROVAL
# ══════════════════════════════════════════════════════════════════════════════
def page_approval():
    page_header("PL/SPL Approval ✅")
    all_ideas = get_all()
    my_email  = ss("email","")
    wip_ideas = rank_ideas([i for i in all_ideas
                            if i.get("status")=="WIP" and i.get("pl_name","").lower()==my_email.lower()])
    if not wip_ideas:
        other = rank_ideas([i for i in all_ideas if i.get("status")=="WIP"])
        if other and user_role()=="super user":
            st.info("No ideas assigned to your PL/SPL email — showing all WIP ideas (super user view).")
            wip_ideas = other
        else:
            st.info("No ideas pending your approval.")
            render_copyright(); return

    for idea in wip_ideas:
        fd = idea.get("feasibility_data",{}) or {}
        with st.expander(f"💡 {idea.get('idea_name','(no name)')}  —  ROI: {round(idea.get('roi',0),2)}  |  {idea.get('priority_label','')}"):
            col1,col2 = st.columns(2)
            with col1:
                st.markdown(f"**Engineer:** {idea.get('assigned_engineer','-')}")
                st.markdown(f"**Category:** {idea.get('category','-')} / {idea.get('automation_category','-')}")
                st.markdown(f"**Project:** {idea.get('project','-')}")
            with col2:
                st.markdown(f"**Manual Effort:** {fd.get('manual','-')} hrs | **FTE:** {fd.get('fte','-')} | **Freq:** {fd.get('freq','-')}")
                st.markdown(f"**Automation Effort:** {fd.get('eng','-')} hrs")
                st.markdown(f"**ROI:** {round(idea.get('roi',0),2)}")
            if idea.get("feasibility_comments"):
                st.caption(f"💬 {idea['feasibility_comments']}")
            if idea.get("delivery_date"):
                st.caption(f"📅 Sprint: {idea.get('sprint_start','-')} → {idea.get('sprint_end','-')}")

            with st.form(f"appr_{idea['id']}"):
                decision = st.radio("Decision", ["GO ✅","NO-GO ❌"], horizontal=True, key=f"dec_{idea['id']}")
                comment  = st.text_area("Comments", key=f"acom_{idea['id']}")
                reason   = ""
                if "NO-GO" in decision:
                    reason = st.selectbox("Rejection Reason", REJ_REASONS, key=f"rej_{idea['id']}")
                if st.form_submit_button("Submit Decision"):
                    is_go = "GO" in decision and "NO-GO" not in decision
                    now   = datetime.now().strftime("%Y-%m-%d %H:%M")
                    if is_go:
                        eng = idea.get("assigned_engineer","")
                        qi  = compute_delivery(get_all(), eng, idea)
                        sprint_start = qi["sprint_start"] if qi else None
                        sprint_end   = qi["sprint_end"]   if qi else None
                        update_idea(idea["id"],{
                            "status":"WIP","decision":"GO","approval_comment":comment,"wip_date":now,
                            **({"sprint_start":fmt_d(sprint_start),"sprint_end":fmt_d(sprint_end),
                                "delivery_date":fmt_d(sprint_end),
                                "sprint_meeting_date":fmt_d(sprint_start)} if sprint_start else {}),
                        })
                        st.success("✅ GO — Idea is now In Progress (WIP).")
                    else:
                        update_idea(idea["id"],{"status":"Rejected","decision":"NO-GO",
                                               "rejection_reason":reason,"approval_comment":comment})
                        st.warning(f"❌ NO-GO — Idea rejected. Reason: {reason}")
                    st.rerun()
    render_copyright()

# ══════════════════════════════════════════════════════════════════════════════
#  PAGE: DASHBOARD  — with interlinked filters
# ══════════════════════════════════════════════════════════════════════════════
def page_dashboard():
    page_header("Dashboard ")
    all_ideas_raw = get_all()
    if not all_ideas_raw:
        st.info("No ideas yet.")
        render_copyright(); return

    # ── LIVE USER BADGE — top-right ───────────────────────────────────────
    users            = get_users()
    total_registered = len(users)
    active_count     = 1
    try:
        sb       = get_supabase()
        my_email = ss("email","")
        now_ts   = datetime.utcnow().isoformat()
        cutoff   = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
        if my_email:
            sb.table("active_sessions").upsert(
                {"email": my_email, "last_seen": now_ts}, on_conflict="email"
            ).execute()
        resp = sb.table("active_sessions").select("email").gte("last_seen", cutoff).execute()
        active_count = len(resp.data or [])
    except Exception:
        pass

    # ── FILTER BAR ────────────────────────────────────────────────────────
    all_pls  = sorted({i.get("pl_name","") for i in all_ideas_raw if i.get("pl_name","")})
    all_regs = sorted({i.get("region","")   for i in all_ideas_raw if i.get("region","")})
    all_cats = CATEGORIES

    fc1, fc2, fc3, fc4, fc5 = st.columns([1.0, 1.0, 1.0, 0.45, 0.6])
    with fc1:
        f_cat = st.multiselect("Category", all_cats, key="f_cat",
                               placeholder="All categories", label_visibility="collapsed")
        st.caption("🗂 Category")
    with fc2:
        f_pl  = st.multiselect("PL/SPL", all_pls, key="f_pl",
                               placeholder="All PLs", label_visibility="collapsed")
        st.caption("🧑‍💼 PL / SPL")
    with fc3:
        f_reg = st.multiselect("Region", all_regs, key="f_reg",
                               placeholder="All regions", label_visibility="collapsed")
        st.caption("🌍 Region")
    with fc4:
        st.write("")
        if st.button("🔄 Reset", use_container_width=True):
            for k in ["f_cat","f_pl","f_reg"]: st.session_state.pop(k,None)
            st.rerun()
        st.caption("Reset filters")
    with fc5:
        st.markdown(
            f'<div style="background:linear-gradient(135deg,#1a4fad,#0ea5e9);'
            f'border-radius:9px;padding:10px 9px;text-align:center;'
            f'box-shadow:0 4px 20px rgba(26,79,173,.35);'>
            f'<div style="display:flex;gap:9px;justify-content:center;align-items:center;">'
            f'<div><div style="font-size:8px;color:rgba(255,255,255,.8);letter-spacing:.8px;'
            f'text-transform:uppercase;font-weight:600;">&#128101; Registered</div>'
            f'<div style="font-size:30px;font-weight:800;color:#fff;line-height:1.1;">{total_registered}</div></div>'
            f'</div></div>',
            unsafe_allow_html=True
        )

    # Apply filters — interlinked (all three narrow the same set)
    ideas = all_ideas_raw
    if f_cat: ideas = [i for i in ideas if i.get("category","") in f_cat]
    if f_pl:  ideas = [i for i in ideas if i.get("pl_name","") in f_pl]
    if f_reg: ideas = [i for i in ideas if i.get("region","") in f_reg]

    active_filters = bool(f_cat or f_pl or f_reg)
    if active_filters:
        st.caption(f"📌 Showing **{len(ideas)}** of **{len(all_ideas_raw)}** ideas after filters.")

    if not ideas:
        st.warning("No ideas match the selected filters.")
        render_copyright(); return

    # ── Local helper lambdas (scoped to filtered set) ─────────────────────
    def cnt(s):        return len([i for i in ideas if i.get("status")==s])
    def cnt_cat(cat):  return len([i for i in ideas if i.get("category")==cat])
    def cnt_cat_ac(cat,ac): return len([i for i in ideas if i.get("category")==cat and i.get("automation_category")==ac])
    def cnt_ac(ac):    return len([i for i in ideas if i.get("automation_category")==ac])
    def hrs_cat(cat):  return round(sum(idea_hours(i) for i in ideas if i.get("category")==cat),1)
    def roi_cat(cat):  return round(sum(float(i.get("roi",0) or 0) for i in ideas if i.get("category")==cat),2)
    def roi_ac(ac):    return round(sum(float(i.get("roi",0) or 0) for i in ideas if i.get("automation_category")==ac),2)

    total     = len(ideas)
    cust_hrs  = hrs_cat("Customer Requirement")
    int_hrs   = hrs_cat("Internal")
    cust_roi  = roi_cat("Customer Requirement")
    int_roi   = roi_cat("Internal")
    cust_cnt  = cnt_cat("Customer Requirement")
    int_cnt   = cnt_cat("Internal")
    completed = cnt("Completed")
    completed_pct = round(completed / total * 100, 1) if total else 0.0

    # ── ROW 1: Premium Illustrated KPI Cards ───────────────────────────────
    st.markdown("##### 📦 Key Metrics")
    auto_total_ideas = len([i for i in ideas if i.get("automation_category") in AUTOMATION_CATS])
    ai_total_ideas   = len([i for i in ideas if i.get("automation_category") in AI_CATS])
    proj_count       = len({i.get("project","") for i in ideas if i.get("project")})
    auto_roi = round(sum(float(i.get("roi",0) or 0) for i in ideas if i.get("automation_category","") in AUTOMATION_CATS),1)
    ai_roi = round(sum(float(i.get("roi",0) or 0) for i in ideas if i.get("automation_category","") in AI_CATS),1)

    icon_total = KPI_ILLUSTRATIONS["total_ideas"]
    icon_completed = KPI_ILLUSTRATIONS["trophy"]
    icon_hours = KPI_ILLUSTRATIONS["clock"]
    icon_roi = KPI_ILLUSTRATIONS["growth"]
    st.markdown(f"""
    <style>
      .km-board{{position:relative;overflow:hidden;margin-bottom:22px;border-radius:24px;border:1px solid rgba(255,255,255,.12);background:rgba(15,23,42,.85);box-shadow:0 18px 50px rgba(15,23,42,.25);}}
      .km-track{{display:flex;gap:12px;width:max-content;animation:km-scroll-left 20s linear infinite;animation-play-state:running;}}
      .km-board:hover .km-track{{animation-play-state:paused;}}
      .km-board.paused .km-track{{animation-play-state:paused;}}
      .km-copy{{display:flex;gap:12px;}}
      .km-card{{flex:0 0 260px;min-width:260px;padding:18px 20px;border-radius:18px;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.08);display:grid;grid-template-columns:60px 1fr;gap:12px;min-height:140px;align-items:start;}}
      .km-icon{{width:56px;height:90px;flex:0 0 56px;display:grid;place-items:center;background:rgba(255,255,255,.1);border-radius:18px;}}
      .km-icon svg{{width:100%;height:100%;}}
      .km-text{{display:grid;grid-template-rows:auto 1fr auto;gap:8px;min-height:100%;}}
      .km-header{{font-size:12px;letter-spacing:.24em;text-transform:uppercase;color:rgba(255,255,255,.75);font-weight:700;}}
      .km-card-body{{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;}}
      .km-value{{font-size:36px;font-weight:900;color:#fff;line-height:1.05;}}
      .km-pct{{font-size:12px;font-weight:700;color:#f8fafc;opacity:.95;white-space:nowrap;}}
      .km-footer{{font-size:11px;color:rgba(255,255,255,.68);line-height:1.4;min-height:18px;}}
      @keyframes km-scroll-left{{0%{{transform:translateX(0);}}100%{{transform:translateX(-50%);}}}}
    </style>
    <div class="km-board" id="km-board">
      <div class="km-track" id="km-track">
        <div class="km-copy">
          <div class="km-card"><div class="km-icon" style="color:#facc15;">{icon_total}</div><div class="km-text"><div class="km-header">Total Ideas</div><div class="km-card-body"><div class="km-value">{total}</div><div class="km-pct"></div></div><div class="km-footer"></div></div></div>
          <div class="km-card"><div class="km-icon" style="color:#059669;">{icon_completed}</div><div class="km-text"><div class="km-header">Completed</div><div class="km-card-body"><div class="km-value">{completed}</div><div class="km-pct"></div></div><div class="km-footer"></div></div></div>
          <div class="km-card"><div class="km-icon" style="color:#0d9488;">{icon_hours}</div><div class="km-text"><div class="km-header">Total Hrs Saved / yr</div><div class="km-card-body"><div class="km-value">{cust_hrs+int_hrs:,.0f}</div><div class="km-pct"></div></div><div class="km-footer">Customer + Internal hours</div></div></div>
          <div class="km-card"><div class="km-icon" style="color:#b45309;">{icon_roi}</div><div class="km-text"><div class="km-header">Total ROI</div><div class="km-card-body"><div class="km-value">{cust_roi+int_roi}</div><div class="km-pct"></div></div><div class="km-footer">Customer + Internal ROI</div></div></div>
        </div>
        <div class="km-copy">
          <div class="km-card"><div class="km-icon" style="color:#facc15;">{icon_total}</div><div class="km-text"><div class="km-header">Total Ideas</div><div class="km-card-body"><div class="km-value">{total}</div><div class="km-pct"></div></div><div class="km-footer"></div></div></div>
          <div class="km-card"><div class="km-icon" style="color:#059669;">{icon_completed}</div><div class="km-text"><div class="km-header">Completed</div><div class="km-card-body"><div class="km-value">{completed}</div><div class="km-pct"></div></div><div class="km-footer"></div></div></div>
          <div class="km-card"><div class="km-icon" style="color:#0d9488;">{icon_hours}</div><div class="km-text"><div class="km-header">Total Hrs Saved / yr</div><div class="km-card-body"><div class="km-value">{cust_hrs+int_hrs:,.0f}</div><div class="km-pct"></div></div><div class="km-footer">Customer + Internal hours</div></div></div>
          <div class="km-card"><div class="km-icon" style="color:#b45309;">{icon_roi}</div><div class="km-text"><div class="km-header">Total ROI</div><div class="km-card-body"><div class="km-value">{cust_roi+int_roi}</div><div class="km-pct"></div></div><div class="km-footer">Customer + Internal ROI</div></div></div>
        </div>
      </div>
    </div>
    <script>
      const board = document.getElementById('km-board');
      let lastTap = 0;
      if (board) {{
        board.addEventListener('dblclick', function() {{ board.classList.toggle('paused'); }});
        board.addEventListener('touchend', function(event) {{
          const currentTime = new Date().getTime();
          const tapLength = currentTime - lastTap;
          if (tapLength < 500 && tapLength > 0) {{
            board.classList.toggle('paused');
            event.preventDefault();
          }}
          lastTap = currentTime;
        }});
      }}
    </script>
    """, unsafe_allow_html=True)

    # second KPI row removed

    # -- ROW 2: Cinematic Automation | AI Canvas (exact reference image match) --
    st.markdown("##### \U0001f916 Automation &amp; AI Category Breakdown")

    auto_total = len([i for i in ideas if i.get("automation_category","") in AUTOMATION_CATS])
    ai_total   = len([i for i in ideas if i.get("automation_category","") in AI_CATS])
    auto_done  = len([i for i in ideas if i.get("automation_category","") in AUTOMATION_CATS and i.get("status")=="Completed"])
    ai_done    = len([i for i in ideas if i.get("automation_category","") in AI_CATS and i.get("status")=="Completed"])
    auto_wip   = len([i for i in ideas if i.get("automation_category","") in AUTOMATION_CATS and i.get("status")=="WIP"])
    ai_wip     = len([i for i in ideas if i.get("automation_category","") in AI_CATS and i.get("status")=="WIP"])
    auto_roi   = round(sum(float(i.get("roi",0) or 0) for i in ideas if i.get("automation_category","") in AUTOMATION_CATS),1)
    ai_roi     = round(sum(float(i.get("roi",0) or 0) for i in ideas if i.get("automation_category","") in AI_CATS),1)

    selected_category = ""
    if hasattr(st, "query_params"):
        qp = st.query_params
        if qp and qp.get("selected_category"):
            selected_category = qp.get("selected_category", [""])[0]
    if selected_category and selected_category not in AUTOMATION_CATS + AI_CATS:
        selected_category = ""

    def _cat_stats(cat):
        subset = [i for i in ideas if i.get("automation_category") == cat]
        total = len(subset)
        completed = len([i for i in subset if i.get("status") == "Completed"])
        wip = len([i for i in subset if i.get("status") == "WIP"])
        uat = len([i for i in subset if i.get("status") == "UAT"])
        roi = round(sum(float(i.get("roi",0) or 0) for i in subset),1)
        hrs = round(sum(idea_hours(i) for i in subset),1)
        return total, completed, wip, uat, roi, hrs

    def _category_card(cat):
        count = len([i for i in ideas if i.get("automation_category") == cat])
        label = cat.split("-",1)[-1]
        icon = CATEGORY_ICONS.get(cat, "•")
        active = "selected" if selected_category == cat else ""
        return (
            f'<div class="category-card {active}" onclick="selectCategory(\'{cat}\')">'
            f'<div class="category-icon">{icon}</div>'
            f'<div class="category-count">{count}</div>'
            f'<div class="category-dash">-</div>'
            f'<div class="category-name">{label}</div>'
            '</div>'
        )

    left_category_html = "".join(_category_card(cat) for cat in AUTOMATION_CATS)
    right_category_html = "".join(_category_card(cat) for cat in AI_CATS)

    if selected_category:
        total, completed, wip, uat, roi, hrs = _cat_stats(selected_category)
        selected_label = selected_category.split("-",1)[-1]
        selected_detail_html = f'''
          <div class="detail-overlay">
            <div class="detail-card">
              <div class="detail-title">{selected_label}</div>
              <div class="detail-value">{total}</div>
              <div class="detail-meta">ROI <strong>{roi}</strong> · {hrs:,.0f} hrs saved</div>
              <div class="detail-sub">{completed} Done · {wip} WIP · {uat} UAT</div>
            </div>
          </div>'''
    else:
        selected_detail_html = '''
          <div class="detail-overlay">
            <div class="detail-card detail-card--circle">
              <img class="detail-image" src="https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcRk5gF2yYSpS4q60kNhN4hLGJ2aoRoseCFJAVrTfN8FSA&s=10" alt="Select a category" />
            </div>
          </div>'''

    _canvas_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"/>
<script type="module" src="https://unpkg.com/@splinetool/viewer@1.0.77/build/spline-viewer.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box;}}
html,body{{width:100%;height:100%;overflow:hidden;background:#000;font-family:'Inter',sans-serif;}}
#scene{{
  position:relative;width:100%;height:420px;
  background:radial-gradient(ellipse at 20% 70%,#1a0240 0%,#06091a 45%,#030710 100%);
  overflow:hidden;display:flex;align-items:center;justify-content:space-between;padding:0 36px;
}}
#scene::after{{
  content:"";position:absolute;left:0;right:0;bottom:0;height:48%;
  background:linear-gradient(rgba(139,92,246,.10) 1px,transparent 1px),
             linear-gradient(90deg,rgba(56,189,248,.08) 1px,transparent 1px);
  background-size:44px 44px;
  transform:perspective(600px) rotateX(52deg);transform-origin:bottom center;
  pointer-events:none;z-index:0;
}}
.panel{{flex:0 0 27%;position:relative;z-index:5;display:flex;flex-direction:column;align-items:flex-start;gap:0;}}
.panel.right{{align-items:flex-end;text-align:right;}}
.ptitle{{font-size:13px;font-weight:900;letter-spacing:4px;text-transform:uppercase;margin-bottom:5px;}}
.psub{{font-size:10px;margin-bottom:10px;opacity:.7;font-style:italic;}}
.stats{{display:flex;gap:5px;flex-wrap:wrap;margin-bottom:8px;}}
.panel.right .stats{{justify-content:flex-end;}}
.stat{{display:flex;flex-direction:column;align-items:center;
       background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);
       border-radius:8px;padding:5px 9px;min-width:50px;}}
.stat-v{{font-size:22px;font-weight:800;color:#fff;line-height:1.1;}}
.stat-l{{font-size:7px;letter-spacing:.8px;color:rgba(255,255,255,.45);margin-top:2px;}}
.centre{{flex:0 0 40%;display:flex;flex-direction:column;align-items:center;
         justify-content:flex-start;padding-top:24px;position:relative;z-index:10;}}
.tagline{{font-size:12px;color:rgba(255,255,255,.7);text-align:center;
          margin-bottom:12px;line-height:1.6;letter-spacing:.2px;}}
.tagline b{{color:rgba(255,255,255,.95);}}
#running-board{{position:absolute;top:18px;left:36px;right:36px;height:86px;overflow:hidden;z-index:6;}}
.board-track{{display:flex;gap:12px;width:max-content;animation:scroll-left 22s linear infinite;}}
.board-copy{{display:flex;gap:12px;}}
.board-chip{{flex:0 0 240px;min-width:240px;padding:14px 18px;border-radius:18px;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.12);backdrop-filter:blur(14px);box-shadow:0 18px 45px rgba(15,23,42,.2);}}
.board-label{{font-size:10px;letter-spacing:.22em;text-transform:uppercase;color:rgba(255,255,255,.72);margin-bottom:8px;}}
.board-value{{font-size:20px;font-weight:800;color:#fff;line-height:1.05;}}
.board-sub{{font-size:11px;color:rgba(255,255,255,.7);margin-top:6px;}}
@keyframes scroll-left{{0%{{transform:translateX(0);}}100%{{transform:translateX(-50%);}}}}
#nexbot-wrap{{
  width:260px;height:260px;cursor:crosshair;overflow:hidden;
  border-radius:50%;
  box-shadow:0 0 70px #7c3aed55,0 0 130px #7c3aed22,0 0 35px #38bdf833;
  position:relative;z-index:5;
  background:radial-gradient(circle,#0d0525 30%,#030712 100%);
}}
.category-grid{{display:grid;gap:10px;}}
.category-card{{display:flex;align-items:center;gap:10px;padding:12px 14px;border-radius:18px;
  background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1);cursor:pointer;transition:transform .18s ease,background .18s ease,border-color .18s ease;
}}
.category-card:hover{{transform:translateY(-1px);background:rgba(255,255,255,.1);border-color:rgba(255,255,255,.18);}}
.category-card.selected{{background:linear-gradient(135deg,rgba(56,189,248,.18),rgba(124,58,237,.18));border-color:rgba(56,189,248,.35);}}
.category-icon{{width:38px;height:38px;border-radius:14px;display:grid;place-items:center;
  background:rgba(255,255,255,.08);color:#fff;font-size:18px;}}
.category-count{{font-size:20px;font-weight:800;color:#fff;}}
.category-dash{{font-size:20px;font-weight:800;color:#93c5fd;}}
.category-name{{font-size:12px;font-weight:700;letter-spacing:.4px;text-transform:uppercase;color:#fff;}}
.detail-overlay{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;pointer-events:none;}}
.detail-card{{width:min(220px,90%);padding:16px 18px;border-radius:22px;background:rgba(8,12,30,.92);border:1px solid rgba(255,255,255,.08);backdrop-filter:blur(8px);box-shadow:0 18px 80px rgba(15,23,42,.35);text-align:center;}}
.detail-card.detail-card--circle{{width:160px;height:160px;padding:0;border-radius:50%;overflow:hidden;display:flex;align-items:center;justify-content:center;}}
.detail-image{{width:100%;height:100%;object-fit:cover;border-radius:50%;display:block;}}
.detail-title{{font-size:15px;font-weight:800;color:#f8fafc;margin-bottom:6px;}}
.detail-value{{font-size:36px;font-weight:900;color:#e0e7ff;margin-bottom:6px;}}
.detail-meta{{font-size:11px;color:rgba(148,163,184,.95);margin-bottom:4px;}}
.detail-sub{{font-size:11px;color:rgba(148,163,184,.75);line-height:1.4;}}
.gring1{{width:200px;height:12px;border-radius:50%;margin-top:-4px;
  background:radial-gradient(ellipse,rgba(124,58,237,.55) 0%,transparent 70%);
  box-shadow:0 0 28px rgba(124,58,237,.4);}}
.gring2{{width:140px;height:8px;border-radius:50%;margin-top:3px;
  background:radial-gradient(ellipse,rgba(56,189,248,.35) 0%,transparent 70%);
  box-shadow:0 0 16px rgba(56,189,248,.3);}}
.wave-wrap{{position:absolute;top:50%;z-index:3;pointer-events:none;}}
.wave-left{{left:28%;transform:translateY(-55%);}}
.wave-right{{right:28%;transform:translateY(-55%);}}
.wave-svg{{width:150px;height:80px;overflow:visible;}}
</style></head>
<body>
<div id="scene">

  <div class="panel">
    <div class="ptitle" style="color:#c084fc;text-shadow:0 0 18px #c084fc88;">-AUTOMATION-</div>
    <div class="category-grid">{left_category_html}</div>
  </div>

  <!-- WAVE LEFT -->
  <div class="wave-wrap wave-left">
    <svg class="wave-svg" viewBox="0 0 150 80">
      <defs><filter id="gp"><feGaussianBlur stdDeviation="3" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>
      <path d="M0 40 Q37 10,75 40 Q113 70,150 40" stroke="#c084fc" stroke-width="2.5" fill="none" filter="url(#gp)" opacity=".9">
        <animate attributeName="d" values="M0 40 Q37 10,75 40 Q113 70,150 40;M0 40 Q37 70,75 40 Q113 10,150 40;M0 40 Q37 10,75 40 Q113 70,150 40" dur="2.4s" repeatCount="indefinite"/>
      </path>
      <path d="M0 40 Q37 25,75 40 Q113 55,150 40" stroke="#7c3aed" stroke-width="1.5" fill="none" filter="url(#gp)" opacity=".6">
        <animate attributeName="d" values="M0 40 Q37 25,75 40 Q113 55,150 40;M0 40 Q37 55,75 40 Q113 25,150 40;M0 40 Q37 25,75 40 Q113 55,150 40" dur="1.8s" repeatCount="indefinite"/>
      </path>
      <circle r="3.5" fill="#c084fc" opacity=".95"><animateMotion dur="2.4s" repeatCount="indefinite" path="M0 40 Q37 10,75 40 Q113 70,150 40"/></circle>
      <circle r="2.5" fill="#e879f9" opacity=".8"><animateMotion dur="1.6s" repeatCount="indefinite" begin="0.8s" path="M0 40 Q37 25,75 40 Q113 55,150 40"/></circle>
      <circle r="2" fill="#a855f7" opacity=".6"><animateMotion dur="2s" repeatCount="indefinite" begin="0.4s" path="M0 40 Q37 10,75 40 Q113 70,150 40"/></circle>
    </svg>
  </div>

  <!-- CENTRE -->
  <div class="centre">
    <div class="tagline">
      <b></b><br>
      <span style="color:#c084fc;">&#8592;</span>
      <span style="color:rgba(255,255,255,.6);"> </span>
      <span style="color:#38bdf8;">&#8594;</span>
    </div>
    <div id="nexbot-wrap">
      <spline-viewer id="spline-nexbot"
        url="https://prod.spline.design/kZDDjO5HmRHKWMYo/scene.splinecode"
        style="width:260px;height:260px;display:block;" loading-anim="true">
      </spline-viewer>
      {selected_detail_html}
    </div>
    <div class="gring1"></div>
    <div class="gring2"></div>
  </div>

  <!-- WAVE RIGHT -->
  <div class="wave-wrap wave-right">
    <svg class="wave-svg" viewBox="0 0 150 80">
      <defs><filter id="gc"><feGaussianBlur stdDeviation="3" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>
      <path d="M0 40 Q37 70,75 40 Q113 10,150 40" stroke="#38bdf8" stroke-width="2.5" fill="none" filter="url(#gc)" opacity=".9">
        <animate attributeName="d" values="M0 40 Q37 70,75 40 Q113 10,150 40;M0 40 Q37 10,75 40 Q113 70,150 40;M0 40 Q37 70,75 40 Q113 10,150 40" dur="2.4s" repeatCount="indefinite"/>
      </path>
      <path d="M0 40 Q37 55,75 40 Q113 25,150 40" stroke="#0ea5e9" stroke-width="1.5" fill="none" filter="url(#gc)" opacity=".6">
        <animate attributeName="d" values="M0 40 Q37 55,75 40 Q113 25,150 40;M0 40 Q37 25,75 40 Q113 55,150 40;M0 40 Q37 55,75 40 Q113 25,150 40" dur="1.8s" repeatCount="indefinite"/>
      </path>
      <circle r="3.5" fill="#38bdf8" opacity=".95"><animateMotion dur="2.4s" repeatCount="indefinite" path="M150 40 Q113 10,75 40 Q37 70,0 40"/></circle>
      <circle r="2.5" fill="#7dd3fc" opacity=".8"><animateMotion dur="1.6s" repeatCount="indefinite" begin="0.8s" path="M150 40 Q113 25,75 40 Q37 55,0 40"/></circle>
      <circle r="2" fill="#0ea5e9" opacity=".6"><animateMotion dur="2s" repeatCount="indefinite" begin="0.4s" path="M150 40 Q113 10,75 40 Q37 70,0 40"/></circle>
    </svg>
  </div>

  <!-- RIGHT -->
  <div class="panel right">
    <div class="ptitle" style="color:#38bdf8;text-shadow:0 0 18px #38bdf888;">-AI-</div>
    <div class="category-grid">{right_category_html}</div>
  </div>

</div>
<script>
(function(){{
  var _last=0;
  document.addEventListener("mousemove",function(e){{
    var now=Date.now();if(now-_last<16)return;_last=now;
    var viewer=document.getElementById("spline-nexbot");
    if(!viewer)return;
    var root=viewer.shadowRoot||viewer;
    var canvas=root.querySelector("canvas");
    if(!canvas)return;
    var cr=canvas.getBoundingClientRect();
    var wr=document.getElementById("nexbot-wrap");
    if(!wr)return;
    var wRect=wr.getBoundingClientRect();
    var nx=(e.clientX-wRect.left)/Math.max(wRect.width,1);
    var ny=(e.clientY-wRect.top)/Math.max(wRect.height,1);
    ["pointermove","mousemove"].forEach(function(t){{
      canvas.dispatchEvent(new MouseEvent(t,{{
        clientX:cr.left+nx*cr.width,clientY:cr.top+ny*cr.height,
        bubbles:true,cancelable:true,view:window
      }}));
    }});
  }});
  window.selectCategory = function(cat) {{
    var params = new URLSearchParams(window.location.search);
    if (params.get('selected_category') === cat) {{
      params.delete('selected_category');
    }} else {{
      params.set('selected_category', cat);
    }}
    window.location.search = params.toString();
  }};
}})();
</script>
</body></html>"""
    st.components.v1.html(_canvas_html, height=440, scrolling=False)

    # ── ROW 3: Charts row (Status BAR chart + Customer pie + clean Hours/Project) ─
    st.markdown("##### 📈 Charts")
    ch1, ch2, ch3 = st.columns(3)

    with ch1:
        st.markdown("<span style='font-size:clamp(10px,1vw,13px);font-weight:600;'>Ideas by Status</span>", unsafe_allow_html=True)
        status_labels = [s for s in STATUSES]
        status_vals   = [cnt(s) for s in STATUSES]
        status_cols   = [STATUS_COLORS.get(s,"#888") for s in STATUSES]
        st_echarts({
            "tooltip":{"trigger":"axis","axisPointer":{"type":"shadow"}},
            "grid":{"left":"3%","right":"4%","bottom":"22%","containLabel":True},
            "xAxis":{"type":"category","data":status_labels,
                     "axisLabel":{"rotate":30,"fontSize":8,"interval":0}},
            "yAxis":{"type":"value","name":"Ideas","nameTextStyle":{"fontSize":8}},
            "series":[{
                "type":"bar","data":[{"value":v,"itemStyle":{"color":c}} for v,c in zip(status_vals,status_cols)],
                "barMaxWidth":34,"animationDuration":700,"animationEasing":"elasticOut",
                "label":{"show":True,"position":"top","fontSize":9,"fontWeight":700},
            }]
        }, height="220px")

    with ch2:
        st.markdown("<span style='font-size:clamp(10px,1vw,13px);font-weight:600;'>Customer — Count &amp; ROI</span>", unsafe_allow_html=True)
        cust_data = {}
        for i in ideas:
            c = i.get("customer","") or "Unknown"
            if c not in cust_data: cust_data[c] = {"count":0,"roi":0.0}
            cust_data[c]["count"] += 1
            cust_data[c]["roi"]   += float(i.get("roi",0) or 0)
        cust_palette = ["#E30613","#00AEEF","#7c3aed","#059669","#0d9488","#b45309","#0369a1"]
        c_pie_cnt = [{"value":v["count"],
                      "name":f'{k}\n({round(v["roi"],1)} ROI)',
                      "itemStyle":{"color":cust_palette[idx%len(cust_palette)]}}
                     for idx,(k,v) in enumerate(cust_data.items())]
        st_echarts({
            "tooltip":{"trigger":"item","formatter":"{b}: {c} ideas ({d}%)"},
            "series":[{"type":"pie","radius":["35%","65%"],"data":c_pie_cnt,
                       "label":{"fontSize":9,"formatter":"{b}"},
                       "labelLine":{"length":8,"length2":5}}]
        }, height="220px")

    with ch3:
        st.markdown("<span style='font-size:clamp(10px,1vw,13px);font-weight:600;'>Hours Saved by Project</span>", unsafe_allow_html=True)
        proj_hrs = {}
        for i in ideas:
            h = idea_hours(i)
            if not h or h <= 0:           # skip NULL / zero / invalid hours
                continue
            proj = i.get("project","")
            if not proj:                  # skip missing project too
                continue
            proj_hrs[proj] = proj_hrs.get(proj, 0) + h
        proj_hrs = {k: v for k, v in proj_hrs.items() if v and v > 0}
        if proj_hrs:
            st_echarts({
                "tooltip":{"trigger":"axis"},
                "grid":{"left":"3%","right":"4%","bottom":"28%","containLabel":True},
                "xAxis":{"type":"category","data":list(proj_hrs.keys()),
                         "axisLabel":{"rotate":30,"fontSize":8,"interval":0}},
                "yAxis":{"type":"value","name":"hrs/yr","nameTextStyle":{"fontSize":8}},
                "series":[{"type":"bar","data":[round(v,1) for v in proj_hrs.values()],
                           "itemStyle":{"color":"#7c3aed"},"barMaxWidth":32}]},
                height="220px")
        else:
            st.caption("No projects with valid Hours Saved data yet.")

    # ── ROW 4: Ideation Tree + Region chart ──────────────────────────────
    tr_col, wl_col = st.columns([1.4, 1])

    with tr_col:
        st.markdown("##### 🌳 Ideation Workflow Tree")
        def cs(cat,st_): return len([i for i in ideas if i.get("category")==cat and i.get("status")==st_])
        def rs(r): return len([i for i in ideas if i.get("status")=="Rejected" and i.get("rejection_reason")==r])
        def add_label_boxes(node):
            color = node.get("itemStyle",{}).get("color","#FFFFFF")
            node["label"] = {"show":True,"backgroundColor":color,"color":"#FFFFFF",
                             "borderRadius":5,"padding":[4,8],"position":"inside",
                             "align":"center","fontSize":10,"fontWeight":"bold"}
            for child in node.get("children",[]): add_label_boxes(child)
        # Tree flow: Ideation → Triage/Feasibility(Queued) → Accepted → Customer → WIP / Deployed
        #                                                              → Internal
        #                                              → Rejected
        tree_data = {
            "name":f"Ideation ({total})","itemStyle":{"color":"#1a4fad"},
            "children":[
                {"name":f"Triage / Feasibility Study\n(Queued: {cnt('Assigned')})","itemStyle":{"color":"#1a4fad"},
                 "children":[
                     {"name":f"Accepted ({cnt('WIP')+cnt('UAT')+cnt('Completed')})","itemStyle":{"color":"#059669"},
                      "children":[
                          {"name":f"Customer ({cust_cnt})","itemStyle":{"color":"#00498F"},
                           "children":[
                               {"name":f"WIP ({cs('Customer Requirement','WIP')+cs('Customer Requirement','UAT')})","itemStyle":{"color":"#0d9488"}},
                               {"name":f"Deployed ({cs('Customer Requirement','Completed')})","itemStyle":{"color":"#059669"}},
                           ]},
                          {"name":f"Internal ({int_cnt})","itemStyle":{"color":"#0ea5e9"},
                           "children":[
                               {"name":f"WIP ({cs('Internal','WIP')+cs('Internal','UAT')})","itemStyle":{"color":"#0d9488"}},
                               {"name":f"Deployed ({cs('Internal','Completed')})","itemStyle":{"color":"#059669"}},
                           ]},
                      ]},
                     {"name":f"Rejected ({cnt('Rejected')})","itemStyle":{"color":"#dc2626"},
                      "children":[
                          {"name":f"Technical ({rs('Technical Rejection')})","itemStyle":{"color":"#ef4444"}},
                          {"name":f"Business ({rs('Business Rejection')})","itemStyle":{"color":"#f97316"}},
                      ]},
                 ]},
            ]
        }
        add_label_boxes(tree_data)
        st_echarts({
            "backgroundColor":"#0B0B0D",
            "tooltip":{"trigger":"item","triggerOn":"mousemove"},
            "series":[{"type":"tree","data":[tree_data],
                       "top":"5%","left":"7%","bottom":"5%","right":"15%",
                       "symbol":"rect","symbolSize":1,
                       "lineStyle":{"color":"#f97316","width":2},
                       "label":{"position":"left","verticalAlign":"middle","align":"right","fontSize":10},
                       "leaves":{"label":{"position":"right","verticalAlign":"middle","align":"left","fontSize":9}},
                       "emphasis":{"focus":"descendant"},
                       "expandAndCollapse":True,"animationDuration":550,"initialTreeDepth":2}]
        }, height="400px")

        with wl_col:
            st.markdown("##### 🌍 Region — World Map")
            region_data = {}
            for i in ideas:
                r = (i.get("region","") or "").strip()
                if not r:
                    continue   # skip ideas with no region set
                key = r.upper()
                if key not in region_data:
                    region_data[key] = {"count":0,"roi":0.0}
                region_data[key]["count"] += 1
                region_data[key]["roi"]   += float(i.get("roi",0) or 0)

            no_region_count = len([i for i in ideas if not (i.get("region","") or "").strip()])

            region_counts = {
                "India": region_data.get("INDIA", {"count":0})["count"],
                "USA": region_data.get("USA", {"count":0})["count"],
                "UK": region_data.get("UK", {"count":0})["count"],
                "Germany": region_data.get("GERMANY", {"count":0})["count"],
            }

            map_html = f"""
            <style>
              .region-map-shell {{position:relative;width:100%;min-height:400px;border-radius:22px;overflow:hidden;
                background:#0b1222;border:1px solid rgba(255,255,255,.08);box-shadow:0 20px 50px rgba(0,0,0,.25);
              }}
              .region-map-shell::before {{content:'';position:absolute;inset:0;
                background-image:url('https://upload.wikimedia.org/wikipedia/commons/8/80/World_map_-_low_resolution.svg');
                background-size:cover;background-repeat:no-repeat;background-position:center center;
                opacity:.85;filter:invert(1) brightness(1.6);
              }}
              .region-map-shell .region-overlay {{position:relative;z-index:1;padding:16px;display:grid;grid-template-rows:auto 1fr;gap:12px;}}
              .region-map-shell .region-header {{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;padding:0 6px;}}
              .region-map-shell .region-title {{font-size:14px;font-weight:700;color:#f8fafc;}}
              .region-map-shell .region-subtitle {{font-size:12px;color:rgba(248,250,252,.72);}}
              .region-map-shell .region-pin {{position:absolute;display:inline-flex;align-items:center;justify-content:center;
                width:62px;height:42px;padding:8px 12px;border-radius:999px;background:rgba(255,255,255,.92);
                color:#0f172a;font-size:14px;font-weight:800;white-space:nowrap;
                text-shadow:none;box-shadow:0 10px 30px rgba(0,0,0,.25);
                transform:translate(-50%,-50%);
              }}
              .region-map-shell .region-pin::before {{content:'';position:absolute;inset:0;border-radius:999px;
                background:radial-gradient(circle,rgba(250,204,21,.45) 0%,rgba(250,204,21,0) 70%);
                z-index:-1;
              }}
              .region-map-shell .region-pin.zero {{opacity:.6;}}
              .region-map-shell .region-pin.india {{top:60%;left:66%;}}
              .region-map-shell .region-pin.usa {{top:36%;left:18%;}}
              .region-map-shell .region-pin.uk {{top:26%;left:30%;}}
              .region-map-shell .region-pin.germany {{top:30%;left:39%;}}
            </style>
            <div class="region-map-shell">
              <div class="region-overlay">
                <div class="region-header">
                  <div>
                    <div class="region-title"></div>
                    <div class="region-subtitle"></div>
                  </div>
                </div>
              </div>
              <div class="region-pin india{' zero' if region_counts['India']==0 else ''}" title="India: {region_counts['India']} idea(s)">{region_counts['India']}</div>
              <div class="region-pin usa{' zero' if region_counts['USA']==0 else ''}" title="USA: {region_counts['USA']} idea(s)">{region_counts['USA']}</div>
              <div class="region-pin uk{' zero' if region_counts['UK']==0 else ''}" title="UK: {region_counts['UK']} idea(s)">{region_counts['UK']}</div>
              <div class="region-pin germany{' zero' if region_counts['Germany']==0 else ''}" title="Germany: {region_counts['Germany']} idea(s)">{region_counts['Germany']}</div>
            </div>
            """
            st.markdown(map_html, unsafe_allow_html=True)

            if no_region_count:
                st.caption(f"ℹ️ {no_region_count} idea(s) have no region assigned and are excluded.")
    # ── All Ideas table + CSV (above Kanban) ────────────────────────────
    st.markdown("##### 📄 All Ideas")
    search = st.text_input("🔎 Search ideas", placeholder="Filter by name, project, status…")
    import pandas as pd
    cols_show = ["idea_name","name","project","category","automation_category","status",
                 "priority_label","assigned_engineer","roi"]
    cols_show_tail = ["sprint_start","delivery_date","customer","region","created_date"]
    rows = []
    for i in ideas:
        row = {c: i.get(c,"") for c in cols_show}
        row["Saving Hours"] = round(idea_hours(i), 1)
        row.update({c: i.get(c,"") for c in cols_show_tail})
        rows.append(row)
    df = pd.DataFrame(rows)
    if search:
        mask = df.apply(lambda r: r.astype(str).str.contains(search, case=False).any(), axis=1)
        df   = df[mask]
    st.dataframe(df, use_container_width=True, hide_index=True)
    csv_buf = io.StringIO()
    df.to_csv(csv_buf, index=False)
    st.download_button("⬇️ Download CSV", csv_buf.getvalue(), "turbodrive_ideas.csv", "text/csv")

    # ── Kanban Board ──────────────────────────────────────────────────────
    st.markdown("##### 📋 Kanban Board")
    render_kanban_board(ideas)

    render_copyright()

# ══════════════════════════════════════════════════════════════════════════════
#  PAGE: OTP LIST  (master lookup table — feeds Submit Idea autofill)
# ══════════════════════════════════════════════════════════════════════════════
def page_otp_list():
    page_header("OTP List 🔑")
    st.caption("Master lookup table. The OTP a user picks on **Submit Idea** auto-fills Project name, Business unit, PD and SPL/PL from this table.")
    otp_rows = get_otp_list()

    tab1, tab2, tab3 = st.tabs(["📋 OTP Table", "⬆️ Upload CSV", "➕ Add / Edit / Delete"])

    with tab1:
        st.markdown(f"**{len(otp_rows)} OTP entries**")
        if otp_rows:
            import pandas as pd
            df = pd.DataFrame([{
                "OTP":r.get("otp",""),
                "Project name":r.get("project_name",""),
                "Business unit":r.get("business_unit",""),
                "PD":r.get("pd",""),
                "SPL/PL":r.get("spl_pl",""),
            } for r in otp_rows])
            search = st.text_input("🔎 Search OTP list", placeholder="Filter by OTP, project, PD…", key="otp_search")
            if search:
                mask = df.apply(lambda r: r.astype(str).str.contains(search, case=False).any(), axis=1)
                df = df[mask]
            st.dataframe(df, use_container_width=True, hide_index=True)
            csv_buf = io.StringIO()
            df.to_csv(csv_buf, index=False)
            st.download_button("⬇️ Download CSV", csv_buf.getvalue(), "otp_list.csv", "text/csv")
        else:
            st.info("No OTP entries yet — add them via **Upload CSV** or **Add / Edit / Delete**.")

    with tab2:
        st.caption("CSV must include columns: **OTP, Project name, Business unit, PD, SPL/PL** (header names matched case-insensitively). Existing OTPs are updated; new OTPs are inserted.")
        upload = st.file_uploader("Upload OTP list CSV", type=["csv"], key="otp_csv_upload")
        if upload is not None:
            try:
                text   = upload.getvalue().decode("utf-8-sig")
                reader = csv.DictReader(io.StringIO(text))
                col_map = {}
                for h in (reader.fieldnames or []):
                    key = h.strip().lower()
                    if key == "otp": col_map[h] = "otp"
                    elif key in ("project name","project"): col_map[h] = "project_name"
                    elif key in ("business unit","businessunit"): col_map[h] = "business_unit"
                    elif key == "pd": col_map[h] = "pd"
                    elif key in ("spl/pl","spl_pl","splpl"): col_map[h] = "spl_pl"
                rows = []
                for raw in reader:
                    mapped = {v:(raw.get(k) or "").strip() for k,v in col_map.items()}
                    if mapped.get("otp"):
                        rows.append(mapped)
                if not rows:
                    st.error("No valid rows found — make sure the CSV has an 'OTP' column.")
                else:
                    st.info(f"Found {len(rows)} valid row(s) ready to import.")
                    if st.button(f"✅ Import {len(rows)} row(s)", key="otp_import_btn"):
                        for r in rows:
                            upsert_otp_row(r.get("otp",""), r.get("project_name",""),
                                          r.get("business_unit",""), r.get("pd",""), r.get("spl_pl",""))
                        st.success(f"Imported/updated {len(rows)} OTP entries.")
                        st.rerun()
            except Exception as e:
                st.error(f"Could not parse CSV: {e}")

    with tab3:
        st.markdown("##### Add or update a single OTP entry")
        with st.form("otp_add_form", clear_on_submit=True):
            otp_val   = st.text_input("OTP *")
            proj_val  = st.text_input("Project name")
            bu_val    = st.text_input("Business unit")
            pd_val    = st.text_input("PD")
            splpl_val = st.text_input("SPL/PL")
            if st.form_submit_button("💾 Save OTP Entry"):
                if not otp_val.strip():
                    st.error("OTP is required.")
                else:
                    upsert_otp_row(otp_val.strip(), proj_val.strip(), bu_val.strip(), pd_val.strip(), splpl_val.strip())
                    st.success(f"Saved OTP entry: {otp_val.strip()}")
                    st.rerun()

        if otp_rows:
            st.markdown("##### Existing entries")
            for r in otp_rows:
                with st.expander(f"🔑 {r.get('otp','')} — {r.get('project_name','-')}"):
                    st.markdown(f"**Business Unit:** {r.get('business_unit','-')}  |  **PD:** {r.get('pd','-')}  |  **SPL/PL:** {r.get('spl_pl','-')}")
                    if st.button("🗑 Delete", key=f"otp_del_{r.get('otp','')}"):
                        delete_otp_row(r.get("otp",""))
                        st.warning(f"Deleted OTP entry: {r.get('otp','')}")
                        st.rerun()

    render_copyright()

# ══════════════════════════════════════════════════════════════════════════════
#  PAGE: WORKFLOW
# ══════════════════════════════════════════════════════════════════════════════
def page_workflow():
    page_header("Workflow 🔀")
    _workflow_html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>EFS Turbo Drive — Automation Workflow</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700;800&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet"/>
<style>
*{margin:0;padding:0;box-sizing:border-box;}
html,body{background:#070b14;color:#e2e8f0;font-family:'Inter',sans-serif;min-height:100vh;overflow-x:hidden;}
#bg{position:fixed;inset:0;z-index:0;opacity:.28;pointer-events:none;}

/* ── Page shell ── */
.page{position:relative;z-index:1;padding:32px 40px 72px;max-width:1120px;margin:0 auto;}

/* ── Header ── */
.hdr{display:flex;align-items:flex-start;justify-content:space-between;
     margin-bottom:36px;padding-bottom:22px;
     border-bottom:1px solid rgba(0,212,255,.13);}
.hdr-left{}
.badge{display:inline-block;padding:5px 13px;border-radius:20px;
       background:rgba(0,212,255,.08);border:1px solid rgba(0,212,255,.28);
       font-family:'Space Grotesk',sans-serif;font-size:10px;font-weight:700;
       letter-spacing:2px;color:#00D4FF;text-transform:uppercase;margin-bottom:10px;}
.hdr-title{font-family:'Space Grotesk',sans-serif;font-size:clamp(20px,2.6vw,30px);
           font-weight:800;background:linear-gradient(100deg,#00D4FF,#8B5CF6 55%,#10B981);
           -webkit-background-clip:text;background-clip:text;color:transparent;line-height:1.2;}
.hdr-sub{font-size:12px;color:#475569;margin-top:5px;}
.legend{display:flex;flex-direction:column;gap:7px;align-items:flex-end;}
.leg{display:flex;align-items:center;gap:7px;font-size:10.5px;color:#64748b;font-weight:500;}
.ld{width:11px;height:11px;border-radius:3px;}

/* ── SVG wrapper ── */
.svg-wrap{background:rgba(255,255,255,.016);border:1px solid rgba(0,212,255,.08);
          border-radius:20px;padding:28px 20px;overflow:hidden;}

/* ── Bottom row cards ── */
.outcome-row{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:22px;}
.ocard{background:rgba(255,255,255,.03);border-radius:14px;padding:16px 18px;
       border:1.5px solid rgba(255,255,255,.07);
       display:flex;align-items:center;gap:13px;
       transition:border-color .25s,box-shadow .25s;}
.ocard:hover{border-color:rgba(0,212,255,.35);box-shadow:0 0 22px rgba(0,212,255,.12);}
.ocard.v:hover{border-color:rgba(139,92,246,.35);box-shadow:0 0 22px rgba(139,92,246,.12);}
.ocard.g:hover{border-color:rgba(16,185,129,.35);box-shadow:0 0 22px rgba(16,185,129,.12);}
.ocard-icon{font-size:26px;flex-shrink:0;}
.ocard-title{font-family:'Space Grotesk',sans-serif;font-size:13px;font-weight:700;}
.ocard-sub{font-size:10px;color:#475569;margin-top:3px;line-height:1.5;}

/* ── Step strip ── */
.step-strip{display:flex;margin-top:22px;
            background:rgba(255,255,255,.02);border:1px solid rgba(255,255,255,.05);
            border-radius:14px;overflow:hidden;}
.step{flex:1;text-align:center;padding:12px 6px;
      border-right:1px solid rgba(255,255,255,.05);}
.step:last-child{border-right:none;}
.step-n{font-family:'Space Grotesk',sans-serif;font-size:17px;font-weight:800;}
.step-l{font-size:9px;color:#475569;margin-top:2px;font-weight:500;line-height:1.4;}

/* ── Footer ── */
.foot{display:flex;justify-content:space-between;align-items:center;
      margin-top:40px;padding-top:16px;border-top:1px solid rgba(255,255,255,.04);
      font-size:10.5px;color:#1e293b;flex-wrap:wrap;gap:8px;}

/* ── Animations ── */
@keyframes gPulse{0%,100%{filter:drop-shadow(0 0 4px rgba(0,212,255,.3));}
                  50%{filter:drop-shadow(0 0 12px rgba(0,212,255,.7));}}
@keyframes vPulse{0%,100%{filter:drop-shadow(0 0 4px rgba(139,92,246,.3));}
                  50%{filter:drop-shadow(0 0 12px rgba(139,92,246,.7));}}
@keyframes spin{from{transform-origin:50% 50%;transform:rotate(0deg);}
                to{transform-origin:50% 50%;transform:rotate(360deg);}}
</style>
</head>
<body>
<canvas id="bg"></canvas>
<div class="page">

  <!-- Header -->
  <div class="hdr">
    <div class="hdr-left">
      <div class="badge">EFS · ALTEN Engineering Services</div>
      <div class="hdr-title">Turbo Drive — Automation Workflow</div>
      <div class="hdr-sub">Agile · Sprint-based · Continuous Improvement &nbsp;|&nbsp; Sensitivity: C1-Internal</div>
    </div>
    <div class="legend">
      <div class="leg"><div class="ld" style="background:#00D4FF;box-shadow:0 0 5px #00D4FF;"></div>Process / Action</div>
      <div class="leg"><div class="ld" style="background:#8B5CF6;border-radius:50%;box-shadow:0 0 5px #8B5CF6;"></div>Decision Gate</div>
      <div class="leg"><div class="ld" style="background:#10B981;box-shadow:0 0 5px #10B981;"></div>Outcome</div>
      <div class="leg"><div class="ld" style="background:#ef4444;box-shadow:0 0 5px #ef4444;"></div>Reject / Park</div>
    </div>
  </div>

  <!-- SVG Diagram -->
  <div class="svg-wrap">
  <svg id="wf" viewBox="0 0 1060 2450" xmlns="http://www.w3.org/2000/svg"
       style="width:100%;height:auto;display:block;">
  <defs>
    <!-- Arrow markers -->
    <marker id="ab" markerWidth="7" markerHeight="7" refX="5" refY="3.5" orient="auto">
      <path d="M0,0.5 L0,6.5 L7,3.5z" fill="#00D4FF"/>
    </marker>
    <marker id="av" markerWidth="7" markerHeight="7" refX="5" refY="3.5" orient="auto">
      <path d="M0,0.5 L0,6.5 L7,3.5z" fill="#8B5CF6"/>
    </marker>
    <marker id="ag" markerWidth="7" markerHeight="7" refX="5" refY="3.5" orient="auto">
      <path d="M0,0.5 L0,6.5 L7,3.5z" fill="#10B981"/>
    </marker>
    <marker id="ar" markerWidth="7" markerHeight="7" refX="5" refY="3.5" orient="auto">
      <path d="M0,0.5 L0,6.5 L7,3.5z" fill="#ef4444"/>
    </marker>
    <marker id="ao" markerWidth="7" markerHeight="7" refX="5" refY="3.5" orient="auto">
      <path d="M0,0.5 L0,6.5 L7,3.5z" fill="#fb923c"/>
    </marker>

    <!-- Filters -->
    <filter id="fb" x="-60%" y="-60%" width="220%" height="220%">
      <feGaussianBlur stdDeviation="5" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <filter id="fv" x="-60%" y="-60%" width="220%" height="220%">
      <feGaussianBlur stdDeviation="5" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <filter id="fg" x="-60%" y="-60%" width="220%" height="220%">
      <feGaussianBlur stdDeviation="5" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <filter id="fr" x="-60%" y="-60%" width="220%" height="220%">
      <feGaussianBlur stdDeviation="4" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>

    <!-- Gradients -->
    <linearGradient id="cylg" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#021a30"/>
      <stop offset="45%" stop-color="#0a2d4a"/>
      <stop offset="100%" stop-color="#021a30"/>
    </linearGradient>
    <radialGradient id="globeg" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#1a0a3d"/>
      <stop offset="100%" stop-color="#0a0618"/>
    </radialGradient>
    <linearGradient id="arrowg" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#00D4FF" stop-opacity="0.7"/>
      <stop offset="50%" stop-color="#8B5CF6" stop-opacity="0.9"/>
      <stop offset="100%" stop-color="#10B981" stop-opacity="0.7"/>
    </linearGradient>
  </defs>

  <!-- ================================================================ -->
  <!-- COLUMN GUIDES: main spine x=530, left branch x=200, right x=780 -->
  <!-- ================================================================ -->

  <!-- ─── 1. IDEA INTAKE — TURBO DRIVE CYLINDER (top, cx=530, y=30) ─── -->
  <g filter="url(#fb)" style="animation:gPulse 3s ease-in-out infinite;">
    <!-- Cylinder top ellipse -->
    <ellipse cx="530" cy="58" rx="72" ry="22" fill="#0a2d4a" stroke="#00D4FF" stroke-width="1.8"/>
    <!-- Cylinder body -->
    <rect x="458" y="58" width="144" height="180" fill="url(#cylg)"/>
    <!-- Cylinder bottom ellipse -->
    <ellipse cx="530" cy="238" rx="72" ry="22" fill="#071824" stroke="rgba(0,212,255,.5)" stroke-width="1.5"/>
    <!-- Side lines -->
    <line x1="458" y1="58" x2="458" y2="238" stroke="#00D4FF" stroke-width="1.8"/>
    <line x1="602" y1="58" x2="602" y2="238" stroke="#00D4FF" stroke-width="1.8"/>
    <!-- Shine -->
    <line x1="472" y1="70" x2="472" y2="226" stroke="rgba(0,212,255,.18)" stroke-width="3"/>
    <!-- Labels -->
    <text x="530" y="140" text-anchor="middle" font-family="Space Grotesk"
          font-size="17" font-weight="800" fill="#00D4FF">Turbo Drive</text>
    <text x="530" y="162" text-anchor="middle" font-family="Inter"
          font-size="10" fill="rgba(0,212,255,.6)">IDEA INTAKE</text>
  </g>

  <!-- Arrow: Cylinder → Initial Screening -->
  <line x1="530" y1="260" x2="530" y2="302" stroke="#00D4FF" stroke-width="2"
        marker-end="url(#ab)"/>
  <circle r="5" fill="#00D4FF" opacity=".9">
    <animateMotion dur="1.6s" repeatCount="indefinite"
      path="M530,260 V302"/>
    <animate attributeName="opacity" values="0;1;1;0" dur="1.6s" repeatCount="indefinite"/>
  </circle>

  <!-- ─── 2. INITIAL SCREENING — RECTANGLE (y=302) ─── -->
  <g filter="url(#fv)">
    <rect x="390" y="302" width="280" height="68" rx="13"
          fill="#110e28" stroke="#8B5CF6" stroke-width="1.8"/>
    <text x="530" y="330" text-anchor="middle" font-family="Space Grotesk"
          font-size="14" font-weight="700" fill="#c4b5fd">🔍 Initial Screening</text>
    <text x="530" y="349" text-anchor="middle" font-family="Inter"
          font-size="10" fill="#64748b">PL / SPL Review · Relevance check</text>
    <text x="530" y="362" text-anchor="middle" font-family="Inter"
          font-size="10" fill="#64748b">Category · Feasibility signal</text>
  </g>

  <!-- Arrow: Screening → Decision diamond -->
  <line x1="530" y1="370" x2="530" y2="412" stroke="#8B5CF6" stroke-width="2"
        marker-end="url(#av)"/>
  <circle r="5" fill="#8B5CF6">
    <animateMotion dur="1.4s" repeatCount="indefinite" begin=".3s"
      path="M530,370 V412"/>
    <animate attributeName="opacity" values="0;1;1;0" dur="1.4s" repeatCount="indefinite" begin=".3s"/>
  </circle>

  <!-- ─── DECISION 1: PL/SPL Approve? (diamond, cx=530, cy=462) ─── -->
  <g filter="url(#fv)" style="animation:vPulse 3.5s ease-in-out infinite 0.5s;">
    <polygon points="530,412 620,462 530,512 440,462"
             fill="#0e0b20" stroke="#8B5CF6" stroke-width="2"/>
    <text x="530" y="457" text-anchor="middle" font-family="Space Grotesk"
          font-size="11" font-weight="700" fill="#e2e8f0">Approved?</text>
    <text x="530" y="472" text-anchor="middle" font-family="Space Grotesk"
          font-size="10" fill="#8B5CF6">PL/SPL Gate</text>
    <!-- YES label -->
    <text x="544" y="504" font-family="Space Grotesk" font-size="10"
          font-weight="700" fill="#10B981">YES</text>
    <!-- NO label -->
    <text x="344" y="466" font-family="Space Grotesk" font-size="10"
          font-weight="700" fill="#ef4444">NO</text>
  </g>

  <!-- NO branch → Reject/Park (left) -->
  <path d="M440,462 H240 V512" stroke="#ef4444" stroke-width="1.6" fill="none"
        stroke-dasharray="5 3" marker-end="url(#ar)" opacity=".75"/>
  <text x="310" y="454" font-family="Space Grotesk" font-size="9.5"
        fill="#ef4444" font-weight="700">NO</text>

  <!-- REJECT/PARK 1 (x=160, y=512) -->
  <g filter="url(#fr)">
    <rect x="160" y="512" width="160" height="52" rx="10"
          fill="#200a0a" stroke="#ef4444" stroke-width="1.6"/>
    <text x="240" y="534" text-anchor="middle" font-family="Space Grotesk"
          font-size="12" font-weight="700" fill="#ef4444">✕ Reject / Park</text>
    <text x="240" y="551" text-anchor="middle" font-family="Inter"
          font-size="9" fill="#64748b">Idea closed or deferred</text>
  </g>

  <!-- YES → Business Impact Assessment -->
  <line x1="530" y1="512" x2="530" y2="558" stroke="#00D4FF" stroke-width="2"
        marker-end="url(#ab)"/>
  <circle r="5" fill="#00D4FF">
    <animateMotion dur="1.3s" repeatCount="indefinite" begin=".6s"
      path="M530,512 V558"/>
    <animate attributeName="opacity" values="0;1;1;0" dur="1.3s" repeatCount="indefinite" begin=".6s"/>
  </circle>

  <!-- ─── 3. BUSINESS IMPACT ASSESSMENT (y=558) ─── -->
  <g filter="url(#fb)">
    <rect x="385" y="558" width="290" height="64" rx="13"
          fill="#061824" stroke="#00D4FF" stroke-width="1.8"/>
    <text x="530" y="582" text-anchor="middle" font-family="Space Grotesk"
          font-size="13" font-weight="700" fill="#00D4FF">📊 Business Impact Assessment</text>
    <text x="530" y="599" text-anchor="middle" font-family="Inter"
          font-size="9.5" fill="#64748b">Value · Complexity · Priority · Alignment</text>
    <text x="530" y="613" text-anchor="middle" font-family="Inter"
          font-size="9.5" fill="#64748b">Sponsor identification · Success criteria</text>
  </g>

  <!-- Arrow → VSM Required? -->
  <line x1="530" y1="622" x2="530" y2="664" stroke="#8B5CF6" stroke-width="2"
        marker-end="url(#av)"/>
  <circle r="5" fill="#8B5CF6">
    <animateMotion dur="1.2s" repeatCount="indefinite" begin=".9s"
      path="M530,622 V664"/>
    <animate attributeName="opacity" values="0;1;1;0" dur="1.2s" repeatCount="indefinite" begin=".9s"/>
  </circle>

  <!-- ─── DECISION 2: VSM Required? (cx=530, cy=714) ─── -->
  <g filter="url(#fv)" style="animation:vPulse 3.5s ease-in-out infinite 1s;">
    <polygon points="530,664 618,714 530,764 442,714"
             fill="#0e0b20" stroke="#8B5CF6" stroke-width="2"/>
    <text x="530" y="709" text-anchor="middle" font-family="Space Grotesk"
          font-size="11" font-weight="700" fill="#e2e8f0">VSM</text>
    <text x="530" y="724" text-anchor="middle" font-family="Space Grotesk"
          font-size="10" fill="#8B5CF6">Required?</text>
    <!-- Labels -->
    <text x="462" y="706" font-family="Space Grotesk" font-size="9.5"
          font-weight="700" fill="#10B981">NO</text>
    <text x="624" y="706" font-family="Space Grotesk" font-size="9.5"
          font-weight="700" fill="#10B981">YES</text>
  </g>

  <!-- YES → VSM Workshop (right branch) -->
  <path d="M618,714 H760 V788" stroke="#10B981" stroke-width="1.7" fill="none"
        marker-end="url(#ag)" opacity=".85"/>
  <circle r="5" fill="#10B981">
    <animateMotion dur="1.8s" repeatCount="indefinite" begin=".2s"
      path="M618,714 H760 V788"/>
    <animate attributeName="opacity" values="0;1;1;0" dur="1.8s" repeatCount="indefinite" begin=".2s"/>
  </circle>

  <!-- ─── VSM WORKSHOP (right, x=670, y=788) ─── -->
  <g filter="url(#fg)">
    <rect x="670" y="788" width="190" height="80" rx="12"
          fill="#061e14" stroke="#10B981" stroke-width="1.8"/>
    <text x="765" y="814" text-anchor="middle" font-family="Space Grotesk"
          font-size="12" font-weight="700" fill="#10B981">🧠 VSM Workshop</text>
    <text x="765" y="832" text-anchor="middle" font-family="Inter"
          font-size="9" fill="#64748b">Brainstorming · Value stream map</text>
    <text x="765" y="847" text-anchor="middle" font-family="Inter"
          font-size="9" fill="#64748b">Waste identification · Future state</text>
    <text x="765" y="860" text-anchor="middle" font-family="Inter"
          font-size="9" fill="#64748b">Stakeholder alignment</text>
  </g>

  <!-- VSM → Feasibility (merge back to spine) -->
  <path d="M765,868 V906 H530 V930" stroke="#10B981" stroke-width="1.5" fill="none"
        stroke-dasharray="5 3" marker-end="url(#ab)" opacity=".6"/>

  <!-- NO → direct to Feasibility -->
  <line x1="530" y1="764" x2="530" y2="930" stroke="#00D4FF" stroke-width="2"
        marker-end="url(#ab)"/>
  <circle r="5" fill="#00D4FF">
    <animateMotion dur="2s" repeatCount="indefinite" begin="1.1s"
      path="M530,764 V930"/>
    <animate attributeName="opacity" values="0;1;1;0" dur="2s" repeatCount="indefinite" begin="1.1s"/>
  </circle>

  <!-- ─── 4. FEASIBILITY STUDY (y=930) ─── -->
  <g filter="url(#fb)">
    <rect x="380" y="930" width="300" height="68" rx="13"
          fill="#061824" stroke="#00D4FF" stroke-width="1.8"/>
    <text x="530" y="956" text-anchor="middle" font-family="Space Grotesk"
          font-size="13" font-weight="700" fill="#00D4FF">📋 Feasibility Study</text>
    <text x="530" y="973" text-anchor="middle" font-family="Inter"
          font-size="9.5" fill="#64748b">ROI · Risk · Effort estimation</text>
    <text x="530" y="988" text-anchor="middle" font-family="Inter"
          font-size="9.5" fill="#64748b">Automation category · Tech approach</text>
  </g>

  <!-- Arrow → Prioritization Matrix -->
  <line x1="530" y1="998" x2="530" y2="1036" stroke="#00D4FF" stroke-width="2"
        marker-end="url(#ab)"/>
  <circle r="5" fill="#00D4FF">
    <animateMotion dur="1.3s" repeatCount="indefinite" begin="1.4s"
      path="M530,998 V1036"/>
    <animate attributeName="opacity" values="0;1;1;0" dur="1.3s" repeatCount="indefinite" begin="1.4s"/>
  </circle>

  <!-- ─── 5. PRIORITIZATION MATRIX (y=1036) ─── -->
  <g>
    <rect x="370" y="1036" width="320" height="64" rx="13"
          fill="#0f0a1e" stroke="#8B5CF6" stroke-width="1.8"/>
    <text x="530" y="1060" text-anchor="middle" font-family="Space Grotesk"
          font-size="13" font-weight="700" fill="#a78bfa">📌 Prioritization Matrix</text>
    <!-- Three priority chips -->
    <rect x="393" y="1073" width="72" height="18" rx="9"
          fill="rgba(239,68,68,.15)" stroke="rgba(239,68,68,.4)" stroke-width="1"/>
    <text x="429" y="1086" text-anchor="middle" font-family="Space Grotesk"
          font-size="9" font-weight="700" fill="#ef4444">HIGH</text>
    <rect x="481" y="1073" width="72" height="18" rx="9"
          fill="rgba(251,146,60,.12)" stroke="rgba(251,146,60,.4)" stroke-width="1"/>
    <text x="517" y="1086" text-anchor="middle" font-family="Space Grotesk"
          font-size="9" font-weight="700" fill="#fb923c">MEDIUM</text>
    <rect x="569" y="1073" width="72" height="18" rx="9"
          fill="rgba(100,116,139,.15)" stroke="rgba(100,116,139,.4)" stroke-width="1"/>
    <text x="605" y="1086" text-anchor="middle" font-family="Space Grotesk"
          font-size="9" font-weight="700" fill="#94a3b8">LOW</text>
  </g>

  <!-- Arrow → Management Approval -->
  <line x1="530" y1="1100" x2="530" y2="1136" stroke="#8B5CF6" stroke-width="2"
        marker-end="url(#av)"/>
  <circle r="5" fill="#8B5CF6">
    <animateMotion dur="1.2s" repeatCount="indefinite" begin="1.7s"
      path="M530,1100 V1136"/>
    <animate attributeName="opacity" values="0;1;1;0" dur="1.2s" repeatCount="indefinite" begin="1.7s"/>
  </circle>

  <!-- ─── DECISION 3: Management Approval / PL-SPL Sign-off (cx=530, cy=1186) ─── -->
  <g filter="url(#fv)" style="animation:vPulse 3.5s ease-in-out infinite 1.5s;">
    <polygon points="530,1136 628,1186 530,1236 432,1186"
             fill="#0e0b20" stroke="#8B5CF6" stroke-width="2"/>
    <text x="530" y="1180" text-anchor="middle" font-family="Space Grotesk"
          font-size="11" font-weight="700" fill="#e2e8f0">Management</text>
    <text x="530" y="1196" text-anchor="middle" font-family="Space Grotesk"
          font-size="10" fill="#8B5CF6">Approval</text>
    <text x="544" y="1228" font-family="Space Grotesk" font-size="9.5"
          font-weight="700" fill="#10B981">YES</text>
    <text x="344" y="1190" font-family="Space Grotesk" font-size="9.5"
          font-weight="700" fill="#ef4444">NO</text>
  </g>

  <!-- NO → Reject/Park 2 (left) -->
  <path d="M432,1186 H240 V1236" stroke="#ef4444" stroke-width="1.6" fill="none"
        stroke-dasharray="5 3" marker-end="url(#ar)" opacity=".75"/>
  <g filter="url(#fr)">
    <rect x="160" y="1236" width="160" height="50" rx="10"
          fill="#200a0a" stroke="#ef4444" stroke-width="1.6"/>
    <text x="240" y="1258" text-anchor="middle" font-family="Space Grotesk"
          font-size="12" font-weight="700" fill="#ef4444">✕ Reject / Park</text>
    <text x="240" y="1275" text-anchor="middle" font-family="Inter"
          font-size="9" fill="#64748b">Idea not approved</text>
  </g>
  <text x="318" y="1178" font-family="Space Grotesk" font-size="9.5"
        fill="#ef4444" font-weight="700">NO</text>

  <!-- YES → Requirement Gathering -->
  <line x1="530" y1="1236" x2="530" y2="1272" stroke="#00D4FF" stroke-width="2"
        marker-end="url(#ab)"/>
  <circle r="5" fill="#00D4FF">
    <animateMotion dur="1.2s" repeatCount="indefinite" begin="2s"
      path="M530,1236 V1272"/>
    <animate attributeName="opacity" values="0;1;1;0" dur="1.2s" repeatCount="indefinite" begin="2s"/>
  </circle>

  <!-- ─── 6. REQUIREMENT GATHERING (y=1272) ─── -->
  <g filter="url(#fb)">
    <rect x="378" y="1272" width="304" height="64" rx="13"
          fill="#061824" stroke="#00D4FF" stroke-width="1.8"/>
    <text x="530" y="1296" text-anchor="middle" font-family="Space Grotesk"
          font-size="13" font-weight="700" fill="#00D4FF">📝 Requirement Gathering</text>
    <text x="530" y="1313" text-anchor="middle" font-family="Inter"
          font-size="9.5" fill="#64748b">User stories · Acceptance criteria</text>
    <text x="530" y="1327" text-anchor="middle" font-family="Inter"
          font-size="9.5" fill="#64748b">Scope · Integration points · Dependencies</text>
  </g>

  <!-- ================================================================ -->
  <!-- ================================================================ -->
  <!-- AGILE SPRINT BLOCK — circular loop style (Image 2 reference)   -->
  <!-- Horizontal arrow bar: Initiation → UAT                          -->
  <!-- Circular arrows above: Develop and test ↔ Plan and design       -->
  <!-- ================================================================ -->

  <!-- Arrow: Req Gathering → Sprint block -->
  <line x1="530" y1="1336" x2="530" y2="1370" stroke="#8B5CF6" stroke-width="2"
        marker-end="url(#av)"/>
  <circle r="4.5" fill="#8B5CF6">
    <animateMotion dur="1.1s" repeatCount="indefinite" path="M530,1336 V1370"/>
    <animate attributeName="opacity" values="0;1;1;0" dur="1.1s" repeatCount="indefinite"/>
  </circle>

  <!-- Sprint outer container -->
  <rect x="220" y="1370" width="620" height="450" rx="18"
        fill="rgba(139,92,246,.035)" stroke="rgba(139,92,246,.18)" stroke-width="1.5"/>
  <text x="530" y="1394" text-anchor="middle" font-family="Space Grotesk"
        font-size="9" font-weight="700" fill="rgba(139,92,246,.5)"
        letter-spacing="3">2-WEEK SPRINT CYCLE</text>

  <!-- ── CIRCULAR LOOP (centre cx=530, cy=1570, r=130) ─────────────── -->
  <!-- Outer glow ring -->
  <circle cx="530" cy="1570" r="132"
          fill="none" stroke="rgba(139,92,246,.1)" stroke-width="20"/>

  <!-- Clockwise arc: top-right quadrant — "Plan and design" side (blue) -->
  <path d="M 530 1438 A 132 132 0 0 1 662 1570"
        fill="none" stroke="#00D4FF" stroke-width="14"
        stroke-linecap="round" opacity=".85">
    <animate attributeName="stroke-opacity" values=".6;1;.6" dur="2.4s" repeatCount="indefinite"/>
  </path>
  <!-- Clockwise arc: bottom-right → bottom-left quadrant — "Develop and test" side (violet) -->
  <path d="M 662 1570 A 132 132 0 1 1 530 1438"
        fill="none" stroke="#8B5CF6" stroke-width="14"
        stroke-linecap="round" opacity=".85">
    <animate attributeName="stroke-opacity" values=".6;1;.6" dur="2.4s" repeatCount="indefinite" begin="1.2s"/>
  </path>

  <!-- Arrow heads on the circle (clockwise direction) -->
  <!-- Top arrowhead (Plan side → going clockwise, pointing right-down) -->
  <polygon points="530,1437 519,1456 541,1456"
           fill="#00D4FF" transform="rotate(30,530,1445)"/>
  <!-- Bottom arrowhead (Develop side → going clockwise, pointing left-up) -->
  <polygon points="530,1703 519,1684 541,1684"
           fill="#8B5CF6" transform="rotate(30,530,1695)"/>

  <!-- Animated dot travelling clockwise around ring -->
  <circle r="7" fill="#00D4FF" filter="url(#fb)" opacity=".9">
    <animateMotion dur="5s" repeatCount="indefinite"
      path="M530,1438 A132,132 0 1,1 529.9,1438"/>
  </circle>
  <circle r="5" fill="#8B5CF6" filter="url(#fv)" opacity=".8">
    <animateMotion dur="5s" repeatCount="indefinite" begin="-2.5s"
      path="M530,1438 A132,132 0 1,1 529.9,1438"/>
  </circle>

  <!-- Centre circle (white inner, like Image 2) -->
  <circle cx="530" cy="1570" r="68" fill="#0a0f1e" stroke="rgba(255,255,255,.06)" stroke-width="1.5"/>

  <!-- ── LABELS around the circle ──────────────────────────────────── -->
  <!-- "Develop and test" — LEFT side -->
  <text x="328" y="1558" text-anchor="middle" font-family="Space Grotesk"
        font-size="13" font-weight="700" fill="#a78bfa">Develop</text>
  <text x="328" y="1576" text-anchor="middle" font-family="Space Grotesk"
        font-size="13" font-weight="700" fill="#a78bfa">and test</text>

  <!-- "Plan and design" — RIGHT side -->
  <text x="732" y="1558" text-anchor="middle" font-family="Space Grotesk"
        font-size="13" font-weight="700" fill="#00D4FF">Plan and</text>
  <text x="732" y="1576" text-anchor="middle" font-family="Space Grotesk"
        font-size="13" font-weight="700" fill="#00D4FF">design</text>

  <!-- Centre label: Sprint phases stacked -->
  <text x="530" y="1556" text-anchor="middle" font-family="Space Grotesk"
        font-size="10" font-weight="700" fill="#e2e8f0">Sprint</text>
  <text x="530" y="1572" text-anchor="middle" font-family="Space Grotesk"
        font-size="10" font-weight="700" fill="#e2e8f0">Planning</text>
  <text x="530" y="1592" text-anchor="middle" font-family="Inter"
        font-size="8.5" fill="#475569">· SIT Testing ·</text>

  <!-- ── HORIZONTAL ARROW BAR: Initiation → UAT (like Image 2) ────── -->
  <!-- Background arrow shape -->
  <defs>
    <linearGradient id="bargrad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="rgba(0,212,255,.35)"/>
      <stop offset="55%" stop-color="rgba(0,212,255,.25)"/>
      <stop offset="100%" stop-color="rgba(16,185,129,.45)"/>
    </linearGradient>
  </defs>
  <!-- Arrow bar body -->
  <path d="M 270 1730 H 760 L 790 1762 L 760 1794 H 270 Z"
        fill="url(#bargrad)" stroke="rgba(0,212,255,.3)" stroke-width="1.2"/>
  <!-- Arrow shimmer -->
  <path d="M 270 1730 H 760 L 790 1762 L 760 1794 H 270 Z"
        fill="url(#bargrad)" opacity=".4">
    <animate attributeName="opacity" values=".2;.5;.2" dur="2.5s" repeatCount="indefinite"/>
  </path>

  <!-- Divider line between Initiation and UAT (at 55% width) -->
  <line x1="554" y1="1732" x2="554" y2="1792" stroke="rgba(255,255,255,.18)" stroke-width="1.2"/>

  <!-- Initiation label (left section) -->
  <text x="412" y="1768" text-anchor="middle" font-family="Space Grotesk"
        font-size="13" font-weight="700" fill="#e2e8f0">Initiation</text>

  <!-- UAT label (right section) -->
  <text x="672" y="1768" text-anchor="middle" font-family="Space Grotesk"
        font-size="13" font-weight="700" fill="#10B981">UAT</text>

  <!-- "Two Weeks Sprint" label below bar -->
  <text x="530" y="1814" text-anchor="middle" font-family="Space Grotesk"
        font-size="11" fill="rgba(139,92,246,.65)" font-weight="600"
        letter-spacing="1">Two Weeks Sprint</text>

  <!-- Animated dots moving along the bar (left to right) -->
  <circle r="5" fill="#00D4FF" opacity=".8">
    <animateMotion dur="2.2s" repeatCount="indefinite"
      path="M270,1762 H785"/>
    <animate attributeName="opacity" values="0;1;1;0" dur="2.2s" repeatCount="indefinite"/>
  </circle>
  <circle r="4" fill="#10B981" opacity=".7">
    <animateMotion dur="2.2s" repeatCount="indefinite" begin="-1.1s"
      path="M270,1762 H785"/>
    <animate attributeName="opacity" values="0;.8;.8;0" dur="2.2s" repeatCount="indefinite" begin="-1.1s"/>
  </circle>

  <!-- FAIL branch: from left end of bar → Rework Sprint -->
  <path d="M 270 1762 H 160 V 1850" stroke="#ef4444" stroke-width="1.5" fill="none"
        stroke-dasharray="5 3" marker-end="url(#ar)" opacity=".7"/>
  <text x="185" y="1755" font-family="Space Grotesk" font-size="9"
        fill="#ef4444" font-weight="700">FAIL</text>

  <!-- REWORK SPRINT -->
  <g filter="url(#fr)">
    <rect x="80" y="1850" width="160" height="54" rx="11"
          fill="#1a0a10" stroke="#ef4444" stroke-width="1.5"/>
    <text x="160" y="1873" text-anchor="middle" font-family="Space Grotesk"
          font-size="11.5" font-weight="700" fill="#f87171">🔁 Rework Sprint</text>
    <text x="160" y="1890" text-anchor="middle" font-family="Inter"
          font-size="9" fill="#64748b">Fix defects · Retest</text>
  </g>
  <!-- Rework loop back to circle top -->
  <path d="M 160 1850 V 1570 H 270"
        stroke="#ef4444" stroke-width="1.2" fill="none"
        stroke-dasharray="4 3" marker-end="url(#av)" opacity=".4"/>

  <!-- PASS: from right tip of bar → Go Live -->
  <text x="808" y="1756" font-family="Space Grotesk" font-size="9"
        fill="#10B981" font-weight="700">PASS</text>
  <path d="M 790 1762 H 830 V 1920 H 684"
        stroke="#10B981" stroke-width="2" fill="none"
        marker-end="url(#ag)"/>
  <circle r="5" fill="#10B981">
    <animateMotion dur="2.4s" repeatCount="indefinite" begin=".5s"
      path="M790,1762 H830 V1920 H684"/>
    <animate attributeName="opacity" values="0;1;1;0" dur="2.4s" repeatCount="indefinite" begin=".5s"/>
  </circle>


  <!-- 13. GO LIVE -->
  <g filter="url(#fg)">
    <rect x="376" y="1920" width="308" height="72" rx="14" fill="#061e14" stroke="#10B981" stroke-width="2"/>
    <rect x="376" y="1920" width="308" height="72" rx="14" fill="none" stroke="#10B981" stroke-width="1.2" opacity=".3">
      <animate attributeName="opacity" values=".1;.5;.1" dur="3s" repeatCount="indefinite"/>
    </rect>
    <text x="530" y="1952" text-anchor="middle" font-family="Space Grotesk" font-size="15" font-weight="800" fill="#10B981">🚀 Go Live</text>
    <text x="530" y="1970" text-anchor="middle" font-family="Inter" font-size="9.5" fill="#64748b">Solution deployed to production</text>
    <text x="530" y="1984" text-anchor="middle" font-family="Inter" font-size="9.5" fill="#64748b">Handover to operations · Stakeholder sign-off</text>
  </g>

  <!-- Arrow Go Live → Hypercare -->
  <line x1="530" y1="1992" x2="530" y2="2034" stroke="#00D4FF" stroke-width="2" marker-end="url(#ab)"/>
  <circle r="5" fill="#00D4FF">
    <animateMotion dur="1.4s" repeatCount="indefinite" begin=".4s" path="M530,1992 V2034"/>
    <animate attributeName="opacity" values="0;1;1;0" dur="1.4s" repeatCount="indefinite" begin=".4s"/>
  </circle>

  <!-- 14. HYPERCARE SUPPORT -->
  <g filter="url(#fb)">
    <rect x="376" y="2034" width="308" height="72" rx="14" fill="#051825" stroke="#00D4FF" stroke-width="1.8"/>
    <text x="530" y="2064" text-anchor="middle" font-family="Space Grotesk" font-size="14" font-weight="700" fill="#00D4FF">🛡️ Hypercare Support</text>
    <text x="530" y="2082" text-anchor="middle" font-family="Inter" font-size="9.5" fill="#64748b">Post-go-live monitoring · Hotfixes · User support</text>
    <text x="530" y="2096" text-anchor="middle" font-family="Inter" font-size="9.5" fill="#64748b">Incident resolution · Stabilisation period</text>
  </g>

  <!-- Arrow Hypercare → Benefits Tracking -->
  <line x1="530" y1="2106" x2="530" y2="2148" stroke="#00D4FF" stroke-width="2" marker-end="url(#ab)"/>
  <circle r="5" fill="#00D4FF">
    <animateMotion dur="1.4s" repeatCount="indefinite" begin=".8s" path="M530,2106 V2148"/>
    <animate attributeName="opacity" values="0;1;1;0" dur="1.4s" repeatCount="indefinite" begin=".8s"/>
  </circle>

  <!-- 15. BENEFITS TRACKING -->
  <g filter="url(#fb)">
    <rect x="348" y="2148" width="364" height="88" rx="14" fill="#051825" stroke="#00D4FF" stroke-width="1.8"/>
    <text x="530" y="2178" text-anchor="middle" font-family="Space Grotesk" font-size="14" font-weight="700" fill="#00D4FF">📈 Benefits Tracking</text>
    <text x="530" y="2196" text-anchor="middle" font-family="Inter" font-size="9.5" fill="#64748b">KPI measurement · Hours saved · Cost saved</text>
    <text x="530" y="2210" text-anchor="middle" font-family="Inter" font-size="9.5" fill="#64748b">ROI validation · Business value report</text>
    <rect x="366" y="2218" width="58" height="17" rx="8" fill="rgba(0,212,255,.12)" stroke="rgba(0,212,255,.35)" stroke-width="1"/>
    <text x="395" y="2230" text-anchor="middle" font-family="Space Grotesk" font-size="8.5" font-weight="700" fill="#00D4FF">KPI</text>
    <rect x="436" y="2218" width="82" height="17" rx="8" fill="rgba(16,185,129,.1)" stroke="rgba(16,185,129,.35)" stroke-width="1"/>
    <text x="477" y="2230" text-anchor="middle" font-family="Space Grotesk" font-size="8.5" font-weight="700" fill="#10B981">HRS SAVED</text>
    <rect x="530" y="2218" width="84" height="17" rx="8" fill="rgba(251,146,60,.1)" stroke="rgba(251,146,60,.35)" stroke-width="1"/>
    <text x="572" y="2230" text-anchor="middle" font-family="Space Grotesk" font-size="8.5" font-weight="700" fill="#fb923c">COST SAVED</text>
    <rect x="626" y="2218" width="58" height="17" rx="8" fill="rgba(139,92,246,.1)" stroke="rgba(139,92,246,.35)" stroke-width="1"/>
    <text x="655" y="2230" text-anchor="middle" font-family="Space Grotesk" font-size="8.5" font-weight="700" fill="#a78bfa">ROI</text>
  </g>

  <!-- Arrow Benefits → Continuous Improvement -->
  <line x1="530" y1="2236" x2="530" y2="2278" stroke="#8B5CF6" stroke-width="2" marker-end="url(#av)"/>
  <circle r="5" fill="#8B5CF6">
    <animateMotion dur="1.4s" repeatCount="indefinite" begin="1.2s" path="M530,2236 V2278"/>
    <animate attributeName="opacity" values="0;1;1;0" dur="1.4s" repeatCount="indefinite" begin="1.2s"/>
  </circle>

  <!-- 16. CONTINUOUS IMPROVEMENT -->
  <g filter="url(#fv)">
    <rect x="348" y="2278" width="364" height="76" rx="14" fill="#0f0b22" stroke="#8B5CF6" stroke-width="2"/>
    <rect x="348" y="2278" width="364" height="76" rx="14" fill="none" stroke="#8B5CF6" stroke-width="1.2" opacity=".25">
      <animate attributeName="opacity" values=".1;.4;.1" dur="3.5s" repeatCount="indefinite"/>
    </rect>
    <text x="530" y="2310" text-anchor="middle" font-family="Space Grotesk" font-size="14" font-weight="700" fill="#a78bfa">🔄 Continuous Improvement</text>
    <text x="530" y="2328" text-anchor="middle" font-family="Inter" font-size="9.5" fill="#64748b">Review learnings · Identify next opportunities</text>
    <text x="530" y="2342" text-anchor="middle" font-family="Inter" font-size="9.5" fill="#64748b">Enhancement requests · Process optimisation</text>
  </g>

  <!-- LOOP BACK: CI → Turbo Drive (left-side arc) -->
  <defs>
    <linearGradient id="loopg" x1="0%" y1="100%" x2="0%" y2="0%">
      <stop offset="0%" stop-color="#8B5CF6"/>
      <stop offset="50%" stop-color="#00D4FF"/>
      <stop offset="100%" stop-color="#10B981"/>
    </linearGradient>
  </defs>
  <path d="M 420 2354 H 100 V 148 H 290"
        stroke="url(#loopg)" stroke-width="2.2" fill="none"
        stroke-dasharray="8 5" marker-end="url(#ao)" opacity=".6"/>
  <!-- Loop label rotated along the left rail -->
  <text x="88" y="1280" font-family="Space Grotesk" font-size="10"
        fill="rgba(139,92,246,.55)" font-weight="700" letter-spacing="2"
        transform="rotate(-90,88,1280)">NEW IDEA LOOP ↑</text>
  <!-- Animated dot travelling the loop -->
  <circle r="5.5" fill="#8B5CF6" opacity=".75">
    <animateMotion dur="7s" repeatCount="indefinite"
      path="M420,2354 H100 V148 H290"/>
    <animate attributeName="opacity" values="0;.75;.75;0" dur="7s" repeatCount="indefinite"/>
  </circle>

  </svg><!-- end SVG -->
  </div><!-- end svg-wrap -->


  <div class="foot">
    <span>© 2025 ALTEN Engineering Services · EFS Automation Team · EFS Turbo Drive</span>
    <span style="background:rgba(255,255,255,.03);padding:4px 12px;border-radius:20px;
          border:1px solid rgba(255,255,255,.05);">Sensitivity: C1-Internal</span>
  </div>
</div>

<script>
/* Particle canvas */
(function(){
  var c=document.getElementById('bg'),ctx=c.getContext('2d'),W,H,pts=[];
  function resize(){W=c.width=window.innerWidth;H=c.height=window.innerHeight;}
  resize(); window.addEventListener('resize',resize);
  for(var i=0;i<80;i++) pts.push({
    x:Math.random()*1920,y:Math.random()*1080,
    vx:(Math.random()-.5)*.22,vy:(Math.random()-.5)*.22,
    r:Math.random()*1.5+.3,
    col:Math.random()>.5?'0,212,255':'139,92,246'
  });
  function draw(){
    ctx.clearRect(0,0,W,H);
    pts.forEach(function(p){
      p.x+=p.vx; p.y+=p.vy;
      if(p.x<0)p.x=W; if(p.x>W)p.x=0;
      if(p.y<0)p.y=H; if(p.y>H)p.y=0;
      ctx.beginPath();
      ctx.arc(p.x,p.y,p.r,0,Math.PI*2);
      ctx.fillStyle='rgba('+p.col+',.5)';
      ctx.shadowColor='rgba('+p.col+',.35)';
      ctx.shadowBlur=5;
      ctx.fill();
    });
    for(var i=0;i<pts.length;i++)
      for(var j=i+1;j<pts.length;j++){
        var dx=pts[i].x-pts[j].x,dy=pts[i].y-pts[j].y,d=Math.sqrt(dx*dx+dy*dy);
        if(d<110){
          ctx.beginPath();
          ctx.moveTo(pts[i].x,pts[i].y);
          ctx.lineTo(pts[j].x,pts[j].y);
          ctx.strokeStyle='rgba(0,212,255,'+(1-d/110)*.07+')';
          ctx.lineWidth=.4; ctx.shadowBlur=0;
          ctx.stroke();
        }
      }
    requestAnimationFrame(draw);
  }
  draw();
})();
</script>
</body>
</html>
    """
    st.components.v1.html(_workflow_html, height=2800, scrolling=True)
    render_copyright()

# ══════════════════════════════════════════════════════════════════════════════
#  PAGE: ADMIN
# ══════════════════════════════════════════════════════════════════════════════
def page_admin():
    page_header("Admin Panel ⚙️")
    users = get_users()
    tab1,tab2,tab3 = st.tabs(["👥 Users","➕ Add User","🔑 Password Reset"])

    with tab1:
        st.markdown(f"**{len(users)} registered users**")
        for u in users:
            with st.expander(f"📧 {u['email']}  —  {u['role']}"):
                col1,col2,col3 = st.columns([2,2,1])
                with col1:
                    new_role = st.selectbox("Role", ROLES_LIST,
                                            index=ROLES_LIST.index(u["role"]) if u["role"] in ROLES_LIST else 0,
                                            key=f"role_{u['email']}")
                with col2:
                    if st.button("💾 Update Role", key=f"upd_{u['email']}"):
                        update_role(u["email"], new_role); st.success("Role updated.")
                with col3:
                    if st.button("🗑 Delete", key=f"del_{u['email']}"):
                        delete_user(u["email"]); st.warning("User deleted.")

    with tab2:
        with st.form("add_user_form", clear_on_submit=True):
            new_email = st.text_input("Email")
            new_role  = st.selectbox("Role", ROLES_LIST)
            if st.form_submit_button("➕ Add User"):
                if new_email:
                    add_user(new_email.strip().lower(), new_role)
                    st.success(f"Added {new_email} as {new_role} (default pw: {DEFAULT_PW})")
                    st.rerun()

    with tab3:
        st.caption(f"Reset any user's password back to default: **{DEFAULT_PW}**")
        emails = [u["email"] for u in users]
        with st.form("reset_pw_form"):
            target = st.selectbox("User", emails)
            col1,col2 = st.columns(2)
            with col1: new_pw = st.text_input("Set New Password (optional)", type="password")
            with col2:
                do_reset = st.form_submit_button(f"Reset to '{DEFAULT_PW}'")
                do_set   = st.form_submit_button("Set Specific Password")
            if do_reset:
                reset_password(target); st.success(f"Password reset to '{DEFAULT_PW}' for {target}")
            elif do_set:
                if not new_pw or len(new_pw)<4: st.error("Minimum 4 characters.")
                else: set_password(target, new_pw); st.success(f"Password updated for {target}")
    render_copyright()

# ══════════════════════════════════════════════════════════════════════════════
#  MAIN ROUTER
# ══════════════════════════════════════════════════════════════════════════════
def main():
    st.set_page_config(page_title="Turbo Drive", page_icon="", layout="wide",
                       initial_sidebar_state="expanded")
    init_db()
    if "theme" not in st.session_state:
        st.session_state["theme"] = "ALTEN Red & Blue"
    apply_theme(ss("theme"))

    override = ss("_page_override")
    if override == "register":    page_register(); return
    if override == "change_password": page_change_password(); return
    if not logged_in():           page_login(); return

    # ── Session timeout check (every rerun = activity signal) ─────────────
    touch_activity_flag = True  # any page render past this point counts as activity
    enforce_session_timeout()
    touch_activity()

    t = THEMES.get(ss("theme"), THEMES["ALTEN Red & Blue"])
    with st.sidebar:
        st.markdown(f"""
        <div style="text-align:center;padding:10px 0 6px;">
          <img src="{ALTEN_LOGO_URL}" style="height:36px;object-fit:contain;margin-bottom:4px;" alt="ALTEN"/><br>
          <span style="font-size:clamp(14px,1.5vw,20px);font-weight:900;
               background:linear-gradient(135deg,{t['primary']},{t['secondary']});
               -webkit-background-clip:text;background-clip:text;color:transparent;">
             TURBO DRIVE
          </span>
          <div style="color:#94a3b8;font-size:10px;margin-top:2px;">Automation Workflow</div>
        </div>""", unsafe_allow_html=True)
        st.divider()

        pages = user_pages()
        icons = {"Dashboard":"📊","Submit Idea":"💡","PL Assignment":"🧑‍💼",
                 "Feasibility":"🔍","Approval":"✅","Admin":"⚙️","OTP List":"🆔","Workflow":"🔀"}
        nav = st.radio("Navigation",
                       [f"{icons.get(p,'')} {p}" for p in pages],
                       label_visibility="collapsed")
        current_page = nav.split(" ",1)[1].strip() if nav else pages[0]

        st.divider()
        st.markdown(f"""
        <div style="color:#94a3b8;font-size:12px;">
          👤 <b style="color:#e2e8f0;">{ss('name','')}</b><br>
          <span style="font-size:11px;">{ss('role','')}</span>
        </div>""", unsafe_allow_html=True)

        # Style buttons to use constant black background and white text
        st.markdown("""
        <style>
        div.stButton > button {background-color:#000 !important; color:#fff !important; border: none !important; border-radius:6px !important; padding:6px 10px !important}
        div.stButton > button:hover {opacity:0.95}
        </style>
        """, unsafe_allow_html=True)

        col1,col2 = st.columns(2)
        with col1:
            if st.button("🔑 PW", help="Change Password"):
                st.session_state["_page_override"] = "change_password"; st.rerun()
        with col2:
            if st.button("🚪 Logout"):
                for k in ["email","role","name","theme"]: st.session_state.pop(k,None)
                st.rerun()

        st.divider()
        st.markdown("**🎨 Theme**")
        chosen = st.selectbox("", list(THEMES.keys()),
                              index=list(THEMES.keys()).index(ss("theme","ALTEN Red & Blue")),
                              label_visibility="collapsed", key="theme_sel")
        if chosen != ss("theme"):
            st.session_state["theme"] = chosen; st.rerun()

        st.divider()
        render_session_countdown()

        st.markdown("---")
        st.markdown(
            f'<p style="font-size:10px;color:#64748b;">Queries?<br>'
            f'<a href="mailto:{SUPPORT_EMAIL}" style="color:#00AEEF;">{SUPPORT_NAME}</a></p>',
            unsafe_allow_html=True
        )

    if   current_page == "Dashboard":     page_dashboard()
    elif current_page == "Submit Idea":   page_submit()
    elif current_page == "PL Assignment": page_pl_assignment()
    elif current_page == "Feasibility":   page_feasibility()
    elif current_page == "Approval":      page_approval()
    elif current_page == "OTP List":      page_otp_list()
    elif current_page == "Workflow":      page_workflow()
    elif current_page == "Admin":         page_admin()

if __name__ == "__main__":
    main()