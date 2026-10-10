"""
Thendralla Fincorp — Vehicle Loan Manager v3
Changes v3:
  - Vertical sidebar navigation (mobile-first, hamburger toggle)
  - Vehicle Details all fields mandatory
  - Customer Address mandatory + GPS location field (press button or manual)
  - Alerts grouped by Loan Number (one row per loan with cumulative overdue amount)
  - EMI Schedule: overdue rows highlighted RED, upcoming (≤10d) rows highlighted YELLOW
  - Pay from Alerts redirects directly to the correct EMI row (anchor #emi_<id>)
  - Dashboard: Chart.js bar chart (monthly collections) + doughnut (loan status breakdown)
  - All previous features preserved
"""

import os, math, sqlite3, hashlib, secrets, calendar, smtplib, base64, io, zipfile, csv, re, tempfile, html, json, time
import requests as http_req
from urllib.parse import quote as urlquote
from datetime import date, datetime, timezone, timedelta
from functools import wraps
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import (Flask, request, redirect, url_for, session,
                   flash, send_file, jsonify, g, get_flashed_messages, Response)
from werkzeug.utils import secure_filename

try:
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                    Table, TableStyle, PageBreak)
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════════════════════
DB_FILE       = os.environ.get("DB_FILE", "vehicle_loans.db")
UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "uploads")
ALLOWED_EXT   = {"pdf","png","jpg","jpeg","doc","docx","xls","xlsx"}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(fn):
    return "." in fn and fn.rsplit(".",1)[1].lower() in ALLOWED_EXT
TURSO_URL     = os.environ.get("TURSO_URL", "")
TURSO_TOKEN   = os.environ.get("TURSO_TOKEN", "")
UPCOMING_DAYS = 10
# Public link to the hosted user_guide.html (e.g. GitHub Pages). Defaults to the copy
# served locally by this app; once uploaded to GitHub, set the USER_GUIDE_URL
# environment variable to that public link — no code change needed either way.
USER_GUIDE_URL = os.environ.get("USER_GUIDE_URL", "/user-guide")

EMAIL_CONFIG = {
    "smtp_host": os.environ.get("SMTP_HOST", "smtp.gmail.com"),
    "smtp_port": int(os.environ.get("SMTP_PORT", 587)),
    "sender":    os.environ.get("SMTP_SENDER", ""),
    "password":  os.environ.get("SMTP_PASSWORD", ""),
    "enabled":   os.environ.get("SMTP_ENABLED", "false").lower() == "true",
}

# ── SMS CONFIG (Fast2SMS — India) ──────────────────────────────────────────────
# Get free API key from https://www.fast2sms.com → Dashboard → Dev API
# Set environment variable FAST2SMS_KEY=your_api_key on Render
# ── Paste your Fast2SMS API key between the quotes below ──────────────────────
FAST2SMS_HARDCODED_KEY = ""   # <-- paste key here if not using environment variable

def _get_sms_key():
    """Returns API key from environment or hardcoded fallback."""
    k = os.environ.get("FAST2SMS_KEY", "").strip()
    if not k:
        k = FAST2SMS_HARDCODED_KEY.strip()
    return k

def _sms_enabled():
    k = _get_sms_key()
    return bool(k and k != "YOUR_ACTUAL_KEY_HERE")

SMS_CONFIG = {
    "sender_id": "TFCORP",
}

ROLES = {
    "superadmin":{"label":"Super Admin","can_approve":True,"can_reject":True,"can_add":True,"can_pay":True,"can_report":True,"can_edit":True,"can_db":True,"can_ack":True},
    "admin":    {"label":"Admin",    "can_approve":True,  "can_reject":True,  "can_add":True,  "can_pay":True,  "can_report":True, "can_edit":False,"can_db":False,"can_ack":True},
    "manager":  {"label":"Manager",  "can_approve":False, "can_reject":False, "can_add":True,  "can_pay":True,  "can_report":True, "can_edit":False,"can_db":False,"can_ack":False},
    "fieldpia": {"label":"Fieldpia", "can_approve":False, "can_reject":False, "can_add":True,  "can_pay":True,  "can_report":False,"can_edit":False,"can_db":False,"can_ack":False},
    # Account Manager: second-level cross-check only (acknowledges payments and follow-ups); no decision-making approvals
    "assocmgr": {"label":"Account Manager","can_approve":False,"can_reject":False,"can_add":False,"can_pay":False,"can_report":True,"can_edit":False,"can_db":False,"can_ack":True},
    "viewer":   {"label":"Viewer",   "can_approve":False, "can_reject":False, "can_add":False, "can_pay":False, "can_report":True, "can_edit":False,"can_db":False,"can_ack":False},
}
ACK_ROLES = ("superadmin", "admin", "assocmgr")     # may acknowledge
DIRECT_ROLES = ("superadmin", "admin")              # their own entries need no extra acknowledgement
BILLING_ROLES = ("superadmin", "admin", "manager", "fieldpia")   # may make bills (all EMI payments go through billing)

DEFAULT_USERS = {
    "superadmin":{"role":"superadmin","pw_hash": hashlib.sha256(b"superadmin123").hexdigest()},
    "admin":    {"role":"admin",    "pw_hash": hashlib.sha256(b"admin123").hexdigest()},
    "manager":  {"role":"manager",  "pw_hash": hashlib.sha256(b"manager123").hexdigest()},
    "fieldpia": {"role":"fieldpia", "pw_hash": hashlib.sha256(b"field123").hexdigest()},
    "viewer":   {"role":"viewer",   "pw_hash": hashlib.sha256(b"viewer123").hexdigest()},
}

# ══════════════════════════════════════════════════════════════════════════════
#  LOGO
# ══════════════════════════════════════════════════════════════════════════════
def _load_logo_b64():
    base = os.path.dirname(os.path.abspath(__file__))
    for p in [os.path.join(base, "logo.png"),
              os.path.join(base, "logo.jpg"),
              "/mnt/user-data/uploads/1780681889493_image.png"]:
        if os.path.exists(p):
            with open(p, "rb") as f:
                return base64.b64encode(f.read()).decode()
    return ""

LOGO_B64 = _load_logo_b64()

# ══════════════════════════════════════════════════════════════════════════════
#  UTILITIES
# ══════════════════════════════════════════════════════════════════════════════
def hash_pw(pw): return hashlib.sha256(pw.encode()).hexdigest()

def parse_date(s):
    try: return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except: return date.today()

def fmt_date(v, empty="—"):
    """Display format for dates: DD/MM/YYYY. Stored values stay YYYY-MM-DD."""
    if not v: return empty
    if isinstance(v, (date, datetime)): return v.strftime("%d/%m/%Y")
    try: return datetime.strptime(str(v)[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError: return str(v)

def vehicle_label(row):
    """Vehicle as people say it: the name (Splendor, Activa) with its model/year; falls back to the
    vehicle type (e.g. 'Two Wheeler') only when no name was recorded."""
    name = (row.get("vehicle_name") or "").strip()
    model = (row.get("vehicle_model") or "").strip()
    main = name or (row.get("vehicle_type") or "").strip()
    if not main: return "—"
    if model and model.lower() not in main.lower(): return f"{main} ({model})"
    return main

def vehicle_tag(v):
    """Vehicle label with its registration number, e.g. 'Activa (6G 2023) · TN01AB1234'."""
    num = (v.get("vehicle_number") or "").strip()
    return f"{vehicle_label(v)} · {num}" if num else vehicle_label(v)

def _xv_cache(attr, table):
    """loan_id -> [rows] for an add-on table, loaded once per request."""
    try: cached = getattr(g, attr, None)
    except RuntimeError: cached = None
    if cached is None:
        c = get_cur(); c.execute(f"SELECT * FROM {table} ORDER BY loan_id, seq, 1")
        cached = {}
        for r in c.fetchall():
            r = dict(r); cached.setdefault(r["loan_id"], []).append(r)
        try: setattr(g, attr, cached)
        except RuntimeError: pass
    return cached

def extra_vehicles_of(row):
    return _xv_cache("_xv_vehicles", "LoanVehicles").get(row.get("loan_id") or row.get("id"), [])

def extra_guarantors_of(row):
    return _xv_cache("_xv_guarantors", "LoanGuarantors").get(row.get("loan_id") or row.get("id"), [])

def vehicle_label_more(row):
    """Plain-text vehicle label; adds '+N more' when the loan has add-on vehicles."""
    n = len(extra_vehicles_of(row))
    return vehicle_label(row) + (f" +{n} more" if n else "")

def vehicle_html(row):
    tip = " · ".join(x for x in [(row.get("vehicle_type") or "").strip(), (row.get("vehicle_number") or "").strip(),
                                 (row.get("vehicle_colour") or "").strip()] if x)
    extras = [] if row.get("_vehicle_scoped") else extra_vehicles_of(row)
    if extras:
        tip += "".join("\n+ " + vehicle_tag(x) for x in extras)
    more = (f' <span class="badge badge-partial" style="font-size:10.5px;padding:1px 6px;">+{len(extras)} more</span>'
            if extras else "")
    return f'<span title="{html.escape(tip)}">{html.escape(vehicle_label(row))}{more}</span>'

def loan_vehicles(loan):
    """Every vehicle on a loan: the main one (vehicle_id None) first, then the add-on vehicles."""
    keys = ("vehicle_type", "vehicle_number", "vehicle_name", "vehicle_model", "engine_number", "chassis_number",
            "vehicle_colour", "key_received", "key_received_date", "rc_received", "rc_received_date",
            "docs_received", "docs_received_date", "key_collected_by", "rc_collected_by", "docs_collected_by",
            "other_owner", "tc_received", "tc_received_date", "tc_collected_by", "police_fine", "key_na_remark")
    main = {"vehicle_id": None, "seq": 1, **{k: loan.get(k) for k in keys}}
    c = get_cur(); c.execute("SELECT * FROM LoanVehicles WHERE loan_id=? ORDER BY seq, vehicle_id", (loan["id"],))
    return [main] + [dict(r) for r in c.fetchall()]

def add_months(d, m):
    month = d.month - 1 + m
    year  = d.year + month // 12
    month = month % 12 + 1
    day   = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)

def normalize_interest(rate):
    try:
        r = float(rate)
        return r / 100.0 if r > 1 else r
    except: return None

def compute_emi_amount(amt, rate_dec, tenure):
    total = amt + (amt * rate_dec * (tenure / 12.0))
    return round(total / tenure, 2)

def compute_total_due(amt, rate_dec, tenure):
    return round(amt + (amt * rate_dec * (tenure / 12.0)), 2)

def plan_emi_schedule(amt, rate_dec, tenure, custom_emi=None):
    """Build the EMI schedule plan: a list of installment amounts.

    Normally returns `tenure` equal installments (computed EMI).
    If `custom_emi` (a rounded EMI amount) is provided, the schedule uses
    that amount for `tenure` installments and adjusts the difference
    between (custom_emi * tenure) and the actual total due:
      - If money is LEFT OVER (custom_emi*tenure < total_due): an extra
        final installment (#tenure+1) is added for the leftover amount.
      - If custom_emi*tenure OVERSHOOTS total_due: the last installment
        is reduced so the total still equals total_due exactly.

    Returns: (amounts: list[float], total_due: float, computed_emi: float, leftover: float)
    """
    total_due = compute_total_due(amt, rate_dec, tenure)
    computed_emi = compute_emi_amount(amt, rate_dec, tenure)

    if custom_emi is None or float(custom_emi) <= 0:
        amounts = [computed_emi] * tenure
        # tiny rounding residue on the last installment so sum == total_due exactly
        residue = round(total_due - sum(amounts), 2)
        if abs(residue) >= 0.01:
            amounts[-1] = round(amounts[-1] + residue, 2)
        return amounts, total_due, computed_emi, 0.0

    custom_emi = round(float(custom_emi), 2)
    amounts = [custom_emi] * tenure
    leftover = round(total_due - (custom_emi * tenure), 2)

    if leftover > 0.005:
        # extra final installment for the remaining balance
        amounts.append(leftover)
    elif leftover < -0.005:
        # custom EMI overshoots — trim the last installment
        amounts[-1] = round(amounts[-1] + leftover, 2)
        if amounts[-1] <= 0:
            # if trimming would zero/negate the last EMI, drop it and
            # spread the remainder back across the prior installment
            removed = amounts.pop()
            if amounts:
                amounts[-1] = round(amounts[-1] + removed, 2)

    return amounts, total_due, computed_emi, leftover


def fmt_inr(v):
    try: return f"₹{float(v):,.2f}"
    except: return "₹0.00"

MAX_EXTRA_NUMBERS = 2   # numbers allowed in addition to the primary one, per person

def parse_extra_numbers_form(f, prefix, label):
    """Reads <prefix>_extra_mobile_N / <prefix>_extra_name_N from a form into a JSON string
    of [{"number","name"}]. Raises ValueError on a bad number or a missing 'belongs to' name."""
    out = []
    for i in range(1, MAX_EXTRA_NUMBERS + 1):
        num  = re.sub(r"\D", "", f.get(f"{prefix}_extra_mobile_{i}", "") or "")
        name = (f.get(f"{prefix}_extra_name_{i}", "") or "").strip()
        if not num and not name: continue
        if len(num) != 10:
            raise ValueError(f"{label} additional number {i} must be exactly 10 digits.")
        if not name:
            raise ValueError(f"{label} additional number {i}: please enter whom the number belongs to.")
        out.append({"number": num, "name": name})
    return json.dumps(out) if out else ""

def _extra_numbers(raw):
    try: data = json.loads(raw) if raw else []
    except (ValueError, TypeError): return []
    return [x for x in data if isinstance(x, dict) and x.get("number")]

def contact_numbers_html(primary, primary_title, extras_raw, owner_word):
    """Click-to-call numbers; hovering a number shows whose it is."""
    def one(num, title):
        return (f'<a href="tel:{html.escape(num)}" title="{html.escape(title)}" '
                f'style="color:inherit;text-decoration:none;border-bottom:1px dotted var(--muted);'
                f'cursor:help;white-space:nowrap;">{html.escape(num)}</a>')
    parts = []
    if (primary or "").strip():
        parts.append(one(primary.strip(), primary_title))
    for x in _extra_numbers(extras_raw):
        parts.append(one(x["number"], f"{x.get('name','')} ({owner_word}'s other number)"))
    return "<br>".join(parts) if parts else "—"

def contact_numbers_text(extras_raw):
    return "; ".join(f"{x['number']} ({x.get('name','')})" for x in _extra_numbers(extras_raw))

_EXTRA_NUMBERS_JS = """<script>
if(!window.addExtraNumber){window.addExtraNumber=function(prefix,btn){
  var rows=document.querySelectorAll('[data-extra-row="'+prefix+'"]'),left=0,shown=false;
  for(var i=0;i<rows.length;i++){
    if(!shown && rows[i].style.display==='none'){rows[i].style.display='flex';shown=true;}
    else if(rows[i].style.display==='none'){left++;}
  }
  if(left===0) btn.style.display='none';
};}
</script>"""

def extra_numbers_block(prefix, raw=None):
    """Form block: up to MAX_EXTRA_NUMBERS additional mobile numbers, each with a 'belongs to' name."""
    existing = _extra_numbers(raw)
    rows = ""
    for i in range(1, MAX_EXTRA_NUMBERS + 1):
        x = existing[i-1] if i <= len(existing) else {}
        rows += (f'<div data-extra-row="{prefix}" style="display:{"flex" if x else "none"};gap:8px;flex-wrap:wrap;margin-bottom:8px;">'
                 f'<input name="{prefix}_extra_mobile_{i}" value="{html.escape(x.get("number",""))}" maxlength="10" '
                 f'placeholder="Mobile number" style="flex:1;min-width:150px;" '
                 f'oninput="this.value=this.value.replace(/[^0-9]/g,\'\').slice(0,10)">'
                 f'<input name="{prefix}_extra_name_{i}" value="{html.escape(x.get("name",""))}" '
                 f'placeholder="Belongs to (e.g. Wife)" style="flex:1;min-width:150px;"></div>')
    hide_btn = "display:none;" if len(existing) >= MAX_EXTRA_NUMBERS else ""
    return (f'<div class="form-group full"><label>Additional Numbers '
            f'<span style="font-size:10px;color:var(--muted);">(optional — up to {MAX_EXTRA_NUMBERS} more; '
            f'type whose number it is)</span></label>{rows}'
            f'<button type="button" class="btn btn-sm btn-amber" style="{hide_btn}width:fit-content;" '
            f'onclick="addExtraNumber(\'{prefix}\',this)">➕ Add another number</button></div>' + _EXTRA_NUMBERS_JS)

# ── Add-on vehicles / guarantors: several of each under one loan number ─────────
VEHICLE_TYPES = ["Two Wheeler", "Three Wheeler", "Four Wheeler", "Commercial Vehicle", "Other"]

_EXTRA_BLOCKS_JS = """<script>
if(!window.addExtraBlock){
window.renumberExtra=function(){
  ['vehicle','guarantor'].forEach(function(k){
    var n=2;
    document.querySelectorAll('.xblock[data-kind="'+k+'"]').forEach(function(b){
      if(b.style.display==='none') return;
      b.querySelector('.xn').textContent=n++;
    });
  });
};
window.addExtraBlock=function(kind){
  var t=document.getElementById('tpl_'+kind), host=document.getElementById('xhost_'+kind);
  host.appendChild(t.content.firstElementChild.cloneNode(true));
  renumberExtra();
  if(window.toggleReloan && document.querySelector('[name=is_reloan]')) toggleReloan(window.reloanValue ? reloanValue() : document.querySelector('[name=is_reloan]').value);
  host.lastElementChild.scrollIntoView({behavior:'smooth',block:'center'});
};
window.toggleHO=function(sel){
  var g=sel.closest('.form-group'), na=g.querySelector('.ho-na');
  if(na){ var ni=na.querySelector('input'); na.style.display=sel.value==='na'?'block':'none';
    if(sel.value==='na') ni.setAttribute('required','required'); else ni.removeAttribute('required'); }
  var d=g.querySelector('.ho-detail'); if(!d) return;
  var yes=sel.value==='yes', by=d.querySelector('input:not([type=date])'), dt=d.querySelector('input[type=date]');
  d.style.display=yes?'flex':'none';
  if(yes){ by.setAttribute('required','required');
    if(!dt.value){ var ld=document.getElementById('loan_date'); dt.value=(ld&&ld.value)?ld.value:new Date().toISOString().slice(0,10); } }
  else { by.removeAttribute('required'); }
};
window.toggleOwner=function(sel){
  var g=sel.closest('.form-group'), w=g.querySelector('.tc-wrap'); if(!w) return;
  var tc=w.querySelector('select'), yes=sel.value==='yes';
  w.style.display=yes?'block':'none';
  if(yes){ tc.setAttribute('required','required'); }
  else { tc.removeAttribute('required'); tc.value=''; window.toggleHO(tc); }
};
window.syncGAddr=function(cb){
  var host=cb.closest('.gaddr'), cur=host.querySelector('.g-cur'), perm=host.querySelector('.g-perm');
  if(cb.checked){ perm.value=cur.value; perm.readOnly=true; perm.style.background='var(--surface2)'; }
  else { perm.readOnly=false; perm.style.background=''; }
};
document.addEventListener('input',function(e){
  var t=e.target; if(!t||!t.classList) return;
  if(t.classList.contains('g-cur')){ var cb=t.closest('.gaddr').querySelector('.g-same'); if(cb&&cb.checked) syncGAddr(cb); }
  if(t.name==='guarantor_name'||t.name==='guarantor_mobile'){
    var any=false; ['guarantor_name','guarantor_mobile'].forEach(function(n){var el=document.querySelector('[name='+n+']'); if(el&&el.value.trim()) any=true;});
    document.querySelectorAll('#gMainAddr .g-cur, #gMainAddr .g-perm').forEach(function(el){ if(any) el.setAttribute('required','required'); else el.removeAttribute('required'); });
  }
});
window.removeExtraBlock=function(btn){
  var b=btn.closest('.xblock');
  if(b.dataset.existing==='1'){
    b.querySelector('[name$="_del[]"]').value='1';
    b.querySelectorAll('[required]').forEach(function(e){e.removeAttribute('required');});
    b.style.display='none';
  } else { b.remove(); }
  renumberExtra();
};
window.xgGPS=function(btn){
  var inp=btn.parentElement.querySelector('input');
  if(!navigator.geolocation){alert('GPS is not supported on this browser.');return;}
  var old=btn.textContent; btn.disabled=true; btn.textContent='Getting...';
  navigator.geolocation.getCurrentPosition(function(p){
    inp.value=p.coords.latitude.toFixed(6)+','+p.coords.longitude.toFixed(6); btn.textContent=old; btn.disabled=false;
  },function(e){alert('GPS: '+e.message+' - enter it manually.'); btn.textContent=old; btn.disabled=false;},
  {enableHighAccuracy:true,timeout:15000,maximumAge:0});
};
}
</script>"""

def _xblock_shell(kind, n, existing, inner, extra_note=""):
    title = "Vehicle" if kind == "vehicle" else "Guarantor"
    icon = "🚗" if kind == "vehicle" else "🛡️"
    return (f'<div class="xblock" data-kind="{kind}" data-existing="{1 if existing else 0}" '
            f'style="grid-column:1/-1;border:1px dashed var(--accent);border-radius:10px;padding:12px;margin:4px 0;background:var(--surface2);">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">'
            f'<b style="color:var(--accent);">{icon} {title} <span class="xn">{n}</span> '
            f'<span style="font-size:11px;color:var(--muted);font-weight:500;">(add-on){extra_note}</span></b>'
            f'<button type="button" class="btn btn-sm btn-danger" onclick="removeExtraBlock(this)">✖ Remove</button></div>'
            f'<div class="form-grid">{inner}</div></div>')

def handover_detail_html(date_name, by_name, extra=""):
    """'When collected' + 'Collected by' inputs, shown only when the answer is Yes (toggleHO)."""
    return (f'<div class="ho-detail" style="display:none;gap:6px;flex-wrap:wrap;margin-top:6px;">'
            f'<input type="date" name="{date_name}" max="{date.today().isoformat()}" title="When collected" style="flex:1;min-width:130px;">'
            f'<input name="{by_name}" placeholder="Collected by *" style="flex:1;min-width:130px;">{extra}</div>')

def na_remark_html(name):
    """'Why not required?' box, shown only when the answer is Not required (toggleHO)."""
    return (f'<div class="ho-na" style="display:none;margin-top:6px;">'
            f'<input name="{name}" placeholder="Why not required? (remark) *" style="width:100%;"></div>')

def ho_group(question, short, extra="", not_required=False, attrs=' required onchange="toggleHO(this)"'):
    """One 'collected? Yes/No' question with its date / collected-by boxes (loan-level documents)."""
    return (f'<div class="form-group"><label>{question} * <span style="font-size:10px;color:var(--muted);">(No = follow-up in {handover_days(short)} days)</span></label>'
            f'<select name="{short}_received"{attrs}><option value="">-- Select --</option>'
            f'<option value="yes">Yes</option><option value="no">No</option>'
            + ('<option value="na">Not required</option>' if not_required else '') + '</select>'
            f'{handover_detail_html(short + "_date", short + "_by", extra)}'
            + (na_remark_html(short + "_na_remark") if not_required else '') + '</div>')

def vehicle_docs_html(owner_name, tc_name, tcd_name, tcb_name, fine_name, fine_value=0, with_owner=True):
    """Police fine (+ 'vehicle in another owner's name?' and the transfer certificate) for one vehicle."""
    limit = police_fine_limit()
    out = (f'<div class="form-group"><label>Police fine amount (₹) * <span style="font-size:10px;color:var(--muted);">(not more than {fmt_inr(limit)}; 0 = none)</span></label>'
           f'<input type="number" name="{fine_name}" value="{fine_value or 0}" min="0" max="{limit:g}" step="0.01" required></div>')
    if with_owner:
        out += (f'<div class="form-group"><label>Vehicle in another owner\'s name? *</label>'
                f'<select name="{owner_name}" required onchange="toggleOwner(this)"><option value="">-- Select --</option>'
                f'<option value="yes">Yes</option><option value="no">No</option></select>'
                f'<div class="tc-wrap" style="display:none;margin-top:8px;"><label>Transfer certificate collected? * '
                f'<span style="font-size:10px;color:var(--muted);">(No = follow-up in {handover_days("tc")} days)</span></label>'
                f'<select name="{tc_name}" onchange="toggleHO(this)"><option value="">-- Select --</option>'
                f'<option value="yes">Yes</option><option value="no">No</option></select>'
                f'{handover_detail_html(tcd_name, tcb_name)}</div></div>')
    return out

def _yn_select(name, required=True, not_required=False):
    return (f'<select name="{name}" onchange="toggleHO(this)"{" required" if required else ""}><option value="">-- Select --</option>'
            f'<option value="yes">Yes</option><option value="no">No</option>'
            + ('<option value="na">Not required</option>' if not_required else '') + '</select>')

def extra_vehicle_block(v=None, n=2):
    """One add-on vehicle. New blocks ask the key / RC / proof questions; saved ones show their status instead."""
    v = v or {}
    existing = bool(v.get("vehicle_id"))
    val = lambda k: html.escape(str(v.get(k) or ""))
    types = "".join(f'<option value="{t}"{" selected" if v.get("vehicle_type") == t else ""}>{t}</option>' for t in VEHICLE_TYPES)
    def fld(label, name, key, extra=""):
        return (f'<div class="form-group"><label>{label}</label>'
                f'<input name="{name}" value="{val(key)}" class="vehicle-req-field" {extra}></div>')
    inner = (f'<input type="hidden" name="xv_id[]" value="{v.get("vehicle_id") or ""}">'
             f'<input type="hidden" name="xv_del[]" value="0">'
             f'<div class="form-group"><label>Vehicle Type</label><select name="xv_type[]" class="vehicle-req-field">'
             f'<option value="">-- Select --</option>{types}</select></div>'
             + fld("Vehicle Number", "xv_number[]", "vehicle_number", "placeholder=\"TN01AB1234\" oninput=\"this.value=this.value.replace(/ /g,'').toUpperCase()\"")
             + fld("Vehicle Name", "xv_name[]", "vehicle_name", "placeholder=\"e.g. Honda Activa\"")
             + fld("Vehicle Model", "xv_model[]", "vehicle_model", "placeholder=\"e.g. 6G 2023\"")
             + fld("Engine Number", "xv_engine[]", "engine_number")
             + fld("Chassis Number", "xv_chassis[]", "chassis_number")
             + fld("Vehicle Colour", "xv_colour[]", "vehicle_colour"))
    inner += vehicle_docs_html("xv_owner[]", "xv_tc[]", "xv_tc_date[]", "xv_tc_by[]", "xv_fine[]", v.get("police_fine") or 0, with_owner=not existing)
    if existing:
        inner += ('<input type="hidden" name="xv_owner[]" value=""><input type="hidden" name="xv_tc[]" value="">'
                  '<input type="hidden" name="xv_tc_date[]" value=""><input type="hidden" name="xv_tc_by[]" value="">'
                  '<input type="hidden" name="xv_key[]" value=""><input type="hidden" name="xv_rc[]" value="">'
                  '<input type="hidden" name="xv_docs[]" value=""><input type="hidden" name="xv_key_na[]" value="">'
                  + "".join(f'<input type="hidden" name="xv_{k}_{w}[]" value="">' for k in ("key", "rc", "docs") for w in ("date", "by")) +
                  ''
                  f'<div class="form-group full" style="font-size:12.5px;">{handover_status_html(v)}</div>')
    else:
        def ho(question, short):
            na = short == "key"
            return (f'<div class="form-group"><label>{question} * <span style="font-size:10px;color:var(--muted);">(No = follow-up in {handover_days(short)} days)</span></label>'
                    f'{_yn_select("xv_" + short + "[]", not_required=na)}{handover_detail_html("xv_" + short + "_date[]", "xv_" + short + "_by[]")}'
                    + (na_remark_html("xv_key_na[]") if na else '') + '</div>')
        inner += ho("Key Received?", "key") + ho("RC Received?", "rc") + ho("Proof &amp; Documents Collected?", "docs")
    return _xblock_shell("vehicle", n, existing, inner)

def extra_guarantor_block(g_=None, n=2):
    g_ = g_ or {}
    existing = bool(g_.get("guarantor_id"))
    val = lambda k: html.escape(str(g_.get(k) or ""))
    inner = (f'<input type="hidden" name="xg_id[]" value="{g_.get("guarantor_id") or ""}">'
             f'<input type="hidden" name="xg_del[]" value="0">'
             f'<div class="form-group"><label>Guarantor Name *</label><input name="xg_name[]" value="{val("name")}"></div>'
             f'<div class="form-group"><label>Guarantor Mobile</label><input name="xg_mobile[]" value="{val("mobile")}" maxlength="10" '
             f'oninput="this.value=this.value.replace(/[^0-9]/g,\'\').slice(0,10)"></div>'
             f'<div class="form-group full gaddr"><label>Current Address * <span style="font-size:10px;color:var(--muted);">(mandatory)</span></label>'
             f'<textarea name="xg_address[]" class="g-cur" rows="2">{val("address")}</textarea>'
             f'<label style="display:flex;align-items:center;gap:8px;font-weight:600;text-transform:none;font-size:13px;margin:6px 0 4px;cursor:pointer;">'
             f'<input type="checkbox" class="g-same" style="width:auto;min-height:0;margin:0;" onchange="syncGAddr(this)"> Permanent address same as current address</label>'
             f'<label>Permanent Address * <span style="font-size:10px;color:var(--muted);">(mandatory)</span></label>'
             f'<textarea name="xg_perm[]" class="g-perm" rows="2">{val("permanent_address")}</textarea></div>'
             f'<div class="form-group full"><label>📍 GPS Location</label><div style="display:flex;gap:8px;flex-wrap:wrap;">'
             f'<input name="xg_location[]" value="{val("location")}" placeholder="e.g. 10.9876,78.1234 or area name" style="flex:1;min-width:160px;">'
             f'<button type="button" class="btn btn-sm btn-amber" onclick="xgGPS(this)">📡 Get GPS</button></div></div>')
    return _xblock_shell("guarantor", n, existing, inner)

def extra_blocks_section(loan_id=None):
    """The '+ Add another vehicle / guarantor' areas (hosts, templates and buttons) for the loan / edit forms."""
    vs, gs = [], []
    if loan_id:
        c = get_cur()
        c.execute("SELECT * FROM LoanVehicles WHERE loan_id=? ORDER BY seq, vehicle_id", (loan_id,)); vs = [dict(r) for r in c.fetchall()]
        c.execute("SELECT * FROM LoanGuarantors WHERE loan_id=? ORDER BY seq, guarantor_id", (loan_id,)); gs = [dict(r) for r in c.fetchall()]
    def area(kind, items, builder, btn_text, blank):
        hosted = "".join(builder(it, i + 2) for i, it in enumerate(items))
        return (f'<div id="xhost_{kind}" class="form-group full" style="gap:6px;">{hosted}</div>'
                f'<template id="tpl_{kind}">{builder(None, 2)}</template>'
                f'<div class="form-group full"><button type="button" class="btn btn-sm btn-amber" style="width:fit-content;" '
                f'onclick="addExtraBlock(\'{kind}\')">{btn_text}</button></div>')
    return (area("vehicle", vs, extra_vehicle_block, "➕ Add another vehicle", None),
            area("guarantor", gs, extra_guarantor_block, "➕ Add another guarantor", None))

def handover_status_html(v):
    """Key / RC / Proof status of one vehicle, e.g. '🔑 Key ✔ 12/09/2026 · 📄 RC ✖ pending · 🗂️ Proof ✔ 12/09/2026'."""
    out = []
    for icon, label, flag, dt, by in (("🔑", "Key", "key_received", "key_received_date", "key_collected_by"),
                                      ("📄", "RC", "rc_received", "rc_received_date", "rc_collected_by"),
                                      ("🗂️", "Proof", "docs_received", "docs_received_date", "docs_collected_by")):
        if v.get(flag) == "yes":
            who = f' by {html.escape(v[by])}' if v.get(by) else ""
            out.append(f'{icon} {label} <span style="color:var(--green);">✔ {fmt_date(v.get(dt), "received")}{who}</span>')
        elif v.get(flag) == "na":
            why = f' — {html.escape(v["key_na_remark"])}' if flag == "key_received" and v.get("key_na_remark") else ""
            out.append(f'{icon} {label} <b style="color:var(--muted);">Not required{why}</b>')
        else:
            out.append(f'{icon} {label} <span style="color:var(--red);">✖ pending</span>')
    return " · ".join(out)

def _form_list(form, key, i):
    lst = form.getlist(key)
    return (lst[i] if i < len(lst) else "").strip()

def parse_extra_vehicles(form, reloan):
    """Reads the add-on vehicle blocks. Vehicle details are mandatory only for a reloan (same rule as the main
    vehicle); every NEW block must answer the key / RC / proof questions."""
    out = []
    for i in range(len(form.getlist("xv_type[]"))):
        r = dict(id=_form_list(form, "xv_id[]", i), delete=_form_list(form, "xv_del[]", i) == "1",
                 vehicle_type=_form_list(form, "xv_type[]", i),
                 vehicle_number=_form_list(form, "xv_number[]", i).replace(" ", "").upper(),
                 vehicle_name=_form_list(form, "xv_name[]", i), vehicle_model=_form_list(form, "xv_model[]", i),
                 engine_number=_form_list(form, "xv_engine[]", i), chassis_number=_form_list(form, "xv_chassis[]", i),
                 vehicle_colour=_form_list(form, "xv_colour[]", i),
                 key=_form_list(form, "xv_key[]", i), rc=_form_list(form, "xv_rc[]", i), docs=_form_list(form, "xv_docs[]", i))
        if r["delete"] and r["id"]:
            out.append(r); continue
        label = f"Add-on vehicle {i + 2}"
        r["fine"] = parse_fine(_form_list(form, "xv_fine[]", i), f"{label}: Police fine")
        if reloan:
            for k, lab in (("vehicle_type", "Vehicle Type"), ("vehicle_number", "Vehicle Number"), ("vehicle_name", "Vehicle Name"),
                           ("vehicle_model", "Vehicle Model"), ("engine_number", "Engine Number"),
                           ("chassis_number", "Chassis Number"), ("vehicle_colour", "Vehicle Colour")):
                if not r[k]: raise ValueError(f"{label}: {lab} is mandatory for a reloan.")
        if not r["id"]:
            for k, lab in (("key", "Key Received"), ("rc", "RC Received"), ("docs", "Proof & Documents Collected")):
                if r[k] == "na" and k == "key":
                    r["key_na"] = _form_list(form, "xv_key_na[]", i)
                    if not r["key_na"]: raise ValueError(f"{label}: enter a remark for why the key is not required.")
                    continue
                if r[k] not in ("yes", "no"): raise ValueError(f"{label}: please answer '{lab}?' (Yes / No).")
                if r[k] == "yes":
                    r[k + "_date"], r[k + "_by"] = parse_collected(_form_list(form, f"xv_{k}_date[]", i), _form_list(form, f"xv_{k}_by[]", i),
                                                                   f"{label}: {lab}")
        if not r["id"]:
            r["owner"] = _form_list(form, "xv_owner[]", i); r["tc"] = ""; r["tc_date"] = ""; r["tc_by"] = ""
            if r["owner"] not in ("yes", "no"): raise ValueError(f"{label}: please answer 'Vehicle in another owner's name?' (Yes / No).")
            if r["owner"] == "yes":
                r["tc"] = _form_list(form, "xv_tc[]", i)
                if r["tc"] not in ("yes", "no"): raise ValueError(f"{label}: please answer 'Transfer certificate collected?' (Yes / No).")
                if r["tc"] == "yes":
                    r["tc_date"], r["tc_by"] = parse_collected(_form_list(form, "xv_tc_date[]", i), _form_list(form, "xv_tc_by[]", i),
                                                               f"{label}: Transfer certificate")
        out.append(r)
    return out

def parse_extra_guarantors(form):
    out = []
    for i in range(len(form.getlist("xg_name[]"))):
        r = dict(id=_form_list(form, "xg_id[]", i), delete=_form_list(form, "xg_del[]", i) == "1",
                 name=_form_list(form, "xg_name[]", i), mobile=re.sub(r"\D", "", _form_list(form, "xg_mobile[]", i)),
                 address=_form_list(form, "xg_address[]", i), location=_form_list(form, "xg_location[]", i),
                 permanent=_form_list(form, "xg_perm[]", i))
        if r["delete"] and r["id"]:
            out.append(r); continue
        if not r["id"] and not (r["name"] or r["mobile"] or r["address"] or r["permanent"] or r["location"]):
            continue        # an empty block that was never filled in
        if not r["name"]: raise ValueError(f"Add-on guarantor {i + 2}: please enter the guarantor's name.")
        if r["mobile"] and len(r["mobile"]) != 10: raise ValueError(f"Add-on guarantor {i + 2}: mobile number must be exactly 10 digits.")
        if not r["id"]:     # new add-on guarantors need both addresses
            if not r["address"]: raise ValueError(f"Add-on guarantor {i + 2}: Current Address is mandatory.")
            if not r["permanent"]: raise ValueError(f"Add-on guarantor {i + 2}: Permanent Address is mandatory (or tick 'same as current').")
        out.append(r)
    return out

def customer_numbers_html(row):
    name = row.get("customer_name") or row.get("name") or "Customer"
    return contact_numbers_html(row.get("customer_mobile"), f"Primary — {name} (Customer)",
                                row.get("customer_extra_numbers"), "Customer")

def guarantor_numbers_html(row, include_addon=True):
    name = row.get("guarantor_name") or "Guarantor"
    base = contact_numbers_html(row.get("guarantor_mobile"), f"Primary — {name} (Guarantor)",
                                row.get("guarantor_extra_numbers"), "Guarantor")
    parts = [] if base == "—" else [base]
    if include_addon:
        for gx in extra_guarantors_of(row):
            if (gx.get("mobile") or "").strip():
                parts.append(contact_numbers_html(gx["mobile"], f"{gx.get('name') or 'Guarantor'} (Guarantor {gx['seq']})", None, "Guarantor"))
    return "<br>".join(parts) if parts else "—"

def next_loan_number():
    START_SEQ = 2201   # T0101–T2200 already used; new loans start from T2201
    c = get_cur()
    c.execute("SELECT loan_number FROM LoanEntry WHERE loan_number LIKE 'T%' ORDER BY loan_number DESC LIMIT 1")
    row = c.fetchone()
    seq = START_SEQ
    if row:
        try:
            existing = int(str(row["loan_number"]).lstrip("T"))
            if existing >= START_SEQ:
                seq = existing + 1
        except: pass
    return f"T{seq:04d}"

def assess_reloan_risk(loan_number):
    c = get_cur()
    c.execute("SELECT id FROM LoanEntry WHERE loan_number=?", (loan_number,))
    row = c.fetchone()
    if not row: return None, "Loan number not found."
    lid = row["id"] if isinstance(row, dict) else row[0]
    c.execute("""SELECT COUNT(*) as total,
                        SUM(CASE WHEN status='Paid' THEN 1 ELSE 0 END) as paid,
                        SUM(CASE WHEN status='Overdue' OR (status IN ('Pending','Partial') AND due_date < ?) THEN 1 ELSE 0 END) as overdue
                 FROM EMI WHERE loan_id=?""", (date.today().isoformat(), lid))
    st = c.fetchone()
    total = (st["total"] or 0) if isinstance(st, dict) else (st[0] or 0)
    paid  = (st["paid"]  or 0) if isinstance(st, dict) else (st[1] or 0)
    delay = (st["overdue"] or 0) if isinstance(st, dict) else (st[2] or 0)
    c.execute("SELECT * FROM LoanEntry WHERE id=?", (lid,))
    old = c.fetchone()
    if delay == 0:   risk, dec = "GOOD",    "✅ Good payment history — safe to sanction."
    elif delay <= 3: risk, dec = "AVERAGE", "⚠️ Average history — proceed with caution."
    else:            risk, dec = "RISK",    f"❌ High risk — {delay} delayed payments. Review carefully."
    return {"risk":risk,"decision":dec,"delay_count":delay,"total":total,"paid":paid,
            "customer": dict(old) if old else {}}, None

# ══════════════════════════════════════════════════════════════════════════════
#  TURSO HTTP CLIENT
# ══════════════════════════════════════════════════════════════════════════════
def _tv(v):
    if v is None: return {"type":"null","value":None}
    if isinstance(v,bool): return {"type":"integer","value":str(int(v))}
    if isinstance(v,int):  return {"type":"integer","value":str(v)}
    if isinstance(v,float):return {"type":"float","value":v}
    return {"type":"text","value":str(v)}

def _fv(cell):
    if cell is None or cell.get("type")=="null": return None
    t,v = cell.get("type","text"), cell.get("value")
    if t=="integer":
        try: return int(v)
        except: return v
    if t=="float":
        try: return float(v)
        except: return v
    return v

class TRow(dict):
    def __getitem__(self,k):
        if isinstance(k,int): return list(self.values())[k]
        return super().__getitem__(k)

# Shared HTTP session — reuses TCP/TLS connections to Turso (big latency win)
_TURSO_SESSION = http_req.Session()

class TCur:
    def __init__(self,url,tok):
        self._u,self._t,self._rows,self._pos,self.lastrowid=url,tok,[],0,None
    def _exec(self,sql,p=()):
        stmt={"sql":sql.strip()}
        if p: stmt["args"]=[_tv(x) for x in p]
        r=_TURSO_SESSION.post(f"{self._u}/v2/pipeline",
            headers={"Authorization":f"Bearer {self._t}","Content-Type":"application/json"},
            json={"requests":[{"type":"execute","stmt":stmt},{"type":"close"}]},timeout=15)
        r.raise_for_status()
        d=r.json();res=d["results"][0]
        if res.get("type")=="error": raise Exception(res["error"]["message"])
        result=res["response"]["result"]
        cols=[c["name"] for c in result.get("cols",[])]
        self._rows=[TRow(zip(cols,[_fv(cell) for cell in row])) for row in result.get("rows",[])]
        self._pos=0
        rid=result.get("last_insert_rowid")
        if rid is not None:
            try: self.lastrowid=int(rid)
            except: self.lastrowid=rid
    def execute(self,sql,p=()):
        self._exec(sql,p); return self
    def executescript(self,script):
        for s in script.split(";"):
            s=s.strip()
            if s: self._exec(s)
        return self
    def fetchone(self):
        if self._pos<len(self._rows): r=self._rows[self._pos];self._pos+=1;return r
        return None
    def fetchall(self):
        r=self._rows[self._pos:];self._pos=len(self._rows);return r
    def __iter__(self): return iter(self._rows)
    def batch(self, statements):
        """Run multiple (sql, params) pairs in ONE HTTP round-trip.
        Returns a list of result-row-lists, one per statement (errors -> []).
        Massively reduces latency vs N separate calls when using Turso."""
        reqs = []
        for sql, p in statements:
            stmt = {"sql": sql.strip()}
            if p: stmt["args"] = [_tv(x) for x in p]
            reqs.append({"type":"execute","stmt":stmt})
        reqs.append({"type":"close"})
        r = _TURSO_SESSION.post(f"{self._u}/v2/pipeline",
            headers={"Authorization":f"Bearer {self._t}","Content-Type":"application/json"},
            json={"requests":reqs}, timeout=20)
        r.raise_for_status()
        d = r.json()
        out = []
        for res in d["results"][:-1]:  # last is the "close" ack
            if res.get("type") == "error":
                out.append([]); continue
            result = res["response"]["result"]
            cols = [c["name"] for c in result.get("cols", [])]
            out.append([TRow(zip(cols,[_fv(cell) for cell in row])) for row in result.get("rows", [])])
        return out

class TConn:
    def __init__(self,url,tok):
        self._u=url.replace("libsql://","https://");self._t=tok;self.row_factory=None
    def cursor(self): return TCur(self._u,self._t)
    def commit(self): pass
    def close(self): pass

# ══════════════════════════════════════════════════════════════════════════════
#  DATABASE
# ══════════════════════════════════════════════════════════════════════════════
def _make_conn():
    if TURSO_URL and TURSO_TOKEN: return TConn(TURSO_URL, TURSO_TOKEN)
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def get_db():
    if "db" not in g: g.db = _make_conn()
    return g.db

def get_cur(): return get_db().cursor()

def batch_query(statements):
    """Run a list of (sql, params) pairs in ONE round-trip when possible (Turso),
    or sequentially for local SQLite. Returns a list of fetchall()-style row lists."""
    c = get_cur()
    if hasattr(c, "batch"):
        return c.batch(statements)
    out = []
    for sql, p in statements:
        c.execute(sql, p)
        out.append([dict(r) for r in c.fetchall()])
    return out

def init_db():
    conn = _make_conn(); cur = conn.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS LoanEntry (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        loan_number TEXT UNIQUE,
        customer_name TEXT,
        customer_mobile TEXT,
        customer_address TEXT,
        customer_permanent_address TEXT,
        customer_location TEXT,
        vehicle_type TEXT,
        vehicle_number TEXT,
        vehicle_model TEXT,
        engine_number TEXT,
        chassis_number TEXT,
        vehicle_colour TEXT,
        loan_amount REAL,
        interest_rate REAL,
        tenure INTEGER,
        start_date TEXT,
        loan_date TEXT,
        status TEXT,
        created_at TEXT,
        attachment TEXT,
        aadhar_number TEXT,
        customer_email TEXT,
        guarantor_name TEXT,
        guarantor_address TEXT,
        guarantor_location TEXT,
        guarantor_mobile TEXT,
        is_reloan INTEGER DEFAULT 0,
        reloan_ref TEXT,
        remarks TEXT,
        custom_emi_amount REAL
    );
    CREATE TABLE IF NOT EXISTS Customers (
        customer_id INTEGER PRIMARY KEY AUTOINCREMENT,
        loan_id INTEGER UNIQUE,
        name TEXT, vehicle_type TEXT,
        loan_amount REAL, emi_amount REAL, status TEXT, created_at TEXT,
        FOREIGN KEY(loan_id) REFERENCES LoanEntry(id)
    );
    CREATE TABLE IF NOT EXISTS EMI (
        emi_id INTEGER PRIMARY KEY AUTOINCREMENT,
        loan_id INTEGER,
        installment_no INTEGER,
        due_date TEXT,
        emi_amount REAL,
        status TEXT,
        paid_at TEXT,
        amount_paid REAL,
        remaining_amount REAL,
        extra_interest REAL,
        bill_number TEXT,
        FOREIGN KEY(loan_id) REFERENCES LoanEntry(id)
    );
    CREATE TABLE IF NOT EXISTS EMIPayments (
        payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
        emi_id INTEGER,
        loan_id INTEGER,
        amount REAL,
        extra_interest REAL,
        bill_number TEXT,
        paid_at TEXT,
        paid_by TEXT,
        FOREIGN KEY(emi_id) REFERENCES EMI(emi_id),
        FOREIGN KEY(loan_id) REFERENCES LoanEntry(id)
    );
    CREATE TABLE IF NOT EXISTS LoanClosure (
        closure_id INTEGER PRIMARY KEY AUTOINCREMENT,
        loan_id INTEGER,
        kind TEXT,
        status TEXT,
        created_at TEXT,
        approved_by TEXT,
        approved_at TEXT,
        ack_requested_by TEXT,
        ack_requested_at TEXT,
        ack_note TEXT,
        acked_by TEXT,
        acked_at TEXT,
        closed_at TEXT,
        FOREIGN KEY(loan_id) REFERENCES LoanEntry(id)
    );
    CREATE TABLE IF NOT EXISTS ClosureItems (
        item_id INTEGER PRIMARY KEY AUTOINCREMENT,
        closure_id INTEGER,
        loan_id INTEGER,
        item TEXT,
        status TEXT,
        returned_on TEXT,
        handed_by TEXT,
        note TEXT,
        recorded_by TEXT,
        recorded_at TEXT,
        FOREIGN KEY(closure_id) REFERENCES LoanClosure(closure_id)
    );
    CREATE TABLE IF NOT EXISTS Settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    CREATE TABLE IF NOT EXISTS Seizures (
        seizure_id INTEGER PRIMARY KEY AUTOINCREMENT,
        loan_id INTEGER,
        status TEXT,
        reason TEXT,
        seized_date TEXT,
        place TEXT,
        writeoff_reason TEXT,
        written_off REAL,
        overdue_count INTEGER,
        snapshot TEXT,
        requested_by TEXT,
        requested_at TEXT,
        approved_by TEXT,
        approved_at TEXT,
        decision_remarks TEXT,
        ack_requested_by TEXT,
        ack_requested_at TEXT,
        ack_note TEXT,
        acked_by TEXT,
        acked_at TEXT,
        details_checked INTEGER,
        legal_checked INTEGER,
        legal_note TEXT,
        closed_at TEXT,
        reopened_by TEXT,
        reopened_at TEXT,
        reopen_reason TEXT,
        FOREIGN KEY(loan_id) REFERENCES LoanEntry(id)
    );
    CREATE TABLE IF NOT EXISTS SeizureItems (
        item_id INTEGER PRIMARY KEY AUTOINCREMENT,
        seizure_id INTEGER,
        loan_id INTEGER,
        vehicle_id INTEGER,
        item TEXT,
        status TEXT,
        received_on TEXT,
        received_by TEXT,
        note TEXT,
        recorded_by TEXT,
        recorded_at TEXT,
        FOREIGN KEY(seizure_id) REFERENCES Seizures(seizure_id)
    );
    CREATE TABLE IF NOT EXISTS LoanVehicles (
        vehicle_id INTEGER PRIMARY KEY AUTOINCREMENT,
        loan_id INTEGER,
        seq INTEGER,
        vehicle_type TEXT,
        vehicle_number TEXT,
        vehicle_name TEXT,
        vehicle_model TEXT,
        engine_number TEXT,
        chassis_number TEXT,
        vehicle_colour TEXT,
        key_received TEXT, key_received_date TEXT,
        rc_received TEXT, rc_received_date TEXT,
        docs_received TEXT, docs_received_date TEXT,
        FOREIGN KEY(loan_id) REFERENCES LoanEntry(id)
    );
    CREATE TABLE IF NOT EXISTS LoanGuarantors (
        guarantor_id INTEGER PRIMARY KEY AUTOINCREMENT,
        loan_id INTEGER,
        seq INTEGER,
        name TEXT, mobile TEXT, address TEXT, location TEXT,
        FOREIGN KEY(loan_id) REFERENCES LoanEntry(id)
    );
    CREATE TABLE IF NOT EXISTS PendingPayments (
        pp_id INTEGER PRIMARY KEY AUTOINCREMENT,
        emi_id INTEGER,
        loan_id INTEGER,
        installment_no INTEGER,
        amount REAL,
        bill_number TEXT,
        paid_on TEXT,
        penalty_rate REAL,
        receipt_id INTEGER,
        requested_by TEXT,
        requested_at TEXT,
        status TEXT,
        decided_by TEXT,
        decided_at TEXT,
        decision_remarks TEXT,
        FOREIGN KEY(emi_id) REFERENCES EMI(emi_id)
    );
    CREATE TABLE IF NOT EXISTS Penalties (
        penalty_id INTEGER PRIMARY KEY AUTOINCREMENT,
        loan_id INTEGER,
        emi_id INTEGER,
        installment_no INTEGER,
        days INTEGER,
        half_paid_date TEXT,
        requested_rate REAL,
        requested_amount REAL,
        requested_by TEXT,
        requested_at TEXT,
        status TEXT,
        final_rate REAL,
        final_amount REAL,
        decided_by TEXT,
        decided_at TEXT,
        decision_remarks TEXT,
        followup_id INTEGER,
        collected_at TEXT,
        FOREIGN KEY(emi_id) REFERENCES EMI(emi_id)
    );
    CREATE TABLE IF NOT EXISTS Receipts (
        receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
        receipt_no TEXT,
        loan_id INTEGER,
        emi_id INTEGER,
        installment_no INTEGER,
        installment_label TEXT,
        received_from TEXT,
        receipt_date TEXT,
        vehicle_number TEXT,
        loan_number TEXT,
        cash REAL,
        online REAL,
        total REAL,
        amount_words TEXT,
        cashier TEXT,
        recorded_on_emi INTEGER DEFAULT 0,
        created_at TEXT,
        FOREIGN KEY(loan_id) REFERENCES LoanEntry(id)
    );
    CREATE TABLE IF NOT EXISTS PreClosure (
        preclose_id INTEGER PRIMARY KEY AUTOINCREMENT,
        loan_id INTEGER,
        status TEXT,
        requested_by TEXT,
        requested_at TEXT,
        original_rate REAL,
        new_rate REAL,
        months_elapsed INTEGER,
        paid_before REAL,
        settlement_amount REAL,
        approved_by TEXT,
        approved_at TEXT,
        decision_remarks TEXT,
        bill_number TEXT,
        paid_on TEXT,
        closed_by TEXT,
        closed_at TEXT,
        FOREIGN KEY(loan_id) REFERENCES LoanEntry(id)
    );
    CREATE TABLE IF NOT EXISTS RejectedLoans (
        reject_id INTEGER PRIMARY KEY AUTOINCREMENT,
        loan_id INTEGER UNIQUE, reason TEXT, created_at TEXT,
        FOREIGN KEY(loan_id) REFERENCES LoanEntry(id)
    );
    CREATE TABLE IF NOT EXISTS ClosedLoans (
        close_id INTEGER PRIMARY KEY AUTOINCREMENT,
        loan_id INTEGER UNIQUE, closure_date TEXT, created_at TEXT,
        FOREIGN KEY(loan_id) REFERENCES LoanEntry(id)
    );
    CREATE TABLE IF NOT EXISTS Users (
        user_id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE, pw_hash TEXT, role TEXT, created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS FollowUp (
        followup_id INTEGER PRIMARY KEY AUTOINCREMENT,
        loan_id INTEGER,
        follow_up_date TEXT,
        remarks TEXT,
        status TEXT DEFAULT 'Pending',
        created_by TEXT,
        created_at TEXT,
        resolved_at TEXT,
        category TEXT DEFAULT 'Loans',
        item TEXT,
        FOREIGN KEY(loan_id) REFERENCES LoanEntry(id)
    )
    """)
    for m in [
        "ALTER TABLE LoanEntry ADD COLUMN customer_mobile TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN customer_address TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN customer_location TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN vehicle_number TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN vehicle_model TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN engine_number TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN chassis_number TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN vehicle_colour TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN guarantor_name TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN guarantor_address TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN guarantor_location TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN customer_permanent_address TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN field_visit TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN field_visit_date TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN field_visit_remark TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN field_visited_by TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN customer_extra_numbers TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN guarantor_extra_numbers TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN key_received TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN key_received_date TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN rc_received TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN rc_received_date TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN docs_received TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN docs_received_date TEXT",
        "ALTER TABLE FollowUp ADD COLUMN category TEXT DEFAULT 'Loans'",
        "ALTER TABLE FollowUp ADD COLUMN item TEXT",
        "ALTER TABLE FollowUp ADD COLUMN ref_id INTEGER",
        "ALTER TABLE FollowUp ADD COLUMN ack_requested_by TEXT",
        "ALTER TABLE FollowUp ADD COLUMN ack_requested_at TEXT",
        "ALTER TABLE FollowUp ADD COLUMN ack_note TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN guarantor_mobile TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN is_reloan INTEGER DEFAULT 0",
        "ALTER TABLE LoanEntry ADD COLUMN reloan_ref TEXT",
        "ALTER TABLE EMI ADD COLUMN bill_number TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN remarks TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN attachment TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN custom_emi_amount REAL",
        "ALTER TABLE LoanEntry ADD COLUMN aadhar_number TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN vehicle_name TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN loan_date TEXT",
        "ALTER TABLE ClosureItems ADD COLUMN vehicle_id INTEGER",
        "ALTER TABLE PreClosure ADD COLUMN requested_rate REAL",
        "ALTER TABLE PreClosure ADD COLUMN penalty_amount REAL",
        "ALTER TABLE PreClosure ADD COLUMN penalty_days INTEGER",
        "ALTER TABLE PreClosure ADD COLUMN further_interest REAL",
        "ALTER TABLE PreClosure ADD COLUMN waived_months INTEGER",
        "ALTER TABLE LoanEntry ADD COLUMN key_collected_by TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN rc_collected_by TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN docs_collected_by TEXT",
        "ALTER TABLE LoanVehicles ADD COLUMN key_collected_by TEXT",
        "ALTER TABLE LoanVehicles ADD COLUMN rc_collected_by TEXT",
        "ALTER TABLE LoanVehicles ADD COLUMN docs_collected_by TEXT",
        "ALTER TABLE FollowUp ADD COLUMN recv_date TEXT",
        "ALTER TABLE FollowUp ADD COLUMN recv_by TEXT",
        "ALTER TABLE Receipts ADD COLUMN extra_label TEXT",
        "ALTER TABLE Receipts ADD COLUMN extra_amount REAL",
        "ALTER TABLE Receipts ADD COLUMN batch_id TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN guarantor_permanent_address TEXT",
        "ALTER TABLE LoanGuarantors ADD COLUMN permanent_address TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN cheque_received TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN cheque_received_date TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN cheque_collected_by TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN cheque_leaves INTEGER",
        "ALTER TABLE LoanEntry ADD COLUMN cheque_numbers TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN key_na_remark TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN cheque_na_remark TEXT",
        "ALTER TABLE LoanVehicles ADD COLUMN key_na_remark TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN aadhar_received TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN aadhar_received_date TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN aadhar_collected_by TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN eb_received TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN eb_received_date TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN eb_collected_by TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN other_owner TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN tc_received TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN tc_received_date TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN tc_collected_by TEXT",
        "ALTER TABLE LoanEntry ADD COLUMN police_fine REAL DEFAULT 0",
        "ALTER TABLE LoanVehicles ADD COLUMN other_owner TEXT",
        "ALTER TABLE LoanVehicles ADD COLUMN tc_received TEXT",
        "ALTER TABLE LoanVehicles ADD COLUMN tc_received_date TEXT",
        "ALTER TABLE LoanVehicles ADD COLUMN tc_collected_by TEXT",
        "ALTER TABLE LoanVehicles ADD COLUMN police_fine REAL DEFAULT 0",
        "ALTER TABLE FollowUp ADD COLUMN recv_extra TEXT",
        "ALTER TABLE EMI ADD COLUMN penalty_due REAL DEFAULT 0",
        "ALTER TABLE EMI ADD COLUMN penalty_paid REAL DEFAULT 0",
        "ALTER TABLE EMIPayments ADD COLUMN penalty_part REAL DEFAULT 0",
        "ALTER TABLE Penalties ADD COLUMN merged_emi_id INTEGER",
    ]:
        try: cur.execute(m)
        except: pass
    for u, info in DEFAULT_USERS.items():
        cur.execute("INSERT OR IGNORE INTO Users (username,pw_hash,role,created_at) VALUES (?,?,?,?)",
                    (u, info["pw_hash"], info["role"], datetime.now(timezone.utc).isoformat()))

    # ── PERFORMANCE INDEXES ──────────────────────────────────────────────────
    for idx in [
        "CREATE INDEX IF NOT EXISTS idx_emi_loan_id ON EMI(loan_id)",
        "CREATE INDEX IF NOT EXISTS idx_emi_status ON EMI(status)",
        "CREATE INDEX IF NOT EXISTS idx_emi_due_date ON EMI(due_date)",
        "CREATE INDEX IF NOT EXISTS idx_emi_status_due ON EMI(status, due_date)",
        "CREATE INDEX IF NOT EXISTS idx_emi_paid_at ON EMI(paid_at)",
        "CREATE INDEX IF NOT EXISTS idx_loan_number ON LoanEntry(loan_number)",
        "CREATE INDEX IF NOT EXISTS idx_loan_status ON LoanEntry(status)",
        "CREATE INDEX IF NOT EXISTS idx_loan_created ON LoanEntry(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_loan_customer_name ON LoanEntry(customer_name)",
        "CREATE INDEX IF NOT EXISTS idx_loan_customer_mobile ON LoanEntry(customer_mobile)",
        "CREATE INDEX IF NOT EXISTS idx_loan_vehicle_number ON LoanEntry(vehicle_number)",
        "CREATE INDEX IF NOT EXISTS idx_customers_loan_id ON Customers(loan_id)",
        "CREATE INDEX IF NOT EXISTS idx_customers_status ON Customers(status)",
        "CREATE INDEX IF NOT EXISTS idx_followup_loan_id ON FollowUp(loan_id)",
        "CREATE INDEX IF NOT EXISTS idx_followup_status ON FollowUp(status)",
        "CREATE INDEX IF NOT EXISTS idx_closure_loan ON LoanClosure(loan_id)",
        "CREATE INDEX IF NOT EXISTS idx_closure_status ON LoanClosure(status)",
        "CREATE INDEX IF NOT EXISTS idx_closureitems_closure ON ClosureItems(closure_id)",
        "CREATE INDEX IF NOT EXISTS idx_seizures_loan ON Seizures(loan_id)",
        "CREATE INDEX IF NOT EXISTS idx_seizures_status ON Seizures(status)",
        "CREATE INDEX IF NOT EXISTS idx_seizureitems_seizure ON SeizureItems(seizure_id)",
        "CREATE INDEX IF NOT EXISTS idx_loanvehicles_loan ON LoanVehicles(loan_id)",
        "CREATE INDEX IF NOT EXISTS idx_loanguarantors_loan ON LoanGuarantors(loan_id)",
        "CREATE INDEX IF NOT EXISTS idx_pp_status ON PendingPayments(status)",
        "CREATE INDEX IF NOT EXISTS idx_pp_emi ON PendingPayments(emi_id)",
        "CREATE INDEX IF NOT EXISTS idx_penalties_status ON Penalties(status)",
        "CREATE INDEX IF NOT EXISTS idx_penalties_emi ON Penalties(emi_id)",
        "CREATE INDEX IF NOT EXISTS idx_receipts_no ON Receipts(receipt_no)",
        "CREATE INDEX IF NOT EXISTS idx_receipts_loan_id ON Receipts(loan_id)",
        "CREATE INDEX IF NOT EXISTS idx_preclosure_loan_id ON PreClosure(loan_id)",
        "CREATE INDEX IF NOT EXISTS idx_preclosure_status ON PreClosure(status)",
        "CREATE INDEX IF NOT EXISTS idx_emipayments_emi_id ON EMIPayments(emi_id)",
        "CREATE INDEX IF NOT EXISTS idx_emipayments_loan_id ON EMIPayments(loan_id)",
    ]:
        try: cur.execute(idx)
        except: pass

    conn.commit(); conn.close()

# ══════════════════════════════════════════════════════════════════════════════
#  BUSINESS LOGIC
# ══════════════════════════════════════════════════════════════════════════════
def authenticate_user(username, password):
    c = get_cur(); c.execute("SELECT * FROM Users WHERE username=?", (username,))
    row = c.fetchone()
    if row and row["pw_hash"] == hash_pw(password): return dict(row)
    return None

def can_pay_emi(loan_id, inst_no):
    if inst_no == 1: return True
    c = get_cur()
    c.execute("SELECT COUNT(*) as n FROM EMI WHERE loan_id=? AND installment_no<? AND status!='Paid'",
              (loan_id, inst_no))
    return c.fetchone()["n"] == 0

def create_loan(ln, cname, cmobile, caddr, cloc,
                vtype, vnum, vmodel, vname, eng, chas, vcol,
                amt, rate_raw, tenure, sdate, aadhar="", cemail="",
                gname="", gaddr="", gmob="", is_reloan=0, reloan_ref="",
                remarks="", attachment="", custom_emi_amount=None, loan_date=None, gloc="", paddr=""):
    r = normalize_interest(rate_raw)
    if r is None: raise ValueError("Invalid interest rate")
    c = get_cur()
    c.execute("""INSERT INTO LoanEntry
                 (loan_number,customer_name,customer_mobile,customer_address,customer_location,
                  vehicle_type,vehicle_number,vehicle_model,vehicle_name,engine_number,chassis_number,vehicle_colour,
                  loan_amount,interest_rate,tenure,start_date,loan_date,status,created_at,
                  attachment,aadhar_number,customer_email,guarantor_name,guarantor_address,guarantor_mobile,
                  is_reloan,reloan_ref,remarks,custom_emi_amount,guarantor_location,customer_permanent_address)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (ln,cname,cmobile,caddr,cloc,
               vtype,vnum,vmodel,vname,eng,chas,vcol,
               float(amt),float(r),int(tenure),sdate,loan_date or sdate,"PendingApproval",
               datetime.now(timezone.utc).isoformat(),
               attachment or None,aadhar,cemail,gname,gaddr,gmob,int(is_reloan),reloan_ref,
               remarks,
               float(custom_emi_amount) if custom_emi_amount not in (None,"","0") else None,
               gloc, paddr))
    get_db().commit(); return c.lastrowid

def record_handover_details(loan_id, loan_date_val, field_visit, field_visit_date,
                            field_visit_remark, answers, username, visited_by="", collected=None):
    """Saves the field-visit / key / RC / documents answers. 'Yes' items are stamped with the
    loan date; 'No' items get an automatic follow-up (key 3 days, proof & documents 3 days,
    RC 15 days after the loan date)."""
    loan_iso = loan_date_val.isoformat()
    col = collected or {}
    def got(short, flag):       # (collected date, collected by) of a 'Yes' item
        if answers[flag] != "yes": return None, None
        d, by = col.get(short, (None, None))
        return (d or loan_iso), (by or None)
    kd, kb = got("key", "key_received"); rd, rb = got("rc", "rc_received"); dd, db_ = got("docs", "docs_received")
    c = get_cur()
    c.execute("""UPDATE LoanEntry SET field_visit=?, field_visit_date=?, field_visit_remark=?, field_visited_by=?,
                    key_received=?, key_received_date=?, key_collected_by=?, rc_received=?, rc_received_date=?, rc_collected_by=?,
                    docs_received=?, docs_received_date=?, docs_collected_by=? WHERE id=?""",
              (field_visit, field_visit_date, field_visit_remark or None, visited_by or None,
               answers["key_received"], kd, kb, answers["rc_received"], rd, rb, answers["docs_received"], dd, db_,
               loan_id))
    get_db().commit()
    schedule_handover_followups(loan_id, loan_date_val, answers, username)

HANDOVER_PLAN = (("key_received",  "key",  "Key Collection",    3,  "Collect vehicle key from customer"),
                 ("rc_received",   "rc",   "Proof & Documents", 15, "Collect RC book from customer"),
                 ("docs_received", "docs", "Proof & Documents", 3,  "Collect proof & documents from customer"))
HANDOVER_DEFAULT_DAYS = {"key": 3, "rc": 15, "docs": 3, "cheque": 3, "aadhar": 3, "eb": 3, "tc": 3}
HANDOVER_LABELS = {"key": "Key", "rc": "RC", "docs": "Proof & Documents", "cheque": "Cheque leaf",
                   "aadhar": "Aadhar (address proof)", "eb": "EB bill (address proof)", "tc": "Transfer certificate"}
# extra proof & document items (cheque leaf, address proof, transfer certificate): answer field, item, follow-up text
EXTRA_PLAN = (("cheque_received", "cheque", "Collect cheque leaf from customer"),
              ("aadhar_received", "aadhar", "Collect Aadhar copy (address proof) from customer"),
              ("eb_received",     "eb",     "Collect EB bill (address proof) from customer"),
              ("tc_received",     "tc",     "Collect transfer certificate (vehicle is in another owner's name)"))
POLICE_FINE_DEFAULT_MAX = 5000

def police_fine_limit():
    try: return max(0.0, float(get_setting("police_fine_max", POLICE_FINE_DEFAULT_MAX)))
    except (TypeError, ValueError): return float(POLICE_FINE_DEFAULT_MAX)

def parse_fine(raw, label="Police fine"):
    raw = (raw or "").strip()
    if not raw: return 0.0
    try: v = round(float(raw), 2)
    except ValueError: raise ValueError(f"{label}: enter a valid amount.")
    if v < 0: raise ValueError(f"{label}: the amount cannot be negative.")
    limit = police_fine_limit()
    if v > limit + 0.001: raise ValueError(f"{label}: cannot be more than {fmt_inr(limit)} (entered {fmt_inr(v)}).")
    return v

def schedule_extra_followups(loan_id, base_date, answers, username, vehicle_id=None, vtag=""):
    """A 'No' for cheque leaf / address proof / transfer certificate creates a Proof & Documents follow-up."""
    for field, item, remark in EXTRA_PLAN:
        if answers.get(field) == "no":
            add_follow_up(loan_id, (base_date + timedelta(days=handover_days(item))).isoformat(),
                          remark + (f" — {vtag}" if vtag else ""), username, "Proof & Documents", item, ref_id=vehicle_id)

def record_extra_documents(loan_id, base_date, docs_extra, collected, leaves, numbers, fine, username):
    """Saves cheque leaf, address proof, transfer certificate and police fine of the main vehicle / loan."""
    iso = base_date.isoformat()
    def got(k):
        if docs_extra.get(k) != "yes": return None, None
        d, by = collected.get(k, (None, None))
        return (d or iso), (by or None)
    cd, cb = got("cheque"); ad, ab = got("aadhar"); ed, eb_ = got("eb"); td, tb = got("tc")
    c = get_cur()
    c.execute("""UPDATE LoanEntry SET cheque_received=?, cheque_received_date=?, cheque_collected_by=?, cheque_leaves=?, cheque_numbers=?,
                    aadhar_received=?, aadhar_received_date=?, aadhar_collected_by=?, eb_received=?, eb_received_date=?, eb_collected_by=?,
                    other_owner=?, tc_received=?, tc_received_date=?, tc_collected_by=?, police_fine=? WHERE id=?""",
              (docs_extra["cheque"], cd, cb, (leaves if docs_extra["cheque"] == "yes" else None), (numbers or None) if docs_extra["cheque"] == "yes" else None,
               docs_extra["aadhar"], ad, ab, docs_extra["eb"], ed, eb_,
               docs_extra["other_owner"], docs_extra.get("tc") or None, td, tb, fine, loan_id))
    get_db().commit()
    schedule_extra_followups(loan_id, base_date,
                             {"cheque_received": docs_extra["cheque"], "aadhar_received": docs_extra["aadhar"],
                              "eb_received": docs_extra["eb"], "tc_received": docs_extra.get("tc")}, username)

def documents_summary_html(loan):
    """Status of the extra documents (cheque leaf, address proof, transfer certificate) and the police fine."""
    def mark(flag, dt, by, extra="", why=""):
        if flag == "yes":
            return (f'<span style="color:var(--green);">✔ {fmt_date(dt, "received")}'
                    f'{(" by " + html.escape(by)) if by else ""}{extra}</span>')
        if flag == "no": return '<span style="color:var(--red);">✖ pending</span>'
        if flag == "na": return f'<b style="color:var(--muted);">Not required{(" — " + html.escape(why)) if why else ""}</b>'
        return '<span style="color:var(--muted);">—</span>'
    cx = ""
    if loan.get("cheque_received") == "yes":
        bits = []
        if loan.get("cheque_leaves"): bits.append(f'{int(loan["cheque_leaves"])} leaf(s)')
        if loan.get("cheque_numbers"): bits.append(html.escape(loan["cheque_numbers"]))
        if bits: cx = " · " + " · ".join(bits)
    out = [f'🧾 <b>Cheque leaf signed:</b> {mark(loan.get("cheque_received"), loan.get("cheque_received_date"), loan.get("cheque_collected_by"), cx, loan.get("cheque_na_remark"))}',
           f'🪪 <b>Aadhar (address proof):</b> {mark(loan.get("aadhar_received"), loan.get("aadhar_received_date"), loan.get("aadhar_collected_by"))}',
           f'💡 <b>EB bill (address proof):</b> {mark(loan.get("eb_received"), loan.get("eb_received_date"), loan.get("eb_collected_by"))}']
    vs = loan_vehicles(loan)
    for v in vs:
        fine = float(v.get("police_fine") or 0)
        tag = f'<b>{html.escape(vehicle_tag(v))}</b> — ' if len(vs) > 1 else ""
        fine_html = (f'<b style="color:#fff;background:var(--red);border-radius:6px;padding:1px 7px;">Police fine {fmt_inr(fine)}</b>' if fine > 0
                     else '<span style="color:var(--muted);">No police fine</span>')
        if v.get("other_owner") == "yes":
            own = f'🔁 Other owner\'s name · Transfer certificate: {mark(v.get("tc_received"), v.get("tc_received_date"), v.get("tc_collected_by"))}'
        elif v.get("other_owner") == "no":
            own = "Vehicle in the customer's name"
        else:
            own = ""
        out.append(f'🚗 {tag}{fine_html}{(" · " + own) if own else ""}')
    return ('<div style="background:var(--surface2);border-radius:8px;padding:8px 12px;font-size:12.5px;line-height:1.9;margin:8px 0;">'
            '<b style="color:var(--accent);">📎 Documents &amp; fines</b><br>' + "<br>".join(out) + '</div>')

def handover_days(item):
    """Follow-up days for a 'No' answer (key / rc / docs); the Super Admin sets them on the Users page."""
    try: return max(1, int(get_setting(f"followup_days_{item}", HANDOVER_DEFAULT_DAYS[item])))
    except (TypeError, ValueError): return HANDOVER_DEFAULT_DAYS[item]

def parse_collected(date_s, by_s, label, default_iso=""):
    """'When collected' (optional, defaults to default_iso) and 'Collected by' (free text, required)."""
    by = (by_s or "").strip()
    if not by: raise ValueError(f"{label}: please enter who collected it.")
    d = default_iso
    if (date_s or "").strip():
        try: dd = datetime.strptime(date_s.strip(), "%Y-%m-%d").date()
        except ValueError: raise ValueError(f"{label}: invalid collected date.")
        if dd > date.today(): raise ValueError(f"{label}: the collected date cannot be in the future.")
        d = dd.isoformat()
    return d, by

def schedule_handover_followups(loan_id, base_date, answers, username, vehicle_id=None, vtag=""):
    """A 'No' answer for key / RC / proof creates a follow-up (key 3 days, proof 3 days, RC 15 days after base_date).
    For an add-on vehicle the follow-up carries the vehicle id in ref_id, so resolving it updates that vehicle only."""
    for field, item, category, _days, remark in HANDOVER_PLAN:
        if answers.get(field) == "no":
            add_follow_up(loan_id, (base_date + timedelta(days=handover_days(item))).isoformat(),
                          remark + (f" — {vtag}" if vtag else ""), username, category, item, ref_id=vehicle_id)

def save_extra_vehicles(loan_id, rows, base_date, username):
    """Creates / updates / removes add-on vehicles. New ones are stamped with base_date when received
    and get follow-ups for whatever was not received."""
    c = get_cur()
    for r in rows:
        if r["id"]:
            vid = int(r["id"])
            c.execute("SELECT vehicle_id FROM LoanVehicles WHERE vehicle_id=? AND loan_id=?", (vid, loan_id))
            if not c.fetchone(): continue
            if r["delete"]:
                c.execute("DELETE FROM LoanVehicles WHERE vehicle_id=?", (vid,))
                c.execute("""DELETE FROM FollowUp WHERE loan_id=? AND ref_id=? AND item IN ('key','rc','docs')
                             AND status IN ('Pending','AwaitingAck')""", (loan_id, vid))
                c.execute("DELETE FROM ClosureItems WHERE vehicle_id=? AND status='Pending'", (vid,))
            else:
                c.execute("""UPDATE LoanVehicles SET vehicle_type=?, vehicle_number=?, vehicle_name=?, vehicle_model=?,
                             engine_number=?, chassis_number=?, vehicle_colour=? WHERE vehicle_id=?""",
                          (r["vehicle_type"], r["vehicle_number"], r["vehicle_name"], r["vehicle_model"],
                           r["engine_number"], r["chassis_number"], r["vehicle_colour"], vid))
                c.execute("UPDATE LoanVehicles SET police_fine=? WHERE vehicle_id=?", (r.get("fine") or 0, vid))
            continue
        c.execute("SELECT COALESCE(MAX(seq),1) as m FROM LoanVehicles WHERE loan_id=?", (loan_id,))
        seq = int(c.fetchone()["m"]) + 1
        stamp = base_date.isoformat()
        def got(k):         # (collected date, collected by) for a 'Yes' item
            return ((r.get(k + "_date") or stamp), r.get(k + "_by")) if r[k] == "yes" else (None, None)
        (kd, kb), (rd, rb), (dd, db_) = got("key"), got("rc"), got("docs")
        c.execute("""INSERT INTO LoanVehicles (loan_id,seq,vehicle_type,vehicle_number,vehicle_name,vehicle_model,
                       engine_number,chassis_number,vehicle_colour,key_received,key_received_date,key_collected_by,
                       rc_received,rc_received_date,rc_collected_by,docs_received,docs_received_date,docs_collected_by)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (loan_id, seq, r["vehicle_type"], r["vehicle_number"], r["vehicle_name"], r["vehicle_model"],
                   r["engine_number"], r["chassis_number"], r["vehicle_colour"],
                   r["key"], kd, kb, r["rc"], rd, rb, r["docs"], dd, db_))
        vid = c.lastrowid
        get_db().commit()
        schedule_handover_followups(loan_id, base_date,
                                    {"key_received": r["key"], "rc_received": r["rc"], "docs_received": r["docs"]},
                                    username, vid, vehicle_tag(r))
        c = get_cur()
        c.execute("""UPDATE LoanVehicles SET other_owner=?, tc_received=?, tc_received_date=?, tc_collected_by=?, police_fine=?
                     WHERE vehicle_id=?""",
                  (r.get("owner"), r.get("tc") or None, (r.get("tc_date") or stamp) if r.get("tc") == "yes" else None,
                   r.get("tc_by") if r.get("tc") == "yes" else None, r.get("fine") or 0, vid))
        if r["key"] == "na":
            c.execute("UPDATE LoanVehicles SET key_na_remark=? WHERE vehicle_id=?", (r.get("key_na"), vid))
        get_db().commit()
        schedule_extra_followups(loan_id, base_date, {"tc_received": r.get("tc")}, username, vid, vehicle_tag(r))
        c = get_cur()
    get_db().commit()

def save_extra_guarantors(loan_id, rows):
    c = get_cur()
    for r in rows:
        if r["id"]:
            gid = int(r["id"])
            c.execute("SELECT guarantor_id FROM LoanGuarantors WHERE guarantor_id=? AND loan_id=?", (gid, loan_id))
            if not c.fetchone(): continue
            if r["delete"]:
                c.execute("DELETE FROM LoanGuarantors WHERE guarantor_id=?", (gid,))
            else:
                c.execute("UPDATE LoanGuarantors SET name=?, mobile=?, address=?, permanent_address=?, location=? WHERE guarantor_id=?",
                          (r["name"], r["mobile"], r["address"], r["permanent"] or None, r["location"], gid))
            continue
        c.execute("SELECT COALESCE(MAX(seq),1) as m FROM LoanGuarantors WHERE loan_id=?", (loan_id,))
        seq = int(c.fetchone()["m"]) + 1
        c.execute("INSERT INTO LoanGuarantors (loan_id,seq,name,mobile,address,permanent_address,location) VALUES (?,?,?,?,?,?,?)",
                  (loan_id, seq, r["name"], r["mobile"], r["address"], r["permanent"] or None, r["location"]))
    get_db().commit()

# ── Billing (printed-style receipts) ────────────────────────────────────────────
_ONES = ["","One","Two","Three","Four","Five","Six","Seven","Eight","Nine","Ten","Eleven","Twelve",
         "Thirteen","Fourteen","Fifteen","Sixteen","Seventeen","Eighteen","Nineteen"]
_TENS = ["","","Twenty","Thirty","Forty","Fifty","Sixty","Seventy","Eighty","Ninety"]

def _words_below_1000(n):
    out = []
    if n >= 100:
        out.append(_ONES[n // 100] + " Hundred"); n %= 100
    if n >= 20:
        out.append(_TENS[n // 10] + ((" " + _ONES[n % 10]) if n % 10 else ""))
    elif n > 0:
        out.append(_ONES[n])
    return " ".join(out)

def amount_in_words(amount):
    """Indian-style words: 2850 -> 'Two Thousand Eight Hundred Fifty Rupees Only'."""
    amount = round(float(amount or 0), 2)
    rupees, paise = int(amount), int(round((amount - int(amount)) * 100))
    if rupees == 0 and paise == 0: return "Zero Rupees Only"
    unit = "Rupee" if rupees == 1 else "Rupees"
    parts = []
    for div, name in ((10000000, "Crore"), (100000, "Lakh"), (1000, "Thousand")):
        if rupees >= div:
            parts.append(_words_below_1000(rupees // div) + " " + name); rupees %= div
    if rupees: parts.append(_words_below_1000(rupees))
    text = " ".join(parts) + " " + unit if parts else ""
    if paise: text += (" and " if text else "") + _words_below_1000(paise) + " Paise"
    return text.strip() + " Only"

def ordinal_due(n):
    n = int(n)
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix} due"

def generate_receipt_pdf(r):
    """One receipt on an A5 page, laid out like the printed receipt book."""
    if not REPORTLAB_AVAILABLE: raise RuntimeError("reportlab not installed.")
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib.pagesizes import A5
    from reportlab.lib.utils import ImageReader
    buf = io.BytesIO()
    W, H = A5
    c = rl_canvas.Canvas(buf, pagesize=A5)
    blue = colors.HexColor("#1a4fad")
    m = 0.8 * cm
    c.setStrokeColor(blue); c.setLineWidth(1.4)
    c.rect(m, m, W - 2*m, H - 2*m)
    top = H - m
    # header block: logo + name/address, receipt no box on the right
    hdr_bottom = top - 3.3*cm
    c.line(m, hdr_bottom, W - m, hdr_bottom)
    box_x = W - m - 4.2*cm
    c.line(box_x, top, box_x, hdr_bottom)
    c.line(box_x, top - 1.1*cm, W - m, top - 1.1*cm)
    logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")
    if os.path.exists(logo_path):
        try: c.drawImage(ImageReader(logo_path), m + 0.25*cm, hdr_bottom + 0.35*cm, 2.3*cm, 2.3*cm, mask="auto", preserveAspectRatio=True)
        except Exception: pass
    c.setFillColor(blue)
    c.setFont("Helvetica-Bold", 17); c.drawString(m + 3.0*cm, top - 1.3*cm, "Thendralla Fincorp")
    c.setFont("Helvetica", 7.6)
    c.drawString(m + 3.0*cm, top - 1.85*cm, "12/360 - 1, Anbu Nagar, Madukkarai Market,")
    c.drawString(m + 3.0*cm, top - 2.3*cm, "Coimbatore - 641 105.  Ph : +91 63697 52877")
    c.setFont("Helvetica-Bold", 9); c.drawCentredString(box_x + 2.1*cm, top - 0.75*cm, "RECEIPT NO")
    c.setFont("Helvetica-Bold", 17); c.drawCentredString(box_x + 2.1*cm, top - 2.3*cm, str(r["receipt_no"]))
    # field rows
    y = hdr_bottom - 1.5*cm
    c.setFillColor(colors.black)
    def field(label, value, x_label, x_value, y, size=11):
        c.setFont("Helvetica-Bold", 8.5); c.setFillColor(blue); c.drawString(x_label, y, label)
        c.setFillColor(colors.black); c.setFont("Helvetica", size); c.drawString(x_value, y, str(value or ""))
        c.setDash(1, 2); c.setStrokeColor(colors.grey); c.setLineWidth(0.6)
        c.line(x_value - 0.1*cm, y - 0.15*cm, W - m - 0.5*cm, y - 0.15*cm); c.setDash(); c.setStrokeColor(blue); c.setLineWidth(1.4)
    xl, xv = m + 0.4*cm, m + 3.6*cm
    field("Received From", r["received_from"], xl, xv, y); y -= 1.45*cm
    field("Date", datetime.strptime(r["receipt_date"], "%Y-%m-%d").strftime("%d/%m/%y"), xl, xv, y); y -= 1.45*cm
    field("Vehicle No.", r["vehicle_number"], xl, xv, y)
    # Loan No. sits on the Vehicle No. row, so the installment list below gets the full width
    c.setFillColor(colors.white); c.rect(W/2 + 0.1*cm, y - 0.3*cm, W/2 - m - 0.5*cm, 0.75*cm, stroke=0, fill=1)
    c.setFont("Helvetica-Bold", 8.5); c.setFillColor(blue); c.drawString(W/2 + 0.2*cm, y, "Loan No.")
    c.setFillColor(colors.black); c.setFont("Helvetica", 11); c.drawString(W/2 + 1.9*cm, y, str(r["loan_number"] or ""))
    c.setDash(1, 2); c.setStrokeColor(colors.grey); c.setLineWidth(0.6)
    c.line(W/2 + 1.8*cm, y - 0.15*cm, W - m - 0.5*cm, y - 0.15*cm); c.setDash(); c.setStrokeColor(blue); c.setLineWidth(1.4)
    y -= 1.45*cm
    c.setFont("Helvetica-Bold", 8.5); c.setFillColor(blue); c.drawString(xl, y, "Installment No.")
    # a long list ("Installment 1, 2, 3, ... 14") shrinks a little, then wraps onto a second line
    inst, room = str(r["installment_label"] or ""), W - m - 0.5*cm - xv
    size = 11
    while size > 8.5 and c.stringWidth(inst, "Helvetica", size) > room: size -= 0.5
    lines = [inst]
    if c.stringWidth(inst, "Helvetica", size) > room and ", " in inst:
        lines = [""]
        for part in inst.split(", "):
            trial = (lines[-1] + ", " + part) if lines[-1] else part
            if lines[-1] and c.stringWidth(trial + ",", "Helvetica", size) > room: lines[-1] += ","; lines.append(part)
            else: lines[-1] = trial
    c.setFillColor(colors.black); c.setFont("Helvetica", size)
    for i, ln in enumerate(lines[:2]): c.drawString(xv, y - i*0.42*cm, ln)
    if r.get("extra_amount"):
        c.setFont("Helvetica", 8); c.setFillColor(colors.black)
        c.drawString(xv, y - 0.45*cm, f"(incl. Rs. {float(r['extra_amount']):,.2f} towards {r.get('extra_label') or 'next due'})")
    y -= 0.7*cm
    # amount block
    block_top = y; block_bottom = block_top - 4.6*cm; mid = W/2 + 0.3*cm
    c.setStrokeColor(blue); c.setLineWidth(1.4)
    c.line(m, block_top, W - m, block_top); c.line(m, block_bottom, W - m, block_bottom); c.line(mid, block_top, mid, block_bottom)
    row_y = block_top - 1.0*cm
    for label, val in (("CASH :", r["cash"]), ("ONLINE :", r["online"])):
        c.setFont("Helvetica-Bold", 9); c.setFillColor(blue); c.drawString(m + 0.5*cm, row_y, label)
        c.setFillColor(colors.black); c.setFont("Helvetica", 11)
        c.drawString(m + 2.6*cm, row_y, f"Rs. {float(val):,.2f}" if val else "Rs.")
        row_y -= 1.1*cm
    c.setStrokeColor(blue); c.line(m, block_bottom + 1.3*cm, mid, block_bottom + 1.3*cm)
    c.setFont("Helvetica-Bold", 12); c.setFillColor(blue); c.drawString(m + 0.5*cm, block_bottom + 0.45*cm, "Total")
    c.setFillColor(colors.black); c.setFont("Helvetica-Bold", 13); c.drawString(m + 2.6*cm, block_bottom + 0.45*cm, f"Rs. {float(r['total']):,.2f}")
    c.setFont("Helvetica-Bold", 9.5); c.setFillColor(blue); c.drawString(mid + 0.4*cm, block_top - 0.8*cm, "For Thendralla Fincorp")
    c.setFillColor(colors.black); c.setFont("Helvetica", 9); c.drawCentredString((mid + W - m)/2, block_bottom + 1.0*cm, str(r.get("cashier") or ""))
    c.setFont("Helvetica-Bold", 8); c.setFillColor(blue); c.drawCentredString((mid + W - m)/2, block_bottom + 0.4*cm, "CASHIER")
    # amount in words
    y = block_bottom - 0.9*cm
    c.setFont("Helvetica-Bold", 9); c.drawString(xl, y, "Rupees :");
    c.setFont("Helvetica", 6.5); c.drawString(xl, y - 0.35*cm, "(Amount in Words)")
    c.setFillColor(colors.black); c.setFont("Helvetica", 10.5)
    words = r["amount_words"]; line, lines = "", []
    for w in words.split():
        if c.stringWidth((line + " " + w).strip(), "Helvetica", 10.5) > W - 2*m - 3.6*cm: lines.append(line); line = w
        else: line = (line + " " + w).strip()
    lines.append(line)
    for i, ln in enumerate(lines[:3]): c.drawString(m + 3.1*cm, y - i*0.55*cm, ln)
    c.showPage(); c.save()
    buf.seek(0)
    return buf

# ── Pre-closure (early closure at a reduced interest rate, admin-approved) ──────
def preclosure_months(loan, as_of=None):
    """Months from the loan date to `as_of`, a part month counting as a full month
    (minimum 1, never more than the tenure)."""
    as_of = as_of or date.today()
    try:
        ld = parse_date(loan.get("loan_date") or add_months(parse_date(loan["start_date"]), -1).isoformat())
    except Exception:
        ld = as_of
    m = (as_of.year - ld.year) * 12 + (as_of.month - ld.month)
    if as_of.day > ld.day: m += 1
    return max(1, min(m, int(loan.get("tenure") or m)))

def preclosure_figures(loan, further_interest=None, as_of=None):
    """Settlement = principal still to collect + the further interest the admin chooses to collect
    + penalty. Paid amounts are split into principal / interest in proportion to the loan's total due."""
    months = preclosure_months(loan, as_of)
    principal = float(loan["loan_amount"])
    c = get_cur()
    c.execute("SELECT COALESCE(SUM(amount_paid),0) as p, COALESCE(SUM(emi_amount),0) as due, COUNT(*) as n FROM EMI WHERE loan_id=?", (loan["id"],))
    r = c.fetchone()
    paid, total_due, n = float(r["p"] or 0), float(r["due"] or 0), int(r["n"] or 0)
    c.execute("SELECT emi_amount FROM EMI WHERE loan_id=? ORDER BY installment_no LIMIT 1", (loan["id"],))
    r = c.fetchone()
    emi = float(r["emi_amount"]) if r else 0.0
    total_interest = round(max(0.0, total_due - principal), 2)
    principal_paid = round(min(principal, paid * principal / total_due), 2) if total_due > 0 else 0.0
    interest_paid = round(max(0.0, paid - principal_paid), 2)
    rem_principal = round(principal - principal_paid, 2)
    rem_interest = round(max(0.0, total_interest - interest_paid), 2)
    monthly_interest = round(total_interest / n, 2) if n else 0.0
    interest = rem_interest if further_interest is None else round(float(further_interest), 2)
    c.execute("""SELECT COALESCE(SUM(MAX(0, COALESCE(penalty_due,0)-COALESCE(penalty_paid,0))),0) as pen FROM EMI
                 WHERE loan_id=? AND status NOT IN ('Paid','PreClosed','Seized')""", (loan["id"],))
    penalty = round(float(c.fetchone()["pen"] or 0), 2)       # penalties added to EMIs that are still unpaid
    settlement = max(0.0, round(rem_principal + interest + penalty, 2))
    return {"months": months, "interest": interest, "paid": round(paid, 2), "settlement": settlement, "penalty": penalty,
            "emi": emi, "principal": principal, "total_interest": total_interest, "principal_paid": principal_paid,
            "interest_paid": interest_paid, "rem_principal": rem_principal, "rem_interest": rem_interest,
            "monthly_interest": monthly_interest}

def get_preclosure(loan_id):
    """Latest pre-closure record of a loan (or None)."""
    c = get_cur()
    c.execute("SELECT * FROM PreClosure WHERE loan_id=? ORDER BY preclose_id DESC LIMIT 1", (loan_id,))
    r = c.fetchone()
    return dict(r) if r else None

def preclosure_in_progress(loan_id):
    pc = get_preclosure(loan_id)
    return bool(pc and pc["status"] in ("Pending", "Approved"))

def request_preclosure(loan_id, username, penalty_rate=None):
    c = get_cur()
    c.execute("SELECT * FROM LoanEntry WHERE id=?", (loan_id,))
    loan = c.fetchone()
    if not loan: raise ValueError("Loan not found")
    if loan["status"] != "Approved": raise ValueError("Only active (approved) loans can be pre-closed.")
    if preclosure_in_progress(loan_id): raise ValueError("A pre-closure request is already open for this loan.")
    if get_open_closure(loan_id): raise ValueError("This loan is already being closed.")
    if get_open_seizure(loan_id): raise ValueError("A vehicle seizure is open for this loan.")
    try: rate = float(penalty_rate) if str(penalty_rate or "").strip() else 0.0
    except ValueError: raise ValueError("Enter a valid penalty per day.")
    if rate < 0: raise ValueError("Penalty per day cannot be negative.")
    c.execute("""INSERT INTO PreClosure (loan_id,status,requested_by,requested_at,original_rate,requested_rate)
                 VALUES (?,?,?,?,?,?)""",
              (loan_id, "Pending", username, datetime.now(timezone.utc).isoformat(), float(loan["interest_rate"]), rate or None))
    get_db().commit()

def preclosure_overdue_rows(loan_id):
    """Overdue unpaid EMIs that do not have a penalty yet: [(emi, days overdue)] — these get the pre-closure penalty."""
    out = []
    c = get_cur()
    for e in overdue_emis(loan_id):
        c.execute("SELECT 1 FROM Penalties WHERE emi_id=? AND status!='Rejected'", (e["emi_id"],))
        if c.fetchone(): continue
        days = (date.today() - parse_date(e["due_date"])).days
        if days > 0: out.append((e, days))
    return out

def approve_preclosure(preclose_id, further_interest, waived_months, username, pending_rates=None, overdue_rates=None):
    pending_rates, overdue_rates = pending_rates or {}, overdue_rates or {}
    c = get_cur()
    c.execute("SELECT * FROM PreClosure WHERE preclose_id=?", (preclose_id,))
    pc = c.fetchone()
    if not pc or pc["status"] != "Pending": raise ValueError("This pre-closure request is not pending.")
    c.execute("SELECT * FROM LoanEntry WHERE id=?", (pc["loan_id"],))
    loan = dict(c.fetchone())
    try: further = round(float(further_interest), 2)
    except (TypeError, ValueError): raise ValueError("Enter the interest amount to be collected further.")
    if further < 0: raise ValueError("Interest to be collected cannot be negative.")
    try: waived = int(str(waived_months or "0").strip() or 0)
    except ValueError: raise ValueError("Enter a valid number of months to waive.")
    if waived < 0: raise ValueError("Months to waive cannot be negative.")
    base = preclosure_figures(loan)
    if further > base["rem_interest"] + 0.005:
        raise ValueError(f"Interest to be collected cannot be more than the interest still pending ({fmt_inr(base['rem_interest'])}).")
    # validate every penalty rate before anything is changed
    lid = pc["loan_id"]
    c.execute("SELECT * FROM Penalties WHERE loan_id=? AND status='Pending' ORDER BY penalty_id", (lid,))
    pend = [dict(r) for r in c.fetchall()]
    def _rate(raw, default):
        try: val = float(raw) if raw not in (None, "") else float(default or 0)
        except (TypeError, ValueError): raise ValueError("Enter a valid penalty per day.")
        if val < 0: raise ValueError("Penalty per day cannot be negative.")
        return val
    chosen_p = {p["penalty_id"]: _rate(pending_rates.get(p["penalty_id"]), p["requested_rate"]) for p in pend}
    chosen_o = []
    for e, days in preclosure_overdue_rows(lid):
        val = _rate(overdue_rates.get(e["emi_id"]), pc["requested_rate"])
        if val > 0: chosen_o.append((e, days, val))
    now = datetime.now(timezone.utc).isoformat()
    for p in pend:              # penalties raised on earlier payments: approved (added to the unpaid EMI) or waived at 0
        approve_penalty(p["penalty_id"], chosen_p[p["penalty_id"]], username)
    c = get_cur()
    for e, days, val in chosen_o:   # overdue days of the unpaid EMIs: penalty = days x rate, added to that EMI
        amt = round(days * val, 2)
        c.execute("""INSERT INTO Penalties (loan_id,emi_id,installment_no,days,requested_rate,requested_amount,requested_by,requested_at,
                     status,final_rate,final_amount,decided_by,decided_at,merged_emi_id,decision_remarks)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (lid, e["emi_id"], e["installment_no"], days, val, amt, pc["requested_by"], pc["requested_at"], "Approved",
                   val, amt, username, now, e["emi_id"], "Pre-closure: overdue days"))
        c.execute("UPDATE EMI SET penalty_due=COALESCE(penalty_due,0)+? WHERE emi_id=?", (amt, e["emi_id"]))
    get_db().commit()
    fig = preclosure_figures(loan, further)
    total_days = sum(d for _, d, _ in chosen_o)
    c.execute("""UPDATE PreClosure SET status='Approved', further_interest=?, waived_months=?, months_elapsed=?, paid_before=?,
                 settlement_amount=?, penalty_amount=?, penalty_days=?, approved_by=?, approved_at=? WHERE preclose_id=?""",
              (further, waived, fig["months"], fig["paid"], fig["settlement"], fig["penalty"], total_days, username, now, preclose_id))
    get_db().commit()

def reject_preclosure(preclose_id, reason, username):
    c = get_cur()
    c.execute("SELECT status FROM PreClosure WHERE preclose_id=?", (preclose_id,))
    pc = c.fetchone()
    if not pc or pc["status"] != "Pending": raise ValueError("This pre-closure request is not pending.")
    c.execute("""UPDATE PreClosure SET status='Rejected', approved_by=?, approved_at=?, decision_remarks=?
                 WHERE preclose_id=?""",
              (username, datetime.now(timezone.utc).isoformat(), (reason or "").strip() or "No reason given", preclose_id))
    get_db().commit()

def complete_preclosure(preclose_id, bill_number, paid_on, username):
    """Records the single closing bill and closes the loan: unpaid EMIs become 'PreClosed'."""
    c = get_cur()
    c.execute("SELECT * FROM PreClosure WHERE preclose_id=?", (preclose_id,))
    pc = c.fetchone()
    if not pc or pc["status"] != "Approved": raise ValueError("This pre-closure has not been approved.")
    if float(pc["settlement_amount"] or 0) > 0 and not (bill_number or "").strip():
        raise ValueError("Bill number is mandatory for the closing payment.")
    bill_number = (bill_number or "").strip()
    try: pd = datetime.strptime((paid_on or "").strip(), "%Y-%m-%d").date()
    except ValueError: raise ValueError("Enter a valid Paid On date.")
    if pd > date.today(): raise ValueError("Paid On date cannot be in the future.")
    lid = pc["loan_id"]; now = datetime.now(timezone.utc).isoformat()
    c.execute("UPDATE EMI SET status='PreClosed', remaining_amount=0 WHERE loan_id=? AND status!='Paid'", (lid,))
    c.execute("UPDATE Penalties SET status='Collected', collected_at=? WHERE loan_id=? AND status='Approved' AND merged_emi_id IS NOT NULL",
              (now, lid))      # penalties added to EMIs were collected in the settlement
    c.execute("""UPDATE FollowUp SET status='Resolved', resolved_at=?
                 WHERE loan_id=? AND status='Pending' AND COALESCE(category,'Loans')='Loans'""", (now, lid))
    c.execute("""UPDATE PreClosure SET status='Completed', bill_number=?, paid_on=?, closed_by=?, closed_at=?
                 WHERE preclose_id=?""", (bill_number, pd.isoformat(), username, now, preclose_id))
    get_db().commit()
    # The money side is done; the loan itself closes after the key / document return is acknowledged.
    start_closure(lid, "PreClosure")

def approve_loan(loan_id, override_emi=None):
    c = get_cur()
    c.execute("SELECT * FROM LoanEntry WHERE id=?", (loan_id,))
    loan = c.fetchone()
    if not loan: raise ValueError("Loan not found")
    if loan["status"] != "PendingApproval": raise ValueError("Loan is not pending approval")

    amt   = float(loan["loan_amount"])
    rate  = float(loan["interest_rate"])
    tenure = int(loan["tenure"])

    # Priority: explicit admin override > stored custom EMI from application > auto-computed
    custom_emi = None
    if override_emi not in (None, "", 0, "0"):
        custom_emi = float(override_emi)
    else:
        try:
            stored = loan["custom_emi_amount"]
            if stored: custom_emi = float(stored)
        except (IndexError, KeyError, TypeError):
            pass

    amounts, total_due, computed_emi, leftover = plan_emi_schedule(amt, rate, tenure, custom_emi)
    emi_amt_for_summary = custom_emi if custom_emi else computed_emi

    now = datetime.now(timezone.utc).isoformat()
    c.execute("INSERT OR REPLACE INTO Customers (loan_id,name,vehicle_type,loan_amount,emi_amount,status,created_at) VALUES (?,?,?,?,?,?,?)",
              (loan_id, loan["customer_name"], loan["vehicle_type"], loan["loan_amount"], emi_amt_for_summary, "Active", now))
    try: sd = parse_date(loan["start_date"])
    except: sd = date.today()

    for idx, installment_amt in enumerate(amounts, start=1):
        c.execute("INSERT INTO EMI (loan_id,installment_no,due_date,emi_amount,status,paid_at,amount_paid,remaining_amount,extra_interest,bill_number) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (loan_id, idx, add_months(sd, idx-1).isoformat(), installment_amt, "Pending", None, 0.0, installment_amt, 0.0, None))

    # Persist the EMI amount actually used (for record-keeping / display)
    if custom_emi:
        c.execute("UPDATE LoanEntry SET status='Approved', custom_emi_amount=? WHERE id=?", (custom_emi, loan_id))
    else:
        c.execute("UPDATE LoanEntry SET status='Approved' WHERE id=?", (loan_id,))

    get_db().commit(); _notify_approval(dict(loan))

def reject_loan(loan_id, reason):
    c = get_cur()
    c.execute("UPDATE LoanEntry SET status='Rejected' WHERE id=?", (loan_id,))
    # drop the automatic key / RC / proof follow-ups that were created at submission
    c.execute("DELETE FROM FollowUp WHERE loan_id=? AND item IS NOT NULL AND status='Pending'", (loan_id,))
    c.execute("INSERT OR REPLACE INTO RejectedLoans (loan_id,reason,created_at) VALUES (?,?,?)",
              (loan_id, reason, datetime.now(timezone.utc).isoformat()))
    get_db().commit()

def emi_penalty_out(e):
    """Penalty that was added to this EMI's amount and is still to be collected."""
    return max(0.0, round(float(e.get("penalty_due") or 0) - float(e.get("penalty_paid") or 0), 2))

def _settle_merged_penalties(emi_id):
    """Marks the penalties added to this EMI as Collected, oldest first, as far as the penalty paid covers them."""
    c = get_cur(); c.execute("SELECT loan_id, penalty_paid FROM EMI WHERE emi_id=?", (emi_id,))
    e = c.fetchone()
    if not e: return
    paid = float(e["penalty_paid"] or 0)
    c.execute("""SELECT * FROM Penalties WHERE merged_emi_id=? AND status IN ('Approved','Collected') ORDER BY penalty_id""", (emi_id,))
    running, now, changed = 0.0, datetime.now(timezone.utc).isoformat(), False
    for p in [dict(r) for r in c.fetchall()]:
        running = round(running + float(p["final_amount"] or 0), 2)
        if p["status"] == "Approved" and running <= paid + 0.005:
            c.execute("UPDATE Penalties SET status='Collected', collected_at=? WHERE penalty_id=?", (now, p["penalty_id"]))
            changed = True
    if changed: get_db().commit()

def pay_emi(emi_id, pay_amount=None, extra_interest=0.0, bill_number="", paid_on=None, paid_by=None):
    c = get_cur(); now = datetime.now(timezone.utc).isoformat()
    paid_at_val = now
    if paid_on:
        try:
            d = datetime.strptime(paid_on, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError("Invalid Paid On date.")
        if d > date.today():
            raise ValueError("Paid On date cannot be in the future.")
        paid_at_val = datetime.combine(d, datetime.now(timezone.utc).timetz()).isoformat()
    c.execute("SELECT * FROM EMI WHERE emi_id=?", (emi_id,))
    emi = c.fetchone()
    if not emi: raise ValueError("EMI not found")
    if emi["status"] == "Paid": raise ValueError("EMI already paid")
    if emi["status"] == "PreClosed": raise ValueError("This loan has been pre-closed.")
    if emi["status"] == "Seized": raise ValueError("The vehicle of this loan was seized, so EMI payments are closed.")
    if preclosure_in_progress(emi["loan_id"]):
        raise ValueError("A pre-closure is in progress for this loan, so EMI payments are paused. "
                         "Finish or reject the pre-closure first.")
    if not can_pay_emi(emi["loan_id"], emi["installment_no"]):
        raise ValueError(f"Cannot pay installment {emi['installment_no']}. Complete previous first.")
    if not bill_number or not bill_number.strip():
        raise ValueError("Bill number is mandatory before payment.")
    amount_paid = emi["amount_paid"] or 0.0
    remaining   = emi["remaining_amount"] if emi["remaining_amount"] is not None else emi["emi_amount"]
    pen_out     = emi_penalty_out(dict(emi))          # penalty added to this EMI that is still unpaid
    if pay_amount is None: pay_amount = remaining + pen_out
    pen_part    = round(min(float(pay_amount), pen_out), 2)       # a payment clears the penalty first ...
    emi_part    = round(float(pay_amount) - pen_part, 2)          # ... and the rest goes to the EMI
    total_due   = remaining + (extra_interest or 0.0)
    pen_paid_new = round(float(emi["penalty_paid"] or 0.0) + pen_part, 2)
    # Guard against float drift (e.g. three partial payments summing to 2.8e-14 short of
    # total_due) so a fully-paid installment doesn't get stuck in "Partial" forever.
    if emi_part < total_due - 0.005:
        new_remaining = round(total_due - emi_part, 2)
        c.execute("UPDATE EMI SET amount_paid=?,remaining_amount=?,extra_interest=?,status=?,bill_number=?,penalty_paid=? WHERE emi_id=?",
                  (amount_paid+emi_part, new_remaining, extra_interest, "Partial", bill_number.strip(), pen_paid_new, emi_id))
        c.execute("INSERT INTO EMIPayments (emi_id,loan_id,amount,extra_interest,bill_number,paid_at,paid_by,penalty_part) VALUES (?,?,?,?,?,?,?,?)",
                  (emi_id, emi["loan_id"], pay_amount, extra_interest, bill_number.strip(), paid_at_val, paid_by, pen_part))
        get_db().commit()
        _settle_merged_penalties(emi_id)
        return f"Partial payment recorded. Remaining: {fmt_inr(new_remaining + max(0.0, pen_out - pen_part))}"
    else:
        c.execute("UPDATE EMI SET status='Paid',paid_at=?,amount_paid=?,remaining_amount=0,extra_interest=0,bill_number=?,penalty_paid=? WHERE emi_id=?",
                  (paid_at_val, amount_paid+emi_part, bill_number.strip(), pen_paid_new, emi_id))
        c.execute("INSERT INTO EMIPayments (emi_id,loan_id,amount,extra_interest,bill_number,paid_at,paid_by,penalty_part) VALUES (?,?,?,?,?,?,?,?)",
                  (emi_id, emi["loan_id"], pay_amount, extra_interest, bill_number.strip(), paid_at_val, paid_by, pen_part))
        get_db().commit()
        _settle_merged_penalties(emi_id)
        lid = emi["loan_id"]
        c.execute("SELECT COUNT(*) as total, SUM(CASE WHEN status='Paid' THEN 1 ELSE 0 END) as pc FROM EMI WHERE loan_id=?", (lid,))
        ct = c.fetchone()
        if ct["total"] > 0 and ct["pc"] == ct["total"]:
            # All EMIs are paid, but the loan is not closed directly: it goes to admin approval, then the
            # penalty / key & document return steps, then acknowledgement (see "Loan closing" below).
            start_closure(lid, "Regular")
            return "EMI paid successfully! All EMIs are paid, so the loan now needs closing approval."
        return "EMI paid successfully!"

# ── Payment acknowledgement (second-level cross-check) & late-payment penalties ──
PENALTY_FOLLOWUP_DAYS = 1      # penalty-collection follow-up falls due this many days after approval (collect within a day)

def pending_payment_for_emi(emi_id):
    c = get_cur()
    c.execute("SELECT * FROM PendingPayments WHERE emi_id=? AND status='Pending' ORDER BY pp_id DESC LIMIT 1", (emi_id,))
    r = c.fetchone()
    return dict(r) if r else None

def validate_payment(emi_id, amount, bill_number, paid_on, in_batch=()):
    """Same rules pay_emi enforces, checked up front so a bad payment is refused before it is queued."""
    c = get_cur(); c.execute("SELECT * FROM EMI WHERE emi_id=?", (emi_id,))
    emi = c.fetchone()
    if not emi: raise ValueError("EMI not found")
    emi = dict(emi)
    if emi["status"] == "Paid": raise ValueError("EMI already paid")
    if emi["status"] == "PreClosed": raise ValueError("This loan has been pre-closed.")
    if emi["status"] == "Seized": raise ValueError("The vehicle of this loan was seized, so EMI payments are closed.")
    if preclosure_in_progress(emi["loan_id"]):
        raise ValueError("A pre-closure is in progress for this loan, so EMI payments are paused. "
                         "Finish or reject the pre-closure first.")
    if pending_payment_for_emi(emi_id):
        raise ValueError("A payment for this installment is already awaiting acknowledgement.")
    if not can_pay_emi(emi["loan_id"], emi["installment_no"]):
        c.execute("SELECT emi_id FROM EMI WHERE loan_id=? AND installment_no<? AND status!='Paid'",
                  (emi["loan_id"], emi["installment_no"]))
        # allowed only when every earlier unpaid installment is part of this same bill batch (paid in order)
        if not in_batch or any(r["emi_id"] not in in_batch for r in c.fetchall()):
            raise ValueError(f"Cannot pay installment {emi['installment_no']}. Complete previous first "
                             f"(a payment awaiting acknowledgement counts as not yet paid).")
    if not (bill_number or "").strip(): raise ValueError("Bill number is mandatory before payment.")
    if not amount or float(amount) <= 0: raise ValueError("Enter a payment amount.")
    if paid_on:
        try: d = datetime.strptime(paid_on, "%Y-%m-%d").date()
        except ValueError: raise ValueError("Invalid Paid On date.")
        if d > date.today(): raise ValueError("Paid On date cannot be in the future.")
    return emi

def queue_payment(emi, amount, bill_number, paid_on, user, penalty_rate=None, receipt_id=None):
    c = get_cur()
    c.execute("""INSERT INTO PendingPayments (emi_id,loan_id,installment_no,amount,bill_number,paid_on,penalty_rate,
                 receipt_id,requested_by,requested_at,status) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
              (emi["emi_id"], emi["loan_id"], emi["installment_no"], float(amount), bill_number.strip(),
               paid_on or date.today().isoformat(), float(penalty_rate or 0) or None, receipt_id, user,
               datetime.now(timezone.utc).isoformat(), "Pending"))
    get_db().commit()
    return c.lastrowid

def submit_payment(emi_id, amount, bill_number, paid_on, user, role, penalty_rate=None):
    """Admin / Super Admin payments are applied straight away. Everyone else's payment is held back as
    'awaiting acknowledgement' until an Account Manager (or an admin) cross-checks and confirms it."""
    emi = validate_payment(emi_id, amount, bill_number, paid_on)
    if role in DIRECT_ROLES:
        msg = pay_emi(emi_id, float(amount), bill_number=bill_number, paid_on=paid_on or None, paid_by=user)
        pen = create_penalty_if_needed(emi_id, penalty_rate, user)
        return msg + ((" " + pen) if pen else "")
    queue_payment(emi, amount, bill_number, paid_on, user, penalty_rate)
    return "Payment sent for acknowledgement. It will be recorded on the EMI once it is acknowledged."

def acknowledge_payment(pp_id, username):
    c = get_cur(); c.execute("SELECT * FROM PendingPayments WHERE pp_id=?", (pp_id,))
    pp = c.fetchone()
    if not pp or pp["status"] != "Pending": raise ValueError("This payment is no longer awaiting acknowledgement.")
    if pp["requested_by"] == username: raise ValueError("You cannot acknowledge your own entry.")
    c.execute("""SELECT installment_no FROM PendingPayments WHERE loan_id=? AND status='Pending' AND installment_no<?
                 ORDER BY installment_no LIMIT 1""", (pp["loan_id"], pp["installment_no"]))
    earlier = c.fetchone()
    if earlier:
        raise ValueError(f"Acknowledge the earlier installment first ({ordinal_due(earlier['installment_no'])}): "
                         f"an EMI cannot be closed while the one before it is still open.")
    msg = pay_emi(pp["emi_id"], float(pp["amount"]), bill_number=pp["bill_number"], paid_on=pp["paid_on"],
                  paid_by=pp["requested_by"])
    c = get_cur()
    c.execute("UPDATE PendingPayments SET status='Acknowledged', decided_by=?, decided_at=? WHERE pp_id=?",
              (username, datetime.now(timezone.utc).isoformat(), pp_id))
    if pp["receipt_id"]:
        c.execute("SELECT 1 FROM PendingPayments WHERE receipt_id=? AND status='Pending'", (pp["receipt_id"],))
        if not c.fetchone():
            c.execute("UPDATE Receipts SET recorded_on_emi=1 WHERE receipt_id=?", (pp["receipt_id"],))
    get_db().commit()
    pen = create_penalty_if_needed(pp["emi_id"], pp["penalty_rate"], pp["requested_by"])
    return msg + ((" " + pen) if pen else "")

def reject_payment(pp_id, reason, username):
    c = get_cur(); c.execute("SELECT * FROM PendingPayments WHERE pp_id=?", (pp_id,))
    pp = c.fetchone()
    if not pp or pp["status"] != "Pending": raise ValueError("This payment is no longer awaiting acknowledgement.")
    c.execute("""UPDATE PendingPayments SET status='Rejected', decided_by=?, decided_at=?, decision_remarks=?
                 WHERE pp_id=?""", (username, datetime.now(timezone.utc).isoformat(), (reason or "").strip() or "No reason given", pp_id))
    if pp["receipt_id"]:
        c.execute("UPDATE Receipts SET recorded_on_emi=3 WHERE receipt_id=?", (pp["receipt_id"],))
        # later installments of the same bill batch cannot be recorded without this one: reject them too
        c.execute("SELECT batch_id FROM Receipts WHERE receipt_id=?", (pp["receipt_id"],))
        bt = c.fetchone()
        if bt and bt["batch_id"]:
            c.execute("""SELECT p.pp_id, p.receipt_id FROM PendingPayments p JOIN Receipts r ON r.receipt_id=p.receipt_id
                         WHERE p.loan_id=? AND p.status='Pending' AND p.installment_no>? AND r.batch_id=?""",
                      (pp["loan_id"], pp["installment_no"], bt["batch_id"]))
            for later in [dict(x) for x in c.fetchall()]:
                c.execute("""UPDATE PendingPayments SET status='Rejected', decided_by=?, decided_at=?, decision_remarks=?
                             WHERE pp_id=?""", (username, datetime.now(timezone.utc).isoformat(),
                                                "Earlier installment of the same bill was rejected", later["pp_id"]))
                c.execute("UPDATE Receipts SET recorded_on_emi=3 WHERE receipt_id=?", (later["receipt_id"],))
    get_db().commit()

def create_penalty_if_needed(emi_id, rate, requested_by):
    """After a payment: if the installment has crossed half of its EMI late (point 22 rule) and a per-day
    rate was given, raise a penalty (days x rate) for admin approval. Returns a short message or ''."""
    try: rate = float(rate or 0)
    except (TypeError, ValueError): return ""
    if rate <= 0: return ""
    c = get_cur(); c.execute("SELECT * FROM EMI WHERE emi_id=?", (emi_id,))
    emi = c.fetchone()
    if not emi: return ""
    emi = dict(emi)
    payments = get_payments_for_emi(emi_id)
    days = late_payment_days(emi, payments, emi["status"] == "Paid")
    if not days:
        return "No penalty raised: the payment is not late, or the installment is not yet more than half paid."
    c.execute("SELECT 1 FROM Penalties WHERE emi_id=? AND status!='Rejected'", (emi_id,))
    if c.fetchone(): return "A penalty already exists for this installment."
    amount = round(days * rate, 2)
    c.execute("""INSERT INTO Penalties (loan_id,emi_id,installment_no,days,half_paid_date,requested_rate,requested_amount,
                 requested_by,requested_at,status) VALUES (?,?,?,?,?,?,?,?,?,?)""",
              (emi["loan_id"], emi_id, emi["installment_no"], days, half_paid_date(emi, payments), rate, amount,
               requested_by, datetime.now(timezone.utc).isoformat(), "Pending"))
    get_db().commit()
    return f"Penalty of {fmt_inr(amount)} ({days} days x {fmt_inr(rate)}/day) sent for admin approval."

def approve_penalty(penalty_id, rate, username):
    c = get_cur(); c.execute("SELECT * FROM Penalties WHERE penalty_id=?", (penalty_id,))
    pen = c.fetchone()
    if not pen or pen["status"] != "Pending": raise ValueError("This penalty is not pending.")
    try: rate = float(rate)
    except (TypeError, ValueError): raise ValueError("Enter a valid per-day penalty amount.")
    if rate < 0: raise ValueError("Penalty per day cannot be negative.")
    final = round(int(pen["days"]) * rate, 2)
    now = datetime.now(timezone.utc).isoformat()
    if final <= 0:
        c.execute("""UPDATE Penalties SET status='Waived', final_rate=0, final_amount=0, decided_by=?, decided_at=?
                     WHERE penalty_id=?""", (username, now, penalty_id))
        get_db().commit(); closure_advance(pen["loan_id"]); return 0.0
    c.execute("""SELECT emi_id FROM EMI WHERE loan_id=? AND status NOT IN ('Paid','PreClosed','Seized')
                 ORDER BY installment_no LIMIT 1""", (pen["loan_id"],))
    tgt = c.fetchone()
    if tgt:
        # EMIs are still left: the penalty is added to the next unpaid EMI and collected with it (no follow-up)
        c.execute("UPDATE EMI SET penalty_due=COALESCE(penalty_due,0)+? WHERE emi_id=?", (final, tgt["emi_id"]))
        c.execute("""UPDATE Penalties SET status='Approved', final_rate=?, final_amount=?, decided_by=?, decided_at=?,
                     merged_emi_id=? WHERE penalty_id=?""", (rate, final, username, now, tgt["emi_id"], penalty_id))
        get_db().commit()
        return final
    c.execute("SELECT loan_number FROM LoanEntry WHERE id=?", (pen["loan_id"],))
    loan_no = c.fetchone()["loan_number"]
    fu_id = add_follow_up(pen["loan_id"], (date.today() + timedelta(days=PENALTY_FOLLOWUP_DAYS)).isoformat(),
                          f"Collect late-payment penalty {fmt_inr(final)} ({pen['days']} days x {fmt_inr(rate)}/day) "
                          f"for installment {pen['installment_no']}",
                          username, "Penalty Collection", "penalty", ref_id=penalty_id)
    c = get_cur()
    c.execute("""UPDATE Penalties SET status='Approved', final_rate=?, final_amount=?, decided_by=?, decided_at=?,
                 followup_id=? WHERE penalty_id=?""", (rate, final, username, now, fu_id, penalty_id))
    get_db().commit()
    return final

def reject_penalty(penalty_id, reason, username):
    c = get_cur(); c.execute("SELECT status, loan_id FROM Penalties WHERE penalty_id=?", (penalty_id,))
    pen = c.fetchone()
    if not pen or pen["status"] != "Pending": raise ValueError("This penalty is not pending.")
    c.execute("""UPDATE Penalties SET status='Rejected', decided_by=?, decided_at=?, decision_remarks=?
                 WHERE penalty_id=?""", (username, datetime.now(timezone.utc).isoformat(), (reason or "").strip() or "No reason given", penalty_id))
    get_db().commit()
    closure_advance(pen["loan_id"])

def get_penalties_by_emi(loan_id):
    """Latest non-rejected penalty per installment of a loan (emi_id -> row)."""
    c = get_cur()
    c.execute("SELECT * FROM Penalties WHERE loan_id=? AND status!='Rejected' ORDER BY penalty_id ASC", (loan_id,))
    return {r["emi_id"]: dict(r) for r in c.fetchall()}

# ── Loan closing: admin approval -> penalty collection -> key/document return -> acknowledgement ──
CLOSURE_ITEM_INFO = {"key": ("🔑", "Key returned"), "rc": ("📄", "RC returned"),
                     "docs": ("🗂️", "Proof & Documents returned"), "noc": ("✅", "NOC provided"),
                     "cheque": ("🧾", "Cheque leaf returned"), "tc": ("📑", "Transfer certificate returned")}
CLOSURE_ITEM_ORDER = ["key", "rc", "tc", "docs", "cheque", "noc"]
CLOSURE_STAGE_TEXT = {"AwaitApproval": "Waiting for admin approval",
                      "Penalty": "Penalty to be collected (within a day)",
                      "Return": "Return of key & documents",
                      "AckPending": "Waiting for acknowledgement",
                      "Closed": "Closed"}

def closure_required_items(loan):
    """Items to hand back, vehicle by vehicle: what was collected for that vehicle at the start, plus its NOC.
    Returns dicts {item, vehicle_id, vtag, vseq}; vtag is empty when the loan has a single vehicle."""
    vs = loan_vehicles(loan)
    multi = len(vs) > 1
    items = []
    for v in vs:
        tag = vehicle_tag(v) if multi else ""
        wanted = [k for k, flag in (("key", "key_received"), ("rc", "rc_received"), ("tc", "tc_received"), ("docs", "docs_received"))
                  if v.get(flag) == "yes"] + ["noc"]
        for k in wanted:
            items.append({"item": k, "vehicle_id": v["vehicle_id"], "vtag": tag, "vseq": v.get("seq") or 1})
    if loan.get("cheque_received") == "yes":     # the cheque leaf belongs to the loan, not to one vehicle
        items.insert(0, {"item": "cheque", "vehicle_id": None, "vtag": "", "vseq": 0})
    return items

def closure_item_title(i):
    """'🔑 Key returned' (plus '— Activa · TN01AB1234' when the loan has several vehicles)."""
    icon, label = CLOSURE_ITEM_INFO[i["item"]]
    tag = (f' <span style="color:var(--muted);font-weight:500;">— {html.escape(i["vtag"])}</span>' if i.get("vtag") else "")
    return f"{icon} {label}{tag}"

def get_closure(loan_id):
    c = get_cur()
    c.execute("SELECT * FROM LoanClosure WHERE loan_id=? ORDER BY closure_id DESC LIMIT 1", (loan_id,))
    r = c.fetchone()
    return dict(r) if r else None

def get_open_closure(loan_id):
    cl = get_closure(loan_id)
    return cl if cl and cl["status"] != "Closed" else None

def closure_items(closure_id):
    c = get_cur()
    c.execute("SELECT * FROM ClosureItems WHERE closure_id=?", (closure_id,))
    rows = [dict(r) for r in c.fetchall()]
    if rows:
        c.execute("SELECT * FROM LoanEntry WHERE id=?", (rows[0]["loan_id"],))
        loan = c.fetchone()
        vs = loan_vehicles(dict(loan)) if loan else []
        info = {v["vehicle_id"]: (v.get("seq") or 1, vehicle_tag(v) if len(vs) > 1 else "") for v in vs}
        for r in rows:
            r["vseq"], r["vtag"] = info.get(r.get("vehicle_id"), (99, ""))
            if r["item"] == "cheque": r["vseq"], r["vtag"] = 0, ""
    return sorted(rows, key=lambda r: (r.get("vseq", 1),
                                       CLOSURE_ITEM_ORDER.index(r["item"]) if r["item"] in CLOSURE_ITEM_ORDER else 99))

def start_closure(loan_id, kind="Regular"):
    """Opens the closing process. Regular closures start with admin approval; a pre-closure was already
    approved, so it goes straight to the key / document return."""
    existing = get_open_closure(loan_id)
    if existing: return existing["closure_id"]
    c = get_cur(); c.execute("SELECT * FROM LoanEntry WHERE id=?", (loan_id,))
    loan = c.fetchone()
    if not loan: raise ValueError("Loan not found")
    loan = dict(loan)
    stage = "AwaitApproval" if kind == "Regular" else "Return"
    c.execute("INSERT INTO LoanClosure (loan_id,kind,status,created_at) VALUES (?,?,?,?)",
              (loan_id, kind, stage, datetime.now(timezone.utc).isoformat()))
    cid = c.lastrowid
    for it in closure_required_items(loan):
        c.execute("INSERT INTO ClosureItems (closure_id,loan_id,item,status,vehicle_id) VALUES (?,?,?,?,?)",
                  (cid, loan_id, it["item"], "Pending", it["vehicle_id"]))
    get_db().commit()
    return cid

def closure_advance(loan_id):
    """Penalty stage -> return stage once every penalty of the loan is collected / waived."""
    cl = get_open_closure(loan_id)
    if not cl or cl["status"] != "Penalty": return
    c = get_cur()
    c.execute("SELECT COUNT(*) as n FROM Penalties WHERE loan_id=? AND status IN ('Pending','Approved')", (loan_id,))
    if c.fetchone()["n"] == 0:
        c.execute("UPDATE LoanClosure SET status='Return' WHERE closure_id=?", (cl["closure_id"],))
        get_db().commit()

def approve_closure(closure_id, rates, username):
    """Admin approval of the closing. `rates` maps penalty_id -> final per-day rate for penalties still pending."""
    c = get_cur(); c.execute("SELECT * FROM LoanClosure WHERE closure_id=?", (closure_id,))
    cl = c.fetchone()
    if not cl or cl["status"] != "AwaitApproval": raise ValueError("This closing request is not waiting for approval.")
    c.execute("SELECT * FROM Penalties WHERE loan_id=? AND status='Pending' ORDER BY penalty_id", (cl["loan_id"],))
    pend = [dict(r) for r in c.fetchall()]
    chosen = {}
    for p in pend:   # validate everything before changing anything
        raw = rates.get(p["penalty_id"])
        try: val = float(raw) if raw not in (None, "") else float(p["requested_rate"])
        except (TypeError, ValueError): raise ValueError("Enter a valid per-day penalty amount.")
        if val < 0: raise ValueError("Penalty per day cannot be negative.")
        chosen[p["penalty_id"]] = val
    for p in pend:
        approve_penalty(p["penalty_id"], chosen[p["penalty_id"]], username)
    c = get_cur()
    c.execute("UPDATE LoanClosure SET status='Penalty', approved_by=?, approved_at=? WHERE closure_id=?",
              (username, datetime.now(timezone.utc).isoformat(), closure_id))
    get_db().commit()
    closure_advance(cl["loan_id"])

def record_closure_item(item_id, returned_on, handed_by, note, username):
    c = get_cur()
    c.execute("""SELECT i.*, cl.status as cstatus FROM ClosureItems i JOIN LoanClosure cl ON cl.closure_id=i.closure_id
                 WHERE i.item_id=?""", (item_id,))
    it = c.fetchone()
    if not it: raise ValueError("Item not found.")
    if it["cstatus"] != "Return": raise ValueError("Key and documents can be recorded only in the return step.")
    if not (handed_by or "").strip(): raise ValueError("Enter who handed it over.")
    try: d = datetime.strptime((returned_on or "").strip(), "%Y-%m-%d").date()
    except ValueError: raise ValueError("Enter a valid date.")
    if d > date.today(): raise ValueError("Date cannot be in the future.")
    c.execute("""UPDATE ClosureItems SET status='Returned', returned_on=?, handed_by=?, note=?, recorded_by=?, recorded_at=?
                 WHERE item_id=?""", (d.isoformat(), handed_by.strip(), (note or "").strip(), username,
                                      datetime.now(timezone.utc).isoformat(), item_id))
    get_db().commit()

def request_closure_ack(closure_id, username):
    c = get_cur(); c.execute("SELECT * FROM LoanClosure WHERE closure_id=?", (closure_id,))
    cl = c.fetchone()
    if not cl or cl["status"] != "Return": raise ValueError("The closing is not in the return step.")
    if any(i["status"] != "Returned" for i in closure_items(closure_id)):
        raise ValueError("Record every item as returned before sending for acknowledgement.")
    c.execute("""UPDATE LoanClosure SET status='AckPending', ack_requested_by=?, ack_requested_at=?, ack_note=NULL
                 WHERE closure_id=?""", (username, datetime.now(timezone.utc).isoformat(), closure_id))
    get_db().commit()

def finalize_closure(closure_id, username):
    c = get_cur(); c.execute("SELECT * FROM LoanClosure WHERE closure_id=?", (closure_id,))
    cl = dict(c.fetchone()); lid = cl["loan_id"]
    now = datetime.now(timezone.utc).isoformat()
    c.execute("UPDATE LoanClosure SET status='Closed', acked_by=?, acked_at=?, closed_at=? WHERE closure_id=?",
              (username, now, now, closure_id))
    c.execute("UPDATE LoanEntry SET status='Closed' WHERE id=?", (lid,))
    c.execute("UPDATE Customers SET status='Closed' WHERE loan_id=?", (lid,))
    c.execute("INSERT OR REPLACE INTO ClosedLoans (loan_id,closure_date,created_at) VALUES (?,?,?)",
              (lid, date.today().isoformat(), now))
    c.execute("""UPDATE FollowUp SET status='Resolved', resolved_at=?
                 WHERE loan_id=? AND status='Pending' AND COALESCE(category,'Loans')='Loans'""", (now, lid))
    get_db().commit()
    if cl["kind"] == "Regular": _notify_closure(lid)

def acknowledge_closure(closure_id, username):
    c = get_cur(); c.execute("SELECT * FROM LoanClosure WHERE closure_id=?", (closure_id,))
    cl = c.fetchone()
    if not cl or cl["status"] != "AckPending": raise ValueError("This closing is not waiting for acknowledgement.")
    recorders = {i["recorded_by"] for i in closure_items(closure_id)} | {cl["ack_requested_by"]}
    if username in recorders: raise ValueError("You recorded this hand-over, so someone else must acknowledge it.")
    finalize_closure(closure_id, username)

def reject_closure_ack(closure_id, reason, username):
    c = get_cur(); c.execute("SELECT status FROM LoanClosure WHERE closure_id=?", (closure_id,))
    cl = c.fetchone()
    if not cl or cl["status"] != "AckPending": raise ValueError("This closing is not waiting for acknowledgement.")
    c.execute("UPDATE LoanClosure SET status='Return', ack_note=? WHERE closure_id=?",
              (f"Not acknowledged by {username}: {(reason or '').strip() or 'no reason given'}", closure_id))
    get_db().commit()

# ── Settings (small key/value store) ───────────────────────────────────────────
def get_setting(key, default=None):
    c = get_cur(); c.execute("SELECT value FROM Settings WHERE key=?", (key,))
    r = c.fetchone()
    return r["value"] if r and r["value"] is not None else default

def set_setting(key, value):
    c = get_cur()
    c.execute("INSERT INTO Settings (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=?",
              (key, str(value), str(value)))
    get_db().commit()

# ── Vehicle seizure: request -> admin approval -> key / RC received -> Account Manager acknowledgement ──
SEIZURE_DEFAULT_MIN = 3        # the "Vehicle Seized" option appears from this many overdue EMIs (Super Admin can change it)
SEIZURE_ITEM_INFO = {"key": ("🔑", "Key received"), "rc": ("📄", "RC received")}
SEIZURE_LIVE = ("Pending", "Return", "AckPending", "Seized")
SEIZURE_STAGE_TEXT = {"Pending": "Waiting for admin approval",
                      "Return": "Key and RC to be received and recorded",
                      "AckPending": "Waiting for Account Manager acknowledgement",
                      "Seized": "Seizure completed"}

def seizure_threshold():
    try: return max(1, int(get_setting("seizure_min_overdue", SEIZURE_DEFAULT_MIN)))
    except (TypeError, ValueError): return SEIZURE_DEFAULT_MIN

def loan_vehicle_tags(loan_id):
    """vehicle_id -> (seq, tag); tag is empty when the loan has a single vehicle (nothing to tell apart)."""
    c = get_cur(); c.execute("SELECT * FROM LoanEntry WHERE id=?", (loan_id,))
    loan = c.fetchone()
    vs = loan_vehicles(dict(loan)) if loan else []
    return {v["vehicle_id"]: (v.get("seq") or 1, vehicle_tag(v) if len(vs) > 1 else "") for v in vs}

def overdue_emis(loan_id):
    """Unpaid EMIs whose due date has passed, oldest first."""
    c = get_cur()
    c.execute("""SELECT * FROM EMI WHERE loan_id=? AND status IN ('Pending','Partial','Overdue') AND due_date < ?
                 ORDER BY installment_no""", (loan_id, date.today().isoformat()))
    return [dict(r) for r in c.fetchall()]

def get_last_seizure(loan_id):
    c = get_cur(); c.execute("SELECT * FROM Seizures WHERE loan_id=? ORDER BY seizure_id DESC LIMIT 1", (loan_id,))
    r = c.fetchone()
    return dict(r) if r else None

def get_open_seizure(loan_id):
    sz = get_last_seizure(loan_id)
    return sz if sz and sz["status"] in SEIZURE_LIVE else None

def seizure_items(seizure_id):
    c = get_cur(); c.execute("SELECT * FROM SeizureItems WHERE seizure_id=?", (seizure_id,))
    rows = [dict(r) for r in c.fetchall()]
    if rows:
        tags = loan_vehicle_tags(rows[0]["loan_id"])
        for r in rows:
            r["vseq"], r["vtag"] = tags.get(r.get("vehicle_id"), (99, ""))
    return sorted(rows, key=lambda r: (r.get("vseq", 1), 0 if r["item"] == "key" else 1))

def seizure_item_title(i):
    icon, label = SEIZURE_ITEM_INFO[i["item"]]
    tag = (f' <span style="color:var(--muted);font-weight:500;">— {html.escape(i["vtag"])}</span>' if i.get("vtag") else "")
    return f"{icon} {label}{tag}"

def _held_payments(loan_id):
    c = get_cur(); c.execute("SELECT COUNT(*) as n FROM PendingPayments WHERE loan_id=? AND status='Pending'", (loan_id,))
    return c.fetchone()["n"]

def seizure_blockers(loan, check_count=True):
    """Why a seizure cannot be raised / approved for this loan right now ('' when it can)."""
    lid = loan["id"]
    if loan["status"] != "Approved": return "Only active (approved) loans can be marked as seized."
    if get_open_closure(lid): return "This loan is already being closed."
    if preclosure_in_progress(lid): return "A pre-closure is in progress for this loan."
    if _held_payments(lid): return "A payment of this loan is awaiting acknowledgement. Acknowledge or reject it first."
    if check_count:
        need, have = seizure_threshold(), len(overdue_emis(lid))
        if have < need: return f"A seizure needs at least {need} overdue EMIs; this loan has {have}."
    return ""

def request_seizure(loan_id, reason, seized_date, place, writeoff_reason, username):
    c = get_cur(); c.execute("SELECT * FROM LoanEntry WHERE id=?", (loan_id,))
    loan = c.fetchone()
    if not loan: raise ValueError("Loan not found.")
    loan = dict(loan)
    if get_open_seizure(loan_id): raise ValueError("A seizure is already open for this loan.")
    err = seizure_blockers(loan)
    if err: raise ValueError(err)
    reason, place, writeoff_reason = (reason or "").strip(), (place or "").strip(), (writeoff_reason or "").strip()
    if not reason: raise ValueError("Enter the reason for the seizure.")
    if not place: raise ValueError("Enter where the vehicle is kept.")
    if not writeoff_reason: raise ValueError("Enter the reason for writing off the outstanding amount.")
    try: d = datetime.strptime((seized_date or "").strip(), "%Y-%m-%d").date()
    except ValueError: raise ValueError("Enter a valid seized date.")
    if d > date.today(): raise ValueError("Seized date cannot be in the future.")
    c.execute("""INSERT INTO Seizures (loan_id,status,reason,seized_date,place,writeoff_reason,overdue_count,requested_by,requested_at)
                 VALUES (?,?,?,?,?,?,?,?,?)""",
              (loan_id, "Pending", reason, d.isoformat(), place, writeoff_reason, len(overdue_emis(loan_id)), username,
               datetime.now(timezone.utc).isoformat()))
    get_db().commit()
    return c.lastrowid

def approve_seizure(seizure_id, writeoff_reason, username):
    """Admin approval: every unpaid EMI is closed as 'Seized', the outstanding amount is written off,
    pending penalties are waived and the key / RC receipt rows (per vehicle) are opened."""
    c = get_cur(); c.execute("SELECT * FROM Seizures WHERE seizure_id=?", (seizure_id,))
    sz = c.fetchone()
    if not sz or sz["status"] != "Pending": raise ValueError("This seizure request is not waiting for approval.")
    sz = dict(sz); lid = sz["loan_id"]
    c.execute("SELECT * FROM LoanEntry WHERE id=?", (lid,))
    loan = dict(c.fetchone())
    err = seizure_blockers(loan, check_count=False)
    if err: raise ValueError(err)
    now = datetime.now(timezone.utc).isoformat()
    c.execute("SELECT * FROM EMI WHERE loan_id=? AND status NOT IN ('Paid','PreClosed','Seized')", (lid,))
    unpaid = [dict(r) for r in c.fetchall()]
    c.execute("SELECT penalty_id, status, merged_emi_id FROM Penalties WHERE loan_id=? AND status IN ('Pending','Approved')", (lid,))
    pens = [dict(r) for r in c.fetchall()]
    written_off = round(sum(float(e["remaining_amount"] if e["remaining_amount"] is not None else e["emi_amount"]) + emi_penalty_out(e)
                            for e in unpaid), 2)
    snap = {"emis": [{"emi_id": e["emi_id"], "status": e["status"]} for e in unpaid], "penalties": pens,
            "customer_status": None}
    c.execute("SELECT status FROM Customers WHERE loan_id=?", (lid,)); cu = c.fetchone()
    snap["customer_status"] = cu["status"] if cu else None
    for e in unpaid:
        c.execute("UPDATE EMI SET status='Seized' WHERE emi_id=?", (e["emi_id"],))
    for p in pens:     # a penalty already added to an EMI is written off with it; the others are waived
        c.execute("UPDATE Penalties SET status=?, decided_by=?, decided_at=?, decision_remarks=? WHERE penalty_id=?",
                  ("WrittenOff" if p.get("merged_emi_id") else "Waived", username, now,
                   "Written off: vehicle seized" if p.get("merged_emi_id") else "Waived: vehicle seized", p["penalty_id"]))
    c.execute("""UPDATE FollowUp SET status='Resolved', resolved_at=? WHERE loan_id=? AND status IN ('Pending','AwaitingAck')""", (now, lid))
    c.execute("UPDATE LoanEntry SET status='Seized' WHERE id=?", (lid,))
    c.execute("UPDATE Customers SET status='Seized' WHERE loan_id=?", (lid,))
    wr = (writeoff_reason or "").strip() or sz["writeoff_reason"]
    c.execute("""UPDATE Seizures SET status='Return', written_off=?, writeoff_reason=?, snapshot=?, approved_by=?, approved_at=?
                 WHERE seizure_id=?""", (written_off, wr, json.dumps(snap), username, now, seizure_id))
    for v in loan_vehicles(loan):
        for item in ("key", "rc"):
            c.execute("INSERT INTO SeizureItems (seizure_id,loan_id,vehicle_id,item,status) VALUES (?,?,?,?,?)",
                      (seizure_id, lid, v["vehicle_id"], item, "Pending"))
    get_db().commit()
    return written_off

def reject_seizure(seizure_id, reason, username):
    c = get_cur(); c.execute("SELECT status FROM Seizures WHERE seizure_id=?", (seizure_id,))
    sz = c.fetchone()
    if not sz or sz["status"] != "Pending": raise ValueError("This seizure request is not waiting for approval.")
    c.execute("UPDATE Seizures SET status='Rejected', approved_by=?, approved_at=?, decision_remarks=? WHERE seizure_id=?",
              (username, datetime.now(timezone.utc).isoformat(), (reason or "").strip() or "No reason given", seizure_id))
    get_db().commit()

def record_seizure_item(item_id, received_on, received_by, note, username):
    c = get_cur()
    c.execute("""SELECT i.*, s.status as sstatus FROM SeizureItems i JOIN Seizures s ON s.seizure_id=i.seizure_id
                 WHERE i.item_id=?""", (item_id,))
    it = c.fetchone()
    if not it: raise ValueError("Item not found.")
    if it["sstatus"] != "Return": raise ValueError("Key and RC can be recorded only after the seizure is approved (and before it is sent for acknowledgement).")
    if not (received_by or "").strip(): raise ValueError("Enter who received it.")
    try: d = datetime.strptime((received_on or "").strip(), "%Y-%m-%d").date()
    except ValueError: raise ValueError("Enter a valid date.")
    if d > date.today(): raise ValueError("Date cannot be in the future.")
    c.execute("""UPDATE SeizureItems SET status='Received', received_on=?, received_by=?, note=?, recorded_by=?, recorded_at=?
                 WHERE item_id=?""", (d.isoformat(), received_by.strip(), (note or "").strip(), username,
                                      datetime.now(timezone.utc).isoformat(), item_id))
    get_db().commit()

def request_seizure_ack(seizure_id, username):
    c = get_cur(); c.execute("SELECT * FROM Seizures WHERE seizure_id=?", (seizure_id,))
    sz = c.fetchone()
    if not sz or sz["status"] != "Return": raise ValueError("The seizure is not in the key / RC step.")
    if any(i["status"] != "Received" for i in seizure_items(seizure_id)):
        raise ValueError("Record the key and RC of every vehicle before sending for acknowledgement.")
    c.execute("UPDATE Seizures SET status='AckPending', ack_requested_by=?, ack_requested_at=?, ack_note=NULL WHERE seizure_id=?",
              (username, datetime.now(timezone.utc).isoformat(), seizure_id))
    get_db().commit()

def acknowledge_seizure(seizure_id, username, details_checked, legal_checked, legal_note=""):
    c = get_cur(); c.execute("SELECT * FROM Seizures WHERE seizure_id=?", (seizure_id,))
    sz = c.fetchone()
    if not sz or sz["status"] != "AckPending": raise ValueError("This seizure is not waiting for acknowledgement.")
    sz = dict(sz)
    recorders = {i["recorded_by"] for i in seizure_items(seizure_id)} | {sz["ack_requested_by"]}
    if username in recorders: raise ValueError("You recorded this, so someone else must acknowledge it.")
    if not details_checked: raise ValueError("Tick 'Seizure details checked' before acknowledging.")
    if not legal_checked: raise ValueError("Tick 'Legal issues checked' before acknowledging.")
    now = datetime.now(timezone.utc).isoformat()
    lid = sz["loan_id"]
    c.execute("""UPDATE Seizures SET status='Seized', acked_by=?, acked_at=?, closed_at=?, details_checked=1, legal_checked=1,
                 legal_note=? WHERE seizure_id=?""", (username, now, now, (legal_note or "").strip() or None, seizure_id))
    c.execute("INSERT OR REPLACE INTO ClosedLoans (loan_id,closure_date,created_at) VALUES (?,?,?)",
              (lid, date.today().isoformat(), now))
    get_db().commit()

def reject_seizure_ack(seizure_id, reason, username):
    c = get_cur(); c.execute("SELECT status FROM Seizures WHERE seizure_id=?", (seizure_id,))
    sz = c.fetchone()
    if not sz or sz["status"] != "AckPending": raise ValueError("This seizure is not waiting for acknowledgement.")
    c.execute("UPDATE Seizures SET status='Return', ack_note=? WHERE seizure_id=?",
              (f"Not acknowledged by {username}: {(reason or '').strip() or 'no reason given'}", seizure_id))
    get_db().commit()

def reopen_seizure(seizure_id, reason, username):
    """Admin only: the customer has paid / settled, so the seizure is undone. EMIs go back to their earlier
    status, the write-off is reversed and penalties that were waived return for approval."""
    c = get_cur(); c.execute("SELECT * FROM Seizures WHERE seizure_id=?", (seizure_id,))
    sz = c.fetchone()
    if not sz or sz["status"] not in ("Return", "AckPending", "Seized"): raise ValueError("This seizure cannot be reopened.")
    if not (reason or "").strip(): raise ValueError("Enter the reason for reopening the loan.")
    sz = dict(sz); lid = sz["loan_id"]
    try: snap = json.loads(sz["snapshot"] or "{}")
    except ValueError: snap = {}
    for e in snap.get("emis", []):
        c.execute("UPDATE EMI SET status=? WHERE emi_id=? AND status='Seized'", (e["status"], e["emi_id"]))
    for p in snap.get("penalties", []):
        if p.get("merged_emi_id"):      # was added to an EMI: it simply becomes collectable again with that EMI
            c.execute("UPDATE Penalties SET status='Approved' WHERE penalty_id=? AND status='WrittenOff'", (p["penalty_id"],))
        else:
            c.execute("UPDATE Penalties SET status='Pending', decided_by=NULL, decided_at=NULL, decision_remarks=NULL, followup_id=NULL "
                      "WHERE penalty_id=? AND status='Waived'", (p["penalty_id"],))
    c.execute("UPDATE LoanEntry SET status='Approved' WHERE id=?", (lid,))
    c.execute("UPDATE Customers SET status=? WHERE loan_id=?", (snap.get("customer_status") or "Active", lid))
    c.execute("DELETE FROM ClosedLoans WHERE loan_id=?", (lid,))
    c.execute("UPDATE Seizures SET status='Reopened', reopened_by=?, reopened_at=?, reopen_reason=? WHERE seizure_id=?",
              (username, datetime.now(timezone.utc).isoformat(), reason.strip(), seizure_id))
    get_db().commit()

# ── Query helpers ──────────────────────────────────────────────────────────────
def list_pending_loans(search=""):
    q=f"%{search}%"; c=get_cur()
    c.execute("SELECT * FROM LoanEntry WHERE status='PendingApproval' AND (loan_number LIKE ? OR customer_name LIKE ?) ORDER BY created_at DESC",(q,q))
    return [dict(r) for r in c.fetchall()]

def list_all_loans(search=""):
    q=f"%{search}%"; c=get_cur()
    c.execute("SELECT * FROM LoanEntry WHERE loan_number LIKE ? OR customer_name LIKE ? OR vehicle_type LIKE ? OR status LIKE ? ORDER BY created_at DESC",(q,q,q,q))
    return [dict(r) for r in c.fetchall()]

def list_customers(search=""):
    q=f"%{search}%"; c=get_cur()
    c.execute("""SELECT cu.*, le.loan_number, le.customer_name, le.customer_mobile, le.customer_extra_numbers,
                        le.vehicle_name, le.vehicle_model, le.vehicle_number, le.vehicle_colour,
                        le.guarantor_name, le.guarantor_mobile, le.guarantor_extra_numbers
                 FROM Customers cu
                 LEFT JOIN LoanEntry le ON le.id = cu.loan_id
                 WHERE cu.name LIKE ? OR cu.vehicle_type LIKE ? OR cu.status LIKE ? OR le.loan_number LIKE ?
                       OR le.customer_mobile LIKE ? OR le.customer_extra_numbers LIKE ?
                       OR le.guarantor_mobile LIKE ? OR le.guarantor_extra_numbers LIKE ?
                 ORDER BY cu.created_at DESC""",(q,q,q,q,q,q,q,q))
    return [dict(r) for r in c.fetchall()]

def get_emis_for_loan(loan_id):
    c=get_cur(); c.execute("SELECT * FROM EMI WHERE loan_id=? ORDER BY installment_no ASC",(loan_id,))
    return [dict(r) for r in c.fetchall()]

def get_payments_for_emi(emi_id):
    c=get_cur(); c.execute("SELECT * FROM EMIPayments WHERE emi_id=? ORDER BY payment_id ASC",(emi_id,))
    return [dict(r) for r in c.fetchall()]

def get_payments_by_emi_for_loan(loan_id):
    """All payments for a loan, grouped by emi_id, in payment order — used to build the
    X.1 / X.2 / X.3 per-installment payment reference trail."""
    c=get_cur(); c.execute("SELECT * FROM EMIPayments WHERE loan_id=? ORDER BY payment_id ASC",(loan_id,))
    grouped = {}
    for r in c.fetchall():
        r = dict(r)
        grouped.setdefault(r["emi_id"], []).append(r)
    return grouped

def delay_badge(due_s, paid_s):
    """On time (paid on/before the EMI due date) or 'N days late', coloured green / red."""
    d = (parse_date(paid_s) - parse_date(due_s)).days
    if d <= 0:
        return '<span style="color:var(--green);font-weight:600;">✅ On time</span>'
    return f'<span style="color:var(--red);font-weight:600;">⏰ {d} day{"s" if d != 1 else ""} late</span>'

def half_paid_date(e, payments):
    """Date (YYYY-MM-DD) on which the payments for this installment first add up to MORE than half
    of the EMI amount; this is the date used to work out the payment delay. Payments are taken in date
    order. Older paid installments without a payment list use their single paid date. None if half
    has not been crossed yet."""
    threshold = round(float(e.get("emi_amount") or 0) / 2.0, 2)
    ordered = sorted((p for p in payments if p.get("paid_at")),
                     key=lambda p: ((p["paid_at"] or "")[:10], p.get("payment_id") or 0))
    running = 0.0
    for p in ordered:
        running = round(running + float(p.get("amount") or 0) - float(p.get("penalty_part") or 0), 2)
        if running > threshold:
            return p["paid_at"][:10]
    if not payments and e.get("status") == "Paid" and e.get("paid_at"):
        return e["paid_at"][:10]
    return None

def paid_on_and_status(e, payments, is_paid, today):
    """Builds the 'Paid On' and 'Payment Status' cells for an EMI row: each sub-bill (5.1, 5.2 …)
    gets its own payment date and delay; once closed, a final line gives the closing payment's delay.
    Unpaid / part-paid rows past their due date also show how many days overdue."""
    inst, due = e["installment_no"], e["due_date"]
    on_lines, st_lines = [], []
    if payments:
        multi = len(payments) > 1
        for i, p in enumerate(payments):
            pd = (p.get("paid_at") or "")[:10]
            tag = f"<b>{inst}.{i+1}</b>: " if multi else ""
            on_lines.append(f"{tag}{fmt_date(pd)}")
            st_lines.append(f"{tag}{delay_badge(due, pd) if pd else '—'}")
        if multi:
            hd = half_paid_date(e, payments)
            if hd:
                on_lines.append(f'<span style="color:var(--accent);">½ <b>{inst}</b> half paid</span>: {fmt_date(hd)}')
                st_lines.append(f"<b>{inst}</b>: {delay_badge(due, hd)}")
    elif is_paid and e.get("paid_at"):
        pd = e["paid_at"][:10]
        on_lines.append(fmt_date(pd))
        st_lines.append(delay_badge(due, pd))
    if not is_paid and e.get("status") not in ("PreClosed", "Seized"):
        overdue_days = (today - parse_date(due)).days
        if overdue_days > 0:
            st_lines.append(f'<span style="color:var(--red);font-weight:600;">🔴 Overdue {overdue_days} day{"s" if overdue_days != 1 else ""}</span>')
    return "<br>".join(on_lines), "<br>".join(st_lines)

def paid_amount_cell(e, payments):
    """Paid column: one line per sub-bill amount (5.1, 5.2 …) plus the installment total;
    a single payment just shows its amount."""
    pen_paid = float(e.get("penalty_paid") or 0)
    total = float(e.get("amount_paid") or 0) + pen_paid
    pen_note = f'<br><span style="font-size:11px;color:#7c3aed;">incl. {fmt_inr(pen_paid)} penalty</span>' if pen_paid > 0 else ""
    if len(payments) > 1:
        inst = e["installment_no"]
        lines = [f"<b>{inst}.{i+1}</b>: {fmt_inr(p.get('amount') or 0)}"
                 + (f' <span style="font-size:11px;color:#7c3aed;">(penalty {fmt_inr(p["penalty_part"])})</span>' if float(p.get("penalty_part") or 0) > 0 else "")
                 for i, p in enumerate(payments)]
        lines.append(f"<b>Total {fmt_inr(total)}</b>")
        return "<br>".join(lines)
    return fmt_inr(total) + pen_note

def late_payment_days(e, payments, is_paid):
    """Days between the EMI due date and the date the installment crossed half of its EMI value
    (0 if that was on/before the due date). None until half has been crossed."""
    hd = half_paid_date(e, payments)
    if not hd: return None
    return max(0, (parse_date(hd) - parse_date(e["due_date"])).days)

def late_days_cell(e, payments, is_paid):
    days = late_payment_days(e, payments, is_paid)
    if days is None: return "—"
    color = "var(--green)" if days == 0 else "var(--red)"
    pad = "<br>" * len(payments) if len(payments) > 1 else ""   # sit on the 'half paid' line
    return f'{pad}<b style="color:{color};">{days}</b>'

def format_bill_ref(installment_no, payments, is_paid):
    """Render the Bill No cell: a single payment shows its bill number plainly;
    multiple partial payments against the same installment are tagged 3.1, 3.2, 3.3…
    so each one stays traceable, and once fully paid the row closes back under
    the plain installment number."""
    if not payments:
        return "—"
    if len(payments) == 1:
        return html.escape(str(payments[0].get("bill_number") or "—"))
    lines = [f"<b>{installment_no}.{i+1}</b>: {html.escape(str(p.get('bill_number') or '—'))}"
             for i, p in enumerate(payments)]
    if is_paid:
        lines.append(f'<span style="color:var(--green);">✅ <b>{installment_no}</b> (Closed)</span>')
    return "<br>".join(lines)

def list_closed_loans(search=""):
    q=f"%{search}%"; c=get_cur()
    c.execute("""SELECT le.id as loan_id,le.loan_number,le.customer_name,le.vehicle_type,le.vehicle_name,le.vehicle_model,
                        le.vehicle_number,le.vehicle_colour,le.loan_amount,cl.closure_date
                 FROM LoanEntry le JOIN ClosedLoans cl ON cl.loan_id=le.id
                 WHERE le.loan_number LIKE ? OR le.customer_name LIKE ? ORDER BY cl.created_at DESC""",(q,q))
    rows = [dict(r) for r in c.fetchall()]
    for r in rows:
        c2 = get_cur()
        c2.execute("""SELECT new_rate, original_rate, settlement_amount, bill_number, penalty_amount, further_interest, waived_months FROM PreClosure
                      WHERE loan_id=? AND status='Completed' ORDER BY preclose_id DESC LIMIT 1""", (r["loan_id"],))
        pc = c2.fetchone()
        r["preclosure"] = dict(pc) if pc else None
        cl = get_closure(r["loan_id"])
        r["closure"] = cl if cl and cl["status"] == "Closed" else None
        r["closure_items"] = closure_items(cl["closure_id"]) if r["closure"] else []
        sz = get_last_seizure(r["loan_id"])
        r["seizure"] = sz if sz and sz["status"] == "Seized" else None
        r["seizure_items"] = seizure_items(sz["seizure_id"]) if r["seizure"] else []
    return rows

def list_rejected_loans(search=""):
    q=f"%{search}%"; c=get_cur()
    c.execute("""SELECT le.id as loan_id,le.loan_number,le.customer_name,rl.reason,rl.created_at
                 FROM LoanEntry le JOIN RejectedLoans rl ON rl.loan_id=le.id
                 WHERE le.loan_number LIKE ? OR le.customer_name LIKE ? ORDER BY rl.created_at DESC""",(q,q))
    return [dict(r) for r in c.fetchall()]

def get_overdue_emis():
    today=date.today().isoformat(); c=get_cur()
    c.execute("""SELECT e.*,le.loan_number,le.customer_name,le.id as lid
                 FROM EMI e JOIN LoanEntry le ON e.loan_id=le.id
                 WHERE e.status='Overdue' OR (e.status IN ('Pending','Partial') AND e.due_date < ?)
                 ORDER BY le.loan_number ASC, e.due_date ASC""",(today,))
    return [dict(r) for r in c.fetchall()]

def get_upcoming_emis():
    today=date.today().isoformat()
    limit=(date.today()+timedelta(days=UPCOMING_DAYS)).isoformat(); c=get_cur()
    c.execute("""SELECT e.*,le.loan_number,le.customer_name,le.id as lid
                 FROM EMI e JOIN LoanEntry le ON e.loan_id=le.id
                 WHERE e.status IN ('Pending','Partial') AND e.due_date>=? AND e.due_date<=?
                 ORDER BY le.loan_number ASC, e.due_date ASC""",(today,limit))
    return [dict(r) for r in c.fetchall()]

def group_alerts_by_loan(emi_list):
    """Group EMI list by loan number → one row per loan with cumulative due amount."""
    grouped = {}
    for e in emi_list:
        ln = e["loan_number"]
        if ln not in grouped:
            grouped[ln] = {
                "loan_number": ln,
                "customer_name": e["customer_name"],
                "loan_id": e["loan_id"],
                "lid": e.get("lid", e["loan_id"]),
                "emi_count": 0,
                "total_due": 0.0,
                "oldest_due": e["due_date"],
                "emis": []
            }
        due = float(e.get("remaining_amount") or e["emi_amount"])
        grouped[ln]["total_due"] += due
        grouped[ln]["emi_count"] += 1
        grouped[ln]["emis"].append(e)
        if e["due_date"] < grouped[ln]["oldest_due"]:
            grouped[ln]["oldest_due"] = e["due_date"]
    return list(grouped.values())

def _location_link(loc):
    """Render a saved GPS/location string as a clickable Google Maps link."""
    loc = (loc or "").strip()
    if not loc: return "—"
    if re.match(r'^-?\d{1,3}\.\d+,\s*-?\d{1,3}\.\d+$', loc):
        url = f"https://www.google.com/maps?q={urlquote(loc)}"
    else:
        url = f"https://www.google.com/maps/search/{urlquote(loc)}"
    return f'<a href="{url}" target="_blank" rel="noopener">📍 {html.escape(loc)}</a>'

# ── Follow Up (customer-requested collection date) ──────────────────────────────
FU_CATEGORIES = ["Loans", "Key Collection", "Proof & Documents", "Penalty Collection"]
DOC_FU_CATEGORIES = ("Key Collection", "Proof & Documents")    # no money columns on these follow-ups
# follow-up "item" -> (LoanEntry received column, LoanEntry received-date column)
FU_ITEM_COLUMNS = {"key": ("key_received", "key_received_date"),
                   "rc":  ("rc_received",  "rc_received_date"),
                   "docs":("docs_received","docs_received_date"),
                   "cheque": ("cheque_received", "cheque_received_date"),
                   "aadhar": ("aadhar_received", "aadhar_received_date"),
                   "eb":     ("eb_received", "eb_received_date"),
                   "tc":     ("tc_received", "tc_received_date")}

def add_follow_up(loan_id, follow_up_date, remarks, created_by, category="Loans", item=None, ref_id=None):
    c = get_cur(); now = datetime.now(timezone.utc).isoformat()
    c.execute("""INSERT INTO FollowUp (loan_id,follow_up_date,remarks,status,created_by,created_at,category,item,ref_id)
                 VALUES (?,?,?,?,?,?,?,?,?)""",
              (loan_id, follow_up_date, remarks.strip(), "Pending", created_by, now, category, item, ref_id))
    get_db().commit()
    return c.lastrowid

FU_ITEM_BY = {"key": "key_collected_by", "rc": "rc_collected_by", "docs": "docs_collected_by", "cheque": "cheque_collected_by",
              "aadhar": "aadhar_collected_by", "eb": "eb_collected_by", "tc": "tc_collected_by"}

def resolve_follow_up(followup_id, recv_date=None, recv_by=None, recv_extra=None):
    """Closes the follow-up. For Key / RC / Proof follow-ups it also marks that item as received (with
    today's date) on the loan record; for a Penalty follow-up it marks the penalty as collected."""
    c = get_cur(); now = datetime.now(timezone.utc).isoformat()
    c.execute("SELECT loan_id, item, ref_id, recv_date, recv_by, recv_extra FROM FollowUp WHERE followup_id=?", (followup_id,))
    row = c.fetchone()
    c.execute("UPDATE FollowUp SET status='Resolved', resolved_at=? WHERE followup_id=?", (now, followup_id))
    if row and row["item"] in FU_ITEM_COLUMNS:
        col, col_date = FU_ITEM_COLUMNS[row["item"]]
        by_col = FU_ITEM_BY[row["item"]]
        rd = recv_date or row["recv_date"] or date.today().isoformat()
        rb = recv_by or row["recv_by"]
        rx = recv_extra or row["recv_extra"]
        c.execute("UPDATE FollowUp SET recv_date=?, recv_by=?, recv_extra=? WHERE followup_id=?", (rd, rb, rx, followup_id))
        if row["item"] == "cheque" and rx:
            c.execute("UPDATE LoanEntry SET cheque_numbers=? WHERE id=?", (rx, row["loan_id"]))
        if row["ref_id"]:       # an add-on vehicle's key / RC / proof
            c.execute(f"UPDATE LoanVehicles SET {col}='yes', {col_date}=?, {by_col}=? WHERE vehicle_id=?", (rd, rb, row["ref_id"]))
        else:
            c.execute(f"UPDATE LoanEntry SET {col}='yes', {col_date}=?, {by_col}=? WHERE id=?", (rd, rb, row["loan_id"]))
    if row and row["item"] == "penalty" and row["ref_id"]:
        c.execute("UPDATE Penalties SET status='Collected', collected_at=? WHERE penalty_id=? AND status='Approved'",
                  (now, row["ref_id"]))
    get_db().commit()
    if row and row["item"] == "penalty":
        closure_advance(row["loan_id"])     # last penalty collected -> key / document return unlocks

def request_followup_ack(followup_id, username, note="", recv_date=None, recv_by=None, recv_extra=None):
    """A follow-up is not closed directly: it goes to the acknowledger for a cross-check first."""
    c = get_cur(); c.execute("SELECT status FROM FollowUp WHERE followup_id=?", (followup_id,))
    row = c.fetchone()
    if not row or row["status"] != "Pending": raise ValueError("This follow-up is not open.")
    c.execute("""UPDATE FollowUp SET status='AwaitingAck', ack_requested_by=?, ack_requested_at=?, ack_note=?,
                 recv_date=COALESCE(?,recv_date), recv_by=COALESCE(?,recv_by), recv_extra=COALESCE(?,recv_extra) WHERE followup_id=?""",
              (username, datetime.now(timezone.utc).isoformat(), (note or "").strip(), recv_date, recv_by, recv_extra, followup_id))
    get_db().commit()

def acknowledge_followup(followup_id, username):
    c = get_cur(); c.execute("SELECT status, ack_requested_by FROM FollowUp WHERE followup_id=?", (followup_id,))
    row = c.fetchone()
    if not row or row["status"] != "AwaitingAck": raise ValueError("This follow-up is not awaiting acknowledgement.")
    if row["ack_requested_by"] == username: raise ValueError("You cannot acknowledge your own entry.")
    resolve_follow_up(followup_id)

def reject_followup_ack(followup_id, reason, username):
    c = get_cur(); c.execute("SELECT status FROM FollowUp WHERE followup_id=?", (followup_id,))
    row = c.fetchone()
    if not row or row["status"] != "AwaitingAck": raise ValueError("This follow-up is not awaiting acknowledgement.")
    c.execute("UPDATE FollowUp SET status='Pending', ack_note=? WHERE followup_id=?",
              (f"Not acknowledged by {username}: {(reason or '').strip() or 'no reason given'}", followup_id))
    get_db().commit()

def reschedule_follow_up(followup_id, new_date, remarks, created_by):
    """Adds one more follow-up (same loan / category / item) and retires the old one."""
    c = get_cur()
    c.execute("SELECT * FROM FollowUp WHERE followup_id=?", (followup_id,))
    old = c.fetchone()
    if not old: raise ValueError("Follow-up not found")
    c.execute("UPDATE FollowUp SET status='Rescheduled' WHERE followup_id=?", (followup_id,))
    get_db().commit()
    add_follow_up(old["loan_id"], new_date, remarks or old["remarks"], created_by,
                  old["category"] or "Loans", old["item"], ref_id=old["ref_id"])

def get_active_follow_ups_map():
    """Latest PENDING *loan-collection* follow-up per loan_id — used to badge the Alerts page."""
    c = get_cur()
    c.execute("""SELECT f.* FROM FollowUp f
                 INNER JOIN (SELECT loan_id, MAX(created_at) as mx FROM FollowUp
                             WHERE status='Pending' AND COALESCE(category,'Loans')='Loans' GROUP BY loan_id) latest
                 ON f.loan_id=latest.loan_id AND f.created_at=latest.mx
                 WHERE COALESCE(f.category,'Loans')='Loans'""")
    return {r["loan_id"]: dict(r) for r in c.fetchall()}

def list_follow_ups(search="", category=""):
    q = f"%{search}%"; c = get_cur()
    sql = """SELECT f.followup_id, f.loan_id, f.follow_up_date, f.remarks, f.status,
                        f.created_by, f.created_at, f.resolved_at,
                        COALESCE(f.category,'Loans') as category, f.item, f.ref_id,
                        f.ack_requested_by, f.ack_requested_at, f.ack_note,
                        le.loan_number, le.vehicle_type, le.vehicle_name, le.vehicle_model, le.vehicle_number, le.vehicle_colour,
                        le.customer_name, le.customer_mobile, le.customer_extra_numbers,
                        le.guarantor_name, le.guarantor_mobile, le.guarantor_extra_numbers,
                        le.customer_address, le.customer_permanent_address, le.customer_location, le.guarantor_location
                 FROM FollowUp f JOIN LoanEntry le ON f.loan_id=le.id
                 WHERE (le.loan_number LIKE ? OR le.customer_name LIKE ? OR le.customer_mobile LIKE ?)"""
    params = [q, q, q]
    if category in FU_CATEGORIES:
        sql += " AND COALESCE(f.category,'Loans')=?"
        params.append(category)
    sql += " ORDER BY f.follow_up_date ASC"
    c.execute(sql, tuple(params))
    rows = [dict(r) for r in c.fetchall()]
    xv_by_id = {v["vehicle_id"]: v for lst in _xv_cache("_xv_vehicles", "LoanVehicles").values() for v in lst}
    for r in rows:      # key / RC / proof follow-ups of an add-on vehicle show THAT vehicle
        v = xv_by_id.get(r.get("ref_id")) if r["item"] in FU_ITEM_COLUMNS else None
        if v:
            for k in ("vehicle_type", "vehicle_name", "vehicle_model", "vehicle_number", "vehicle_colour"):
                r[k] = v.get(k)
            r["_vehicle_scoped"] = True
    today_s = date.today().isoformat()
    per_loan = {}
    for r in rows:
        lid = r["loan_id"]
        if lid not in per_loan:
            c2 = get_cur()
            c2.execute("""SELECT
                  SUM(CASE WHEN status NOT IN ('Paid','PreClosed','Seized') THEN COALESCE(remaining_amount,emi_amount)+MAX(0,COALESCE(penalty_due,0)-COALESCE(penalty_paid,0)) ELSE 0 END) as outstanding,
                  MIN(CASE WHEN status NOT IN ('Paid','PreClosed','Seized') THEN due_date END) as oldest_due,
                  SUM(CASE WHEN status='Overdue' OR (status IN ('Pending','Partial') AND due_date<?)
                           THEN COALESCE(remaining_amount,emi_amount)+MAX(0,COALESCE(penalty_due,0)-COALESCE(penalty_paid,0)) ELSE 0 END) as overdue_amt,
                  SUM(CASE WHEN status='Overdue' OR (status IN ('Pending','Partial') AND due_date<?)
                           THEN 1 ELSE 0 END) as pending_dues,
                  MAX(paid_at) as last_paid_emi
                FROM EMI WHERE loan_id=?""", (today_s, today_s, lid))
            agg = c2.fetchone()
            c3 = get_cur()
            c3.execute("SELECT emi_amount FROM EMI WHERE loan_id=? ORDER BY installment_no ASC LIMIT 1", (lid,))
            first = c3.fetchone()
            c4 = get_cur()
            c4.execute("SELECT MAX(paid_at) as p FROM EMIPayments WHERE loan_id=?", (lid,))
            pay = c4.fetchone()
            paid_dates = [d[:10] for d in ((agg["last_paid_emi"] if agg else None), (pay["p"] if pay else None)) if d]
            per_loan[lid] = {
                "overdue_amount": float((agg["overdue_amt"] if agg else 0) or 0),
                "pending_dues":   int((agg["pending_dues"] if agg else 0) or 0),
                "outstanding":    float((agg["outstanding"] if agg else 0) or 0),
                "oldest_due":     (agg["oldest_due"] if agg else None) or "",
                "last_paid_date": max(paid_dates) if paid_dates else "",
                "emi_amount":     float(first["emi_amount"]) if first and first["emi_amount"] is not None else None,
            }
        r.update(per_loan[lid])
    return rows

def get_loan_summary_counts():
    today = date.today().isoformat()
    limit = (date.today()+timedelta(days=UPCOMING_DAYS)).isoformat()

    results = batch_query([
        ("SELECT status, COUNT(*) as n FROM LoanEntry GROUP BY status", ()),
        ("SELECT COUNT(*) as n FROM EMI WHERE status='Overdue' OR (status IN ('Pending','Partial') AND due_date < ?)", (today,)),
        ("SELECT COUNT(*) as n FROM EMI WHERE status IN ('Pending','Partial') AND due_date>=? AND due_date<=?", (today, limit)),
    ])

    status_counts = {r["status"]: r["n"] for r in results[0]}
    overdue_n  = (results[1][0]["n"] if results[1] else 0) or 0
    upcoming_n = (results[2][0]["n"] if results[2] else 0) or 0

    return dict(
        total   = sum(status_counts.values()),
        pending = status_counts.get("PendingApproval", 0),
        approved= status_counts.get("Approved", 0),
        rejected= status_counts.get("Rejected", 0),
        closed  = status_counts.get("Closed", 0),
        overdue = overdue_n,
        upcoming= upcoming_n,
    )

def get_kpi_totals():
    results = batch_query([
        ("SELECT COUNT(*) as n, SUM(loan_amount) as amt FROM LoanEntry", ()),
        ("SELECT SUM(emi_amount) as amt FROM EMI WHERE status='Paid'", ()),
        ("SELECT SUM(remaining_amount) as amt FROM EMI WHERE status IN ('Pending','Overdue','Partial')", ()),
        # pre-closed loans: what was paid on the unpaid EMIs + the single closing settlement
        ("SELECT SUM(amount_paid) as amt FROM EMI WHERE status='PreClosed'", ()),
        ("SELECT SUM(settlement_amount) as amt FROM PreClosure WHERE status='Completed'", ()),
    ])
    row = results[0][0] if results[0] else {}
    tl  = row.get("n") or 0
    tla = row.get("amt") or 0.0
    tr  = (results[1][0].get("amt") if results[1] else 0) or 0.0
    tr += ((results[3][0].get("amt") if results[3] else 0) or 0.0) + ((results[4][0].get("amt") if results[4] else 0) or 0.0)
    tp  = (results[2][0].get("amt") if results[2] else 0) or 0.0
    return tl, tla, tr, tp

def get_monthly_paid_series():
    c=get_cur()
    c.execute("SELECT strftime('%Y-%m',paid_at) as ym,SUM(emi_amount) as amt FROM EMI WHERE status='Paid' AND paid_at IS NOT NULL GROUP BY ym ORDER BY ym ASC")
    series = {r["ym"]: float(r["amt"] or 0) for r in c.fetchall()}
    c.execute("SELECT strftime('%Y-%m',paid_on) as ym,SUM(settlement_amount) as amt FROM PreClosure WHERE status='Completed' AND paid_on IS NOT NULL GROUP BY ym")
    for r in c.fetchall():
        series[r["ym"]] = series.get(r["ym"], 0.0) + float(r["amt"] or 0)
    months = sorted(series)
    return months, [series[m] for m in months]

def get_loan_status_breakdown():
    c=get_cur()
    c.execute("SELECT status,COUNT(*) as cnt FROM LoanEntry GROUP BY status")
    return [dict(r) for r in c.fetchall()]

def get_loan_type_breakdown():
    c=get_cur()
    c.execute("SELECT vehicle_type,COUNT(*) as cnt FROM LoanEntry GROUP BY vehicle_type")
    return [dict(r) for r in c.fetchall()]

# ── Email ──────────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
#  SMS via Fast2SMS (India)
# ══════════════════════════════════════════════════════════════════════════════
def _send_sms(mobile, message):
    """Send SMS via Fast2SMS. Returns (success:bool, info:str)."""
    if not _sms_enabled():
        return False, "SMS not configured (FAST2SMS_KEY not set)"
    mobile = str(mobile or "").strip()
    if len(mobile) != 10:
        return False, f"Invalid mobile: {mobile}"
    try:
        resp = http_req.post(
            "https://www.fast2sms.com/dev/bulkV2",
            headers={
                "authorization": _get_sms_key(),
                "Content-Type":  "application/x-www-form-urlencoded",
                "Cache-Control": "no-cache",
            },
            data={
                "route":    "v3",
                "message":  message[:160],
                "language": "english",
                "flash":    "0",
                "numbers":  mobile,
            },
            timeout=15
        )
        print(f"[SMS] {resp.status_code} {resp.text}")
        result = resp.json()
        if result.get("return") == True:
            return True, f"SMS sent to {mobile}"
        errmsg = result.get("message","Unknown error")
        if isinstance(errmsg, list): errmsg = " | ".join(errmsg)
        return False, f"Fast2SMS says: {errmsg}"
    except Exception as e:
        return False, f"Exception: {e}"

def _send_sms_bulk(mobiles, message):
    """Send SMS to multiple numbers."""
    if not _sms_enabled():
        return False, "SMS not configured"
    nums = [str(m).strip() for m in mobiles if m and len(str(m).strip()) == 10]
    if not nums: return False, "No valid 10-digit numbers"
    try:
        resp = http_req.post(
            "https://www.fast2sms.com/dev/bulkV2",
            headers={
                "authorization": _get_sms_key(),
                "Content-Type":  "application/x-www-form-urlencoded",
                "Cache-Control": "no-cache",
            },
            data={
                "route":    "v3",
                "message":  message[:160],
                "language": "english",
                "flash":    "0",
                "numbers":  ",".join(nums),
            },
            timeout=15
        )
        print(f"[SMS Bulk] {resp.status_code} {resp.text}")
        result = resp.json()
        if result.get("return") == True:
            return True, f"SMS sent to {len(nums)} number(s)"
        errmsg = result.get("message","Unknown")
        if isinstance(errmsg, list): errmsg = " | ".join(errmsg)
        return False, f"Fast2SMS says: {errmsg}"
    except Exception as e:
        return False, f"Exception: {e}"


def _notify_approval(loan):
    name   = loan.get("customer_name","Customer")
    ln     = loan.get("loan_number","")
    amt    = fmt_inr(loan.get("loan_amount",0))
    mobile = loan.get("customer_mobile","")
    msg = (f"Dear {name}, Your loan {ln} of {amt} has been APPROVED by "
           f"Thendralla Fincorp. Thank you for choosing us.")
    _send_sms(mobile, msg)
    em = loan.get("customer_email","")
    if em:
        _send_email(em,"Your Vehicle Loan Has Been Approved",
            f"<h2>Loan Approved</h2><p>Dear {name},</p>"
            f"<p>Loan <b>{ln}</b> of {amt} has been approved.</p>")

def _notify_closure(loan_id):
    c=get_cur(); c.execute("SELECT * FROM LoanEntry WHERE id=?",(loan_id,))
    loan=c.fetchone()
    if not loan: return
    name   = loan["customer_name"]
    ln     = loan["loan_number"]
    mobile = loan.get("customer_mobile","")
    msg = (f"Dear {name}, Congratulations! All EMIs for loan {ln} are PAID. "
           f"Loan is now CLOSED. Thank you - Thendralla Fincorp.")
    _send_sms(mobile, msg)
    if loan.get("customer_email"):
        _send_email(loan["customer_email"],"Loan Fully Repaid — Congratulations!",
            f"<h2>Loan Closed</h2><p>Dear {name},</p>"
            f"<p>All EMIs for loan <b>{ln}</b> paid. Loan is now closed!</p>")

def _notify_emi_due(loan_number, customer_name, mobile, due_date, amount, days_left):
    """Upcoming EMI reminder SMS."""
    if days_left <= 0:
        msg = (f"Dear {customer_name}, EMI of {fmt_inr(amount)} for loan {loan_number} "
               f"was DUE on {fmt_date(due_date)}. Please pay immediately. -Thendralla Fincorp")
    else:
        msg = (f"Dear {customer_name}, Reminder: EMI of {fmt_inr(amount)} for loan "
               f"{loan_number} is DUE on {fmt_date(due_date)} ({days_left} days). -Thendralla Fincorp")
    return _send_sms(mobile, msg)

def send_bulk_overdue_sms():
    """Send SMS to all overdue loan customers. Called from Alerts page."""
    overdue = get_overdue_emis()
    # Group by loan number to avoid duplicate SMS
    seen = set(); results = []; total_sent = 0
    for e in overdue:
        ln = e["loan_number"]
        if ln in seen: continue
        seen.add(ln)
        c = get_cur()
        c.execute("SELECT customer_name,customer_mobile,loan_number FROM LoanEntry WHERE loan_number=?", (ln,))
        row = c.fetchone()
        if not row: continue
        name   = row["customer_name"]
        mobile = row.get("customer_mobile","")
        due_d  = parse_date(e["due_date"])
        days   = (date.today() - due_d).days
        amt    = sum(float(x.get("remaining_amount") or x["emi_amount"])
                     for x in overdue if x["loan_number"]==ln)
        msg = (f"Dear {name}, URGENT: Total overdue EMI of {fmt_inr(amt)} for loan "
               f"{ln} is pending {days} day(s). Pay now to avoid penalty. -Thendralla Fincorp")
        ok, info = _send_sms(mobile, msg)
        results.append({"loan":ln,"name":name,"mobile":mobile,"ok":ok,"info":info})
        if ok: total_sent += 1
    return results, total_sent

def send_bulk_upcoming_sms():
    """Send SMS to customers with EMIs due in 3 days."""
    upcoming = get_upcoming_emis()
    seen = set(); results = []; total_sent = 0
    today = date.today()
    for e in upcoming:
        ln = e["loan_number"]
        if ln in seen: continue
        due_d = parse_date(e["due_date"])
        days_left = (due_d - today).days
        if days_left > 3: continue   # only send if ≤3 days away
        seen.add(ln)
        c = get_cur()
        c.execute("SELECT customer_name,customer_mobile FROM LoanEntry WHERE loan_number=?", (ln,))
        row = c.fetchone()
        if not row: continue
        name   = row["customer_name"]
        mobile = row.get("customer_mobile","")
        amt    = sum(float(x.get("remaining_amount") or x["emi_amount"])
                     for x in upcoming if x["loan_number"]==ln)
        msg = (f"Dear {name}, Reminder: EMI of {fmt_inr(amt)} for loan {ln} is due "
               f"on {fmt_date(e['due_date'])} ({days_left} day(s)). -Thendralla Fincorp")
        ok, info = _send_sms(mobile, msg)
        results.append({"loan":ln,"name":name,"mobile":mobile,"ok":ok,"info":info})
        if ok: total_sent += 1
    return results, total_sent

# ══════════════════════════════════════════════════════════════════════════════
#  PDF
# ══════════════════════════════════════════════════════════════════════════════
def generate_pdf(path):
    if not REPORTLAB_AVAILABLE: raise RuntimeError("reportlab not installed.")
    styles=getSampleStyleSheet()
    ts=ParagraphStyle("T",parent=styles["Title"],fontSize=18,spaceAfter=12)
    h2=ParagraphStyle("H2",parent=styles["Heading2"],fontSize=13,spaceAfter=6)
    story=[Paragraph("Thendralla Fincorp — Loan Report",ts),
           Paragraph(f"Generated: {datetime.now().strftime('%d %b %Y %H:%M')}",styles["Normal"]),
           Spacer(1,0.5*cm)]
    tl,tla,tr,tp=get_kpi_totals(); counts=get_loan_summary_counts()
    kd=[["Metric","Value"],["Total Loans",str(tl)],["Disbursed",f"Rs {tla:,.2f}"],
        ["Collected",f"Rs {tr:,.2f}"],["Outstanding",f"Rs {tp:,.2f}"],
        ["Pending",str(counts["pending"])],["Active",str(counts["approved"])],
        ["Closed",str(counts["closed"])],["Rejected",str(counts["rejected"])],
        ["Overdue EMIs",str(counts["overdue"])]]
    kt=Table(kd,colWidths=[8*cm,7*cm])
    kt.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#1a4fad")),
        ("TEXTCOLOR",(0,0),(-1,0),colors.white),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#e8f0fb")]),
        ("GRID",(0,0),(-1,-1),0.5,colors.grey)]))
    story.append(kt); story.append(PageBreak())
    story.append(Paragraph("All Loans",h2))
    loans=list_all_loans()
    ld=[["Loan #","Customer","Mobile","Amount","Rate","Tenure","Status"]]
    for l in loans:
        ld.append([l["loan_number"],l["customer_name"],l.get("customer_mobile",""),
                   f"Rs {l['loan_amount']:,.0f}",f"{l['interest_rate']*100:.1f}%",f"{l['tenure']}m",l["status"]])
    if len(ld)>1:
        lt=Table(ld,repeatRows=1,colWidths=[2.8*cm,3.5*cm,2.8*cm,2.8*cm,1.8*cm,1.8*cm,2.8*cm])
        lt.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#1a4fad")),
            ("TEXTCOLOR",(0,0),(-1,0),colors.white),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
            ("FONTSIZE",(0,0),(-1,-1),8),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#e8f0fb")]),
            ("GRID",(0,0),(-1,-1),0.4,colors.grey)]))
        story.append(lt)
    doc=SimpleDocTemplate(path,pagesize=A4,leftMargin=1.5*cm,rightMargin=1.5*cm,topMargin=2*cm,bottomMargin=2*cm)
    doc.build(story)

def generate_followup_pdf(path, items):
    if not REPORTLAB_AVAILABLE: raise RuntimeError("reportlab not installed.")
    styles = getSampleStyleSheet()
    ts = ParagraphStyle("T", parent=styles["Title"], fontSize=16, spaceAfter=10)
    story = [Paragraph("Thendralla Fincorp — Follow Up Report", ts),
             Paragraph(f"Generated: {datetime.now().strftime('%d %b %Y %H:%M')}", styles["Normal"]),
             Spacer(1, 0.4*cm)]
    today = date.today()
    data = [["Loan #","Customer","Mobile","Vehicle","Address","Follow-up Date","Status","Overdue Amt","Outstanding","Remarks","By"]]
    for r in items:
        fu_date = parse_date(r["follow_up_date"])
        if r["status"] in ("Resolved","Rescheduled"): status = r["status"]
        elif r["status"] == "AwaitingAck": status = "Awaiting ack"
        elif fu_date < today: status = "Missed"
        else: status = "Pending"
        data.append([
            r["loan_number"], r.get("customer_name") or "", r.get("customer_mobile") or "",
            vehicle_label(r), (r.get("customer_address") or "")[:36], fmt_date(r["follow_up_date"]), status,
            f"Rs {r['overdue_amount']:,.2f}", f"Rs {r['outstanding']:,.2f}", (r.get("remarks") or "")[:50], r.get("created_by") or ""
        ])
    tbl = Table(data, repeatRows=1,
                colWidths=[1.8*cm,2.4*cm,2.1*cm,2.0*cm,3.0*cm,2.1*cm,1.7*cm,2.2*cm,2.3*cm,4.4*cm,1.6*cm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#1a4fad")),
        ("TEXTCOLOR",(0,0),(-1,0),colors.white),
        ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
        ("FONTSIZE",(0,0),(-1,-1),7.5),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#e8f0fb")]),
        ("GRID",(0,0),(-1,-1),0.4,colors.grey),
        ("VALIGN",(0,0),(-1,-1),"TOP"),
    ]))
    story.append(tbl)
    doc = SimpleDocTemplate(path, pagesize=landscape(A4),
                             leftMargin=1*cm, rightMargin=1*cm, topMargin=1.2*cm, bottomMargin=1.2*cm)
    doc.build(story)

# ══════════════════════════════════════════════════════════════════════════════
#  CSS + LAYOUT  (Vertical Sidebar, Mobile-first)
# ══════════════════════════════════════════════════════════════════════════════
BASE_CSS = """
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#f0f4fb;--surface:#fff;--surface2:#f1f5fb;
  --border:#c8d4e8;--accent:#1a4fad;--accent2:#1d6fdb;
  --green:#059669;--red:#dc2626;--amber:#d97706;
  --text:#1e293b;--muted:#475569;
  --sidebar-w:220px;
}
html,body{height:100%;font-family:system-ui,'Segoe UI',sans-serif;font-size:14px;
          background:var(--bg);color:var(--text);}

/* ── LAYOUT ── */
.layout{display:flex;min-height:100vh;}

/* ── SIDEBAR ── */
.sidebar{
  width:var(--sidebar-w);min-width:var(--sidebar-w);
  background:var(--accent);color:#fff;
  display:flex;flex-direction:column;
  position:fixed;top:0;left:0;height:100vh;
  z-index:200;transition:transform .25s ease;
  overflow-y:auto;
}
.sidebar .brand{
  display:flex;align-items:center;gap:10px;
  padding:16px 14px 12px;border-bottom:1px solid rgba(255,255,255,.15);
}
.sidebar .brand img{height:36px;border-radius:4px;background:#fff;padding:2px;flex-shrink:0;}
.sidebar .brand-text{font-size:13px;font-weight:700;line-height:1.3;letter-spacing:.3px;}
.sidebar nav{padding:10px 0;flex:1;}
.sidebar nav a{
  display:flex;align-items:center;gap:10px;
  padding:11px 18px;color:rgba(255,255,255,.82);
  text-decoration:none;font-size:13.5px;transition:.15s;
  border-left:3px solid transparent;
}
.sidebar nav a:hover,.sidebar nav a.active{
  background:rgba(255,255,255,.13);color:#fff;
  border-left-color:rgba(255,255,255,.8);
}
.sidebar nav a .icon{font-size:16px;min-width:20px;text-align:center;display:inline-flex;align-items:center;justify-content:center;}
img.emoji{height:1.1em;width:1.1em;margin:0 .05em 0 .1em;vertical-align:-0.15em;display:inline-block;}
.sidebar .sidebar-footer{
  padding:12px 14px;border-top:1px solid rgba(255,255,255,.15);
  font-size:12px;color:rgba(255,255,255,.7);
}
.sidebar .sidebar-footer a{color:rgba(255,255,255,.8);text-decoration:none;}
.sidebar .sidebar-footer a:hover{color:#fff;}

/* ── TOPBAR (mobile hamburger) ── */
.topbar{
  display:none;background:var(--accent);color:#fff;
  padding:0 16px;height:52px;align-items:center;gap:12px;
  position:fixed;top:0;left:0;right:0;z-index:100;
  box-shadow:0 2px 6px rgba(0,0,0,.2);
}
.topbar .brand-text{font-size:15px;font-weight:700;flex:1;}
.topbar img{height:32px;border-radius:3px;background:#fff;padding:2px;}
#hamburger{background:none;border:none;color:#fff;font-size:22px;cursor:pointer;padding:4px;}
.sidebar-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:150;}

/* ── MAIN CONTENT ── */
.main-wrap{
  margin-left:var(--sidebar-w);
  min-height:100vh;padding:24px 20px;
  flex:1;max-width:calc(100% - var(--sidebar-w));
}
h1{font-size:22px;margin-bottom:16px;color:var(--accent);}
h2{font-size:17px;margin-bottom:12px;}

/* ── CARDS ── */
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;
      padding:20px;margin-bottom:20px;box-shadow:0 1px 4px rgba(0,0,0,.06);}
.kpi-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px;}
.kpi{background:var(--surface);border-radius:10px;padding:14px;
     border-left:4px solid var(--accent);box-shadow:0 1px 4px rgba(0,0,0,.06);}
.kpi .val{font-size:24px;font-weight:700;color:var(--accent);}
.kpi .lbl{font-size:11px;color:var(--muted);margin-top:3px;}

/* ── FORMS ── */
.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;}
.form-grid.three{grid-template-columns:1fr 1fr 1fr;}
.form-group{display:flex;flex-direction:column;gap:5px;}
.form-group.full{grid-column:1/-1;}
label{font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;
      letter-spacing:.3px;white-space:normal;line-height:1.3;}
input,select,textarea{
  padding:11px 12px;border:1px solid var(--border);border-radius:8px;
  font-size:15px;background:#fff;color:var(--text);width:100%;
  transition:border-color .15s;box-sizing:border-box;
}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--accent2);
  box-shadow:0 0 0 3px rgba(29,111,219,.12);}
input[readonly]{background:var(--surface2);color:var(--muted);}
.section-title{font-size:12px;font-weight:700;color:var(--accent);text-transform:uppercase;
               letter-spacing:.5px;padding:10px 0 5px;border-bottom:2px solid var(--accent);
               margin-bottom:12px;grid-column:1/-1;margin-top:10px;}

/* ── BUTTONS ── */
.btn{display:inline-flex;align-items:center;gap:5px;padding:9px 18px;border-radius:7px;
     font-size:13px;font-weight:600;cursor:pointer;border:none;transition:.15s;text-decoration:none;}
.btn-primary{background:var(--accent);color:#fff;}
.btn-primary:hover{background:var(--accent2);}
.btn-success{background:var(--green);color:#fff;}
.btn-danger{background:var(--red);color:#fff;}
.btn-amber{background:var(--amber);color:#fff;}
.btn-sm{padding:5px 11px;font-size:12px;}
.btn:disabled{opacity:.5;cursor:not-allowed;}

/* ── TABLES ── */
.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;}
table{width:100%;border-collapse:collapse;font-size:13px;}
th{background:var(--accent);color:#fff;padding:10px 8px;text-align:left;white-space:nowrap;}
td{padding:9px 8px;border-bottom:1px solid var(--border);vertical-align:middle;}
tr:nth-child(even) td{background:var(--surface2);}
tr:hover td{background:#e8f0fb;}

/* EMI row highlight */
tr.row-overdue td{background:#fee2e2 !important;border-left:3px solid var(--red);}
tr.row-overdue:hover td{background:#fecaca !important;}
tr.row-upcoming td{background:#fef9c3 !important;border-left:3px solid var(--amber);}
tr.row-upcoming:hover td{background:#fef08a !important;}
tr.row-paid td{opacity:.65;}

/* ── BADGES ── */
.badge{display:inline-block;padding:3px 9px;border-radius:12px;font-size:11px;font-weight:700;}
.badge-pending{background:#fef3c7;color:#92400e;}
.badge-approved,.badge-paid,.badge-good{background:#d1fae5;color:#065f46;}
.badge-rejected,.badge-overdue,.badge-risk{background:#fee2e2;color:#991b1b;}
.badge-closed{background:#e0e7ff;color:#3730a3;}
.badge-partial,.badge-average{background:#ffedd5;color:#9a3412;}
.badge-admin{background:var(--accent);color:#fff;}
.badge-superadmin{background:linear-gradient(135deg,#7c3aed,#dc2626);color:#fff;box-shadow:0 1px 4px rgba(124,58,237,.4);}
.badge-manager{background:#059669;color:#fff;}
.badge-fieldpia{background:#d97706;color:#fff;}
.badge-viewer{background:#6b7280;color:#fff;}
.badge-assocmgr{background:#0e7490;color:#fff;}

/* ── ALERTS ── */
.alert{padding:10px 14px;border-radius:6px;margin-bottom:12px;font-size:13px;}
.alert-success{background:#d1fae5;color:#065f46;border:1px solid #a7f3d0;}
.alert-danger{background:#fee2e2;color:#991b1b;border:1px solid #fca5a5;}
.alert-info{background:#dbeafe;color:#1e40af;border:1px solid #93c5fd;}
.alert-warning{background:#fef3c7;color:#92400e;border:1px solid #fde68a;}

/* ── FOLLOW-UP MODAL ── */
.fu-modal-overlay{position:fixed;inset:0;background:rgba(15,23,42,.55);
  z-index:500;display:none;align-items:center;justify-content:center;padding:16px;}
.fu-modal-overlay.open{display:flex;}
.fu-modal{background:var(--surface);border-radius:12px;padding:22px;
  width:100%;max-width:420px;box-shadow:0 10px 40px rgba(0,0,0,.3);}
.fu-modal h3{font-size:16px;margin-bottom:4px;color:var(--accent);}

/* ── DUE PREVIEW ── */
.due-preview{background:linear-gradient(135deg,#1a4fad,#1d6fdb);color:#fff;
             border-radius:10px;padding:16px 20px;margin:14px 0;display:none;}
.due-preview h3{font-size:14px;margin-bottom:10px;opacity:.9;}
.due-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;}
.due-item .lbl{font-size:10px;opacity:.7;text-transform:uppercase;}
.due-item .val{font-size:18px;font-weight:700;}

/* ── RISK BOX ── */
.risk-box{border-radius:8px;padding:12px;margin-top:10px;display:none;}
.risk-good{background:#d1fae5;border:1px solid #6ee7b7;}
.risk-average{background:#fef3c7;border:1px solid #fde68a;}
.risk-risk{background:#fee2e2;border:1px solid #fca5a5;}

/* ── CALCULATOR ── */
.calc-result{background:linear-gradient(135deg,#1a4fad,#0ea5e9);color:#fff;
             border-radius:10px;padding:18px 24px;margin:14px 0;text-align:center;}
.calc-result .big-val{font-size:36px;font-weight:800;}
.calc-result .lbl{font-size:13px;opacity:.8;margin-bottom:6px;}
.calc-summary{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-top:12px;}
.calc-summary .item{background:rgba(255,255,255,.15);border-radius:7px;padding:9px;}
.calc-summary .item .val{font-size:16px;font-weight:700;}
.calc-summary .item .lbl{font-size:11px;opacity:.8;}

/* ── CHARTS ── */
.chart-grid{display:grid;grid-template-columns:2fr 1fr;gap:14px;margin-top:14px;}
.chart-box{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px;max-height:260px;overflow:hidden;
           box-shadow:0 1px 4px rgba(0,0,0,.06);}
.chart-box h3{font-size:14px;color:var(--muted);margin-bottom:12px;text-transform:uppercase;letter-spacing:.4px;}

/* ── GPS ── */
.gps-row{display:flex;gap:6px;align-items:center;}
.gps-row input{flex:1;}

/* ── MOBILE ── */
@media(max-width:768px){
  /* Sidebar */
  .sidebar{transform:translateX(-100%);}
  .sidebar.open{transform:translateX(0);}
  .sidebar-overlay.open{display:block;}

  /* Topbar */
  .topbar{display:flex;}

  /* Main wrap — full width, below topbar */
  .main-wrap{margin-left:0 !important;padding:62px 10px 20px !important;
             max-width:100% !important;width:100% !important;}

  /* Page title */
  h1{font-size:18px;margin-bottom:12px;}
  h2{font-size:15px;}

  /* Cards */
  .card{padding:14px;border-radius:8px;}

  /* Forms — single column on mobile */
  .form-grid,
  .form-grid.three{grid-template-columns:1fr !important;}
  .form-group.full{grid-column:1 !important;}

  /* Inputs bigger touch targets */
  input,select,textarea{
    font-size:16px !important;   /* prevents iOS zoom */
    padding:12px 12px !important;
    min-height:46px;
  }
  label{font-size:11px;margin-bottom:2px;}
  .section-title{font-size:11px;}

  /* Buttons */
  .btn{padding:10px 16px;font-size:13px;}
  .btn-sm{padding:8px 12px;font-size:12px;}

  /* KPIs */
  .kpi-grid{grid-template-columns:repeat(2,1fr);gap:8px;}
  .kpi{padding:10px 12px;}
  .kpi .val{font-size:20px;}

  /* Due preview */
  .due-grid{grid-template-columns:1fr 1fr !important;}
  .due-item .val{font-size:14px;}

  /* Charts */
  .calc-summary,.chart-grid{grid-template-columns:1fr;}
  .chart-box{max-height:180px !important;padding:10px;}
  .chart-box h3{font-size:11px;margin-bottom:6px;}
  canvas{max-height:130px !important;}

  /* Tables — horizontal scroll */
  .table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;border-radius:6px;}
  table{font-size:12px;min-width:480px;}
  th,td{padding:7px 6px;}

  /* Alert messages */
  .alert{font-size:12px;padding:8px 10px;}
}
/* ── CHATBOT WIDGET ── */
.chatbot-fab{
  position:fixed;bottom:20px;right:20px;z-index:500;
  width:56px;height:56px;border-radius:50%;
  background:linear-gradient(135deg,var(--accent),var(--accent2));
  color:#fff;border:none;font-size:24px;cursor:pointer;
  box-shadow:0 4px 14px rgba(26,79,173,.4);
  display:flex;align-items:center;justify-content:center;
  transition:transform .15s ease,box-shadow .15s ease;
}
.chatbot-fab:hover{transform:scale(1.08);box-shadow:0 6px 18px rgba(26,79,173,.5);}
/* ── USER GUIDE CORNER BUTTON ── */
.guide-fab{
  position:fixed;top:16px;right:16px;z-index:210;
  display:flex;align-items:center;gap:6px;
  background:var(--green);color:#fff;border:none;text-decoration:none;
  padding:0 16px;height:40px;border-radius:20px;font-size:13px;font-weight:700;
  box-shadow:0 4px 14px rgba(5,150,105,.4);
  transition:transform .15s ease,box-shadow .15s ease;
}
.guide-fab:hover{transform:scale(1.05);box-shadow:0 6px 18px rgba(5,150,105,.5);color:#fff;}
.chatbot-window{
  position:fixed;bottom:88px;right:20px;z-index:500;
  width:360px;max-width:92vw;height:480px;max-height:72vh;
  background:var(--surface);border-radius:14px;
  box-shadow:0 10px 40px rgba(0,0,0,.25);
  display:none;flex-direction:column;overflow:hidden;
  border:1px solid var(--border);
}
.chatbot-window.open{display:flex;}
.chatbot-header{
  background:linear-gradient(135deg,var(--accent),var(--accent2));color:#fff;
  padding:12px 14px;display:flex;justify-content:space-between;align-items:center;
  font-size:14px;font-weight:700;
}
.chatbot-header button{background:none;border:none;color:#fff;font-size:18px;cursor:pointer;padding:2px 6px;}
.chatbot-body{flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:8px;background:var(--surface2);}
.chat-msg{max-width:88%;padding:8px 12px;border-radius:10px;font-size:13px;line-height:1.45;white-space:pre-wrap;}
.chat-msg.bot{background:#fff;border:1px solid var(--border);align-self:flex-start;border-bottom-left-radius:2px;}
.chat-msg.user{background:var(--accent);color:#fff;align-self:flex-end;border-bottom-right-radius:2px;}
.chat-suggestions{display:flex;gap:6px;flex-wrap:wrap;padding:0 12px 8px;background:var(--surface2);}
.chat-suggestions button{
  background:#fff;border:1px solid var(--border);border-radius:14px;
  padding:5px 10px;font-size:11px;cursor:pointer;color:var(--accent);
  transition:.15s;
}
.chat-suggestions button:hover{background:var(--accent);color:#fff;}
.chat-options{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px;}
.chat-options button{
  background:#fff;border:1px solid var(--accent);border-radius:14px;
  padding:5px 11px;font-size:11.5px;cursor:pointer;color:var(--accent);
  transition:.15s;
}
.chat-options button:hover{background:var(--accent);color:#fff;}
.chat-options button.back-btn{border-color:var(--border);color:var(--muted);}
.chat-options button.back-btn:hover{background:var(--surface2);color:var(--text);}
.chatbot-input-row{display:flex;gap:6px;padding:10px;border-top:1px solid var(--border);background:var(--surface);}
.chatbot-input-row input{flex:1;padding:9px 10px;font-size:13px;min-height:auto;}
.chatbot-input-row button{
  background:var(--accent);color:#fff;border:none;border-radius:8px;
  padding:0 14px;font-size:14px;cursor:pointer;
}
@media(max-width:480px){
  .chatbot-window{width:94vw;right:3vw;bottom:80px;height:65vh;}
  .chatbot-fab{bottom:14px;right:14px;}
  .guide-fab{top:62px;right:10px;padding:0 12px;height:34px;font-size:12px;}
}
</style>
"""

# Old computers without an emoji font show emoji as "?" or boxes. This detects that (colour test on a
# canvas) and swaps every emoji for a small picture (Twemoji). Add ?emoji=1 to any page URL to force it.
EMOJI_FALLBACK_JS = r"""<script>
(function(){
  function emojiOK(){
    try{
      var c=document.createElement('canvas'); c.width=c.height=32;
      var x=c.getContext('2d'); x.textBaseline='top'; x.font='24px sans-serif'; x.fillStyle='#000';
      x.fillText('😀',0,0);
      var d=x.getImageData(0,0,32,32).data;
      for(var i=0;i<d.length;i+=4){ if(d[i+3]>0 && (d[i]!==d[i+1]||d[i+1]!==d[i+2])) return true; }
      return false;
    }catch(e){ return true; }
  }
  if(emojiOK() && location.search.indexOf('emoji=1')<0) return;
  var s=document.createElement('script');
  s.src='https://cdn.jsdelivr.net/npm/@twemoji/api@15.0.3/dist/twemoji.min.js';
  s.onload=function(){
    function run(){ try{ twemoji.parse(document.body,{folder:'svg',ext:'.svg',className:'emoji'}); }catch(e){} }
    run();
    var t=null;
    new MutationObserver(function(){ clearTimeout(t); t=setTimeout(run,150); }).observe(document.body,{childList:true,subtree:true});
  };
  document.head.appendChild(s);
})();
</script>"""

# Menu icons are drawn as inline SVG so they show on every computer (emoji need an emoji font,
# which old laptops don't have).
_NAV_SVG = {
    "dashboard": '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>',
    "loans":     '<path d="M9 4h6v3H9z"/><path d="M7 5H5v16h14V5h-2"/><line x1="9" y1="12" x2="15" y2="12"/><line x1="9" y1="16" x2="15" y2="16"/>',
    "add":       '<circle cx="12" cy="12" r="9"/><line x1="12" y1="8" x2="12" y2="16"/><line x1="8" y1="12" x2="16" y2="12"/>',
    "approval":  '<circle cx="12" cy="12" r="9"/><polyline points="8 12.5 11 15.5 16 9"/>',
    "customers": '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c0-3.6 2.9-6 6.5-6s6.5 2.4 6.5 6"/><circle cx="17" cy="9" r="2.6"/><path d="M17 14c2.8 0 4.5 1.9 4.5 4.5"/>',
    "billing":   '<path d="M6 3h12v18l-3-2-3 2-3-2-3 2z"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="9" y1="12" x2="15" y2="12"/>',
    "ack":       '<path d="M9 11l3 3 8-8"/><path d="M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h9"/>',
    "alerts":    '<path d="M6 16v-5a6 6 0 0 1 12 0v5l2 2H4z"/><path d="M10 21a2 2 0 0 0 4 0"/>',
    "followup":  '<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z"/>',
    "closed":    '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    "rejected":  '<circle cx="12" cy="12" r="9"/><line x1="9" y1="9" x2="15" y2="15"/><line x1="15" y1="9" x2="9" y2="15"/>',
    "calculator":'<rect x="5" y="3" width="14" height="18" rx="2"/><rect x="8" y="6" width="8" height="4"/><line x1="8" y1="14" x2="8.01" y2="14"/><line x1="12" y1="14" x2="12.01" y2="14"/><line x1="16" y1="14" x2="16.01" y2="14"/><line x1="8" y1="18" x2="8.01" y2="18"/><line x1="12" y1="18" x2="12.01" y2="18"/><line x1="16" y1="18" x2="16.01" y2="18"/>',
    "report":    '<line x1="6" y1="20" x2="6" y2="11"/><line x1="12" y1="20" x2="12" y2="5"/><line x1="18" y1="20" x2="18" y2="14"/>',
    "users":     '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/>',
    "database":  '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
    "user":      '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7"/>',
    "logout":    '<path d="M9 21H5V3h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>',
    "menu":      '<line x1="4" y1="6" x2="20" y2="6"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="18" x2="20" y2="18"/>',
}

def nav_svg(key, size=20):
    return (f'<svg viewBox="0 0 24 24" width="{size}" height="{size}" fill="none" stroke="currentColor" stroke-width="2" '
            f'stroke-linecap="round" stroke-linejoin="round" style="vertical-align:middle;flex-shrink:0;">{_NAV_SVG[key]}</svg>')

def _nav_links(role, active):
    can_approve = ROLES.get(role,{}).get("can_approve", False)
    can_add     = ROLES.get(role,{}).get("can_add",     False)
    can_report  = ROLES.get(role,{}).get("can_report",  False)
    can_db      = ROLES.get(role,{}).get("can_db",      False)

    def lnk(href, icon, label, key):
        cls = "active" if active == key else ""
        return f'<a href="{href}" class="{cls}"><span class="icon">{nav_svg(key if key in _NAV_SVG else "dashboard")}</span>{label}</a>'

    links = lnk("/dashboard","🏠","Dashboard","dashboard")
    links += lnk("/loans","📋","Loans","loans")
    if can_add:     links += lnk("/loan/add","➕","New Loan","add")
    if can_approve: links += lnk("/approval","✅","Approval","approval")
    links += lnk("/customers","👥","Customers","customers")
    if ROLES.get(role,{}).get("can_pay", False): links += lnk("/billing","🧾","Billing","billing")
    if ROLES.get(role,{}).get("can_ack", False):
        try:
            n_p, n_f, n_k, n_z = ack_counts(); n_ack = n_p + n_f + n_k + n_z
        except Exception:
            n_ack = 0
        links += lnk("/acknowledgements","🔎",f"Acknowledgements" + (f' <span style="background:#dc2626;color:#fff;border-radius:999px;padding:1px 7px;font-size:11px;margin-left:4px;">{n_ack}</span>' if n_ack else ""),"ack")
    links += lnk("/alerts","🔔","Alerts","alerts")
    links += lnk("/followup","📞","Follow Up","followup")
    links += lnk("/closed","🔒","Closed","closed")
    links += lnk("/rejected","❌","Rejected","rejected")
    links += lnk("/calculator","🧮","Calculator","calculator")
    if can_report:  links += lnk("/report","📊","Report","report")
    if role in ("admin","superadmin"): links += lnk("/users","⚙️","Users","users")
    if can_db:      links += lnk("/database","🗄️","Database","database")
    return links

CHATBOT_JS = """<script>
function tfcChatToggle(){
  const win = document.getElementById('tfcChatWindow');
  win.classList.toggle('open');
  if(win.classList.contains('open')){
    if(!document.getElementById('tfcChatBody').dataset.greeted){
      tfcGreet();
      document.getElementById('tfcChatBody').dataset.greeted='1';
    }
    document.getElementById('tfcChatInput')?.focus();
  }
}
function tfcGreet(){
  const body = document.getElementById('tfcChatBody');
  const div = document.createElement('div');
  div.className = 'chat-msg bot';
  div.textContent = '⏳ ...';
  body.appendChild(div);
  fetch('/api/chatbot', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({message: '__greet__'})
  }).then(r=>r.json()).then(data=>{
    div.textContent = data.reply || 'Hello!';
    body.scrollTop = body.scrollHeight;
  }).catch(()=>{ div.textContent = 'Hello! Ask me about your loans, EMIs, or customers.'; });
}
function tfcAddMsg(who, text){
  const body = document.getElementById('tfcChatBody');
  const div = document.createElement('div');
  div.className = 'chat-msg ' + who;
  div.textContent = text;
  body.appendChild(div);
  body.scrollTop = body.scrollHeight;
}
function tfcAsk(text){
  document.getElementById('tfcChatInput').value = text;
  document.getElementById('tfcChatForm').requestSubmit();
}

// ── Guided Question Builder (level-by-level option chips) ──
const TFC_TOPICS = [
  {label:"📈 Profit",            needs:"period",  build:(p)=>`${p} profit`},
  {label:"📊 Profit Comparison", needs:"period",  build:(p)=>`${p} profit comparison`},
  {label:"🏦 Loan Comparison",   needs:"period",  build:(p)=>`${p} loan comparison`},
  {label:"💰 Disbursed",         needs:"period",  build:(p)=>`${p} disbursed`},
  {label:"✅ Expected Collections", needs:"period", build:(p)=>`${p} how much will get collected`},
  {label:"🏆 Top High-Amount Loans", needs:"count", build:(n)=>`high amount pending loans top ${n}`},
  {label:"🔴 Overdue Loans",     needs:null, direct:"overdue loans"},
  {label:"⏳ Upcoming EMI",       needs:null, direct:"upcoming emi"},
  {label:"📅 Today Summary",     needs:null, direct:"today summary"},
  {label:"📆 This Week Insights", needs:null, direct:"this week insights"},
  {label:"👥 Customer Stats",    needs:null, direct:"how many customers"},
  {label:"📞 Follow Up Summary", needs:null, direct:"follow up summary"},
  {label:"📅 Follow Ups Today",  needs:null, direct:"follow ups today"},
  {label:"⏰ Missed Follow Ups", needs:null, direct:"missed follow ups"},
];
const TFC_PERIODS = ["This Week","This Month","Next Month","Last Month","This Quarter","Next Quarter","Last Quarter","This Year","Next Year"];
const TFC_COUNTS = ["3","5","10","20"];

function tfcGuidedStart(){
  tfcAddMsg('bot', "Sure! What would you like to know about? (Step 1 of 2)");
  tfcShowOptions(TFC_TOPICS.map(t=>t.label), (label)=>{
    const topic = TFC_TOPICS.find(t=>t.label===label);
    if(topic.direct){
      tfcAsk(topic.direct);
    } else if(topic.needs==="period"){
      tfcAddMsg('bot', `Got it — "${label}". Now pick a time period (Step 2 of 2):`);
      tfcShowOptions(TFC_PERIODS, (period)=>{
        tfcAsk(topic.build(period.toLowerCase()));
      }, true);
    } else if(topic.needs==="count"){
      tfcAddMsg('bot', `Got it — "${label}". How many would you like to see? (Step 2 of 2)`);
      tfcShowOptions(TFC_COUNTS, (n)=>{
        tfcAsk(topic.build(n));
      }, true);
    }
  });
}

function tfcShowOptions(options, onPick, withBack){
  const body = document.getElementById('tfcChatBody');
  const wrap = document.createElement('div');
  wrap.className = 'chat-msg bot';
  const optsDiv = document.createElement('div');
  optsDiv.className = 'chat-options';
  options.forEach(opt=>{
    const btn = document.createElement('button');
    btn.textContent = opt;
    btn.onclick = ()=>{ wrap.remove(); tfcAddMsg('user', opt); onPick(opt); };
    optsDiv.appendChild(btn);
  });
  if(withBack){
    const back = document.createElement('button');
    back.textContent = '⬅ Back';
    back.className = 'back-btn';
    back.onclick = ()=>{ wrap.remove(); tfcGuidedStart(); };
    optsDiv.appendChild(back);
  }
  wrap.appendChild(optsDiv);
  body.appendChild(wrap);
  body.scrollTop = body.scrollHeight;
}

function tfcSendMsg(e){
  e.preventDefault();
  const input = document.getElementById('tfcChatInput');
  const msg = input.value.trim();
  if(!msg) return false;
  tfcAddMsg('user', msg);
  input.value='';
  const body = document.getElementById('tfcChatBody');
  const thinking = document.createElement('div');
  thinking.className = 'chat-msg bot';
  thinking.textContent = '⏳ Thinking...';
  body.appendChild(thinking);
  body.scrollTop = body.scrollHeight;
  fetch('/api/chatbot', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({message: msg})
  }).then(r=>r.json()).then(data=>{
    thinking.textContent = data.reply || 'No response.';
    body.scrollTop = body.scrollHeight;
  }).catch(err=>{
    thinking.textContent = '❌ Error contacting server. Please try again.';
  });
  return false;
}
</script>"""


def page(title, content, active=""):
    username = session.get("username","")
    role     = session.get("role","")
    logo_img = f'<img src="data:image/jpeg;base64,{LOGO_B64}" alt="TFC">' if LOGO_B64 else "🏦"
    flash_html = ""
    for cat, msg in get_flashed_messages(with_categories=True):
        cat_map = {"success":"success","danger":"danger","info":"info","warning":"warning"}
        flash_html += f'<div class="alert alert-{cat_map.get(cat,"info")}">{msg}</div>'

    sidebar_nav = _nav_links(role, active)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>TFC — {title}</title>
{BASE_CSS}
</head>
<body>
<!-- Mobile topbar -->
<div class="topbar">
  <button id="hamburger" onclick="toggleSidebar()" aria-label="Menu">{nav_svg('menu', 24)}</button>
  {logo_img}
  <span class="brand-text">Thendralla Fincorp</span>
</div>
<!-- Sidebar overlay (mobile) -->
<div class="sidebar-overlay" id="overlay" onclick="toggleSidebar()"></div>
<!-- Sidebar -->
<div class="sidebar" id="sidebar">
  <div class="brand">
    {logo_img}
    <div class="brand-text">Thendralla<br>Fincorp</div>
  </div>
  <nav>{sidebar_nav}</nav>
  <div class="sidebar-footer">
    {nav_svg('user', 15)} <b>{username}</b> <span style="opacity:.6">({role})</span><br>
    <a href="/logout" style="color:#ff9999;">{nav_svg('logout', 15)} Logout</a>
  </div>
</div>
<!-- Main -->
<div class="layout">
  <div class="main-wrap">
    {flash_html}
    {content}
  </div>
</div>
<script>
function toggleSidebar(){{
  document.getElementById('sidebar').classList.toggle('open');
  document.getElementById('overlay').classList.toggle('open');
}}
// Close sidebar on nav link click (mobile)
document.querySelectorAll('.sidebar nav a').forEach(a=>{{
  a.addEventListener('click',()=>{{
    if(window.innerWidth<=768){{
      document.getElementById('sidebar').classList.remove('open');
      document.getElementById('overlay').classList.remove('open');
    }}
  }});
}});
</script>
<!-- User Guide corner button (all roles) -->
<a class="guide-fab" href="{USER_GUIDE_URL}" target="_blank" rel="noopener" title="User Guide">📘 Guide</a>
<!-- Chatbot Widget (Admin / Super Admin only) -->
{f'''<button class="chatbot-fab" onclick="tfcChatToggle()" title="Ask Thendralla">💬</button>
<div class="chatbot-window" id="tfcChatWindow">
  <div class="chatbot-header">
    <span>🤖 Thendralla — AI Assistant</span>
    <button onclick="tfcChatToggle()">✕</button>
  </div>
  <div id="tfcChatBody" class="chatbot-body"></div>
  <div class="chat-suggestions">
    <button onclick="tfcGuidedStart()">🧩 Build a Question</button>
    <button onclick="tfcAsk('How many loans do I have')">📊 My Loans</button>
    <button onclick="tfcAsk('Today summary')">📅 Today</button>
    <button onclick="tfcAsk('Upcoming EMI')">⏳ Upcoming EMI</button>
    <button onclick="tfcAsk('Overdue loans')">🔴 Overdue</button>
    <button onclick="tfcAsk('This week insights')">📈 This Week</button>
    <button onclick="tfcAsk('Follow up summary')">📞 Follow Ups</button>
  </div>
  <form id="tfcChatForm" class="chatbot-input-row" onsubmit="return tfcSendMsg(event)">
    <input id="tfcChatInput" placeholder="Ask Thendralla anything..." autocomplete="off">
    <button type="submit">➤</button>
  </form>
</div>
{CHATBOT_JS}''' if role in ("admin","superadmin") else ""}
{EMOJI_FALLBACK_JS}
</body>
</html>"""

# ══════════════════════════════════════════════════════════════════════════════
#  FLASK APP
# ══════════════════════════════════════════════════════════════════════════════
def _get_secret_key():
    """Stable secret key across restarts (a fresh random key on every restart would
    invalidate every logged-in session, forcing surprise logouts mid-entry)."""
    env_key = os.environ.get("SECRET_KEY", "").strip()
    if env_key:
        return env_key
    key_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".flask_secret_key")
    try:
        with open(key_file, "r") as f:
            existing = f.read().strip()
            if existing:
                return existing
    except FileNotFoundError:
        pass
    new_key = secrets.token_hex(32)
    try:
        with open(key_file, "w") as f:
            f.write(new_key)
    except OSError:
        pass
    return new_key

app = Flask(__name__)
app.secret_key = _get_secret_key()
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db: db.close()

def login_required(f):
    @wraps(f)
    def dec(*a,**kw):
        if "username" not in session: return redirect(url_for("login"))
        # pick up role changes / removed users made by the Super Admin (checked at most once a minute)
        now = time.time()
        if now - session.get("role_checked", 0) > 60:
            c = get_cur(); c.execute("SELECT role FROM Users WHERE username=?", (session["username"],))
            row = c.fetchone()
            if not row:
                session.clear(); return redirect(url_for("login"))
            session["role"] = row["role"]; session["role_checked"] = now
        return f(*a,**kw)
    return dec

def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def dec(*a,**kw):
            if session.get("role") not in roles:
                flash("Access denied for your role.","danger")
                return redirect(url_for("dashboard"))
            return f(*a,**kw)
        return dec
    return decorator

# ══════════════════════════════════════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════════════════════════════════════
@app.route("/")
def index(): return redirect(url_for("dashboard"))

@app.route("/user-guide")
def user_guide_page():
    """Serves the local picture guide. Once the same file is uploaded to GitHub,
    point USER_GUIDE_URL at that link instead and this route becomes an unused fallback."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_guide.html")
    if os.path.exists(path):
        return send_file(path)
    flash("User guide not found.", "danger")
    return redirect(url_for("dashboard"))

# ── Login ──────────────────────────────────────────────────────────────────────
@app.route("/login", methods=["GET","POST"])
def login():
    if "username" in session: return redirect(url_for("dashboard"))
    err = ""
    if request.method == "POST":
        user = authenticate_user(request.form["username"], request.form["password"])
        if user:
            session.permanent = True
            session["username"] = user["username"]; session["role"] = user["role"]; session["role_checked"] = time.time()
            flash(f"Welcome, {user['username']}!","success")
            return redirect(url_for("dashboard"))
        err = "Invalid credentials."
    logo_html = f"<img src='data:image/jpeg;base64,{LOGO_B64}' style='height:80px;margin-bottom:10px;'><br>" if LOGO_B64 else ""
    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>TFC Login</title>{BASE_CSS}</head>
<body style="background:linear-gradient(135deg,#1a4fad,#0ea5e9);display:flex;
             align-items:center;justify-content:center;min-height:100vh;">
<div style="background:#fff;border-radius:16px;padding:36px 32px;width:340px;max-width:95vw;
            box-shadow:0 8px 32px rgba(0,0,0,.18);">
  <div style="text-align:center;margin-bottom:22px;">
    {logo_html}
    <h2 style="color:#1a4fad;font-size:21px;">Thendralla Fincorp</h2>
    <p style="color:#64748b;font-size:12px;">Vehicle Loan Management</p>
  </div>
  {"<div class='alert alert-danger'>"+err+"</div>" if err else ""}
  <form method="POST">
    <div class="form-group" style="margin-bottom:12px;">
      <label>Username</label><input name="username" required autofocus autocomplete="username">
    </div>
    <div class="form-group" style="margin-bottom:18px;">
      <label>Password</label><input type="password" name="password" required autocomplete="current-password">
    </div>
    <button class="btn btn-primary" style="width:100%;padding:11px;font-size:15px;">Login</button>
  </form>
</div>
</body></html>"""

@app.route("/logout")
def logout(): session.clear(); return redirect(url_for("login"))

# ── Dashboard ──────────────────────────────────────────────────────────────────
FOLLOWUP_SOON_DAYS = 2   # same "due within 2 days" rule as the Follow Up page
ATTENTION_ROWS = 5

def waiting_items_html():
    """Short banner on the dashboard: payments/follow-ups waiting for acknowledgement (Account Manager,
    Admin, Super Admin) and penalties waiting for approval (Admin, Super Admin)."""
    role = session.get("role", "")
    bits = []
    if role in ACK_ROLES:
        n_p, n_f, n_k, n_z = ack_counts()
        if n_p or n_f or n_k or n_z:
            bits.append(f'<a href="/acknowledgements" style="color:inherit;"><b>{n_p}</b> payment(s), <b>{n_f}</b> follow-up(s), '
                        f'<b>{n_k}</b> loan closing hand-over(s) and <b>{n_z}</b> vehicle seizure(s) awaiting acknowledgement →</a>')
    if role in DIRECT_ROLES:
        c = get_cur(); c.execute("SELECT COUNT(*) as n FROM Penalties WHERE status='Pending'")
        n_pen = c.fetchone()["n"]
        if n_pen:
            bits.append(f'<a href="/approval" style="color:inherit;"><b>{n_pen}</b> late-payment penalty(ies) awaiting your approval →</a>')
        c.execute("SELECT COUNT(*) as n FROM LoanClosure WHERE status='AwaitApproval'")
        n_cl = c.fetchone()["n"]
        if n_cl:
            bits.append(f'<a href="/approval" style="color:inherit;"><b>{n_cl}</b> loan closing(s) awaiting your approval →</a>')
        c.execute("SELECT COUNT(*) as n FROM Seizures WHERE status='Pending'")
        n_sz = c.fetchone()["n"]
        if n_sz:
            bits.append(f'<a href="/approval" style="color:inherit;"><b>{n_sz}</b> vehicle seizure(s) awaiting your approval →</a>')
    if not bits: return ""
    return ('<div style="background:#eff6ff;border:1px solid #93c5fd;border-left:6px solid #1d6fdb;border-radius:12px;'
            'padding:10px 14px;margin-bottom:12px;font-size:13.5px;">🔔 ' + " &nbsp;|&nbsp; ".join(bits) + '</div>')

def attention_panel_html(overdue_emis):
    """Highlighted 'Needs Attention' panel: missed / due-soon follow-ups and overdue EMI alerts."""
    today = date.today()
    soon = (today + timedelta(days=FOLLOWUP_SOON_DAYS)).isoformat()
    c = get_cur()
    c.execute("""SELECT f.followup_id, f.loan_id, f.follow_up_date, f.remarks, COALESCE(f.category,'Loans') as category,
                        le.loan_number, le.customer_name
                 FROM FollowUp f JOIN LoanEntry le ON le.id=f.loan_id
                 WHERE f.status='Pending' AND f.follow_up_date<=?
                 ORDER BY f.follow_up_date ASC""", (soon,))
    fus = [dict(r) for r in c.fetchall()]
    missed = [f for f in fus if parse_date(f["follow_up_date"]) < today]
    due_today = [f for f in fus if parse_date(f["follow_up_date"]) == today]
    due_soon = [f for f in fus if parse_date(f["follow_up_date"]) > today]

    def fu_when(f):
        d = (parse_date(f["follow_up_date"]) - today).days
        if d < 0:  return f'<span style="color:var(--red);font-weight:700;">Missed {-d} day{"s" if d != -1 else ""} ago</span>'
        if d == 0: return '<span style="color:var(--amber);font-weight:700;">Today</span>'
        return f'<span style="color:#a16207;font-weight:700;">In {d} day{"s" if d != 1 else ""}</span>'

    fu_rows = "".join(f"""<a href="/followup?cat={urlquote(f['category'])}" style="display:block;padding:7px 0;border-bottom:1px solid var(--border);color:inherit;text-decoration:none;">
          <b style="color:var(--accent);">{html.escape(f['loan_number'])}</b> — {html.escape(f['customer_name'] or '')}
          <span class="badge badge-partial" style="margin-left:4px;">{html.escape(f['category'])}</span><br>
          <span style="font-size:12px;">{fu_when(f)} · {fmt_date(f['follow_up_date'])} ·<span style="color:var(--muted);">{html.escape((f.get('remarks') or '')[:60])}</span></span></a>"""
        for f in (missed + due_today + due_soon)[:ATTENTION_ROWS])

    groups = sorted(group_alerts_by_loan(overdue_emis), key=lambda g: g["oldest_due"])
    total_overdue_amt = sum(g["total_due"] for g in groups)
    od_rows = "".join(f"""<a href="/emis/{g['lid']}" style="display:block;padding:7px 0;border-bottom:1px solid var(--border);color:inherit;text-decoration:none;">
          <b style="color:var(--accent);">{html.escape(g['loan_number'])}</b> — {html.escape(g['customer_name'] or '')}
          <b style="float:right;color:var(--red);">₹{g['total_due']:,.2f}</b><br>
          <span style="font-size:12px;color:var(--muted);">{g['emi_count']} EMI{"s" if g['emi_count'] != 1 else ""} overdue ·
          <span style="color:var(--red);font-weight:700;">{(today - parse_date(g['oldest_due'])).days} days</span> since {fmt_date(g['oldest_due'])}</span></a>"""
        for g in groups[:ATTENTION_ROWS])

    nothing = not fus and not groups
    border = "#059669" if nothing else ("#dc2626" if (missed or groups) else "#d97706")
    bg = "#ecfdf5" if nothing else ("#fef2f2" if (missed or groups) else "#fffbeb")
    def badge(n, label, color):
        return (f'<span style="background:{color};color:#fff;border-radius:999px;padding:3px 10px;font-size:12px;font-weight:700;">{n} {label}</span>') if n else ""
    fu_badges = " ".join(x for x in (badge(len(missed), "missed", "#dc2626"), badge(len(due_today), "today", "#d97706"),
                                      badge(len(due_soon), f"in {FOLLOWUP_SOON_DAYS} days", "#ca8a04")) if x)
    return f"""
    <div style="background:{bg};border:1px solid {border};border-left:6px solid {border};border-radius:12px;padding:14px 16px;margin-bottom:16px;">
      <div style="font-size:16px;font-weight:800;margin-bottom:10px;">{'✅ All clear — nothing needs attention' if nothing else '🚨 Needs Attention'}</div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px;">
        <div style="background:var(--surface);border-radius:10px;padding:12px 14px;">
          <div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:6px;">
            <b>📞 Follow-ups</b><span>{fu_badges or '<span style="font-size:12px;color:var(--green);">none due</span>'}</span>
          </div>
          {fu_rows or '<div style="font-size:13px;color:var(--muted);padding:6px 0;">No follow-ups missed or due in the next days.</div>'}
          <div style="margin-top:8px;"><a href="/followup" class="btn btn-sm btn-primary">View all follow-ups</a></div>
        </div>
        <div style="background:var(--surface);border-radius:10px;padding:12px 14px;">
          <div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:6px;">
            <b>🔔 Overdue EMI alerts</b>
            <span>{badge(len(groups), 'loans', '#dc2626')} <b style="color:var(--red);font-size:13px;">{('₹{:,.2f}'.format(total_overdue_amt)) if groups else ''}</b></span>
          </div>
          {od_rows or '<div style="font-size:13px;color:var(--muted);padding:6px 0;">No overdue EMIs.</div>'}
          <div style="margin-top:8px;"><a href="/alerts" class="btn btn-sm btn-danger">View all alerts</a></div>
        </div>
      </div>
    </div>"""

@app.route("/dashboard")
@login_required
def dashboard():
    counts = get_loan_summary_counts()
    tl,tla,tr,tp = get_kpi_totals()
    overdue  = get_overdue_emis()
    upcoming = get_upcoming_emis()
    months, amounts = get_monthly_paid_series()
    status_bd = get_loan_status_breakdown()
    type_bd   = get_loan_type_breakdown()

    # Chart.js data
    import json
    bar_labels  = json.dumps(months)
    bar_data    = json.dumps(amounts)
    stat_labels = json.dumps([r["status"] for r in status_bd])
    stat_data   = json.dumps([r["cnt"]    for r in status_bd])
    type_labels = json.dumps([r["vehicle_type"] for r in type_bd])
    type_data   = json.dumps([r["cnt"]          for r in type_bd])

    kpi = f"""
    <div class="kpi-grid">
      <div class="kpi"><div class="val">{counts['total']}</div><div class="lbl">Total Loans</div></div>
      <div class="kpi" style="border-color:#d97706"><div class="val" style="color:#d97706">{counts['pending']}</div><div class="lbl">Pending Approval</div></div>
      <div class="kpi" style="border-color:#059669"><div class="val" style="color:#059669">{counts['approved']}</div><div class="lbl">Active Loans</div></div>
      <div class="kpi" style="border-color:#dc2626"><div class="val" style="color:#dc2626">{counts['overdue']}</div><div class="lbl">Overdue EMIs</div></div>
      <div class="kpi" style="border-color:#0ea5e9"><div class="val" style="color:#0ea5e9">{counts['upcoming']}</div><div class="lbl">Due in 10 Days</div></div>
      <div class="kpi" style="border-color:#6366f1"><div class="val" style="color:#6366f1">{counts['closed']}</div><div class="lbl">Closed Loans</div></div>
    </div>
    <div class="form-grid" style="margin-top:16px;">
      <div class="card"><b>💰 Total Disbursed</b><br><span style="font-size:21px;color:var(--accent);font-weight:700;">₹{tla:,.2f}</span></div>
      <div class="card"><b>✅ Total Collected</b><br><span style="font-size:21px;color:var(--green);font-weight:700;">₹{tr:,.2f}</span></div>
      <div class="card"><b>⏳ Outstanding</b><br><span style="font-size:21px;color:var(--red);font-weight:700;">₹{tp:,.2f}</span></div>
      <div class="card"><b>📋 Total Loans</b><br><span style="font-size:21px;color:var(--muted);font-weight:700;">{tl}</span></div>
    </div>
    """

    charts = f"""
    <div class="chart-grid">
      <div class="chart-box">
        <h3>📈 Monthly Collections</h3>
        <canvas id="barChart" height="110" style="max-height:110px"></canvas>
      </div>
      <div class="chart-box">
        <h3>🍩 Loan Status</h3>
        <canvas id="donutChart" height="110" style="max-height:110px"></canvas>
      </div>
    </div>
    <div class="chart-grid" style="margin-top:0;">
      <div class="chart-box">
        <h3>🚗 Loans by Vehicle Type</h3>
        <canvas id="typeChart" height="110" style="max-height:110px"></canvas>
      </div>
      <div class="chart-box" style="display:flex;flex-direction:column;justify-content:center;">
        <h3>📊 Quick Stats</h3>
        <div style="display:flex;flex-direction:column;gap:8px;font-size:13px;">
          <div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border);">
            <span>Collection Rate</span>
            <b style="color:var(--green)">{"N/A" if tla==0 else f"{tr/tla*100:.1f}%"}</b>
          </div>
          <div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border);">
            <span>Overdue Loans</span>
            <b style="color:var(--red)">{len(set(e['loan_number'] for e in overdue))}</b>
          </div>
          <div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border);">
            <span>Total Overdue Amount</span>
            <b style="color:var(--red)">₹{sum(float(e.get('remaining_amount') or e['emi_amount']) for e in overdue):,.2f}</b>
          </div>
          <div style="display:flex;justify-content:space-between;padding:6px 0;">
            <span>Upcoming Due (10d)</span>
            <b style="color:var(--amber)">₹{sum(float(e.get('remaining_amount') or e['emi_amount']) for e in upcoming):,.2f}</b>
          </div>
        </div>
      </div>
    </div>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <script>
    const COLORS=['#1a4fad','#059669','#d97706','#dc2626','#6366f1','#0ea5e9','#7c3aed','#db2777'];

    // Monthly bar chart
    const barCtx=document.getElementById('barChart').getContext('2d');
    new Chart(barCtx,{{
      type:'bar',
      data:{{
        labels:{bar_labels},
        datasets:[{{
          label:'Collections (₹)',
          data:{bar_data},
          backgroundColor:'rgba(26,79,173,0.7)',
          borderColor:'#1a4fad',
          borderWidth:1,
          borderRadius:4
        }}]
      }},
      options:{{
        maintainAspectRatio:false,
        responsive:true,plugins:{{legend:{{display:false}},
          tooltip:{{callbacks:{{label:c=>'₹'+c.parsed.y.toLocaleString('en-IN')}}}}
        }},
        scales:{{y:{{ticks:{{callback:v=>'₹'+v.toLocaleString('en-IN')}},grid:{{color:'#eef2f9'}}}}}}
      }}
    }});

    // Status donut
    const dCtx=document.getElementById('donutChart').getContext('2d');
    new Chart(dCtx,{{
      type:'doughnut',
      data:{{
        labels:{stat_labels},
        datasets:[{{data:{stat_data},backgroundColor:COLORS,borderWidth:2,borderColor:'#fff'}}]
      }},
      options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{position:'bottom',labels:{{font:{{size:10}},boxWidth:12}}}}}}}}
    }});

    // Vehicle type bar
    const tCtx=document.getElementById('typeChart').getContext('2d');
    new Chart(tCtx,{{
      type:'bar',
      data:{{
        labels:{type_labels},
        datasets:[{{
          label:'Loans',
          data:{type_data},
          backgroundColor:COLORS,
          borderRadius:4
        }}]
      }},
      options:{{
        maintainAspectRatio:false,
        responsive:true,
        plugins:{{legend:{{display:false}}}},
        scales:{{y:{{beginAtZero:true,ticks:{{stepSize:1}}}}}}
      }}
    }});
    </script>
    """

    content = f"<h1>📊 Dashboard</h1>{waiting_items_html()}{attention_panel_html(overdue)}{kpi}{charts}"
    return page("Dashboard", content, "dashboard")

# ── Loans List ─────────────────────────────────────────────────────────────────
@app.route("/loans")
@login_required
def loans():
    q = request.args.get("q","")
    ll = list_all_loans(q)
    rows = ""
    for l in ll:
        sc = {"PendingApproval":"pending","Approved":"approved","Rejected":"rejected","Closed":"closed","Seized":"rejected"}.get(l["status"],"pending")
        rows += f"""<tr>
          <td><b>{l['loan_number']}</b></td><td>{l['customer_name']}</td>
          <td>{l.get('customer_mobile','')}</td><td>{vehicle_html(l)}</td>
          <td>₹{l['loan_amount']:,.2f}</td><td>{l['interest_rate']*100:.1f}%</td>
          <td>{l['tenure']}m</td>
          <td><span class="badge badge-{sc}">{l['status']}</span></td>
          <td><a class="btn btn-sm btn-primary" href="/emis/{l['id']}">EMI</a></td>
        </tr>"""
    content = f"""
    <h1>📋 All Loans</h1>
    <form method="GET" style="margin-bottom:12px;display:flex;gap:8px;flex-wrap:wrap;">
      <input name="q" value="{q}" placeholder="Search loans…" style="max-width:260px;">
      <button class="btn btn-primary btn-sm">Search</button>
      <a href="/loan/add" class="btn btn-success btn-sm">➕ New Loan</a>
    </form>
    <div class="card"><div class="table-wrap">
      <table>
        <tr><th>Loan #</th><th>Customer</th><th>Mobile</th><th>Vehicle</th>
            <th>Amount</th><th>Rate</th><th>Tenure</th><th>Status</th><th>EMIs</th></tr>
        {rows or '<tr><td colspan="9" style="text-align:center;color:var(--muted);">No loans found</td></tr>'}
      </table>
    </div></div>"""
    return page("Loans", content, "loans")

# ── Add Loan ───────────────────────────────────────────────────────────────────
@app.route("/loan/add", methods=["GET","POST"])
@login_required
@role_required("superadmin","admin","manager","fieldpia")
def add_loan():
    if request.method == "POST":
        f = request.form
        mob = f.get("customer_mobile","").strip()
        if len(mob) != 10:
            flash("Customer mobile number must be exactly 10 digits.","danger")
            return redirect(url_for("add_loan"))
        try:
            customer_extra = parse_extra_numbers_form(f, "customer", "Customer")
            guarantor_extra = parse_extra_numbers_form(f, "guarantor", "Guarantor")
        except ValueError as e:
            flash(str(e),"danger")
            return redirect(url_for("add_loan"))
        # Vehicle details are only mandatory for a reloan
        is_reloan_flag = f.get("is_reloan") == "yes"
        # Trim vehicle number spaces
        vehicle_number_clean = f.get("vehicle_number","").strip().replace(" ","").upper()
        if is_reloan_flag:
            if not vehicle_number_clean:
                flash("Vehicle Number is mandatory for a reloan.","danger")
                return redirect(url_for("add_loan"))
            # Validate mandatory vehicle fields
            for field,label in [("vehicle_type","Vehicle Type"),("vehicle_model","Vehicle Model"),("vehicle_name","Vehicle Name"),
                                 ("engine_number","Engine Number"),("chassis_number","Chassis Number"),("vehicle_colour","Vehicle Colour")]:
                if not f.get(field,"").strip():
                    flash(f"{label} is mandatory for a reloan.","danger")
                    return redirect(url_for("add_loan"))
        try:
            extra_vehicles = parse_extra_vehicles(f, is_reloan_flag)
            extra_guarantors = parse_extra_guarantors(f)
        except ValueError as e:
            flash(str(e),"danger")
            return redirect(url_for("add_loan"))
        current_addr = f.get("customer_address","").strip()
        if not current_addr:
            flash("Current address is mandatory.","danger")
            return redirect(url_for("add_loan"))
        permanent_addr = current_addr if f.get("same_address") == "yes" else f.get("customer_permanent_address","").strip()
        if not permanent_addr:
            flash("Permanent address is mandatory (or tick 'Same as current address').","danger")
            return redirect(url_for("add_loan"))
        # Validate Aadhar (12 digits)
        aadhar = f.get("aadhar_number","").strip().replace(" ","")
        if aadhar and (not aadhar.isdigit() or len(aadhar) != 12):
            flash("Aadhar number must be exactly 12 digits.","danger")
            return redirect(url_for("add_loan"))
        # Handle attachment — now a Google Drive link (text), not a file
        attachment = f.get("attachment","").strip()
        # Back-compat: if someone somehow posted a file, ignore it gracefully

        # Validate Loan Date (disbursal date) — mandatory, cannot be a future date
        loan_date_str = f.get("loan_date","").strip()
        if not loan_date_str:
            flash("Loan Date is mandatory.","danger")
            return redirect(url_for("add_loan"))
        try:
            loan_date_val = datetime.strptime(loan_date_str, "%Y-%m-%d").date()
        except ValueError:
            flash("Invalid Loan Date.","danger")
            return redirect(url_for("add_loan"))
        if loan_date_val > date.today():
            flash("Loan Date cannot be a future date.","danger")
            return redirect(url_for("add_loan"))

        # EMI Start Date — user-editable; defaults to 1 month after Loan Date if left blank
        emi_start_date_str = f.get("emi_start_date","").strip()
        if emi_start_date_str:
            try:
                emi_start_date = datetime.strptime(emi_start_date_str, "%Y-%m-%d").date()
            except ValueError:
                flash("Invalid EMI Start Date.","danger")
                return redirect(url_for("add_loan"))
        else:
            emi_start_date = add_months(loan_date_val, 1)

        # Loan Number — user-editable; auto-suggested but can be overridden
        loan_number_val = f.get("loan_number","").strip()
        if not loan_number_val:
            try:
                loan_number_val = next_loan_number()
            except Exception:
                flash("Could not generate loan number. Please try again.","danger")
                return redirect(url_for("add_loan"))

        # Field visit / key / RC / proof & documents questions
        field_visit = f.get("field_visit","")
        if field_visit not in ("yes","no"):
            flash("Please answer 'Field Visit Done?' (Yes / No).","danger")
            return redirect(url_for("add_loan"))
        field_visit_remark = f.get("field_visit_remark","").strip()
        field_visit_date = None
        field_visited_by = ""
        if field_visit == "no":
            if not field_visit_remark:
                flash("Please enter why the field visit was not done.","danger")
                return redirect(url_for("add_loan"))
        else:
            field_visit_remark = ""
            field_visited_by = f.get("field_visited_by","").strip()
            if not field_visited_by:
                flash("Please enter the name of the person who did the field visit.","danger")
                return redirect(url_for("add_loan"))
            fv_str = f.get("field_visit_date","").strip()
            if fv_str:
                try:
                    fv_date = datetime.strptime(fv_str, "%Y-%m-%d").date()
                except ValueError:
                    flash("Invalid Field Visit Date.","danger")
                    return redirect(url_for("add_loan"))
                if fv_date > date.today():
                    flash("Field Visit Date cannot be a future date.","danger")
                    return redirect(url_for("add_loan"))
                field_visit_date = fv_date.isoformat()
            else:
                field_visit_date = loan_date_val.isoformat()
        handover = {}
        for key, label in (("key_received","Key Received"),("rc_received","RC Received"),
                           ("docs_received","Proof & Documents Collected")):
            ans = f.get(key,"")
            if key == "key_received" and ans == "na":
                if not f.get("key_na_remark","").strip():
                    flash("Key Received = Not required: please enter the remark (why it is not required).","danger")
                    return redirect(url_for("add_loan"))
            elif ans not in ("yes","no"):
                flash(f"Please answer '{label}?' (Yes / No).","danger")
                return redirect(url_for("add_loan"))
            handover[key] = ans
        collected = {}
        try:
            for short, flag, lab in (("key", "key_received", "Key Received"), ("rc", "rc_received", "RC Received"),
                                     ("docs", "docs_received", "Proof & Documents Collected")):
                if handover[flag] == "yes":
                    collected[short] = parse_collected(f.get(short + "_date"), f.get(short + "_by"), lab, loan_date_val.isoformat())
        except ValueError as e:
            flash(str(e), "danger")
            return redirect(url_for("add_loan"))
        # cheque leaf, address proof, transfer certificate, police fine, guarantor addresses
        docs_extra, leaves, cheque_numbers, fine_main = {}, None, "", 0.0
        try:
            ans = f.get("cheque_received", "")
            if ans not in ("yes", "no", "na"): raise ValueError("Please answer 'Cheque leaf signed?' (Yes / No / Not required).")
            docs_extra["cheque"] = ans
            if ans == "na" and not f.get("cheque_na_remark", "").strip():
                raise ValueError("Cheque leaf signed = Not required: please enter the remark (why it is not required).")
            if ans == "yes":
                collected["cheque"] = parse_collected(f.get("cheque_date"), f.get("cheque_by"), "Cheque leaf signed", loan_date_val.isoformat())
            # address proof: Aadhar or EB bill — at least one must be answered
            for short, lab in (("aadhar", "Aadhar (address proof) collected"), ("eb", "EB bill (address proof) collected")):
                ans = f.get(short + "_received", "")
                if ans not in ("yes", "no", ""): raise ValueError(f"Please answer '{lab}?' (Yes / No).")
                docs_extra[short] = ans or None
                if ans == "yes":
                    collected[short] = parse_collected(f.get(short + "_date"), f.get(short + "_by"), lab, loan_date_val.isoformat())
            if not (docs_extra["aadhar"] or docs_extra["eb"]):
                raise ValueError("Address proof: please answer either 'Aadhar collected?' or 'EB bill collected?'.")
            owner = f.get("other_owner", "")
            if owner not in ("yes", "no"): raise ValueError("Please answer 'Vehicle in another owner's name?' (Yes / No).")
            docs_extra["other_owner"] = owner; docs_extra["tc"] = ""
            if owner == "yes":
                ans = f.get("tc_received", "")
                if ans not in ("yes", "no"): raise ValueError("Please answer 'Transfer certificate collected?' (Yes / No).")
                docs_extra["tc"] = ans
                if ans == "yes":
                    collected["tc"] = parse_collected(f.get("tc_date"), f.get("tc_by"), "Transfer certificate", loan_date_val.isoformat())
            fine_main = parse_fine(f.get("police_fine"), "Police fine")
            g_cur = f.get("guarantor_address", "").strip()
            g_perm = g_cur if f.get("guarantor_same_address") == "yes" else f.get("guarantor_permanent_address", "").strip()
            if f.get("guarantor_name", "").strip() or f.get("guarantor_mobile", "").strip() or g_cur or g_perm:
                if not g_cur: raise ValueError("Guarantor Current Address is mandatory.")
                if not g_perm: raise ValueError("Guarantor Permanent Address is mandatory (or tick 'same as current address').")
        except ValueError as e:
            flash(str(e), "danger")
            return redirect(url_for("add_loan"))

        # Resolve interest rate depending on calc_mode
        calc_mode = f.get("calc_mode","rate")
        if calc_mode == "emi":
            # Back-calculated rate stored in dedicated hidden field (not the visible interest_rate input)
            interest_rate_val = f.get("interest_rate_calc","").strip()
            if not interest_rate_val:
                flash("Could not calculate interest rate — please enter Loan Amount, EMI Amount, and Tenure, then wait for the preview to appear before submitting.","danger")
                return redirect(url_for("add_loan"))
        else:
            interest_rate_val = f.get("interest_rate","").strip()
            if not interest_rate_val:
                flash("Interest Rate is mandatory.","danger")
                return redirect(url_for("add_loan"))
        try:
            new_loan_id = create_loan(
                loan_number_val, f["customer_name"], mob,
                f["customer_address"], f.get("customer_location",""),
                f["vehicle_type"], vehicle_number_clean, f["vehicle_model"], f.get("vehicle_name",""),
                f["engine_number"], f["chassis_number"], f["vehicle_colour"],
                f["loan_amount"], interest_rate_val, f["tenure"], emi_start_date.isoformat(),
                aadhar, f.get("customer_email",""),
                f.get("guarantor_name",""), f.get("guarantor_address",""), f.get("guarantor_mobile",""),
                1 if f.get("is_reloan")=="yes" else 0, f.get("reloan_ref",""),
                f.get("remarks",""), attachment,
                f.get("custom_emi_amount","") if f.get("emi_mode")=="manual" else None,
                loan_date_val.isoformat(),
                f.get("guarantor_location","").strip(),
                permanent_addr
            )
            c_ = get_cur()
            c_.execute("UPDATE LoanEntry SET customer_extra_numbers=?, guarantor_extra_numbers=? WHERE id=?",
                       (customer_extra or None, guarantor_extra or None, new_loan_id))
            get_db().commit()
            record_handover_details(new_loan_id, loan_date_val, field_visit, field_visit_date,
                                    field_visit_remark, handover, session.get("username",""),
                                    field_visited_by, collected)
            record_extra_documents(new_loan_id, loan_date_val, docs_extra, collected, leaves, cheque_numbers, fine_main,
                                   session.get("username",""))
            c_ = get_cur()
            c_.execute("UPDATE LoanEntry SET guarantor_permanent_address=?, key_na_remark=?, cheque_na_remark=? WHERE id=?",
                       (g_perm or None, f.get("key_na_remark","").strip() if handover["key_received"] == "na" else None,
                        f.get("cheque_na_remark","").strip() if docs_extra["cheque"] == "na" else None, new_loan_id))
            get_db().commit()
            save_extra_vehicles(new_loan_id, extra_vehicles, loan_date_val, session.get("username",""))
            save_extra_guarantors(new_loan_id, extra_guarantors)
            flash("Loan submitted for approval.","success")
            return redirect(url_for("loans"))
        except Exception as e:
            if "UNIQUE" in str(e).upper() and "loan_number" in str(e):
                flash(f"Loan Number '{loan_number_val}' is already in use. Please choose a different one.","danger")
            else:
                flash(str(e),"danger")

    try: next_ln = next_loan_number()
    except: next_ln = f"LN-{datetime.now().year}-01"
    xv_area, xg_area = extra_blocks_section()
    main_vehicle_docs = vehicle_docs_html("other_owner", "tc_received", "tc_date", "tc_by", "police_fine", 0)
    addr_attrs = ' class="addr-proof" required onchange="toggleHO(this);syncAddrProof()"'
    loan_docs_groups = (ho_group("Cheque leaf signed?", "cheque", not_required=True)
                        + ho_group("Address proof — Aadhar collected?", "aadhar", attrs=addr_attrs)
                        + ho_group("Address proof — EB bill collected?", "eb", attrs=addr_attrs)
                        + '<div class="form-group full" style="font-size:11px;color:var(--muted);margin-top:-6px;">'
                          '🪪 Address proof: answer at least one — Aadhar or EB bill.</div>'
                        + '<script>window.syncAddrProof=function(){var s=document.querySelectorAll(".addr-proof"),'
                          'any=Array.prototype.some.call(s,function(x){return x.value;});'
                          's.forEach(function(x){if(any)x.removeAttribute("required");else x.setAttribute("required","required");});};</script>')

    content = f"""
    {_EXTRA_BLOCKS_JS}
    <h1>➕ New Loan Application</h1>
    <div class="card">
    <form method="POST" id="loanForm" enctype="multipart/form-data">
      <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:14px;">
        <label class="loan-kind"><input type="radio" name="is_reloan" value="no" required onchange="pickLoanKind(this.value)"> 🆕 New Loan</label>
        <label class="loan-kind"><input type="radio" name="is_reloan" value="yes" required onchange="pickLoanKind(this.value)"> 🔄 Reloan</label>
      </div>
      <style>.loan-kind{{display:flex;align-items:center;gap:8px;padding:10px 18px;border:2px solid var(--border);border-radius:10px;
        cursor:pointer;font-size:14px;font-weight:700;text-transform:none;margin:0;}}
        .loan-kind:has(input:checked){{border-color:var(--accent);background:var(--surface2);color:var(--accent);}}
        .loan-kind input{{width:auto;min-height:0;margin:0;}}</style>
      <p id="loan_kind_hint" style="font-size:13px;color:var(--muted);">Choose <b>New Loan</b> or <b>Reloan</b> to open the form.</p>
      <div id="loan_body" style="display:none;">
      <div class="form-grid">

        <div class="section-title">📄 Loan Details</div>
        <div class="form-group">
          <label>Loan Number * <span style="font-size:10px;color:var(--muted);">(auto-suggested — edit if needed)</span></label>
          <input name="loan_number" id="loan_number" value="{next_ln}" required>
        </div>
        <div class="form-group">
          <label>Loan Date * <span style="font-size:10px;color:var(--muted);">(date loan disbursed — cannot be future)</span></label>
          <input type="date" name="loan_date" id="loan_date" value="{date.today().isoformat()}"
                 max="{date.today().isoformat()}" required oninput="updateEmiStartDate()">
        </div>
        <div class="form-group">
          <label>EMI Start Date * <span style="font-size:10px;color:var(--muted);">(auto-suggested: 1 month after Loan Date — edit if needed)</span></label>
          <input type="date" name="emi_start_date" id="emi_start_date_display" required>
        </div>

        <!-- EMI Smart Calculator Mode -->
        <div class="form-group full">
          <label>🧮 Calculator Mode *</label>
          <select name="calc_mode" id="calc_mode" onchange="switchCalcMode(this.value)">
            <option value="rate">Enter Amount + Interest Rate + Tenure → Auto EMI</option>
            <option value="emi">Enter Amount + EMI + Tenure → Auto Interest Rate</option>
          </select>
        </div>

        <div class="form-group">
          <label>Loan Amount (₹) *</label>
          <input type="number" name="loan_amount" id="loan_amount" min="1" step="0.01" required oninput="calcDue()">
        </div>
        <div class="form-group" id="interest_group">
          <label>Interest Rate (% p.a.) *</label>
          <input type="number" name="interest_rate" id="interest_rate" min="0" step="0.01" oninput="calcDue()" placeholder="e.g. 24">
        </div>
        <div class="form-group" id="emi_input_group" style="display:none;">
          <label>EMI Amount (₹) * <span style="font-size:10px;color:var(--accent);">Interest rate will be auto-calculated</span></label>
          <input type="number" name="entered_emi" id="entered_emi" min="1" step="0.01" oninput="calcDue()" placeholder="e.g. 2500">
          <!-- Unique name so Flask doesn't confuse it with the visible interest_rate field -->
          <input type="hidden" name="interest_rate_calc" id="interest_rate_hidden">
        </div>
        <div class="form-group">
          <label>Tenure (Months) *</label>
          <input type="number" name="tenure" id="tenure" min="1" max="360" required oninput="calcDue()">
        </div>
        <div class="form-group" id="emi_mode_group">
          <label>EMI Calculation *</label>
          <select name="emi_mode" id="emi_mode" onchange="toggleEmiMode(this.value)">
            <option value="auto">Auto (from Interest Rate)</option>
            <option value="manual">Manual (I'll set a rounded EMI)</option>
          </select>
        </div>
        <div class="form-group" id="custom_emi_group" style="display:none;">
          <label>Custom EMI Amount (₹) <span style="font-size:10px;color:var(--muted);">(rounded — leftover becomes a final installment)</span></label>
          <input type="number" name="custom_emi_amount" id="custom_emi_amount" min="1" step="1" oninput="calcDue()" placeholder="e.g. 1250">
        </div>
        <div class="form-group">
          <label>Field Visit Done? *</label>
          <select name="field_visit" id="field_visit" required onchange="toggleFieldVisit(this.value)">
            <option value="">-- Select --</option><option value="yes">Yes</option><option value="no">No</option>
          </select>
        </div>
        <div class="form-group" id="fv_by_group" style="display:none;">
          <label>Visited By * <span style="font-size:10px;color:var(--muted);">(name of the person)</span></label>
          <input name="field_visited_by" id="field_visited_by" placeholder="Enter name">
        </div>
        <div class="form-group" id="fv_date_group" style="display:none;">
          <label>Field Visit Date <span style="font-size:10px;color:var(--muted);">(defaults to loan date)</span></label>
          <input type="date" name="field_visit_date" id="field_visit_date" max="{date.today().isoformat()}">
        </div>
        <div class="form-group full" id="fv_remark_group" style="display:none;">
          <label>Why was the field visit not done? *</label>
          <textarea name="field_visit_remark" id="field_visit_remark" rows="2" placeholder="Enter the reason"></textarea>
        </div>

        <div class="section-title">👤 Customer Details</div>
        <div class="form-group">
          <label>Customer Name *</label>
          <input name="customer_name" id="cust_name" required>
        </div>
        <div class="form-group">
          <label>Mobile (10 digits) *</label>
          <input name="customer_mobile" id="cust_mobile" maxlength="10"
                 pattern="[0-9]{{10}}" required
                 oninput="this.value=this.value.replace(/[^0-9]/g,'').slice(0,10)">
        </div>
        {extra_numbers_block('customer')}
        <div class="form-group full">
          <label>Current Address * (mandatory)</label>
          <textarea name="customer_address" id="cust_address" rows="2" required oninput="syncPermanentAddress()"></textarea>
        </div>
        <div class="form-group full">
          <label>Permanent Address * (mandatory)</label>
          <label style="display:flex;align-items:center;gap:8px;font-weight:600;text-transform:none;font-size:13px;margin-bottom:6px;cursor:pointer;">
            <input type="checkbox" name="same_address" id="same_address" value="yes"
                   style="width:auto;min-height:0;margin:0;" onchange="syncPermanentAddress()">
            Same as current address
          </label>
          <textarea name="customer_permanent_address" id="cust_perm_address" rows="2" required></textarea>
        </div>
        <div class="form-group full">
          <label>📍 GPS Location <span style="font-size:10px;color:var(--muted);">(tap button or enter manually)</span></label>
          <div style="display:flex;gap:8px;align-items:stretch;flex-wrap:wrap;">
            <input name="customer_location" id="cust_location"
                   placeholder="e.g. 10.9876,78.1234 or area name"
                   style="flex:1;min-width:160px;"
                   oninput="onLocationTyped(this.value)">
            <button type="button" onclick="getGPS()" id="gps_btn"
                    style="background:var(--amber);color:#fff;border:none;border-radius:8px;
                           padding:0 14px;font-size:13px;font-weight:700;cursor:pointer;
                           white-space:nowrap;min-height:46px;flex-shrink:0;">
              📡 Get GPS
            </button>
            <a id="map_link" href="#" target="_blank"
               style="display:none;background:#34a853;color:#fff;border-radius:8px;
                      padding:0 14px;font-size:13px;font-weight:700;text-decoration:none;
                      white-space:nowrap;min-height:46px;line-height:46px;flex-shrink:0;">
              🗺️ Show on Map
            </a>
          </div>
          <small id="gps_status" style="color:var(--muted);font-size:11px;margin-top:2px;"></small>
        </div>
        <div class="form-group">
          <label>Aadhar Number</label>
          <input name="aadhar_number" id="cust_aadhar" maxlength="12" placeholder="12-digit Aadhar"
                 oninput="this.value=this.value.replace(/[^0-9]/g,'').slice(0,12)">
        </div>

        <!-- Reloan -->
        <div id="reloan_section" style="display:none;grid-column:1/-1;">
          <div class="form-grid">
            <div class="section-title">🔄 Reloan Details</div>
            <div class="form-group">
              <label>Previous Loan Number</label>
              <input name="reloan_ref" id="reloan_ref" placeholder="LN-2025-XX">
            </div>
            <div class="form-group" style="align-self:flex-end;">
              <button type="button" class="btn btn-amber" onclick="checkReloan()">Check History</button>
            </div>
          </div>
          <div id="risk_box" class="risk-box"></div>
        </div>

        <div class="section-title">🛡️ Guarantor Details <span style="font-size:11px;text-transform:none;color:var(--muted);">(optional — but if a guarantor is entered, both addresses are required)</span></div>
        <div class="form-group">
          <label>Guarantor Name</label><input name="guarantor_name">
        </div>
        <div class="form-group">
          <label>Guarantor Mobile</label>
          <input name="guarantor_mobile" maxlength="10"
                 oninput="this.value=this.value.replace(/[^0-9]/g,'').slice(0,10)">
        </div>
        {extra_numbers_block('guarantor')}
        <div class="form-group full gaddr" id="gMainAddr">
          <label>Guarantor Current Address * <span style="font-size:10px;color:var(--muted);">(mandatory when a guarantor is entered)</span></label>
          <textarea name="guarantor_address" class="g-cur" rows="2"></textarea>
          <label style="display:flex;align-items:center;gap:8px;font-weight:600;text-transform:none;font-size:13px;margin:6px 0 4px;cursor:pointer;">
            <input type="checkbox" name="guarantor_same_address" value="yes" class="g-same" style="width:auto;min-height:0;margin:0;" onchange="syncGAddr(this)">
            Permanent address same as current address</label>
          <label>Guarantor Permanent Address * <span style="font-size:10px;color:var(--muted);">(mandatory when a guarantor is entered)</span></label>
          <textarea name="guarantor_permanent_address" class="g-perm" rows="2"></textarea>
        </div>
        <div class="form-group full">
          <label>📍 Guarantor GPS Location <span style="font-size:10px;color:var(--muted);">(tap button or enter manually)</span></label>
          <div style="display:flex;gap:8px;align-items:stretch;flex-wrap:wrap;">
            <input name="guarantor_location" id="g_location"
                   placeholder="e.g. 10.9876,78.1234 or area name"
                   style="flex:1;min-width:160px;"
                   oninput="onLocationTyped(this.value,'g_gps_status','g_map_link')">
            <button type="button" onclick="getGPS('g_location','g_gps_btn','g_gps_status','g_map_link')" id="g_gps_btn"
                    style="background:var(--amber);color:#fff;border:none;border-radius:8px;
                           padding:0 14px;font-size:13px;font-weight:700;cursor:pointer;
                           white-space:nowrap;min-height:46px;flex-shrink:0;">
              📡 Get GPS
            </button>
            <a id="g_map_link" href="#" target="_blank"
               style="display:none;background:#34a853;color:#fff;border-radius:8px;
                      padding:0 14px;font-size:13px;font-weight:700;text-decoration:none;
                      white-space:nowrap;min-height:46px;line-height:46px;flex-shrink:0;">
              🗺️ Show on Map
            </a>
          </div>
          <small id="g_gps_status" style="color:var(--muted);font-size:11px;margin-top:2px;"></small>
        </div>
        {xg_area}

        <div class="section-title">🚗 Vehicle Details <span id="vehicle_mandatory_note">(optional unless Reloan = Yes)</span></div>
        <div class="form-group">
          <label>Vehicle Type</label>
          <select name="vehicle_type" id="vehicle_type" class="vehicle-req-field">
            <option value="">-- Select --</option>
            <option>Two Wheeler</option><option>Three Wheeler</option>
            <option>Four Wheeler</option><option>Commercial Vehicle</option><option>Other</option>
          </select>
        </div>
        <div class="form-group">
          <label>Vehicle Number <span style="font-size:10px;color:var(--muted);">(spaces auto-removed)</span></label>
          <input name="vehicle_number" id="vehicle_number" class="vehicle-req-field" placeholder="TN01AB1234"
                 oninput="this.value=this.value.replace(/ /g,'').toUpperCase()">
        </div>
        <div class="form-group">
          <label>Vehicle Name <span style="font-size:10px;color:var(--muted);">(brand &amp; variant)</span></label>
          <input name="vehicle_name" id="vehicle_name" class="vehicle-req-field" placeholder="e.g. Honda Activa">
        </div>
        <div class="form-group">
          <label>Vehicle Model <span style="font-size:10px;color:var(--muted);">(model/year)</span></label>
          <input name="vehicle_model" id="vehicle_model" class="vehicle-req-field" placeholder="e.g. 6G 2023">
        </div>
        <div class="form-group">
          <label>Engine Number</label>
          <input name="engine_number" id="engine_number" class="vehicle-req-field">
        </div>
        <div class="form-group">
          <label>Chassis Number</label>
          <input name="chassis_number" id="chassis_number" class="vehicle-req-field">
        </div>
        <div class="form-group">
          <label>Vehicle Colour</label>
          <input name="vehicle_colour" id="vehicle_colour" class="vehicle-req-field">
        </div>
        <div class="form-group">
          <label>Key Received? * <span style="font-size:10px;color:var(--muted);">(No = follow-up in {handover_days('key')} days)</span></label>
          <select name="key_received" id="key_received" required onchange="toggleHO(this)">
            <option value="">-- Select --</option><option value="yes">Yes</option><option value="no">No</option><option value="na">Not required</option>
          </select>
          {handover_detail_html('key_date', 'key_by')}
          {na_remark_html('key_na_remark')}
        </div>
        <div class="form-group">
          <label>RC Received? * <span style="font-size:10px;color:var(--muted);">(No = follow-up in {handover_days('rc')} days)</span></label>
          <select name="rc_received" id="rc_received" required onchange="toggleHO(this)">
            <option value="">-- Select --</option><option value="yes">Yes</option><option value="no">No</option>
          </select>
          {handover_detail_html('rc_date', 'rc_by')}
        </div>
        {main_vehicle_docs}
        <div class="form-group full" style="font-size:12px;color:var(--muted);">
          🚗 Giving two or three vehicles under this one loan number? Add each extra vehicle below &mdash; every vehicle has its own key / RC / proof status.
        </div>
        {xv_area}

        <div class="section-title">📎 Documents & Remarks</div>
        <div class="form-group">
          <label>Proof &amp; Documents Collected? * <span style="font-size:10px;color:var(--muted);">(No = follow-up in {handover_days('docs')} days)</span></label>
          <select name="docs_received" id="docs_received" required onchange="toggleHO(this)">
            <option value="">-- Select --</option><option value="yes">Yes</option><option value="no">No</option>
          </select>
          {handover_detail_html('docs_date', 'docs_by')}
        </div>
        {loan_docs_groups}
        <div class="form-group full">
          <label>Attachment <span style="font-size:10px;color:var(--muted);">(Upload to Google Drive)</span></label>
          <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;">
            <a href="https://drive.google.com/drive/folders/YOUR_FOLDER_ID_HERE" target="_blank"
               style="display:inline-flex;align-items:center;gap:8px;background:#1a73e8;color:#fff;
                      border:none;border-radius:8px;padding:10px 18px;font-size:13px;font-weight:700;
                      cursor:pointer;text-decoration:none;white-space:nowrap;">
              📁 Upload to Google Drive
            </a>
          </div>
          <small style="color:var(--muted);font-size:11px;margin-top:4px;display:block;">
            Click the button → Google Drive opens in a new tab. Upload your document there.
          </small>
          <!-- Hidden field so form submission doesn't break -->
          <input type="hidden" name="attachment" value="">
        </div>
        <div class="form-group full">
          <label>Remarks <span style="font-size:10px;color:var(--muted);">(optional — any notes about this loan)</span></label>
          <textarea name="remarks" rows="3" placeholder="e.g. Customer verified, documents checked..."></textarea>
        </div>
      </div>

      <!-- Due Preview -->
      <div class="due-preview" id="due_preview">
        <h3>💵 Loan Summary (Preview)</h3>
        <div class="due-grid">
          <div class="due-item"><div class="lbl">Loan Amount</div><div class="val" id="dp_principal">—</div></div>
          <div class="due-item"><div class="lbl">Total Interest</div><div class="val" id="dp_interest">—</div></div>
          <div class="due-item"><div class="lbl">Total Due</div><div class="val" id="dp_total">—</div></div>
          <div class="due-item"><div class="lbl">Monthly EMI</div><div class="val" id="dp_emi">—</div></div>
          <div class="due-item"><div class="lbl">Tenure</div><div class="val" id="dp_tenure">—</div></div>
          <div class="due-item"><div class="lbl">Rate p.a.</div><div class="val" id="dp_rate">—</div></div>
        </div>
        <div id="dp_schedule_note" style="display:none;margin-top:12px;padding:10px 12px;background:rgba(255,255,255,.15);border-radius:8px;font-size:12.5px;line-height:1.6;"></div>
      </div>

      <div style="margin-top:18px;display:flex;gap:10px;flex-wrap:wrap;">
        <button type="submit" class="btn btn-primary">Submit Application</button>
        <a href="/loans" class="btn" style="background:var(--surface2);color:var(--text);">Cancel</a>
      </div>
    </div>
    </form>
    </div>

    <div class="fu-modal-overlay" id="loanConfirmModal">
      <div class="fu-modal">
        <h3>Confirm Loan Application</h3>
        <p style="font-size:12px;color:var(--muted);margin-bottom:8px;">Please review before submitting:</p>
        <div id="loanConfirmSummary" style="font-size:13px;line-height:1.9;"></div>
        <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:16px;">
          <button type="button" class="btn" style="background:var(--surface2);color:var(--text);" onclick="closeLoanConfirm()">✎ Edit</button>
          <button type="button" class="btn btn-primary" onclick="confirmLoanSubmit()">✅ Confirm &amp; Submit</button>
        </div>
      </div>
    </div>

    <script>
    /* ── Unsaved-data tab-switch warning ─────────────────────────────────── */
    let _loanFormDirty = false;
    let _loanConfirmed = false;
    const _loanForm = document.getElementById('loanForm');
    if(_loanForm){{
      _loanForm.addEventListener('input', ()=>{{ _loanFormDirty = true; }});
      _loanForm.addEventListener('change', ()=>{{ _loanFormDirty = true; }});
      // Pressing Enter in any field (e.g. while typing the Aadhaar number) must NOT
      // submit this long multi-section form early — a failed server-side check
      // redirects to a blank form and wipes everything already typed.
      _loanForm.addEventListener('keydown', function(e){{
        if(e.key === 'Enter' && e.target.tagName !== 'TEXTAREA' && e.target.type !== 'submit'){{
          e.preventDefault();
        }}
      }});
      _loanForm.addEventListener('submit', function(e){{
        if(_loanConfirmed){{ _loanFormDirty = false; return; }}
        e.preventDefault();
        const gn = name => {{ const el = _loanForm.querySelector('[name="'+name+'"]'); return el ? el.value : ''; }};
        const amt = parseFloat(gn('loan_amount')||0);
        const dmy = s => {{ const p=(s||'').split('-'); return p.length===3 ? p[2]+'/'+p[1]+'/'+p[0] : '—'; }};
        document.getElementById('loanConfirmSummary').innerHTML = `
          <div><b>Loan Number:</b> ${{gn('loan_number')||'—'}}</div>
          <div><b>Customer:</b> ${{gn('customer_name')||'—'}}</div>
          <div><b>Mobile:</b> ${{gn('customer_mobile')||'—'}}</div>
          <div><b>Loan Amount:</b> ₹${{amt.toLocaleString('en-IN')}}</div>
          <div><b>Tenure:</b> ${{gn('tenure')||'—'}} months</div>
          <div><b>Loan Date:</b> ${{dmy(gn('loan_date'))}}</div>
          <div><b>EMI Start Date:</b> ${{dmy(gn('emi_start_date'))}}</div>
        `;
        document.getElementById('loanConfirmModal').classList.add('open');
      }});
    }}
    function closeLoanConfirm(){{
      document.getElementById('loanConfirmModal').classList.remove('open');
    }}
    function confirmLoanSubmit(){{
      _loanConfirmed = true;
      document.getElementById('loanConfirmModal').classList.remove('open');
      if(_loanForm.requestSubmit) _loanForm.requestSubmit(); else _loanForm.submit();
    }}
    // Intercept sidebar / nav link clicks
    document.addEventListener('DOMContentLoaded', ()=>{{
      document.querySelectorAll('nav a, .sidebar a, .nav-link, [data-nav]').forEach(link=>{{
        link.addEventListener('click', function(e){{
          if(!_loanFormDirty) return;
          const dest = this.getAttribute('href');
          if(!dest || dest==='#') return;
          e.preventDefault();
          if(confirm('⚠️ You have unsaved loan entry data.\\nLeaving this page will clear all data you have entered.\\n\\nClick OK to leave, or Cancel to stay and save.')){{
            _loanFormDirty = false;
            window.location.href = dest;
          }}
        }});
      }});
    }});
    // Also catch browser back/forward/close
    window.addEventListener('beforeunload', function(e){{
      if(_loanFormDirty){{
        e.preventDefault();
        e.returnValue = 'You have unsaved loan entry data. Leave anyway?';
        return e.returnValue;
      }}
    }});

    /* ── Field visit: date when Yes, reason when No ───────────────────────── */
    function toggleFieldVisit(v){{
      const dateGrp = document.getElementById('fv_date_group');
      const remGrp  = document.getElementById('fv_remark_group');
      const rem     = document.getElementById('field_visit_remark');
      const dt      = document.getElementById('field_visit_date');
      const byInp = document.getElementById('field_visited_by');
      document.getElementById('fv_by_group').style.display = v==='yes' ? 'block' : 'none';
      if(v==='yes') byInp.setAttribute('required','required'); else byInp.removeAttribute('required');
      dateGrp.style.display = v==='yes' ? 'block' : 'none';
      remGrp.style.display  = v==='no'  ? 'block' : 'none';
      if(v==='no') rem.setAttribute('required','required'); else rem.removeAttribute('required');
      if(v==='yes' && !dt.value) dt.value = document.getElementById('loan_date').value;
    }}

    /* ── Permanent address = current address (tick box) ──────────────────── */
    function syncPermanentAddress(){{
      const same = document.getElementById('same_address').checked;
      const perm = document.getElementById('cust_perm_address');
      if(same){{
        perm.value = document.getElementById('cust_address').value;
        perm.readOnly = true;
        perm.style.background = 'var(--surface2)';
      }} else {{
        perm.readOnly = false;
        perm.style.background = '';
      }}
    }}

    /* ── GPS ─────────────────────────────────────────────────────────────── */
    function getGPS(inputId='cust_location', btnId='gps_btn', statusId='gps_status', mapId='map_link'){{
      const btn=document.getElementById(btnId);
      const st=document.getElementById(statusId);
      const mapLink=document.getElementById(mapId);
      if(!navigator.geolocation){{
        st.textContent='❌ GPS not supported on this browser.';
        st.style.color='var(--red)'; return;
      }}
      btn.textContent='⏳ Getting…'; btn.disabled=true;
      st.textContent='📡 Getting your location…';
      st.style.color='var(--muted)';
      navigator.geolocation.getCurrentPosition(
        pos=>{{
          const lat=pos.coords.latitude.toFixed(6);
          const lon=pos.coords.longitude.toFixed(6);
          const coords=lat+','+lon;
          document.getElementById(inputId).value=coords;
          st.textContent='✅ Location captured: '+coords;
          st.style.color='var(--green)';
          btn.textContent='📡 Get GPS'; btn.disabled=false;
          // Show map link
          mapLink.href='https://www.google.com/maps?q='+lat+','+lon;
          mapLink.style.display='inline-flex';
        }},
        err=>{{
          st.textContent='❌ '+err.message+' — enter manually.';
          st.style.color='var(--red)';
          btn.textContent='📡 Get GPS'; btn.disabled=false;
        }},
        {{enableHighAccuracy:true,timeout:15000,maximumAge:0}}
      );
    }}

    /* ── GPS location typed manually → show map link ─────────────────────── */
    function onLocationTyped(val, statusId='gps_status', mapId='map_link'){{
      const mapLink = document.getElementById(mapId);
      const statusEl = document.getElementById(statusId);
      val = val.trim();
      // Try to parse as lat,lon coordinates
      const parts = val.split(',');
      if(parts.length === 2){{
        const lat = parseFloat(parts[0].trim());
        const lon = parseFloat(parts[1].trim());
        if(!isNaN(lat) && !isNaN(lon) && lat>=-90 && lat<=90 && lon>=-180 && lon<=180){{
          mapLink.href = 'https://www.google.com/maps?q='+lat+','+lon;
          mapLink.style.display = 'inline-block';
          statusEl.textContent = '✅ Coordinates valid — click Show on Map to verify.';
          statusEl.style.color = 'var(--green)';
          return;
        }}
      }}
      // Plain text address — open maps search
      if(val.length > 3){{
        mapLink.href = 'https://www.google.com/maps/search/'+encodeURIComponent(val);
        mapLink.style.display = 'inline-block';
        statusEl.textContent = 'Click Show on Map to verify this address.';
        statusEl.style.color = 'var(--muted)';
      }} else {{
        mapLink.style.display = 'none';
        statusEl.textContent = '';
      }}
    }}

    /* ── EMI Start Date auto-calc (Loan Date + 1 month, same day) ─────────── */
    function addMonthsJS(dateStr, months){{
      const d = new Date(dateStr + 'T00:00:00');
      const day = d.getDate();
      d.setDate(1);
      d.setMonth(d.getMonth() + months);
      const lastDay = new Date(d.getFullYear(), d.getMonth()+1, 0).getDate();
      d.setDate(Math.min(day, lastDay));
      return d.toISOString().slice(0,10);
    }}
    let emiDateManuallyEdited = false;
    function updateEmiStartDate(){{
      if(emiDateManuallyEdited) return;
      const ld = document.getElementById('loan_date').value;
      if(ld){{
        document.getElementById('emi_start_date_display').value = addMonthsJS(ld, 1);
      }}
    }}
    document.getElementById('emi_start_date_display').addEventListener('input', function(){{
      emiDateManuallyEdited = true;
    }});
    updateEmiStartDate();

    /* ── Reloan helpers ───────────────────────────────────────────────────── */
    function toggleReloan(v){{
      document.getElementById('reloan_section').style.display=v==='yes'?'block':'none';
      const mandatory = v==='yes';
      document.querySelectorAll('.vehicle-req-field').forEach(function(el){{
        if(mandatory){{ el.setAttribute('required','required'); }}
        else{{ el.removeAttribute('required'); }}
      }});
      document.getElementById('vehicle_mandatory_note').textContent =
        mandatory ? '(mandatory for a reloan)' : '(optional unless Reloan = Yes)';
    }}
    function reloanValue(){{ const r=document.querySelector('[name=is_reloan]:checked'); return r ? r.value : 'no'; }}
    function pickLoanKind(v){{
      document.getElementById('loan_body').style.display='block';
      document.getElementById('loan_kind_hint').style.display='none';
      toggleReloan(v);
    }}
    toggleReloan(reloanValue());
    function checkReloan(){{
      const ref=document.getElementById('reloan_ref').value.trim();
      if(!ref){{alert('Enter previous loan number first.');return;}}
      fetch('/api/reloan_check?loan_number='+encodeURIComponent(ref))
        .then(r=>r.json()).then(data=>{{
          const box=document.getElementById('risk_box');
          if(data.error){{box.className='risk-box risk-risk';box.style.display='block';box.innerHTML='<b>⚠️ '+data.error+'</b>';return;}}
          box.className='risk-box risk-'+data.risk.toLowerCase();
          box.style.display='block';
          box.innerHTML=`<b>${{data.decision}}</b><br><small>EMIs: ${{data.total}} | Paid: ${{data.paid}} | Delays: ${{data.delay_count}}</small>`;
          if(data.customer&&data.customer.customer_name){{
            document.getElementById('cust_name').value=data.customer.customer_name||'';
            document.getElementById('cust_mobile').value=data.customer.customer_mobile||'';
            document.getElementById('cust_address').value=data.customer.customer_address||'';
            document.getElementById('cust_perm_address').value=data.customer.customer_permanent_address||'';
            syncPermanentAddress();
            document.getElementById('cust_location').value=data.customer.customer_location||'';
          }}
        }});
    }}

    /* ── Calculator Mode Switch ───────────────────────────────────────────── */
    function switchCalcMode(mode){{
      const rateGroup = document.getElementById('interest_group');
      const emiInputGroup = document.getElementById('emi_input_group');
      const emiModeGroup = document.getElementById('emi_mode_group');
      const customEmiGroup = document.getElementById('custom_emi_group');

      if(mode === 'emi'){{
        // Mode: Amount + EMI + Tenure → back-calculate interest rate
        rateGroup.style.display = 'none';
        document.getElementById('interest_rate').removeAttribute('required');
        emiInputGroup.style.display = 'block';
        document.getElementById('entered_emi').setAttribute('required','required');
        // Hide the manual EMI mode selector (not relevant when EMI is the input)
        emiModeGroup.style.display = 'none';
        customEmiGroup.style.display = 'none';
        document.querySelector('[name=emi_mode]').value = 'auto';
      }} else {{
        // Mode: Amount + Interest + Tenure → auto-calculate EMI
        rateGroup.style.display = 'block';
        document.getElementById('interest_rate').setAttribute('required','required');
        emiInputGroup.style.display = 'none';
        document.getElementById('entered_emi').removeAttribute('required');
        emiModeGroup.style.display = 'block';
      }}
      calcDue();
    }}

    /* ── Format INR ───────────────────────────────────────────────────────── */
    function fmt(v){{return '₹'+parseFloat(v).toLocaleString('en-IN',{{minimumFractionDigits:2,maximumFractionDigits:2}});}}

    function toggleEmiMode(v){{
      document.getElementById('custom_emi_group').style.display = (v==='manual')?'block':'none';
      calcDue();
    }}

    /* ── Main Calculator ──────────────────────────────────────────────────── */
    function calcDue(){{
      const calcMode = document.getElementById('calc_mode').value;
      const amt = parseFloat(document.getElementById('loan_amount').value)||0;
      const tenure = parseInt(document.getElementById('tenure').value)||0;
      const p = document.getElementById('due_preview');
      const note = document.getElementById('dp_schedule_note');

      if(calcMode === 'emi'){{
        // REVERSE CALC: EMI + Amount + Tenure → Interest Rate
        const emiAmt = parseFloat(document.getElementById('entered_emi').value)||0;
        if(amt>0 && emiAmt>0 && tenure>0){{
          // Simple interest formula: Total Due = Principal + P*r*(T/12)
          // Total Due = EMI * Tenure (for simple interest flat rate)
          // So: P*r*(T/12) = (EMI*Tenure) - P
          // r = ((EMI*Tenure) - P) / (P * T/12)  [as decimal]
          const totalDue = emiAmt * tenure;
          const totalInterest = totalDue - amt;
          if(totalInterest < 0){{
            note.style.display='block';
            note.innerHTML='⚠️ EMI × Tenure is less than loan amount! Please check values.';
            p.style.display='block'; return;
          }}
          const rateDecimal = totalInterest / (amt * (tenure/12.0));
          const ratePct = rateDecimal * 100;
          // Store back-calculated rate in hidden field for form submission
          document.getElementById('interest_rate_hidden').value = ratePct.toFixed(4);

          document.getElementById('dp_principal').textContent=fmt(amt);
          document.getElementById('dp_interest').textContent=fmt(totalInterest);
          document.getElementById('dp_total').textContent=fmt(totalDue);
          document.getElementById('dp_emi').textContent=fmt(emiAmt);
          document.getElementById('dp_tenure').textContent=tenure+' months';
          document.getElementById('dp_rate').textContent=ratePct.toFixed(2)+'% p.a. (calculated)';
          note.style.display='block';
          note.innerHTML=`🔢 Back-calculated Interest Rate: <b>${{ratePct.toFixed(2)}}% p.a.</b> — this will be saved automatically.`;
          p.style.display='block';
        }} else {{ p.style.display='none'; }}

      }} else {{
        // NORMAL CALC: Amount + Rate + Tenure → EMI
        const rate = parseFloat(document.getElementById('interest_rate').value)||0;
        const mode = document.getElementById('emi_mode').value;
        const customEmi = parseFloat(document.getElementById('custom_emi_amount').value)||0;
        if(amt>0&&rate>0&&tenure>0){{
          const interest=amt*(rate/100)*(tenure/12);
          const total=amt+interest; const emi=total/tenure;
          document.getElementById('dp_principal').textContent=fmt(amt);
          document.getElementById('dp_interest').textContent=fmt(interest);
          document.getElementById('dp_total').textContent=fmt(total);
          document.getElementById('dp_tenure').textContent=tenure+' months';
          document.getElementById('dp_rate').textContent=rate+'%';
          if(mode==='manual' && customEmi>0){{
            document.getElementById('dp_emi').textContent=fmt(customEmi)+' (custom)';
            const leftover = Math.round((total-(customEmi*tenure))*100)/100;
            if(leftover > 0.005){{
              note.style.display='block';
              note.innerHTML = `📅 Schedule: <b>${{tenure}}</b> × ${{fmt(customEmi)}} + final installment #${{tenure+1}} of <b>${{fmt(leftover)}}</b>.`;
            }} else if(leftover < -0.005){{
              note.style.display='block';
              note.innerHTML = `📅 Schedule: <b>${{tenure-1}}</b> × ${{fmt(customEmi)}} + final installment #${{tenure}} trimmed to <b>${{fmt(customEmi+leftover)}}</b>.`;
            }} else {{
              note.style.display='block';
              note.innerHTML = `📅 <b>${{tenure}}</b> installments of ${{fmt(customEmi)}} — divides exactly.`;
            }}
          }} else {{
            document.getElementById('dp_emi').textContent=fmt(emi);
            note.style.display='none';
          }}
          p.style.display='block';
        }}else{{p.style.display='none';}}
      }}
    }}
    </script>
    """
    return page("New Loan", content, "add")

# ── Reloan / Next LN APIs ──────────────────────────────────────────────────────
@app.route("/api/reloan_check")
@login_required
def api_reloan_check():
    ln = request.args.get("loan_number","").strip()
    if not ln: return jsonify({"error":"No loan number provided"})
    result, err = assess_reloan_risk(ln)
    if err: return jsonify({"error": err})
    return jsonify(result)

@app.route("/api/next_loan_number")
@login_required
def api_next_loan_number():
    try: return jsonify({"loan_number": next_loan_number()})
    except Exception as e: return jsonify({"error": str(e)})

# ── Approval ───────────────────────────────────────────────────────────────────
@app.route("/approval", methods=["GET","POST"])
@login_required
@role_required("superadmin","admin")
def approval():
    if request.method == "POST":
        lid = int(request.form["loan_id"]); action = request.form["action"]
        try:
            if action == "approve":
                emi_override = request.form.get("emi_amount","").strip()
                approve_loan(lid, override_emi=emi_override or None)
                flash("Loan approved!","success")
            else:
                reject_loan(lid, request.form.get("reason","No reason given"))
                flash("Loan rejected.","success")
        except Exception as e:
            flash(str(e),"danger")
        return redirect(url_for("approval", q=request.args.get("q","")))

    q = request.args.get("q","")
    ll = list_pending_loans(q)
    cards = ""
    for l in ll:
        amt = float(l["loan_amount"]); rate = float(l["interest_rate"]); t = int(l["tenure"])
        emi_amt = compute_emi_amount(amt, rate, t)
        total_due = compute_total_due(amt, rate, t)
        try:
            stored_custom = float(l["custom_emi_amount"]) if l["custom_emi_amount"] else 0
        except (IndexError, KeyError, TypeError):
            stored_custom = 0
        prefill_emi = stored_custom or emi_amt
        custom_note = ""
        if stored_custom:
            custom_note = f'<div class="alert alert-info" style="margin:8px 0;font-size:12px;padding:8px 10px;">📝 Customer requested EMI of <b>₹{stored_custom:,.2f}</b> at application time. You can edit it below before approving.</div>'

        lvs = loan_vehicles(l)
        ho_lines = "<br>".join((("🚗 " + html.escape(vehicle_tag(v)) + " — ") if len(lvs) > 1 else "") + handover_status_html(v) for v in lvs)
        cards += f"""
        <div class="card" data-amt="{amt}" data-rate="{rate}" data-tenure="{t}" data-total="{total_due}" data-emi="{emi_amt}">
          <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:10px;margin-bottom:8px;">
            <div>
              <b style="font-size:15px;color:var(--accent);">{l['loan_number']}</b> — {l['customer_name']}
              <div style="font-size:12px;color:var(--muted);margin-top:2px;">
                📱 {l.get('customer_mobile','')} &nbsp;|&nbsp; 🚗 {html.escape(vehicle_label_more(l))} &nbsp;|&nbsp; 📅 Start: {fmt_date(l['start_date'])}
              </div>
            </div>
            <div style="text-align:right;">
              <div style="font-size:12px;color:var(--muted);">Loan Amount</div>
              <div style="font-size:17px;font-weight:700;">₹{amt:,.2f}</div>
            </div>
          </div>

          <div class="kpi-grid" style="grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:10px;">
            <div class="kpi" style="padding:8px 10px;"><div class="val" style="font-size:15px;">{rate*100:.1f}%</div><div class="lbl">Rate p.a.</div></div>
            <div class="kpi" style="padding:8px 10px;"><div class="val" style="font-size:15px;">{t}m</div><div class="lbl">Tenure</div></div>
            <div class="kpi" style="padding:8px 10px;"><div class="val" style="font-size:15px;">₹{total_due:,.2f}</div><div class="lbl">Total Due</div></div>
            <div class="kpi" style="padding:8px 10px;"><div class="val" style="font-size:15px;">₹{emi_amt:,.2f}</div><div class="lbl">Calculated EMI</div></div>
          </div>

          {custom_note}
          <div style="font-size:12.5px;line-height:1.9;">{ho_lines}</div>
          {documents_summary_html(l)}

          <form method="POST" class="approve-form" onsubmit="return true;">
            <input type="hidden" name="loan_id" value="{l['id']}">
            <input type="hidden" name="action" value="approve">
            <div class="form-grid" style="align-items:end;">
              <div class="form-group">
                <label>EMI Amount to Approve (₹) <span style="font-size:10px;color:var(--muted);">(edit if rounding is needed)</span></label>
                <input type="number" name="emi_amount" class="emi-input" value="{prefill_emi:.2f}" min="1" step="0.01" oninput="previewSchedule(this)">
              </div>
              <div class="form-group">
                <div class="schedule-note" style="font-size:12.5px;color:var(--muted);line-height:1.6;padding:8px 10px;background:var(--surface2);border-radius:8px;min-height:42px;"></div>
              </div>
            </div>
            <div style="margin-top:10px;display:flex;gap:8px;">
              <button type="submit" class="btn btn-success btn-sm">✅ Approve with this EMI</button>
            </div>
          </form>
          <form method="POST" onsubmit="return getReason(this)" style="margin-top:6px;">
            <input type="hidden" name="loan_id" value="{l['id']}">
            <input type="hidden" name="action" value="reject">
            <input type="hidden" name="reason" class="reason_inp">
            <button type="submit" class="btn btn-danger btn-sm">❌ Reject</button>
          </form>
        </div>"""

    c = get_cur()
    c.execute("""SELECT p.*, le.loan_number, le.customer_name, le.customer_mobile, le.vehicle_type, le.vehicle_name,
                        le.vehicle_model, le.vehicle_number, le.vehicle_colour, le.loan_amount,
                        le.tenure, le.loan_date, le.start_date
                 FROM PreClosure p JOIN LoanEntry le ON le.id=p.loan_id
                 WHERE p.status='Pending' ORDER BY p.preclose_id ASC""")
    pc_rows = [dict(r) for r in c.fetchall()]
    pc_cards = ""
    for p in pc_rows:
        loan_like = dict(p); loan_like["id"] = p["loan_id"]
        fig = preclosure_figures(loan_like)
        c.execute("""SELECT pn.*, e.due_date FROM Penalties pn JOIN EMI e ON e.emi_id=pn.emi_id
                     WHERE pn.loan_id=? AND pn.status='Pending' ORDER BY pn.installment_no""", (p["loan_id"],))
        pc_pend = [dict(r) for r in c.fetchall()]
        pc_over = preclosure_overdue_rows(p["loan_id"])
        req_rate = float(p.get("requested_rate") or 0)
        pen_lines = "".join(
            f'<tr><td>{ordinal_due(x["installment_no"])}</td><td>{fmt_date(x["due_date"])}</td>'
            f'<td><b>{int(x["days"])} days</b> <span style="color:var(--muted);font-size:11px;">(late payment)</span></td>'
            f'<td><input type="number" name="rate_{x["penalty_id"]}" class="pc-prate" data-days="{int(x["days"])}" value="{float(x["requested_rate"]):.2f}" '
            f'min="0" step="0.01" oninput="pcPreview(this)" style="width:110px;font-size:12px;padding:5px 6px;"></td><td class="pc-line">—</td></tr>' for x in pc_pend)
        pen_lines += "".join(
            f'<tr><td>{ordinal_due(e["installment_no"])}</td><td>{fmt_date(e["due_date"])}</td>'
            f'<td><b style="color:var(--red);">{d} days overdue</b> <span style="color:var(--muted);font-size:11px;">(unpaid)</span></td>'
            f'<td><input type="number" name="orate_{e["emi_id"]}" class="pc-orate" data-days="{d}" value="{req_rate:.2f}" '
            f'min="0" step="0.01" oninput="pcPreview(this)" style="width:110px;font-size:12px;padding:5px 6px;"></td><td class="pc-line">—</td></tr>' for e, d in pc_over)
        pen_block = (f'<div style="margin:10px 0 4px;font-weight:700;color:#7c3aed;">💰 Penalty for the closing (days × per-day amount)'
                     f'{(" — requested " + fmt_inr(req_rate) + " per day") if req_rate else ""}</div>'
                     f'<div class="form-group" style="max-width:260px;margin-bottom:8px;"><label>Penalty per day (₹) — apply to all</label>'
                     f'<input type="number" class="pc-allrate" min="0" step="0.01" value="{req_rate:.2f}" placeholder="e.g. 10" oninput="pcApplyAll(this)"></div>'
                     f'<div class="table-wrap"><table><tr><th>Installment</th><th>Due</th><th>Delay (fixed)</th><th>Penalty per day (₹)</th><th>Penalty</th></tr>{pen_lines}</table></div>'
                     if pen_lines else '<div style="font-size:13px;color:var(--green);margin:8px 0;">No overdue EMI, so no late penalty for this closing.</div>')
        if fig["penalty"] > 0:
            pen_block += (f'<div style="font-size:12.5px;margin:4px 0;">Penalty already added to unpaid EMIs: <b>{fmt_inr(fig["penalty"])}</b> '
                          f'(included in the settlement).</div>')
        pc_cards += f"""
        <div class="card pc-card" data-rem-principal="{fig['rem_principal']}" data-rem-interest="{fig['rem_interest']}" data-monthly-interest="{fig['monthly_interest']}" data-pen="{fig['penalty']}">
          <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:10px;margin-bottom:8px;">
            <div>
              <b style="font-size:15px;color:var(--accent);">{html.escape(p['loan_number'])}</b> — {html.escape(p['customer_name'] or '')}
              <div style="font-size:12px;color:var(--muted);margin-top:2px;">
                📱 {html.escape(p.get('customer_mobile') or '')} &nbsp;|&nbsp; 🚗 {html.escape(vehicle_label_more(p))}
                &nbsp;|&nbsp; Requested by <b>{html.escape(p.get('requested_by') or '')}</b> on {fmt_date((p.get('requested_at') or '')[:10])}
              </div>
            </div>
            <a class="btn btn-sm btn-primary" href="/emis/{p['loan_id']}">View EMIs</a>
          </div>
          <div class="kpi-grid" style="grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:8px;">
            <div class="kpi" style="padding:8px 10px;"><div class="val" style="font-size:15px;">₹{fig['emi']:,.2f}</div><div class="lbl">EMI Due per Month</div></div>
            <div class="kpi" style="padding:8px 10px;"><div class="val" style="font-size:15px;">₹{fig['principal']:,.2f}</div><div class="lbl">Principal Amount</div></div>
            <div class="kpi" style="padding:8px 10px;"><div class="val" style="font-size:15px;">₹{fig['total_interest']:,.2f}</div><div class="lbl">Interest Amount</div></div>
          </div>
          <div class="kpi-grid" style="grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:10px;">
            <div class="kpi" style="padding:8px 10px;"><div class="val" style="font-size:15px;">₹{fig['principal_paid']:,.2f}</div><div class="lbl">Principal Collected</div></div>
            <div class="kpi" style="padding:8px 10px;"><div class="val" style="font-size:15px;">₹{fig['interest_paid']:,.2f}</div><div class="lbl">Interest Collected</div></div>
            <div class="kpi" style="padding:8px 10px;"><div class="val" style="font-size:15px;">₹{fig['paid']:,.2f}</div><div class="lbl">Total Already Paid</div></div>
          </div>
          <form method="POST" action="/preclose/approve/{p['preclose_id']}">
            <div class="form-grid" style="align-items:end;">
              <div class="form-group">
                <label>Interest to be collected further (₹) <span style="font-size:10px;color:var(--muted);">(pending interest: {fmt_inr(fig['rem_interest'])})</span></label>
                <input type="number" name="further_interest" class="pc-int" value="{fig['rem_interest']:.2f}" min="0" max="{fig['rem_interest']:.2f}" step="0.01" required oninput="pcPreview(this)">
              </div>
              <div class="form-group">
                <label>Months to waive <span style="font-size:10px;color:var(--muted);">(auto-fills the interest above: {fmt_inr(fig['monthly_interest'])} per month)</span></label>
                <input type="number" name="waived_months" class="pc-waive" value="0" min="0" step="1" oninput="pcWaive(this)">
              </div>
              <div class="form-group">
                <div class="pc-note" style="font-size:13px;line-height:1.7;padding:8px 10px;background:var(--surface2);border-radius:8px;min-height:42px;"></div>
              </div>
            </div>
            {pen_block}
            <div style="margin-top:10px;display:flex;gap:8px;">
              <button type="submit" class="btn btn-success btn-sm">✅ Approve Pre-closure</button>
            </div>
          </form>
          <form method="POST" action="/preclose/reject/{p['preclose_id']}" onsubmit="return getReason(this)" style="margin-top:6px;">
            <input type="hidden" name="reason" class="reason_inp">
            <button type="submit" class="btn btn-danger btn-sm">❌ Reject</button>
          </form>
        </div>"""
    c = get_cur()
    c.execute("""SELECT pn.*, le.loan_number, le.customer_name, le.customer_mobile, e.due_date, e.emi_amount
                 FROM Penalties pn JOIN LoanEntry le ON le.id=pn.loan_id JOIN EMI e ON e.emi_id=pn.emi_id
                 WHERE pn.status='Pending' ORDER BY pn.penalty_id ASC""")
    pen_rows = [dict(r) for r in c.fetchall()]
    pen_cards = ""
    for p in pen_rows:
        pen_cards += f"""
        <div class="card pen-card" data-days="{int(p['days'])}">
          <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:10px;margin-bottom:8px;">
            <div>
              <b style="font-size:15px;color:var(--accent);">{html.escape(p['loan_number'])}</b> — {html.escape(p['customer_name'] or '')}
              <div style="font-size:12px;color:var(--muted);margin-top:2px;">
                Installment <b>{ordinal_due(p['installment_no'])}</b> · EMI {fmt_inr(p['emi_amount'])} · due {fmt_date(p['due_date'])}
                · half of the EMI paid on {fmt_date(p['half_paid_date'])}<br>
                Requested by <b>{html.escape(p.get('requested_by') or '')}</b> on {fmt_date((p.get('requested_at') or '')[:10])}
                at {fmt_inr(p['requested_rate'])}/day = {fmt_inr(p['requested_amount'])}
              </div>
            </div>
            <a class="btn btn-sm btn-primary" href="/emis/{p['loan_id']}">View EMIs</a>
          </div>
          <div class="kpi-grid" style="grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:10px;">
            <div class="kpi" style="padding:8px 10px;border-color:var(--red);"><div class="val" style="font-size:16px;color:var(--red);">{int(p['days'])} days</div><div class="lbl">Delayed payment</div></div>
            <div class="kpi" style="padding:8px 10px;"><div class="val" style="font-size:15px;">{fmt_inr(p['requested_rate'])}</div><div class="lbl">Requested per day</div></div>
            <div class="kpi" style="padding:8px 10px;"><div class="val" style="font-size:15px;">{fmt_inr(p['requested_amount'])}</div><div class="lbl">Requested penalty</div></div>
          </div>
          <form method="POST" action="/penalty/approve/{p['penalty_id']}">
            <div class="form-grid" style="align-items:end;">
              <div class="form-group">
                <label>Final penalty per day (₹) <span style="font-size:10px;color:var(--muted);">(edit to set the final amount; 0 waives it)</span></label>
                <input type="number" name="rate" class="pen-rate" value="{float(p['requested_rate']):.2f}" min="0" step="0.01" required oninput="penPreview(this)">
              </div>
              <div class="form-group">
                <div class="pen-note" style="font-size:13px;line-height:1.7;padding:8px 10px;background:var(--surface2);border-radius:8px;min-height:42px;"></div>
              </div>
            </div>
            <div style="margin-top:10px;display:flex;gap:8px;">
              <div style="font-size:12px;color:var(--muted);margin-right:8px;align-self:center;">If EMIs are still left, the penalty is added to the next unpaid EMI and collected with it; otherwise a collection follow-up is created.</div>
              <button type="submit" class="btn btn-success btn-sm">✅ Approve Penalty</button>
            </div>
          </form>
          <form method="POST" action="/penalty/reject/{p['penalty_id']}" onsubmit="return getReason(this)" style="margin-top:6px;">
            <input type="hidden" name="reason" class="reason_inp">
            <button type="submit" class="btn btn-danger btn-sm">❌ Reject</button>
          </form>
        </div>"""
    c = get_cur()
    c.execute("""SELECT cl.*, le.loan_number, le.customer_name, le.customer_mobile
                 FROM LoanClosure cl JOIN LoanEntry le ON le.id=cl.loan_id
                 WHERE cl.status='AwaitApproval' ORDER BY cl.closure_id ASC""")
    cl_rows = [dict(r) for r in c.fetchall()]
    cl_cards = ""
    for k in cl_rows:
        c.execute("""SELECT pn.*, e.due_date FROM Penalties pn JOIN EMI e ON e.emi_id=pn.emi_id
                     WHERE pn.loan_id=? AND pn.status!='Rejected' ORDER BY pn.installment_no""", (k["loan_id"],))
        kp = [dict(r) for r in c.fetchall()]
        pen_lines = ""
        for p in kp:
            if p["status"] == "Pending":
                pen_lines += (f'<tr><td>{ordinal_due(p["installment_no"])}</td><td>{fmt_date(p["due_date"])}</td>'
                              f'<td><b style="color:var(--red);">{int(p["days"])} days</b></td>'
                              f'<td><input type="number" name="rate_{p["penalty_id"]}" class="cl-rate" data-days="{int(p["days"])}" '
                              f'value="{float(p["requested_rate"]):.2f}" min="0" step="0.01" oninput="clPreview(this)" style="width:110px;font-size:12px;padding:5px 6px;"></td>'
                              f'<td class="cl-line">—</td></tr>')
            else:
                amt = float(p["final_amount"] if p["final_amount"] is not None else p["requested_amount"] or 0)
                pen_lines += (f'<tr><td>{ordinal_due(p["installment_no"])}</td><td>{fmt_date(p["due_date"])}</td>'
                              f'<td><b>{int(p["days"])} days</b></td><td>{fmt_inr(p["final_rate"] or 0)}/day (already {p["status"].lower()})</td>'
                              f'<td class="cl-fixed" data-amt="{amt}">{fmt_inr(amt)}</td></tr>')
        pen_table = (f'<div class="table-wrap"><table><tr><th>Installment</th><th>Due</th><th>Delay (fixed)</th><th>Penalty per day (₹)</th><th>Penalty</th></tr>{pen_lines}</table></div>'
                     if kp else '<div style="font-size:13px;color:var(--green);">No late-payment penalty on this loan.</div>')
        items_txt = " · ".join(closure_item_title(i) for i in closure_items(k["closure_id"]))
        cl_cards += f"""
        <div class="card cl-card">
          <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:10px;margin-bottom:8px;">
            <div>
              <b style="font-size:15px;color:var(--accent);">{html.escape(k['loan_number'])}</b> — {html.escape(k['customer_name'] or '')}
              <div style="font-size:12px;color:var(--muted);margin-top:2px;">All EMIs are paid · closing request raised {fmt_date((k.get('created_at') or '')[:10])}</div>
            </div>
            <a class="btn btn-sm btn-primary" href="/emis/{k['loan_id']}">View EMIs</a>
          </div>
          <form method="POST" action="/closure/approve/{k['closure_id']}">
            {pen_table}
            <div class="cl-note" style="margin:8px 0;font-size:14px;font-weight:700;"></div>
            <div style="font-size:12px;color:var(--muted);margin-bottom:8px;">After approval: any penalty must be collected within a day, then to be handed back: {items_txt}. An Account Manager acknowledges the hand-over before the loan closes.</div>
            <button type="submit" class="btn btn-success btn-sm">✅ Approve closing</button>
          </form>
        </div>"""
    c.execute("""SELECT sz.*, le.loan_number, le.customer_name, le.customer_mobile, le.vehicle_type, le.vehicle_name,
                        le.vehicle_model, le.vehicle_number, le.vehicle_colour, le.id as lid
                 FROM Seizures sz JOIN LoanEntry le ON le.id=sz.loan_id WHERE sz.status='Pending' ORDER BY sz.seizure_id""")
    sz_rows = [dict(r) for r in c.fetchall()]
    sz_cards = ""
    for z in sz_rows:
        od = overdue_emis(z["loan_id"])
        total_wo = sum(float(e["remaining_amount"] if e["remaining_amount"] is not None else e["emi_amount"]) + emi_penalty_out(e)
                       for e in get_emis_for_loan(z["loan_id"]) if e["status"] not in ("Paid", "PreClosed", "Seized"))
        c.execute("SELECT COUNT(*) as n FROM Penalties WHERE loan_id=? AND status IN ('Pending','Approved')", (z["loan_id"],))
        n_pen = c.fetchone()["n"]
        od_lines = "".join(
            f'<tr><td>{ordinal_due(e["installment_no"])}</td><td>{fmt_date(e["due_date"])}</td>'
            f'<td><b style="color:var(--red);">{(date.today() - parse_date(e["due_date"])).days} days</b></td>'
            f'<td>{fmt_inr(e["remaining_amount"] if e["remaining_amount"] is not None else e["emi_amount"])}</td></tr>' for e in od)
        sz_cards += f"""
        <div class="card" style="border-left:5px solid var(--red);">
          <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:10px;margin-bottom:8px;">
            <div>
              <b style="font-size:15px;color:var(--accent);">{html.escape(z['loan_number'])}</b> — {html.escape(z['customer_name'] or '')}
              <div style="font-size:12px;color:var(--muted);margin-top:2px;">📱 {html.escape(z.get('customer_mobile') or '')} &nbsp;|&nbsp; 🚗 {html.escape(vehicle_label_more(z))}
                &nbsp;|&nbsp; requested by {html.escape(z.get('requested_by') or '')} on {fmt_date((z.get('requested_at') or '')[:10])}</div>
            </div>
            <a class="btn btn-sm btn-primary" href="/emis/{z['loan_id']}">View EMIs</a>
          </div>
          <div style="font-size:13px;line-height:1.8;">
            <b>Seized on:</b> {fmt_date(z.get('seized_date'))} &nbsp;|&nbsp; <b>Kept at:</b> {html.escape(z.get('place') or '—')}<br>
            <b>Reason:</b> {html.escape(z.get('reason') or '—')}
          </div>
          <div class="table-wrap" style="margin:8px 0;"><table><tr><th>Overdue EMI</th><th>Due</th><th>Overdue by</th><th>Outstanding</th></tr>{od_lines}</table></div>
          <div style="background:#fee2e2;border-radius:8px;padding:8px 12px;font-size:13.5px;margin-bottom:8px;">
            If approved: <b>{fmt_inr(total_wo)}</b> is written off (all unpaid EMIs close as Seized)
            {(f'and <b>{n_pen}</b> pending penalty(ies) are waived') if n_pen else ''}. Key and RC then have to be recorded and an Account Manager acknowledges.
          </div>
          <form method="POST" action="/seizure/approve/{z['seizure_id']}"
                onsubmit="return confirm('Approve the seizure? The outstanding amount will be written off.')">
            <div class="form-group" style="margin-bottom:8px;"><label>Reason for writing off (edit if needed)</label>
              <textarea name="writeoff_reason" rows="2" required>{html.escape(z.get('writeoff_reason') or '')}</textarea></div>
            <button type="submit" class="btn btn-success btn-sm">✅ Approve seizure</button>
          </form>
          <form method="POST" action="/seizure/reject/{z['seizure_id']}" onsubmit="return getReason(this)" style="margin-top:6px;">
            <input type="hidden" name="reason" class="reason_inp">
            <button type="submit" class="btn btn-danger btn-sm">❌ Reject</button>
          </form>
        </div>"""
    parts = ""
    if sz_rows:
        parts += f'<h2 style="margin:6px 0 10px;">🚫 Vehicle Seizure Requests ({len(sz_rows)})</h2>{sz_cards}'
    if cl_rows:
        parts += f'<h2 style="margin:18px 0 10px;">🔒 Loan Closing Requests ({len(cl_rows)})</h2>{cl_cards}'
    if pen_rows:
        parts += f'<h2 style="margin:18px 0 10px;">💰 Late Payment Penalties ({len(pen_rows)})</h2>{pen_cards}'
    if pc_rows:
        parts += f'<h2 style="margin:18px 0 10px;">⏩ Pre-closure Requests ({len(pc_rows)})</h2>{pc_cards}'
    pc_section = (parts + '<h2 style="margin:18px 0 10px;">🆕 New Loan Applications</h2>') if parts else ""

    content = f"""
    <h1>✅ Loan Approval</h1>
    <form method="GET" style="margin-bottom:12px;display:flex;gap:8px;flex-wrap:wrap;">
      <input name="q" value="{q}" placeholder="Search…" style="max-width:240px;">
      <button class="btn btn-primary btn-sm">Search</button>
    </form>
    {pc_section}
    {cards or '<div class="card"><p style="text-align:center;color:var(--muted);">No pending loans</p></div>'}
    <script>
    function pcPreview(input){{
      const card = input.closest('.pc-card');
      const P = parseFloat(card.dataset.remPrincipal), pen0 = parseFloat(card.dataset.pen)||0;
      const interest = parseFloat(card.querySelector('.pc-int').value)||0;
      let newPen = 0;
      card.querySelectorAll('.pc-prate,.pc-orate').forEach(function(x){{
        const a = Math.round((parseInt(x.dataset.days)||0)*(parseFloat(x.value)||0)*100)/100;
        x.closest('tr').querySelector('.pc-line').textContent = fmtINR(a); newPen += a;
      }});
      const pen = pen0 + newPen;
      const settle = Math.max(0, Math.round((P+interest+pen)*100)/100);
      card.querySelector('.pc-note').innerHTML =
        'Principal to collect: <b>'+fmtINR(P)+'</b> &nbsp;|&nbsp; Interest to collect: <b>'+fmtINR(interest)+'</b>'+(pen>0?' &nbsp;|&nbsp; Penalty: <b>'+fmtINR(pen)+'</b>':'')+'<br>'+
        '→ <b style="color:var(--green);">Settlement to collect: '+fmtINR(settle)+'</b>';
    }}
    function pcWaive(input){{
      const card = input.closest('.pc-card');
      const rem = parseFloat(card.dataset.remInterest)||0, mi = parseFloat(card.dataset.monthlyInterest)||0;
      const w = Math.max(0, parseInt(input.value)||0);
      card.querySelector('.pc-int').value = Math.max(0, Math.round((rem - w*mi)*100)/100).toFixed(2);
      pcPreview(card.querySelector('.pc-int'));
    }}
    function pcApplyAll(input){{
      const card = input.closest('.pc-card');
      card.querySelectorAll('.pc-prate,.pc-orate').forEach(function(x){{ x.value = input.value; }});
      pcPreview(input);
    }}
    window.addEventListener('DOMContentLoaded',function(){{ document.querySelectorAll('.pc-int').forEach(pcPreview); }});
    function penPreview(input){{
      const card = input.closest('.pen-card');
      const days = parseInt(card.dataset.days), r = parseFloat(input.value)||0;
      const amt = Math.round(days*r*100)/100;
      card.querySelector('.pen-note').innerHTML = days+' day(s) × '+fmtINR(r)+' = <b style="color:var(--red);">Final penalty: '+fmtINR(amt)+'</b>'
        + (amt>0 ? '' : ' <span style="color:var(--muted);">(waived)</span>');
    }}
    window.addEventListener('DOMContentLoaded',function(){{ document.querySelectorAll('.pen-rate').forEach(penPreview); }});
    function clPreview(input){{
      const card = input.closest('.cl-card');
      let total = 0;
      card.querySelectorAll('.cl-rate').forEach(function(r){{
        const amt = Math.round(parseInt(r.dataset.days)*(parseFloat(r.value)||0)*100)/100;
        r.closest('tr').querySelector('.cl-line').textContent = fmtINR(amt);
        total += amt;
      }});
      card.querySelectorAll('.cl-fixed').forEach(function(f){{ total += parseFloat(f.dataset.amt)||0; }});
      card.querySelector('.cl-note').innerHTML = total>0 ? 'Total penalty to collect: <span style="color:var(--red);">'+fmtINR(total)+'</span>' : 'No penalty to collect';
    }}
    window.addEventListener('DOMContentLoaded',function(){{ document.querySelectorAll('.cl-card').forEach(function(card){{
      const first = card.querySelector('.cl-rate'); if(first) clPreview(first); else card.querySelector('.cl-note').innerHTML='';
      if(!first){{ let t=0; card.querySelectorAll('.cl-fixed').forEach(function(f){{ t+=parseFloat(f.dataset.amt)||0; }}); card.querySelector('.cl-note').innerHTML = t>0?'Total penalty: <span style="color:var(--red);">'+fmtINR(t)+'</span>':''; }}
    }}); }});
    function fmtINR(v){{return '₹'+v.toLocaleString('en-IN',{{minimumFractionDigits:2,maximumFractionDigits:2}});}}
    function previewSchedule(input){{
      const card = input.closest('.card');
      const amt = parseFloat(card.dataset.amt);
      const rate = parseFloat(card.dataset.rate);
      const tenure = parseInt(card.dataset.tenure);
      const total = parseFloat(card.dataset.total);
      const calcEmi = parseFloat(card.dataset.emi);
      const emi = parseFloat(input.value)||0;
      const note = card.querySelector('.schedule-note');
      if(emi<=0){{ note.innerHTML='Enter an EMI amount to preview the schedule.'; return; }}
      const fullTotal = Math.round(emi*tenure*100)/100;
      const leftover = Math.round((total-fullTotal)*100)/100;
      if(Math.abs(emi-calcEmi) < 0.01){{
        note.innerHTML = `📅 ${{tenure}} installments of ${{fmtINR(emi)}} (matches calculated EMI exactly).`;
      }} else if(leftover > 0.005){{
        note.innerHTML = `📅 ${{tenure}} installments of ${{fmtINR(emi)}} + <b>final installment #${{tenure+1}}</b> of <b>${{fmtINR(leftover)}}</b> (leftover balance).`;
      }} else if(leftover < -0.005){{
        const lastEmi = emi + leftover;
        note.innerHTML = `📅 ${{tenure-1}} installments of ${{fmtINR(emi)}} + final installment #${{tenure}} reduced to <b>${{fmtINR(lastEmi)}}</b> (EMI × tenure exceeds total due).`;
      }} else {{
        note.innerHTML = `📅 ${{tenure}} installments of ${{fmtINR(emi)}} — divides exactly, no adjustment needed.`;
      }}
    }}
    document.querySelectorAll('.emi-input').forEach(previewSchedule);
    function getReason(form){{
      const r=prompt('Rejection reason:'); if(!r) return false;
      form.querySelector('.reason_inp').value=r; return true;
    }}
    </script>"""
    return page("Approval", content, "approval")

# ── Customers ──────────────────────────────────────────────────────────────────
@app.route("/customers")
@login_required
def customers():
    q = request.args.get("q","")
    cl = list_customers(q)
    role = session.get("role","")
    can_edit = ROLES.get(role,{}).get("can_edit", False)
    rows = ""
    for c in cl:
        sc = {"Active":"approved","Closed":"closed","Seized":"rejected"}.get(c["status"],"pending")
        lid = c["loan_id"]
        edit_btn = f'<a class="btn btn-sm btn-amber" href="/customer/edit/{lid}">&#9998; Edit</a>' if can_edit else ""
        loan_no = c.get('loan_number') or '—'
        rows += f"""<tr>
          <td><b style="color:var(--accent);">{loan_no}</b> — {c['name']}</td><td>{vehicle_html(c)}</td>
          <td>{customer_numbers_html(c)}</td><td>{guarantor_numbers_html(c)}</td>
          <td>₹{c['loan_amount']:,.2f}</td><td><b>₹{c['emi_amount']:,.2f}</b></td>
          <td><span class="badge badge-{sc}">{c['status']}</span></td>
          <td style="white-space:nowrap;">
            <a class="btn btn-sm btn-primary" href="/emis/{c['loan_id']}">View EMIs</a>
            {edit_btn}
          </td>
        </tr>"""
    content = f"""
    <h1>👥 Customers</h1>
    <form method="GET" style="margin-bottom:12px;display:flex;gap:8px;flex-wrap:wrap;">
      <input name="q" value="{q}" placeholder="Search…" style="max-width:240px;">
      <button class="btn btn-primary btn-sm">Search</button>
    </form>
    <p style="font-size:12px;color:var(--muted);margin-bottom:8px;">📞 Place the cursor on a number to see whose number it is. Tap a number to call.</p>
    <div class="card"><div class="table-wrap"><table>
      <tr><th>Loan # — Name</th><th>Vehicle</th><th>Customer Mobile</th><th>Guarantor Mobile</th><th>Loan Amt</th><th>EMI/mo</th><th>Status</th><th>Actions</th></tr>
      {rows or '<tr><td colspan="8" style="text-align:center;color:var(--muted);">No customers found</td></tr>'}
    </table></div></div>"""
    return page("Customers", content, "customers")

# ── Customer Edit (Super Admin only) ──────────────────────────────────────────
@app.route("/customer/edit/<int:loan_id>", methods=["GET","POST"])
@login_required
@role_required("superadmin")
def customer_edit(loan_id):
    c = get_cur()
    c.execute("SELECT * FROM LoanEntry WHERE id=?", (loan_id,))
    loan = dict(c.fetchone() or {})
    if not loan:
        flash("Loan not found.","danger")
        return redirect(url_for("customers"))

    if request.method == "POST":
        f = request.form
        try:
            customer_extra = parse_extra_numbers_form(f, "customer", "Customer")
            guarantor_extra = parse_extra_numbers_form(f, "guarantor", "Guarantor")
            xv_rows = parse_extra_vehicles(f, bool(loan.get("is_reloan")))
            fine_main_edit = parse_fine(f.get("police_fine"), "Police fine")
            xg_rows = parse_extra_guarantors(f)
            c.execute("""UPDATE LoanEntry SET customer_extra_numbers=?, guarantor_extra_numbers=? WHERE id=?""",
                      (customer_extra or None, guarantor_extra or None, loan_id))
            c.execute("""UPDATE LoanEntry SET
                customer_name=?, customer_mobile=?, customer_address=?, customer_permanent_address=?, customer_location=?,
                customer_email=?, vehicle_type=?, vehicle_number=?, vehicle_name=?, vehicle_model=?,
                engine_number=?, chassis_number=?, vehicle_colour=?,
                guarantor_name=?, guarantor_address=?, guarantor_mobile=?, guarantor_location=?,
                loan_amount=?, interest_rate=?, tenure=?, start_date=?, remarks=?
                WHERE id=?""",
                (f.get("customer_name","").strip(),
                 f.get("customer_mobile","").strip(),
                 f.get("customer_address","").strip(),
                 f.get("customer_permanent_address","").strip(),
                 f.get("customer_location","").strip(),
                 f.get("customer_email","").strip(),
                 f.get("vehicle_type","").strip(),
                 f.get("vehicle_number","").strip(),
                 f.get("vehicle_name","").strip(),
                 f.get("vehicle_model","").strip(),
                 f.get("engine_number","").strip(),
                 f.get("chassis_number","").strip(),
                 f.get("vehicle_colour","").strip(),
                 f.get("guarantor_name","").strip(),
                 f.get("guarantor_address","").strip(),
                 f.get("guarantor_mobile","").strip(),
                 f.get("guarantor_location","").strip(),
                 float(f.get("loan_amount",0)),
                 float(f.get("interest_rate",0))/100 if float(f.get("interest_rate",0))>1 else float(f.get("interest_rate",0)),
                 int(f.get("tenure",0)),
                 f.get("start_date",""),
                 f.get("remarks","").strip(),
                 loan_id))
            # Also update Customers summary table
            new_amt = float(f.get("loan_amount",0))
            rate_raw = float(f.get("interest_rate",0))
            rate = rate_raw/100 if rate_raw>1 else rate_raw
            tenure = int(f.get("tenure",0))
            emi_amt = compute_emi_amount(new_amt, rate, tenure) if tenure>0 else 0
            c.execute("UPDATE Customers SET name=?,vehicle_type=?,loan_amount=?,emi_amount=? WHERE loan_id=?",
                      (f.get("customer_name","").strip(), f.get("vehicle_type","").strip(), new_amt, emi_amt, loan_id))
            get_db().commit()
            c.execute("UPDATE LoanEntry SET guarantor_permanent_address=?, police_fine=? WHERE id=?",
                      (f.get("guarantor_permanent_address","").strip() or None, fine_main_edit, loan_id))
            get_db().commit()
            save_extra_vehicles(loan_id, xv_rows, date.today(), session.get("username",""))
            save_extra_guarantors(loan_id, xg_rows)
            flash("Customer / Loan details updated successfully!", "success")
            return redirect(url_for("customers"))
        except Exception as e:
            flash(f"Error updating: {e}", "danger")

    # Display rate as percentage
    rate_display = round(float(loan.get("interest_rate",0))*100, 4)
    xv_area, xg_area = extra_blocks_section(loan_id)

    content = f"""
    {_EXTRA_BLOCKS_JS}
    <h1>✏️ Edit Customer — {loan.get('loan_number','')}</h1>
    <div class="alert alert-warning">⚠️ <b>Super Admin Edit:</b> Changes here directly update the database. Proceed with care.</div>
    <div class="card">
    <form method="POST">
      <div class="form-grid">
        <div class="section-title">📄 Loan Details</div>
        <div class="form-group">
          <label>Loan Number</label>
          <input value="{loan.get('loan_number','')}" readonly>
        </div>
        <div class="form-group">
          <label>Start Date *</label>
          <input type="date" name="start_date" value="{loan.get('start_date','')}" required>
        </div>
        <div class="form-group">
          <label>Loan Amount (₹) *</label>
          <input type="number" name="loan_amount" value="{loan.get('loan_amount',0)}" min="1" step="0.01" required>
        </div>
        <div class="form-group">
          <label>Interest Rate (% p.a.) *</label>
          <input type="number" name="interest_rate" value="{rate_display}" min="0" step="0.01" required>
        </div>
        <div class="form-group">
          <label>Tenure (Months) *</label>
          <input type="number" name="tenure" value="{loan.get('tenure',0)}" min="1" max="360" required>
        </div>
        <div class="form-group">
          <label>Vehicle Type *</label>
          <select name="vehicle_type" required>
            {''.join(f'<option value="{v}" {"selected" if loan.get("vehicle_type")==v else ""}>{v}</option>' for v in ["Two Wheeler","Three Wheeler","Four Wheeler","Commercial Vehicle","Other"])}
          </select>
        </div>

        <div class="section-title">👤 Customer Details</div>
        <div class="form-group">
          <label>Customer Name *</label>
          <input name="customer_name" value="{loan.get('customer_name','')}" required>
        </div>
        <div class="form-group">
          <label>Mobile (10 digits)</label>
          <input name="customer_mobile" value="{loan.get('customer_mobile','')}" maxlength="10"
                 oninput="this.value=this.value.replace(/[^0-9]/g,'').slice(0,10)">
        </div>
        {extra_numbers_block('customer', loan.get('customer_extra_numbers'))}
        <div class="form-group full">
          <label>Current Address</label>
          <textarea name="customer_address" id="e_cur_addr" rows="2">{loan.get('customer_address','')}</textarea>
        </div>
        <div class="form-group full">
          <label>Permanent Address</label>
          <label style="display:flex;align-items:center;gap:8px;font-weight:600;text-transform:none;font-size:13px;margin-bottom:6px;cursor:pointer;">
            <input type="checkbox" style="width:auto;min-height:0;margin:0;"
                   onchange="if(this.checked) document.getElementById('e_perm_addr').value=document.getElementById('e_cur_addr').value;">
            Same as current address
          </label>
          <textarea name="customer_permanent_address" id="e_perm_addr" rows="2">{loan.get('customer_permanent_address') or ''}</textarea>
        </div>
        <div class="form-group full">
          <label>GPS Location</label>
          <input name="customer_location" value="{loan.get('customer_location','')}">
        </div>
        <div class="form-group">
          <label>Email</label>
          <input type="email" name="customer_email" value="{loan.get('customer_email','')}">
        </div>

        <div class="section-title">🚗 Vehicle Details</div>
        <div class="form-group">
          <label>Vehicle Number</label>
          <input name="vehicle_number" value="{loan.get('vehicle_number','')}">
        </div>
        <div class="form-group">
          <label>Vehicle Name</label>
          <input name="vehicle_name" value="{html.escape(loan.get('vehicle_name') or '')}">
        </div>
        <div class="form-group">
          <label>Vehicle Model</label>
          <input name="vehicle_model" value="{loan.get('vehicle_model','')}">
        </div>
        <div class="form-group">
          <label>Engine Number</label>
          <input name="engine_number" value="{loan.get('engine_number','')}">
        </div>
        <div class="form-group">
          <label>Chassis Number</label>
          <input name="chassis_number" value="{loan.get('chassis_number','')}">
        </div>
        <div class="form-group">
          <label>Vehicle Colour</label>
          <input name="vehicle_colour" value="{loan.get('vehicle_colour','')}">
        </div>
        <div class="form-group">
          <label>Police fine amount (₹) <span style="font-size:10px;color:var(--muted);">(not more than {fmt_inr(police_fine_limit())})</span></label>
          <input type="number" name="police_fine" value="{float(loan.get('police_fine') or 0):g}" min="0" max="{police_fine_limit():g}" step="0.01">
        </div>
        <div class="form-group full" style="font-size:12.5px;">🚗 Vehicle 1 &mdash; {handover_status_html(loan)}</div>
        <div class="form-group full">{documents_summary_html(loan)}</div>
        {xv_area}

        <div class="section-title">🛡️ Guarantor Details</div>
        <div class="form-group">
          <label>Guarantor Name</label>
          <input name="guarantor_name" value="{loan.get('guarantor_name','')}">
        </div>
        <div class="form-group">
          <label>Guarantor Mobile</label>
          <input name="guarantor_mobile" value="{loan.get('guarantor_mobile','')}" maxlength="10"
                 oninput="this.value=this.value.replace(/[^0-9]/g,'').slice(0,10)">
        </div>
        {extra_numbers_block('guarantor', loan.get('guarantor_extra_numbers'))}
        <div class="form-group full gaddr">
          <label>Guarantor Current Address</label>
          <textarea name="guarantor_address" class="g-cur" rows="2">{loan.get('guarantor_address','')}</textarea>
          <label style="display:flex;align-items:center;gap:8px;font-weight:600;text-transform:none;font-size:13px;margin:6px 0 4px;cursor:pointer;">
            <input type="checkbox" class="g-same" style="width:auto;min-height:0;margin:0;" onchange="syncGAddr(this)"> Permanent address same as current address</label>
          <label>Guarantor Permanent Address</label>
          <textarea name="guarantor_permanent_address" class="g-perm" rows="2">{html.escape(loan.get('guarantor_permanent_address') or '')}</textarea>
        </div>
        <div class="form-group full">
          <label>Guarantor GPS Location</label>
          <input name="guarantor_location" value="{loan.get('guarantor_location') or ''}">
        </div>
        {xg_area}

        <div class="section-title">📝 Remarks</div>
        <div class="form-group full">
          <textarea name="remarks" rows="3">{loan.get('remarks','')}</textarea>
        </div>
      </div>
      <div style="margin-top:18px;display:flex;gap:10px;flex-wrap:wrap;">
        <button type="submit" class="btn btn-primary">💾 Save Changes</button>
        <a href="/customers" class="btn" style="background:var(--surface2);color:var(--text);">Cancel</a>
      </div>
    </form>
    </div>"""
    return page("Edit Customer", content, "customers")

def closing_section_html(loan, can_pay, penalties, today):
    """Rows at the end of the EMI table: closing banner, penalty summary, and one row per item to hand back
    (key / RC / proof & documents if collected, plus NOC)."""
    lid = loan["id"]
    cl = get_closure(lid)
    stage = cl["status"] if cl else None
    items = closure_items(cl["closure_id"]) if cl else \
        [{**k, "status": "Pending", "item_id": None} for k in closure_required_items(loan)]
    msg = {None: "Closing checklist — starts when the last EMI is paid: admin approval → penalty collection (within a day) → "
                 "return of key &amp; documents → acknowledgement → loan closes.",
           "AwaitApproval": "⏳ Waiting for admin approval of the closing (the admin reviews the delay days and penalty).",
           "Penalty": "⏳ Penalty must be collected within a day. The key &amp; document return starts after it is collected.",
           "Return": "📝 Record each item as returned, then send it for acknowledgement.",
           "AckPending": "⏳ Waiting for acknowledgement by an Account Manager / admin.",
           "Closed": f"✅ Loan closed on {fmt_date((cl or {}).get('closed_at'))}."}[stage]
    if cl and cl["kind"] == "PreClosure" and stage != "Closed":
        msg = "⏩ Pre-closure bill paid. " + msg
    note = (f'<div style="color:var(--red);font-weight:600;margin-top:4px;">{html.escape(cl["ack_note"])}</div>'
            if cl and cl.get("ack_note") and stage == "Return" else "")
    banner_bg = {"Closed": "#d1fae5", "Return": "#e0f2fe"}.get(stage, "#eef2ff")
    out = (f'<tr id="closing"><td colspan="11" style="background:{banner_bg};padding:10px 12px;">'
           f'<b>🔒 Loan closing</b> — {msg}{note}</td></tr>')
    # penalty summary row
    pens = list(penalties.values())
    if pens:
        days = sum(int(p["days"]) for p in pens)
        total = sum(float(p["final_amount"] if p["status"] != "Pending" and p["final_amount"] is not None
                          else (p["requested_amount"] or 0)) for p in pens)
        counts = {}
        for p in pens: counts[p["status"]] = counts.get(p["status"], 0) + 1
        pen_txt = (f'<b style="color:#7c3aed;">{fmt_inr(total)}</b> — {days} delay day(s) over {len(pens)} installment(s) '
                   f'({", ".join(str(n) + " " + k.lower() for k, n in counts.items())})')
    else:
        pen_txt = '<span style="color:var(--muted);">No penalty</span>'
    out += f'<tr><td colspan="2"><b>💰 Penalty</b></td><td colspan="9">{pen_txt}</td></tr>'
    # item rows
    lock_txt = {None: "Not started — unlocks after the last EMI is paid, admin approval and penalty collection",
                "AwaitApproval": "🔒 Unlocks after admin approval and penalty collection",
                "Penalty": "🔒 Unlocks after the penalty is collected"}.get(stage, "")
    for it in items:
        returned = it.get("status") == "Returned"
        parts = ""
        if returned:
            parts = (f'✅ Returned on <b>{fmt_date(it["returned_on"])}</b> · handed over by <b>{html.escape(it.get("handed_by") or "")}</b>'
                     + (f' · {html.escape(it["note"])}' if it.get("note") else "")
                     + f' <span style="color:var(--muted);font-size:11.5px;">(recorded by {html.escape(it.get("recorded_by") or "")})</span>')
        if stage == "Return" and can_pay:
            form = (f'<form method="POST" action="/closure/item/{it["item_id"]}" style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:{4 if returned else 0}px;">'
                    f'<input type="hidden" name="loan_id" value="{lid}">'
                    f'<input type="date" name="returned_on" value="{it.get("returned_on") or today.isoformat()}" max="{today.isoformat()}" required style="width:135px;font-size:12px;padding:5px 6px;" title="Date returned">'
                    f'<input name="handed_by" value="{html.escape(it.get("handed_by") or "")}" placeholder="Handed over by *" required style="width:150px;font-size:12px;padding:5px 6px;">'
                    f'<input name="note" value="{html.escape(it.get("note") or "")}" placeholder="Note (optional)" style="width:150px;font-size:12px;padding:5px 6px;">'
                    f'<button class="btn btn-success btn-sm">{"Update" if returned else "Mark returned"}</button></form>')
            parts += form
        elif not returned:
            parts = f'<span style="color:var(--muted);">{lock_txt or "Not returned yet"}</span>'
        out += f'<tr><td colspan="2"><b>{closure_item_title(it)}</b></td><td colspan="9">{parts}</td></tr>'
    if stage == "Return" and can_pay:
        all_done = all(i.get("status") == "Returned" for i in items)
        out += (f'<tr><td colspan="11"><form method="POST" action="/closure/send_ack/{cl["closure_id"]}" style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;">'
                f'<input type="hidden" name="loan_id" value="{lid}">'
                f'<button class="btn btn-primary btn-sm" {"" if all_done else "disabled"}>📤 Send for acknowledgement</button>'
                f'<span style="font-size:12px;color:var(--muted);">{"All items recorded." if all_done else "Record every item above first."}</span></form></td></tr>')
    return out

def seizure_card_html(loan, sz, can_pay, can_reopen, today):
    """The 'Vehicle seized' card on the EMI page: details, key / RC receipt rows (per vehicle),
    send-for-acknowledgement button and the admin-only reopen option."""
    lid, st, sid = loan["id"], sz["status"], sz["seizure_id"]
    items = seizure_items(sid) if st != "Pending" else []
    color = {"Pending": "#fef3c7", "Return": "#e0f2fe", "AckPending": "#ede9fe", "Seized": "#fee2e2"}[st]
    info = (f'<div style="line-height:1.8;font-size:13px;">'
            f'<b>Seized on:</b> {fmt_date(sz.get("seized_date"))} &nbsp;|&nbsp; <b>Kept at:</b> {html.escape(sz.get("place") or "—")}<br>'
            f'<b>Reason:</b> {html.escape(sz.get("reason") or "—")}<br>'
            f'<span style="color:var(--muted);">Requested by {html.escape(sz.get("requested_by") or "")} on {fmt_date((sz.get("requested_at") or "")[:10])}'
            f'{(" · approved by " + html.escape(sz["approved_by"]) + " on " + fmt_date((sz.get("approved_at") or "")[:10])) if sz.get("approved_by") else ""}</span>')
    if st != "Pending":
        info += (f'<br><b>Written off:</b> <b style="color:var(--red);">{fmt_inr(sz.get("written_off") or 0)}</b> — '
                 f'{html.escape(sz.get("writeoff_reason") or "")}')
    if st == "Seized":
        info += (f'<br><span style="color:var(--muted);">Acknowledged by <b>{html.escape(sz.get("acked_by") or "")}</b> on {fmt_date((sz.get("acked_at") or "")[:10])}'
                 f' · seizure details checked ✔ · legal issues checked ✔'
                 f'{(" — " + html.escape(sz["legal_note"])) if sz.get("legal_note") else ""}</span>')
    info += '</div>'
    msg = {"Pending": "⏳ Waiting for admin approval.",
           "Return": "📝 Record the key and RC received for each vehicle, then send it for acknowledgement.",
           "AckPending": "⏳ Waiting for the Account Manager to check the seizure details and legal issues.",
           "Seized": "🚫 Vehicle seized — the remaining EMIs are closed and the outstanding amount is written off."}[st]
    note = (f'<div style="color:var(--red);font-weight:600;margin-top:4px;">{html.escape(sz["ack_note"])}</div>'
            if st == "Return" and sz.get("ack_note") else "")
    rows = ""
    for it in items:
        got = it["status"] == "Received"
        parts = ""
        if got:
            parts = (f'✅ Received on <b>{fmt_date(it["received_on"])}</b> · by <b>{html.escape(it.get("received_by") or "")}</b>'
                     + (f' · {html.escape(it["note"])}' if it.get("note") else "")
                     + f' <span style="color:var(--muted);font-size:11.5px;">(recorded by {html.escape(it.get("recorded_by") or "")})</span>')
        if st == "Return" and can_pay:
            parts += (f'<form method="POST" action="/seizure/item/{it["item_id"]}" style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:{4 if got else 0}px;">'
                      f'<input type="hidden" name="loan_id" value="{lid}">'
                      f'<input type="date" name="received_on" value="{it.get("received_on") or today.isoformat()}" max="{today.isoformat()}" required style="width:135px;font-size:12px;padding:5px 6px;" title="Date received">'
                      f'<input name="received_by" value="{html.escape(it.get("received_by") or "")}" placeholder="Received by *" required style="width:150px;font-size:12px;padding:5px 6px;">'
                      f'<input name="note" value="{html.escape(it.get("note") or "")}" placeholder="Note (optional)" style="width:150px;font-size:12px;padding:5px 6px;">'
                      f'<button class="btn btn-success btn-sm">{"Update" if got else "Mark received"}</button></form>')
        elif not got:
            parts = '<span style="color:var(--muted);">Not recorded yet</span>'
        rows += f'<tr><td style="white-space:nowrap;"><b>{seizure_item_title(it)}</b></td><td>{parts}</td></tr>'
    items_html = f'<div class="table-wrap" style="margin-top:8px;"><table>{rows}</table></div>' if rows else ""
    send = ""
    if st == "Return" and can_pay:
        all_done = all(i["status"] == "Received" for i in items)
        send = (f'<form method="POST" action="/seizure/send_ack/{sid}" style="margin-top:8px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;">'
                f'<input type="hidden" name="loan_id" value="{lid}">'
                f'<button class="btn btn-primary btn-sm" {"" if all_done else "disabled"}>📤 Send for acknowledgement</button>'
                f'<span style="font-size:12px;color:var(--muted);">{"All items recorded." if all_done else "Record every item above first."}</span></form>')
    reopen = ""
    if can_reopen and st in ("Return", "AckPending", "Seized"):
        reopen = (f'<form method="POST" action="/seizure/reopen/{sid}" style="margin-top:10px;" '
                  f'onsubmit="var r=prompt(\'Reason for reopening this loan (e.g. customer paid the dues):\');if(!r)return false;this.reason.value=r;'
                  f'return confirm(\'Reopen the loan? The EMIs go back to their earlier status and the write-off is reversed.\')">'
                  f'<input type="hidden" name="loan_id" value="{lid}"><input type="hidden" name="reason">'
                  f'<button class="btn btn-amber btn-sm">♻️ Reopen loan (customer paid)</button></form>')
    return (f'<div class="card" id="seizure" style="margin-bottom:12px;border-left:5px solid var(--red);">'
            f'<div style="background:{color};border-radius:8px;padding:8px 12px;margin-bottom:8px;"><b>🚫 Vehicle seizure</b> — {msg}{note}</div>'
            f'{info}{items_html}{send}{reopen}</div>')

# ── EMIs ────────────────────────────────────────────────────────────────────────
@app.route("/emis/<int:loan_id>")
@login_required
def emis(loan_id):
    c = get_cur(); c.execute("SELECT * FROM LoanEntry WHERE id=?", (loan_id,))
    loan = dict(c.fetchone() or {}); emi_list = get_emis_for_loan(loan_id)
    payments_by_emi = get_payments_by_emi_for_loan(loan_id)
    c.execute("SELECT * FROM PendingPayments WHERE loan_id=? AND status='Pending'", (loan_id,))
    pending_by_emi = {r["emi_id"]: dict(r) for r in c.fetchall()}
    penalty_by_emi = get_penalties_by_emi(loan_id)
    role = session.get("role",""); can_pay = ROLES.get(role,{}).get("can_pay", False)
    can_edit_emi = ROLES.get(role,{}).get("can_edit", False)
    today = date.today(); upcoming_limit = today + timedelta(days=UPCOMING_DAYS)

    pc = get_preclosure(loan_id)
    pc_open = bool(pc and pc["status"] in ("Pending", "Approved"))
    sz = get_last_seizure(loan_id)
    sz_live = sz if sz and sz["status"] in SEIZURE_LIVE else None
    total_remaining = sum(float(e.get("remaining_amount") or e["emi_amount"]) + emi_penalty_out(e)
                          for e in emi_list if e["status"] not in ("Paid", "PreClosed", "Seized"))
    rows = ""
    next_no = min((x["installment_no"] for x in emi_list if x["status"] not in ("Paid", "PreClosed", "Seized")), default=None)
    for e in emi_list:
        due_d = parse_date(e["due_date"])
        is_paid = e["status"] == "Paid"
        is_pre = e["status"] in ("PreClosed", "Seized")
        is_overdue = not is_paid and not is_pre and due_d < today
        is_upcoming = not is_paid and not is_pre and not is_overdue and due_d <= upcoming_limit

        row_class = ""
        if is_overdue:   row_class = "row-overdue"
        elif is_upcoming: row_class = "row-upcoming"
        elif is_paid or is_pre: row_class = "row-paid"

        sc = {"Paid":"paid","Partial":"partial","Overdue":"overdue","PreClosed":"closed","Seized":"rejected"}.get(e["status"],"pending")
        remaining = 0.0 if (is_paid or is_pre) else float(e.get("remaining_amount") if e.get("remaining_amount") is not None else e["emi_amount"])
        pen_out = 0.0 if (is_paid or is_pre) else emi_penalty_out(e)
        remaining_total = remaining + pen_out          # EMI balance + penalty added to this EMI: collected in full
        bill_no   = format_bill_ref(e["installment_no"], payments_by_emi.get(e["emi_id"], []), is_paid)
        paid_on_html, pay_status_html = paid_on_and_status(e, payments_by_emi.get(e["emi_id"], []), is_paid, today)

        pay_form = ""
        pend = pending_by_emi.get(e["emi_id"])
        pen = penalty_by_emi.get(e["emi_id"])
        if pend:
            pay_form = (f'<div style="font-size:12px;background:#fef3c7;border:1px solid #fde68a;border-radius:8px;padding:6px 8px;white-space:normal;max-width:230px;">'
                        f'⏳ <b>₹{float(pend["amount"]):,.2f}</b> awaiting acknowledgement<br>'
                        f'<span style="color:var(--muted);">bill {html.escape(pend["bill_number"])} · entered by {html.escape(pend["requested_by"] or "")}</span></div>')
        elif can_pay and not is_paid and not is_pre and not pc_open and e["installment_no"] != next_no:
            pay_form = '<span style="font-size:11.5px;color:var(--muted);">Pay the earlier EMI first — EMIs close in order.</span>'
        elif can_pay and not is_paid and not is_pre and not pc_open:
            pay_form = f'<a class="btn btn-success btn-sm" href="/billing/new/{loan_id}">🧾 Pay via Billing</a>'
        if pen:
            label = {"Pending": "pending admin approval", "Approved": "approved - to be collected", "Collected": "collected",
                     "Waived": "waived", "WrittenOff": "written off (vehicle seized)"}.get(pen["status"], pen["status"])
            if pen.get("merged_emi_id") and pen["status"] in ("Approved", "Collected"):
                tgt_no = next((x["installment_no"] for x in emi_list if x["emi_id"] == pen["merged_emi_id"]), None)
                where = "this EMI" if pen["merged_emi_id"] == e["emi_id"] else f"installment {tgt_no}"
                label = (f"added to {where}'s amount, collected with it" if pen["status"] == "Approved"
                         else f"collected with {where}")
            amt = pen["final_amount"] if pen["status"] != "Pending" and pen["final_amount"] is not None else pen["requested_amount"]
            pay_status_html += (f'<br><span style="font-weight:600;color:#7c3aed;">💰 Penalty {fmt_inr(amt or 0)} '
                                f'({pen["days"]} d) — {label}</span>')

        emi_edit_link = f'<a class="btn btn-sm btn-amber" href="/emi/edit/{e["emi_id"]}?loan_id={loan_id}">&#9998;</a>' if can_edit_emi else ""
        rows += f'<tr class="{row_class}" id="emi_{e["emi_id"]}">'
        rows += f"""<td>{e['installment_no']}</td><td>{fmt_date(e['due_date'])}</td>
          <td>₹{e['emi_amount']:,.2f}{('<br><span style="font-size:11px;font-weight:700;color:#7c3aed;">+ ' + fmt_inr(e.get('penalty_due')) + ' penalty</span>') if float(e.get('penalty_due') or 0) > 0 else ''}</td>
          <td style="white-space:nowrap;">{paid_amount_cell(e, payments_by_emi.get(e["emi_id"], []))}</td>
          <td><b>₹{remaining_total:,.2f}</b>{('<br><span style="font-size:11px;color:#7c3aed;">EMI ' + fmt_inr(remaining) + ' + penalty ' + fmt_inr(pen_out) + '</span>') if pen_out else ''}</td>
          <td><span class="badge badge-{sc}">{"Closed - Seized" if e["status"] == "Seized" else ("Pre-closed" if is_pre else e['status'])}</span></td>
          <td>{bill_no}</td><td style="white-space:nowrap;">{paid_on_html}</td>
          <td style="white-space:nowrap;">{pay_status_html}</td>
          <td style="text-align:center;">{late_days_cell(e, payments_by_emi.get(e["emi_id"], []), is_paid)}</td>
          <td style="white-space:nowrap;">{pay_form}{emi_edit_link}
          </td>
        </tr>"""

    # Legend
    legend = """
    <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:10px;font-size:12px;">
      <span style="display:flex;align-items:center;gap:4px;">
        <span style="width:14px;height:14px;background:#fee2e2;border-left:3px solid #dc2626;display:inline-block;"></span> Overdue
      </span>
      <span style="display:flex;align-items:center;gap:4px;">
        <span style="width:14px;height:14px;background:#fef9c3;border-left:3px solid #d97706;display:inline-block;"></span> Due in 10 days
      </span>
      <span style="display:flex;align-items:center;gap:4px;">
        <span style="width:14px;height:14px;background:#d1fae5;display:inline-block;border-radius:2px;"></span> Paid
      </span>
    </div>"""

    guarantor_header = ""
    g_nums = guarantor_numbers_html(loan, include_addon=False)
    if g_nums != "—":
        guarantor_header = (f"<div><b>Guarantor:</b> {html.escape(loan.get('guarantor_name') or '')} — "
                            f"{g_nums.replace('<br>', ' &nbsp;·&nbsp; ')}"
                            f"{('<br><span style=font-size:12px;color:var(--muted);>Current: ' + html.escape(loan['guarantor_address']) + '</span>') if loan.get('guarantor_address') else ''}"
                            f"{('<br><span style=font-size:12px;color:var(--muted);>Permanent: ' + html.escape(loan['guarantor_permanent_address']) + '</span>') if loan.get('guarantor_permanent_address') else ''}</div>")
    for gx in extra_guarantors_of(loan):
        guarantor_header += (f"<div><b>Guarantor {gx['seq']}:</b> {html.escape(gx.get('name') or '')} — "
                             f"{contact_numbers_html(gx.get('mobile'), (gx.get('name') or 'Guarantor') + ' (Guarantor ' + str(gx['seq']) + ')', None, 'Guarantor')}"
                             f"{(' · Current: ' + html.escape(gx['address'])) if gx.get('address') else ''}"
                             f"{(' · Permanent: ' + html.escape(gx['permanent_address'])) if gx.get('permanent_address') else ''}"
                             f"{(' · ' + _location_link(gx['location'])) if gx.get('location') else ''}</div>")
    xvs = extra_vehicles_of(loan)
    vehicle_extra_header = ""
    if xvs:
        vehicle_extra_header = (f'<div style="grid-column:1/-1;font-size:13px;line-height:1.8;">'
                                f'<b>🚗 Vehicle 1:</b> {html.escape(vehicle_tag(loan))} &nbsp;<span style="font-size:12px;">{handover_status_html(loan)}</span>')
        for v in xvs:
            vehicle_extra_header += (f'<br><b>🚗 Vehicle {v["seq"]}:</b> {html.escape(vehicle_tag(v))}'
                                     f'{(" · " + html.escape(v["vehicle_type"])) if v.get("vehicle_type") else ""}'
                                     f'{(" · " + html.escape(v["vehicle_colour"])) if v.get("vehicle_colour") else ""}'
                                     f' &nbsp;<span style="font-size:12px;">{handover_status_html(v)}</span>')
        vehicle_extra_header += '</div>'
    preclose_box = ""
    if pc and pc["status"] == "Pending":
        preclose_box = (f'<span class="badge badge-pending" style="font-size:13px;padding:8px 12px;">'
                        f'⏳ Pre-closure sent for admin approval (requested by {html.escape(pc.get("requested_by") or "")}'
                        f'{(" · penalty " + fmt_inr(pc["requested_rate"]) + "/day requested") if pc.get("requested_rate") else ""})</span>')
    elif pc and pc["status"] == "Approved":
        settle = float(pc["settlement_amount"] or 0)
        if settle > 0:
            close_form = (f'<form method="POST" action="/preclose/close/{pc["preclose_id"]}" '
                          f'style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:8px;" '
                          f'onsubmit="return confirm(\'Close this loan with the settlement bill? This cannot be undone.\')">'
                          f'<input name="bill_number" placeholder="Closing Bill No.*" required style="width:150px;font-size:12px;padding:6px;">'
                          f'<input type="date" name="paid_on" value="{today.isoformat()}" max="{today.isoformat()}" required '
                          f'style="width:140px;font-size:12px;padding:6px;" title="Paid On">'
                          f'<button class="btn btn-success btn-sm">🔒 Close Loan</button></form>')
        else:
            close_form = (f'<form method="POST" action="/preclose/close/{pc["preclose_id"]}" style="margin-top:8px;" '
                          f'onsubmit="return confirm(\'Close this loan now? This cannot be undone.\')">'
                          f'<input type="hidden" name="paid_on" value="{today.isoformat()}">'
                          f'<button class="btn btn-success btn-sm">🔒 Close Loan</button></form>')
        pay_line = (f'Pay <b style="font-size:16px;">₹{settle:,.2f}</b> in <b>one bill</b> to close the loan.' if settle > 0
                    else 'No further payment is due.')
        preclose_box = (f'<div style="background:#d1fae5;border:1px solid #6ee7b7;border-radius:10px;padding:10px 14px;max-width:440px;font-size:13px;">'
                        f'<b>✅ Pre-closure approved</b> by {html.escape(pc.get("approved_by") or "")}<br>'
                        + (f'Interest to collect further <b>{fmt_inr(pc["further_interest"])}</b> '
                           f'({int(pc.get("waived_months") or 0)} month(s) waived; already paid ₹{float(pc["paid_before"] or 0):,.2f})<br>'
                           if pc.get("further_interest") is not None else
                           f'Interest rate reduced <b>{float(pc["original_rate"] or 0)*100:.2f}% → {float(pc["new_rate"] or 0)*100:.2f}%</b> '
                           f'(for {int(pc["months_elapsed"] or 0)} month(s); already paid ₹{float(pc["paid_before"] or 0):,.2f})<br>') +
                        f'{("💰 Includes a penalty of <b>" + fmt_inr(pc["penalty_amount"]) + "</b>" + ((" for " + str(int(pc["penalty_days"])) + " overdue day(s)") if pc.get("penalty_days") else "") + "<br>") if float(pc.get("penalty_amount") or 0) > 0 else ""}'
                        f'{pay_line}{close_form if can_pay else ""}</div>')
    elif pc and pc["status"] == "Completed" and loan.get("status") == "Closed":
        preclose_box = (f'<span class="badge badge-closed" style="font-size:13px;padding:8px 12px;">'
                        f'⏩ Pre-closed on {fmt_date(pc.get("paid_on"), "")} — settled ₹{float(pc["settlement_amount"] or 0):,.2f} '
                        f'{("(incl. penalty " + fmt_inr(pc["penalty_amount"]) + ") ") if float(pc.get("penalty_amount") or 0) > 0 else ""}'
                        f'(bill {html.escape(pc.get("bill_number") or "—")})</span>')
    elif pc and pc["status"] == "Completed":
        preclose_box = (f'<span class="badge badge-partial" style="font-size:13px;padding:8px 12px;">'
                        f'⏩ Pre-closure bill paid on {fmt_date(pc.get("paid_on"), "")} (₹{float(pc["settlement_amount"] or 0):,.2f}'
                        f'{(", incl. penalty " + fmt_inr(pc["penalty_amount"])) if float(pc.get("penalty_amount") or 0) > 0 else ""}) — '
                        f'key &amp; document return is in progress below</span>')
    elif loan.get("status") == "Approved" and can_pay and not get_open_closure(loan_id) and not sz_live:
        rejected_note = ""
        if pc and pc["status"] == "Rejected":
            rejected_note = (f'<div style="font-size:12px;color:var(--red);margin-bottom:6px;text-align:right;">'
                             f'Last pre-closure request was rejected: {html.escape(pc.get("decision_remarks") or "")}</div>')
        od_rows = preclosure_overdue_rows(loan_id)
        od_note = ""
        if od_rows:
            od_note = (f'<div style="font-size:12.5px;margin-bottom:8px;line-height:1.6;"><b style="color:var(--red);">{len(od_rows)} overdue EMI(s)</b> '
                       f'({sum(d for _, d in od_rows)} overdue days in total). Enter a per-day penalty to send a penalty (days × amount) '
                       f'to the approver, or leave it blank for none.</div>'
                       f'<div class="form-group" style="margin-bottom:8px;"><label>Penalty per day (₹) — optional</label>'
                       f'<input type="number" name="penalty_rate" min="0" step="0.01" placeholder="e.g. 10"></div>')
        preclose_box = (f'<div>{rejected_note}<details style="max-width:380px;"><summary class="btn btn-amber" style="list-style:none;cursor:pointer;display:inline-block;">⏩ Pre-Close Loan</summary>'
                        f'<form method="POST" action="/preclose/request/{loan_id}" class="card" style="margin-top:8px;padding:12px;" '
                        f'onsubmit="return confirm(\'Send a pre-closure request to the admin for approval?\')">{od_note}'
                        f'<button class="btn btn-amber btn-sm">Send pre-closure request</button></form></details></div>')
    seizure_box = ""
    if sz_live and sz_live["status"] == "Pending":
        seizure_box = (f'<span class="badge badge-pending" style="font-size:13px;padding:8px 12px;">'
                       f'⏳ Vehicle seizure sent for admin approval (requested by {html.escape(sz_live.get("requested_by") or "")})</span>')
    elif loan.get("status") == "Approved" and can_pay and not sz_live and not pc_open and not get_open_closure(loan_id):
        n_over, need = len(overdue_emis(loan_id)), seizure_threshold()
        if n_over >= need:
            sz_note = ""
            if sz and sz["status"] == "Rejected":
                sz_note = (f'<div style="font-size:12px;color:var(--red);margin-bottom:6px;">Last seizure request was rejected: '
                           f'{html.escape(sz.get("decision_remarks") or "")}</div>')
            seizure_box = f"""<details style="max-width:470px;">
              <summary class="btn btn-danger" style="list-style:none;cursor:pointer;display:inline-block;">🚫 Vehicle Seized</summary>
              <form method="POST" action="/seizure/request/{loan_id}" class="card" style="margin-top:8px;padding:12px;"
                    onsubmit="return confirm('Send the vehicle seizure request to the admin for approval?')">
                {sz_note}<div style="font-size:12.5px;margin-bottom:8px;line-height:1.6;">This loan has <b>{n_over} overdue EMIs</b>.
                After the admin approves, every unpaid EMI is closed as <b>Seized</b> and the outstanding
                <b>{fmt_inr(total_remaining)}</b> is written off.</div>
                <div class="form-group" style="margin-bottom:8px;"><label>Reason for seizure *</label><textarea name="reason" rows="2" required></textarea></div>
                <div class="form-group" style="margin-bottom:8px;"><label>Seized date *</label>
                  <input type="date" name="seized_date" value="{today.isoformat()}" max="{today.isoformat()}" required></div>
                <div class="form-group" style="margin-bottom:8px;"><label>Where the vehicle is kept *</label><input name="place" required></div>
                <div class="form-group" style="margin-bottom:10px;"><label>Reason for writing off the outstanding amount *</label>
                  <textarea name="writeoff_reason" rows="2" required></textarea></div>
                <button class="btn btn-danger btn-sm">Send for approval</button>
              </form></details>"""
    preclose_row = (f'<div style="display:flex;justify-content:flex-end;gap:10px;flex-wrap:wrap;align-items:flex-start;margin-bottom:10px;">'
                    f'{seizure_box}{preclose_box}</div>') if (preclose_box or seizure_box) else ""
    seizure_card = seizure_card_html(loan, sz_live, can_pay, role in DIRECT_ROLES, today) if sz_live else ""
    closing_section = closing_section_html(loan, can_pay, penalty_by_emi, today) if loan.get("status") in ("Approved", "Closed") else ""
    content = f"""
    <h1>💳 EMI Schedule — {loan.get('loan_number','')}</h1>
    <div class="card" style="margin-bottom:12px;">
      {preclose_row}
      <div class="form-grid">
        <div><b>Customer:</b> {loan.get('customer_name','')}</div>
        <div><b>Mobile:</b> {customer_numbers_html(loan).replace("<br>", " &nbsp;·&nbsp; ")}</div>
        {guarantor_header}
        <div><b>Vehicle:</b> {html.escape(vehicle_label_more(loan))} — {loan.get('vehicle_number','')}</div>
        <div><b>Type:</b> {html.escape(loan.get('vehicle_type') or '—')}{(' · ' + html.escape(loan['vehicle_colour'])) if loan.get('vehicle_colour') else ''}</div>
        {vehicle_extra_header}
        <div style="grid-column:1/-1;">{documents_summary_html(loan)}</div>
        <div><b>Loan Amount:</b> ₹{float(loan.get('loan_amount',0)):,.2f}</div>
        <div><b>Tenure:</b> {loan.get('tenure','')} months | <b>Status:</b> {loan.get('status','')}</div>
        <div style="grid-column:1/-1;background:#fef3c7;border-radius:6px;padding:10px;border-left:4px solid #d97706;">
          <b>⏳ Total Outstanding: </b>
          <span style="font-size:18px;font-weight:700;color:var(--amber);">₹{total_remaining:,.2f}</span>
        </div>
      </div>
    </div>
    {seizure_card}
    <div class="card">
      <p style="font-size:12px;color:var(--muted);margin-bottom:8px;">
        📌 Bill number is <b>mandatory</b> before payment. Partial payments need a bill number each time.
      </p>
      {legend}
      <div class="table-wrap"><table>
        <tr><th>#</th><th>Due Date</th><th>EMI</th><th>Paid</th><th>Remaining</th>
            <th>Status</th><th>Bill No</th><th>Paid On</th><th>Payment Status</th><th>Late Payment Days</th><th>Action</th></tr>
        {rows or '<tr><td colspan="11" style="text-align:center;">No EMIs</td></tr>'}
        {closing_section}
      </table></div>
    </div>
    <script>
    function chkBill(btn){{
      const bn=btn.closest('form').querySelector('[name=bill_number]').value.trim();
      if(!bn){{alert('Bill number is mandatory!');return false;}} return true;
    }}
    // Scroll to highlighted emi if anchor
    const h=window.location.hash;
    if(h){{const el=document.querySelector(h);if(el){{el.scrollIntoView({{behavior:'smooth',block:'center'}});}}}}
    </script>
    <a href="/loans" class="btn" style="background:var(--surface2);color:var(--text);"
       onclick="if(document.referrer && document.referrer.indexOf(window.location.host)!==-1){{history.back();return false;}}">← Back</a>"""
    return page("EMIs", content, "emis")

@app.route("/emi/pay", methods=["POST"])
@login_required
@role_required("superadmin","admin","manager","fieldpia")
def emi_pay():
    loan_id = int(request.form["loan_id"])
    flash("EMI payments are made through Billing only.","warning")
    return redirect(url_for("billing_new", loan_id=loan_id))

# ── Billing ────────────────────────────────────────────────────────────────────
def _unpaid_emis(loan_id):
    c = get_cur()
    c.execute("""SELECT * FROM EMI WHERE loan_id=? AND status NOT IN ('Paid','PreClosed','Seized')
                 ORDER BY installment_no ASC""", (loan_id,))
    return [dict(r) for r in c.fetchall()]

def _emi_remaining(e):
    """What is still to be collected on an installment: EMI balance + any penalty added to it."""
    rem = e.get("remaining_amount")
    return float(rem if rem is not None else e["emi_amount"]) + emi_penalty_out(e)

@app.route("/billing")
@login_required
@role_required("superadmin","admin","manager","fieldpia")
def billing():
    q = request.args.get("q","").strip()
    c = get_cur(); today = date.today()
    cards = ""
    if q:
        digits = re.sub(r"\D", "", q)
        like = f"%{q}%"; dlike = f"%{digits}%" if len(digits) >= 3 else like
        c.execute("""SELECT * FROM LoanEntry WHERE status='Approved' AND
                        (loan_number LIKE ? OR customer_name LIKE ? OR vehicle_number LIKE ?
                         OR customer_mobile LIKE ? OR guarantor_mobile LIKE ?
                         OR customer_extra_numbers LIKE ? OR guarantor_extra_numbers LIKE ?)
                     ORDER BY loan_number ASC LIMIT 20""", (like, like, like, dlike, dlike, dlike, dlike))
        for l in [dict(r) for r in c.fetchall()]:
            nxt = (_unpaid_emis(l["id"]) or [None])[0]
            if nxt:
                late = (today - parse_date(nxt["due_date"])).days
                due_txt = (f'<b>{ordinal_due(nxt["installment_no"])}</b> — due {fmt_date(nxt["due_date"])} — pending '
                           f'<b>₹{_emi_remaining(nxt):,.2f}</b>'
                           + (f' <span style="color:var(--red);font-weight:600;">({late} day{"s" if late != 1 else ""} overdue)</span>' if late > 0 else ""))
                btn = f'<a class="btn btn-primary btn-sm" href="/billing/new/{l["id"]}">🧾 Make Bill</a>'
            else:
                due_txt, btn = "No unpaid EMI.", ""
            cards += f"""<div class="card" style="margin-bottom:10px;">
              <div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;align-items:center;">
                <div>
                  <b style="color:var(--accent);font-size:15px;">{html.escape(l['loan_number'])}</b> — {html.escape(l['customer_name'] or '')}
                  <div style="font-size:12px;color:var(--muted);margin:2px 0 6px;">
                    🚗 {html.escape(vehicle_label_more(l))} · {html.escape(l.get('vehicle_number') or '—')} &nbsp;|&nbsp; 📱 {customer_numbers_html(l).replace('<br>', ' · ')}
                  </div>
                  <div style="font-size:13px;">Next EMI: {due_txt}</div>
                </div>
                <div>{btn}</div>
              </div></div>"""
        if not cards:
            cards = '<div class="card"><p style="text-align:center;color:var(--muted);">No active loan matches that phone number, loan number or name.</p></div>'
    # receipts list (filtered by the same search box)
    rlike = f"%{q}%"
    c.execute("""SELECT * FROM Receipts WHERE (? = '' OR receipt_no LIKE ? OR received_from LIKE ? OR loan_number LIKE ?)
                 ORDER BY receipt_id DESC LIMIT 50""", (q, rlike, rlike, rlike))
    receipts = [dict(r) for r in c.fetchall()]
    rrows = "".join(f"""<tr>
        <td><b>{html.escape(r['receipt_no'])}</b></td><td>{fmt_date(r['receipt_date'])}</td><td>{html.escape(r['received_from'] or '')}</td>
        <td>{html.escape(r['loan_number'] or '')}</td><td>{html.escape(r['installment_label'] or '')}</td>
        <td>₹{float(r['cash'] or 0):,.2f}</td><td>₹{float(r['online'] or 0):,.2f}</td><td><b>₹{float(r['total'] or 0):,.2f}</b></td>
        <td>{html.escape(r['cashier'] or '')}</td>
        <td style="white-space:nowrap;"><a class="btn btn-sm btn-primary" href="/billing/receipt/{r['receipt_id']}">View</a>
            <a class="btn btn-sm btn-success" href="/billing/receipt/{r['receipt_id']}/pdf">⬇ PDF</a></td></tr>""" for r in receipts)
    content = f"""
    <h1>🧾 Billing</h1>
    <form method="GET" style="margin-bottom:12px;display:flex;gap:8px;flex-wrap:wrap;">
      <input name="q" value="{html.escape(q)}" placeholder="Search by phone number, loan number or customer name…" style="max-width:420px;" autofocus>
      <button class="btn btn-primary btn-sm">Search</button>
    </form>
    {('<h3 style="margin:6px 0 10px;">Matching loans</h3>' + cards) if q else '<p style="font-size:12.5px;color:var(--muted);margin-bottom:10px;">Search for the customer to make a bill. Phone numbers match the customer’s and guarantor’s numbers, including additional ones.</p>'}
    <h3 style="margin:16px 0 10px;">Receipts {('matching “' + html.escape(q) + '”') if q else '(latest 50)'}</h3>
    <div class="card"><div class="table-wrap"><table>
      <tr><th>Receipt No</th><th>Date</th><th>Received From</th><th>Loan No</th><th>Installment</th>
          <th>Cash</th><th>Online</th><th>Total</th><th>Cashier</th><th>Bill</th></tr>
      {rrows or '<tr><td colspan="10" style="text-align:center;color:var(--muted);">No receipts yet.</td></tr>'}
    </table></div></div>"""
    return page("Billing", content, "billing")

_BILLING_JS = """<script>
(function(){
var emis=__EMIS__;            // unpaid installments in order: {no, rem}
function inr(v){return '\\u20b9'+v.toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2});}
function r2(x){return Math.round(x*100)/100;}
var wt=null;
window.recalc=function(){
  var total=r2((parseFloat(document.getElementById('cash').value)||0)+(parseFloat(document.getElementById('online').value)||0));
  document.getElementById('totalAmt').textContent=inr(total);
  clearTimeout(wt);
  wt=setTimeout(function(){
    if(total<=0){document.getElementById('wordsAmt').textContent='';return;}
    fetch('/billing/words?amount='+total).then(function(r){return r.json();}).then(function(d){document.getElementById('wordsAmt').textContent='Rupees: '+d.words;});
  },200);
  var box=document.getElementById('alloc');
  if(total<=0){ box.innerHTML='<div style="font-size:12.5px;color:var(--muted);">Enter the amount to see which installments it closes.</div>'; return; }
  var left=total, full=[], part=null;
  emis.forEach(function(e){ if(left<=0.004) return; var a=r2(Math.min(left,e.rem)); left=r2(left-a);
    if(a>=e.rem-0.004) full.push(e.no); else part={no:e.no,amt:a,rem:r2(e.rem-a)}; });
  if(!full.length && !part){ box.innerHTML=''; return; }
  var lab=full.length?('Installment '+full.join(', ')):('Installment '+part.no+' (part payment)');
  var h='<div style="font-size:14px;">Bill shows: <b>'+lab+'</b> &mdash; '+inr(total)+'</div>';
  if(full.length && part) h+='<div style="font-size:12.5px;color:var(--muted);margin-top:4px;">'+inr(part.amt)+' is adjusted in installment '+part.no+
     ' (not shown on the bill; '+inr(part.rem)+' still to pay on it).</div>';
  else if(part) h+='<div style="font-size:12.5px;color:var(--muted);margin-top:4px;">'+inr(part.rem)+' still to pay on installment '+part.no+'.</div>';
  if(left>0.004) h+='<div style="color:var(--red);font-weight:600;font-size:12.5px;margin-top:6px;">The amount is '+inr(left)+' more than everything pending on this loan.</div>';
  box.innerHTML=h;
};
window.recalc();
})();
</script>"""

def _billing_form(loan_id, values=None):
    values = values or {}
    c = get_cur(); c.execute("SELECT * FROM LoanEntry WHERE id=?", (loan_id,))
    loan = c.fetchone()
    if not loan:
        flash("Loan not found.","danger"); return redirect(url_for("billing"))
    loan = dict(loan)
    if loan["status"] != "Approved":
        flash("Bills can only be made for active (approved) loans.","danger"); return redirect(url_for("billing"))
    emis_open = _unpaid_emis(loan_id)
    pc_open = preclosure_in_progress(loan_id)
    opts = "".join(
        f'<div style="padding:7px 10px;border-bottom:1px solid var(--border);font-size:13px;">'
        f'<b>Installment {e["installment_no"]}</b> — due {fmt_date(e["due_date"])} — pending <b>₹{_emi_remaining(e):,.2f}</b>'
        f'{(" (incl. penalty ₹" + format(emi_penalty_out(e), ",.2f") + ")") if emi_penalty_out(e) else ""}</div>'
        for e in emis_open)
    emis_js = json.dumps([{"no": e["installment_no"], "rem": round(_emi_remaining(e), 2)} for e in emis_open])
    c.execute("SELECT receipt_no FROM Receipts ORDER BY receipt_id DESC LIMIT 1")
    last = c.fetchone(); last_txt = f"Last receipt: <b>{html.escape(last['receipt_no'])}</b>" if last else "No receipts yet"
    pc_note = ('<div class="alert alert-warning" style="font-size:12px;">A pre-closure is in progress for this loan, so payments are paused '
               'and a bill cannot be made until it is finished.</div>') if pc_open else ""
    content = f"""
    <h1>🧾 Make Bill — {html.escape(loan['loan_number'])}</h1>
    {pc_note}
    <div class="card"><form method="POST" action="/billing/create" id="billForm">
      <input type="hidden" name="loan_id" value="{loan_id}">
      <div class="form-grid">
        <div class="form-group">
          <label>Receipt No * <span style="font-size:10px;color:var(--muted);">({last_txt})</span></label>
          <input name="receipt_no" id="receipt_no" value="{html.escape(values.get('receipt_no',''))}" required placeholder="e.g. B745" oninput="recalc()">
        </div>
        <div class="form-group">
          <label>Date *</label>
          <input type="date" name="receipt_date" value="{html.escape(values.get('receipt_date') or date.today().isoformat())}" max="{date.today().isoformat()}" required>
        </div>
        <div class="form-group">
          <label>Received From *</label>
          <input name="received_from" value="{html.escape(values.get('received_from') or loan.get('customer_name') or '')}" required>
        </div>
        <div class="form-group">
          <label>Vehicle No.</label>
          <input value="{html.escape(loan.get('vehicle_number') or '')}" readonly style="background:var(--surface2);">
        </div>
        <div class="form-group">
          <label>Loan No.</label>
          <input value="{html.escape(loan['loan_number'])}" readonly style="background:var(--surface2);">
        </div>
        <div class="form-group">
          <label>Cash (₹)</label>
          <input type="number" name="cash" id="cash" min="0" step="0.01" value="{html.escape(str(values.get('cash','')))}" oninput="recalc()">
        </div>
        <div class="form-group full">
          <label>Pending installments <span style="font-size:10px;color:var(--muted);">(the amount closes them in order, oldest first)</span></label>
          <div style="max-height:230px;overflow:auto;border:1px solid var(--border);border-radius:8px;background:var(--surface);">{opts}</div>
        </div>
        <div class="form-group">
          <label>Online (₹)</label>
          <input type="number" name="online" id="online" min="0" step="0.01" value="{html.escape(str(values.get('online','')))}" oninput="recalc()">
        </div>
        <div class="form-group">
          <label>Penalty per day (₹) <span style="font-size:10px;color:var(--muted);">(optional — only for an EMI paid late; goes to admin approval)</span></label>
          <input type="number" name="penalty_rate" min="0" step="0.01" value="{html.escape(str(values.get('penalty_rate','')))}" placeholder="e.g. 50">
        </div>
        <div class="form-group full">
          <div style="background:var(--surface2);border-radius:8px;padding:10px 12px;">
            <div style="font-size:12px;color:var(--muted);" id="remHint"></div>
            <div style="font-size:18px;font-weight:800;margin-top:2px;">Total: <span id="totalAmt">₹0.00</span></div>
            <div style="font-size:13px;margin-top:2px;" id="wordsAmt"></div>
            <div id="alloc" style="margin-top:8px;"></div>
          </div>
          <div style="font-size:12px;color:var(--muted);margin-top:6px;">One bill for the whole amount. It closes the pending installments in order (a penalty on an EMI is cleared first);
            a balance that does not cover a full installment is adjusted in the next EMI. The bill is recorded on the EMIs only after it is acknowledged (an Admin / Super Admin's own bill is recorded straight away).</div>
        </div>
      </div>
      <input type="hidden" name="record_on_emi" value="yes">
      <div style="margin-top:14px;display:flex;gap:10px;flex-wrap:wrap;">
        <button type="submit" class="btn btn-primary" {"disabled" if (pc_open or not emis_open) else ""}>🧾 Generate Bill</button>
        <a href="/billing" class="btn" style="background:var(--surface2);color:var(--text);">Cancel</a>
      </div>
    </form></div>
    """ + _BILLING_JS.replace("__EMIS__", emis_js)
    return page("Make Bill", content, "billing")

@app.route("/billing/new/<int:loan_id>")
@login_required
@role_required("superadmin","admin","manager","fieldpia")
def billing_new(loan_id):
    return _billing_form(loan_id)

@app.route("/billing/words")
@login_required
def billing_words():
    try: return jsonify({"words": amount_in_words(float(request.args.get("amount","0")))})
    except ValueError: return jsonify({"words": ""})

@app.route("/billing/create", methods=["POST"])
@login_required
@role_required("superadmin","admin","manager","fieldpia")
def billing_create():
    f = request.form
    loan_id = int(f.get("loan_id", 0) or 0)
    def back(msg):
        flash(msg, "danger")
        return _billing_form(loan_id, dict(f))
    c = get_cur(); c.execute("SELECT * FROM LoanEntry WHERE id=?", (loan_id,))
    loan = c.fetchone()
    if not loan or loan["status"] != "Approved":
        flash("Bills can only be made for active (approved) loans.","danger"); return redirect(url_for("billing"))
    loan = dict(loan)
    if preclosure_in_progress(loan_id):
        return back("A pre-closure is in progress for this loan, so payments are paused. Finish or reject the pre-closure first.")
    receipt_no = f.get("receipt_no","").strip()
    if not receipt_no: return back("Receipt number is required.")
    try: rdate = datetime.strptime(f.get("receipt_date","").strip(), "%Y-%m-%d").date()
    except ValueError: return back("Enter a valid date.")
    if rdate > date.today(): return back("Date cannot be in the future.")
    try:
        cash = round(float(f.get("cash") or 0), 2); online = round(float(f.get("online") or 0), 2)
    except ValueError: return back("Enter valid amounts.")
    if cash < 0 or online < 0: return back("Amounts cannot be negative.")
    total = round(cash + online, 2)
    if total <= 0: return back("Enter a Cash or Online amount.")
    received_from = f.get("received_from","").strip()
    if not received_from: return back("'Received From' is required.")

    # the amount closes the earliest unpaid installments in order; a balance short of a full EMI goes to the next one
    unpaid = _unpaid_emis(loan_id)
    if not unpaid: return back("There is no unpaid EMI on this loan.")
    pending_total = round(sum(_emi_remaining(e) for e in unpaid), 2)
    if total > pending_total + 0.004:
        return back(f"The amount is more than the {fmt_inr(pending_total)} pending on this loan.")
    left = total; plan = []
    for e in unpaid:
        if left <= 0.004: break
        a = round(min(left, _emi_remaining(e)), 2)
        left = round(left - a, 2)
        plan.append((e, a))
    full = [e["installment_no"] for e, a in plan if a >= _emi_remaining(e) - 0.004]
    label = ("Installment " + ", ".join(str(n) for n in full)) if full else f"Installment {plan[0][0]['installment_no']} (part payment)"
    c.execute("SELECT 1 FROM Receipts WHERE LOWER(receipt_no)=LOWER(?)", (receipt_no,))
    if c.fetchone(): return back(f"Receipt number '{receipt_no}' has already been used.")

    user, role = session.get("username",""), session.get("role","")
    penalty_rate = f.get("penalty_rate")
    batch_emis = {e["emi_id"] for e, _ in plan}
    try:                                                        # check every payment before anything is saved
        emi_rows = {e["emi_id"]: validate_payment(e["emi_id"], a, receipt_no, rdate.isoformat(), in_batch=batch_emis) for e, a in plan}
    except Exception as ex:
        return back(str(ex))

    direct = role in DIRECT_ROLES
    first = plan[0][0]
    c = get_cur()
    c.execute("""INSERT INTO Receipts (receipt_no,loan_id,emi_id,installment_no,installment_label,received_from,receipt_date,
                 vehicle_number,loan_number,cash,online,total,amount_words,cashier,recorded_on_emi,created_at,batch_id)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (receipt_no, loan_id, first["emi_id"], first["installment_no"], label, received_from,
               rdate.isoformat(), loan.get("vehicle_number") or "", loan["loan_number"], cash, online, total,
               amount_in_words(total), user, 1 if direct else 2, datetime.now(timezone.utc).isoformat(),
               secrets.token_hex(6) if len(plan) > 1 else None))
    get_db().commit()
    rid = c.lastrowid
    pen_msgs = []
    for e, a in plan:
        if direct:
            pay_emi(e["emi_id"], a, bill_number=receipt_no, paid_on=rdate.isoformat(), paid_by=user)
            m = create_penalty_if_needed(e["emi_id"], penalty_rate, user)
            if m: pen_msgs.append(m)
        else:
            queue_payment(emi_rows[e["emi_id"]], a, receipt_no, rdate.isoformat(), user, penalty_rate, receipt_id=rid)
    msg = "Bill generated" + (" and recorded on the EMIs." if direct else ". It is awaiting acknowledgement before it is recorded on the EMIs (in order).")
    flash(msg + ((" " + pen_msgs[0]) if pen_msgs else ""), "success")
    return redirect(url_for("billing_receipt", receipt_id=rid))

def _get_receipt(receipt_id):
    c = get_cur(); c.execute("SELECT * FROM Receipts WHERE receipt_id=?", (receipt_id,))
    r = c.fetchone()
    return dict(r) if r else None

@app.route("/billing/receipt/<int:receipt_id>")
@login_required
@role_required("superadmin","admin","manager","fieldpia")
def billing_receipt(receipt_id):
    r = _get_receipt(receipt_id)
    if not r:
        flash("Receipt not found.","danger"); return redirect(url_for("billing"))
    def row(label, value): return (f'<div style="display:flex;gap:10px;border-bottom:1px dotted var(--border);padding:8px 0;">'
                                   f'<div style="width:130px;font-weight:700;color:var(--accent);font-size:12px;">{label}</div><div>{html.escape(str(value or ""))}</div></div>')
    batch_box = ""
    if r.get("batch_id"):
        c = get_cur(); c.execute("SELECT * FROM Receipts WHERE batch_id=? ORDER BY receipt_id", (r["batch_id"],))
        sibs = [dict(x) for x in c.fetchall()]
        if len(sibs) > 1:
            batch_box = ('<div class="card" style="max-width:560px;margin-top:12px;"><b>Bills made together</b><div style="margin-top:6px;line-height:2;">'
                         + "".join(f'<div>{"➡️ " if x["receipt_id"] == receipt_id else ""}<a href="/billing/receipt/{x["receipt_id"]}">'
                                   f'{html.escape(x["receipt_no"])}</a> — {html.escape(x["installment_label"] or "")} — ₹{float(x["total"]):,.2f} '
                                   f'<a href="/billing/receipt/{x["receipt_id"]}/pdf" style="font-size:12px;">⬇ PDF</a></div>' for x in sibs)
                         + '</div></div>')
    content = f"""
    <h1>🧾 Receipt {html.escape(r['receipt_no'])}</h1>
    <div class="card" style="max-width:560px;border:2px solid var(--accent);">
      <div style="display:flex;justify-content:space-between;gap:10px;border-bottom:2px solid var(--accent);padding-bottom:10px;margin-bottom:6px;">
        <div><div style="font-size:20px;font-weight:800;color:var(--accent);">Thendralla Fincorp</div>
             <div style="font-size:11px;color:var(--muted);">12/360 - 1, Anbu Nagar, Madukkarai Market,<br>Coimbatore - 641 105. Ph : +91 63697 52877</div></div>
        <div style="text-align:center;border-left:2px solid var(--accent);padding-left:12px;">
             <div style="font-size:10px;font-weight:800;color:var(--accent);">RECEIPT NO</div><div style="font-size:22px;font-weight:800;">{html.escape(r['receipt_no'])}</div></div>
      </div>
      {row('Received From', r['received_from'])}{row('Date', datetime.strptime(r['receipt_date'], '%Y-%m-%d').strftime('%d/%m/%Y'))}
      {row('Vehicle No.', r['vehicle_number'])}{row('Installment No.', r['installment_label'])}{row('Loan No.', r['loan_number'])}
      {row('Also paid', f"₹{float(r['extra_amount']):,.2f} towards {r.get('extra_label') or 'the next due'} (excess on the same bill)") if r.get('extra_amount') else ''}
      {row('Cash', f"₹{float(r['cash'] or 0):,.2f}")}{row('Online', f"₹{float(r['online'] or 0):,.2f}")}
      <div style="display:flex;gap:10px;padding:10px 0;font-size:18px;font-weight:800;"><div style="width:130px;color:var(--accent);">Total</div><div>₹{float(r['total']):,.2f}</div></div>
      {row('Rupees', r['amount_words'])}{row('Cashier', r['cashier'])}
      <div style="font-size:12px;margin-top:6px;color:{ {1:'var(--green)',2:'var(--amber)',3:'var(--red)'}.get(r['recorded_on_emi'],'var(--muted)') };">
        {({1:'✅ Payment recorded on the EMI with this receipt number as Bill No.',
           2:'⏳ Payment is awaiting acknowledgement; it will be recorded on the EMI once acknowledged.',
           3:'❌ The payment was not acknowledged, so it was NOT recorded on the EMI.'}).get(r['recorded_on_emi'], 'Receipt only — no EMI payment was recorded.')}</div>
    </div>
    {batch_box}
    <div style="margin-top:14px;display:flex;gap:10px;flex-wrap:wrap;">
      <a class="btn btn-success" href="/billing/receipt/{receipt_id}/pdf">⬇ Download Bill (PDF)</a>
      <a class="btn btn-primary" href="/billing">🧾 New Bill / Search</a>
      <a class="btn" style="background:var(--surface2);color:var(--text);" href="/emis/{r['loan_id']}">View EMIs</a>
    </div>"""
    return page("Receipt", content, "billing")

@app.route("/billing/receipt/<int:receipt_id>/pdf")
@login_required
@role_required("superadmin","admin","manager","fieldpia")
def billing_receipt_pdf(receipt_id):
    r = _get_receipt(receipt_id)
    if not r:
        flash("Receipt not found.","danger"); return redirect(url_for("billing"))
    if not REPORTLAB_AVAILABLE:
        flash("reportlab not installed. Run: pip install reportlab","danger"); return redirect(url_for("billing_receipt", receipt_id=receipt_id))
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", r["receipt_no"])
    return send_file(generate_receipt_pdf(r), as_attachment=True, download_name=f"Receipt_{safe}.pdf", mimetype="application/pdf")

# ── Pre-closure ────────────────────────────────────────────────────────────────
@app.route("/preclose/request/<int:loan_id>", methods=["POST"])
@login_required
@role_required("superadmin","admin","manager","fieldpia")
def preclose_request(loan_id):
    try:
        request_preclosure(loan_id, session.get("username",""), request.form.get("penalty_rate"))
        flash("Pre-closure request sent to the admin for approval.","success")
    except Exception as e:
        flash(str(e),"danger")
    return redirect(url_for("emis", loan_id=loan_id))

@app.route("/preclose/approve/<int:preclose_id>", methods=["POST"])
@login_required
@role_required("superadmin","admin")
def preclose_approve(preclose_id):
    try:
        pr, orr = {}, {}
        for k, v in request.form.items():
            if k.startswith("rate_") and k[5:].isdigit(): pr[int(k[5:])] = v
            elif k.startswith("orate_") and k[6:].isdigit(): orr[int(k[6:])] = v
        approve_preclosure(preclose_id, request.form.get("further_interest",""), request.form.get("waived_months","0"),
                           session.get("username",""), pr, orr)
        flash("Pre-closure approved. The staff can now record the closing bill (penalty included).","success")
    except Exception as e:
        flash(str(e),"danger")
    return redirect(url_for("approval"))

@app.route("/preclose/reject/<int:preclose_id>", methods=["POST"])
@login_required
@role_required("superadmin","admin")
def preclose_reject(preclose_id):
    try:
        reject_preclosure(preclose_id, request.form.get("reason",""), session.get("username",""))
        flash("Pre-closure request rejected.","success")
    except Exception as e:
        flash(str(e),"danger")
    return redirect(url_for("approval"))

@app.route("/preclose/close/<int:preclose_id>", methods=["POST"])
@login_required
@role_required("superadmin","admin","manager","fieldpia")
def preclose_close(preclose_id):
    pc_loan = None
    try:
        c = get_cur(); c.execute("SELECT loan_id FROM PreClosure WHERE preclose_id=?", (preclose_id,))
        row = c.fetchone(); pc_loan = row["loan_id"] if row else None
        complete_preclosure(preclose_id, request.form.get("bill_number",""), request.form.get("paid_on",""),
                            session.get("username",""))
        flash("Loan pre-closed and moved to Closed Loans.","success")
    except Exception as e:
        flash(str(e),"danger")
    return redirect(url_for("emis", loan_id=pc_loan) if pc_loan else url_for("dashboard"))

# ── Alerts (grouped by loan number) ────────────────────────────────────────────
@app.route("/alerts")
@login_required
def alerts():
    od    = get_overdue_emis()
    up    = get_upcoming_emis()
    today = date.today()

    # Group overdue by loan number
    od_grouped = group_alerts_by_loan(od)
    up_grouped = group_alerts_by_loan(up)
    fu_map = get_active_follow_ups_map()

    def fu_cell(loan_id, loan_number, customer_name):
        fu = fu_map.get(loan_id)
        btn = (f'<button type="button" class="btn btn-sm fu-btn" '
               f'style="background:var(--surface2);color:var(--text);margin-top:4px;" '
               f'data-loan-id="{loan_id}" data-loan-number="{html.escape(loan_number)}" '
               f'data-customer="{html.escape(customer_name)}">'
               f'{"✏️ Update" if fu else "📅 Follow Up"}</button>')
        if not fu:
            return btn
        fu_date = parse_date(fu["follow_up_date"])
        overdue_badge = ' <span class="badge badge-overdue">missed</span>' if fu_date < today else ''
        return (f'<div style="font-size:11.5px;max-width:180px;">'
                f'<span class="badge badge-partial">📅 {fmt_date(fu["follow_up_date"])}</span>{overdue_badge}'
                f'<div style="color:var(--muted);margin-top:2px;white-space:normal;">{html.escape(fu["remarks"])}</div>'
                f'{btn}</div>')

    od_rows = ""
    for g in od_grouped:
        oldest_days = (today - parse_date(g["oldest_due"])).days
        od_rows += f"""<tr style="background:#fee2e2;">
          <td><b><a href="/emis/{g['lid']}" style="color:var(--accent);">{g['loan_number']}</a></b></td>
          <td>{g['customer_name']}</td>
          <td style="text-align:center;">{g['emi_count']}</td>
          <td>{fmt_date(g['oldest_due'])}</td>
          <td><b style="color:var(--red);">₹{g['total_due']:,.2f}</b></td>
          <td><b style="color:var(--red);">{oldest_days} days</b></td>
          <td>{fu_cell(g['loan_id'], g['loan_number'], g['customer_name'])}</td>
          <td><a class="btn btn-sm btn-danger" href="/billing/new/{g['lid']}">🧾 Billing</a></td>
        </tr>"""

    up_rows = ""
    for g in up_grouped:
        days_left = (parse_date(g["oldest_due"]) - today).days
        up_rows += f"""<tr style="background:#fef9c3;">
          <td><b><a href="/emis/{g['lid']}" style="color:var(--accent);">{g['loan_number']}</a></b></td>
          <td>{g['customer_name']}</td>
          <td style="text-align:center;">{g['emi_count']}</td>
          <td>{fmt_date(g['oldest_due'])}</td>
          <td><b style="color:var(--amber);">₹{g['total_due']:,.2f}</b></td>
          <td><b style="color:var(--amber);">in {days_left} days</b></td>
          <td>{fu_cell(g['loan_id'], g['loan_number'], g['customer_name'])}</td>
          <td><a class="btn btn-sm btn-amber" href="/emis/{g['lid']}">📋 View</a></td>
        </tr>"""

    content = f"""
    <h1>🔔 Alerts</h1>

    <div class="card">
      <h2 style="color:var(--red);">🔴 Overdue — {len(od_grouped)} Loan(s) &nbsp;
        <small style="font-size:13px;color:var(--muted);">Total: ₹{sum(g['total_due'] for g in od_grouped):,.2f}</small></h2>
      <p style="font-size:12px;color:var(--muted);margin-bottom:8px;">
        Each row = one loan. Amount shown is cumulative of all overdue EMIs for that loan.
        Use <b>Follow Up</b> if the customer asked to collect on another date — it will also appear on the Follow Up tab.
      </p>
      <div class="table-wrap"><table>
        <tr><th>Loan #</th><th>Customer</th><th>EMIs Due</th><th>Oldest Due</th>
            <th>Total Due Amt</th><th>Days Overdue</th><th>Follow Up</th><th>Action</th></tr>
        {od_rows or '<tr><td colspan="8" style="color:var(--green);text-align:center;background:#d1fae5;">✅ No overdue EMIs!</td></tr>'}
      </table></div>
    </div>
    <div class="card">
      <h2 style="color:var(--amber);">🟡 Upcoming (10 days) — {len(up_grouped)} Loan(s) &nbsp;
        <small style="font-size:13px;color:var(--muted);">Total: ₹{sum(g['total_due'] for g in up_grouped):,.2f}</small></h2>
      <p style="font-size:12px;color:var(--muted);margin-bottom:8px;">
        Each row = one loan. Click loan number or Pay to go to EMI schedule.
      </p>
      <div class="table-wrap"><table>
        <tr><th>Loan #</th><th>Customer</th><th>EMIs</th><th>First Due</th>
            <th>Total Amount</th><th>Due In</th><th>Follow Up</th><th>Action</th></tr>
        {up_rows or '<tr><td colspan="8" style="color:var(--green);text-align:center;background:#d1fae5;">✅ No upcoming EMIs in 10 days.</td></tr>'}
      </table></div>
    </div>

    <!-- Follow Up modal -->
    <div class="fu-modal-overlay" id="fuModal">
      <div class="fu-modal">
        <h3>📅 Follow Up</h3>
        <p id="fuModalLoanInfo" style="font-size:12px;color:var(--muted);margin-bottom:12px;"></p>
        <form method="POST" action="/followup/add">
          <input type="hidden" name="loan_id" id="fu_loan_id">
          <input type="hidden" name="next" value="/alerts">
          <div class="form-group">
            <label>Date Customer Asked To Collect *</label>
            <input type="date" name="follow_up_date" id="fu_date" required min="{today.isoformat()}">
          </div>
          <div class="form-group" style="margin-top:12px;">
            <label>Remarks *</label>
            <textarea name="remarks" rows="3" required placeholder="e.g. Customer asked to collect next Monday, salary delayed"></textarea>
          </div>
          <div style="display:flex;gap:8px;margin-top:16px;justify-content:flex-end;">
            <button type="button" class="btn" style="background:var(--surface2);color:var(--text);" onclick="fuCloseModal()">Cancel</button>
            <button class="btn btn-primary">💾 Save Follow Up</button>
          </div>
        </form>
      </div>
    </div>
    <script>
    function fuCloseModal(){{ document.getElementById('fuModal').classList.remove('open'); }}
    document.querySelectorAll('.fu-btn').forEach(function(btn){{
      btn.addEventListener('click', function(){{
        document.getElementById('fu_loan_id').value = btn.dataset.loanId;
        document.getElementById('fuModalLoanInfo').textContent = btn.dataset.loanNumber + ' — ' + btn.dataset.customer;
        document.getElementById('fuModal').classList.add('open');
      }});
    }});
    document.getElementById('fuModal').addEventListener('click', function(e){{
      if(e.target === this) fuCloseModal();
    }});
    </script>"""
    return page("Alerts", content, "alerts")

@app.route("/followup/add", methods=["POST"])
@login_required
def followup_add():
    loan_id = int(request.form["loan_id"])
    fdate   = request.form.get("follow_up_date","").strip()
    remarks = request.form.get("remarks","").strip()
    nxt     = request.form.get("next") or "/followup"
    if not fdate or not remarks:
        flash("Follow-up date and remarks are required.","danger")
        return redirect(nxt)
    add_follow_up(loan_id, fdate, remarks, session.get("username",""))
    flash("Follow-up saved.","success")
    return redirect(nxt)

@app.route("/followup/resolve/<int:followup_id>", methods=["POST"])
@login_required
def followup_resolve(followup_id):
    try:
        c = get_cur(); c.execute("SELECT item FROM FollowUp WHERE followup_id=?", (followup_id,))
        fr = c.fetchone()
        recv_date = recv_by = None
        recv_extra = (request.form.get("recv_extra") or "").strip() or None
        if fr and fr["item"] in FU_ITEM_COLUMNS:       # key / RC / proof: ask when and by whom it was collected
            recv_date, recv_by = parse_collected(request.form.get("recv_date"), request.form.get("recv_by"),
                                                 HANDOVER_LABELS[fr["item"]], date.today().isoformat())
        if session.get("role","") in DIRECT_ROLES:
            resolve_follow_up(followup_id, recv_date, recv_by, recv_extra)
            flash("Follow-up marked as resolved.","success")
        else:
            request_followup_ack(followup_id, session.get("username",""), request.form.get("note",""), recv_date, recv_by, recv_extra)
            flash("Sent for acknowledgement. The follow-up closes once an Account Manager or admin acknowledges it.","success")
    except Exception as e:
        flash(str(e),"danger")
    return redirect(request.form.get("next") or url_for("followups"))

# ── Acknowledgements (second-level cross-check) ────────────────────────────────
def ack_counts():
    c = get_cur()
    c.execute("SELECT COUNT(*) as n FROM PendingPayments WHERE status='Pending'"); p = c.fetchone()["n"]
    c.execute("SELECT COUNT(*) as n FROM FollowUp WHERE status='AwaitingAck'"); f = c.fetchone()["n"]
    c.execute("SELECT COUNT(*) as n FROM LoanClosure WHERE status='AckPending'"); k = c.fetchone()["n"]
    c.execute("SELECT COUNT(*) as n FROM Seizures WHERE status='AckPending'"); z = c.fetchone()["n"]
    return p, f, k, z

@app.route("/acknowledgements")
@login_required
@role_required(*ACK_ROLES)
def acknowledgements():
    c = get_cur(); me = session.get("username","")
    c.execute("""SELECT p.*, le.loan_number, le.customer_name, e.due_date, e.emi_amount, e.remaining_amount
                 FROM PendingPayments p JOIN LoanEntry le ON le.id=p.loan_id JOIN EMI e ON e.emi_id=p.emi_id
                 WHERE p.status='Pending' ORDER BY p.pp_id ASC""")
    pays = [dict(r) for r in c.fetchall()]
    pay_cards = ""
    for p in pays:
        late = (parse_date(p["paid_on"]) - parse_date(p["due_date"])).days
        own = p["requested_by"] == me
        pay_cards += f"""<div class="card" style="margin-bottom:10px;">
          <div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;">
            <div>
              <b style="color:var(--accent);font-size:15px;">{html.escape(p['loan_number'])}</b> — {html.escape(p['customer_name'] or '')}
              <div style="font-size:12.5px;margin-top:4px;line-height:1.7;">
                Installment <b>{ordinal_due(p['installment_no'])}</b> · due {fmt_date(p['due_date'])} · EMI {fmt_inr(p['emi_amount'])}<br>
                Payment <b style="font-size:15px;">{fmt_inr(p['amount'])}</b> · bill <b>{html.escape(p['bill_number'])}</b> · paid on <b>{fmt_date(p['paid_on'])}</b>
                {('· <span style="color:var(--red);font-weight:700;">' + str(late) + ' day(s) after due date</span>') if late > 0 else '· <span style="color:var(--green);font-weight:700;">on time</span>'}<br>
                <span style="color:var(--muted);">Entered by <b>{html.escape(p['requested_by'] or '')}</b> on {fmt_date((p.get('requested_at') or '')[:10])}
                {(' · penalty ₹' + format(p['penalty_rate'], ',.2f') + '/day requested') if p.get('penalty_rate') else ''}</span>
              </div>
            </div>
            <div style="display:flex;gap:8px;align-items:flex-start;flex-wrap:wrap;">
              {'<span style="font-size:12px;color:var(--muted);">You entered this, so someone else must acknowledge it.</span>' if own else f'''
              <form method="POST" action="/ack/payment/{p['pp_id']}" onsubmit="return confirm('Acknowledge this payment? It will be recorded on the EMI.')">
                <input type="hidden" name="action" value="ack"><button class="btn btn-success btn-sm">✅ Acknowledge</button></form>
              <form method="POST" action="/ack/payment/{p['pp_id']}" onsubmit="return getReason(this)">
                <input type="hidden" name="action" value="reject"><input type="hidden" name="reason" class="reason_inp">
                <button class="btn btn-danger btn-sm">❌ Reject</button></form>'''}
            </div>
          </div></div>"""
    c.execute("""SELECT f.*, COALESCE(f.category,'Loans') as cat, le.loan_number, le.customer_name
                 FROM FollowUp f JOIN LoanEntry le ON le.id=f.loan_id
                 WHERE f.status='AwaitingAck' ORDER BY f.follow_up_date ASC""")
    fus = [dict(r) for r in c.fetchall()]
    fu_cards = ""
    for f in fus:
        own = f["ack_requested_by"] == me
        fu_cards += f"""<div class="card" style="margin-bottom:10px;">
          <div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;">
            <div>
              <b style="color:var(--accent);font-size:15px;">{html.escape(f['loan_number'])}</b> — {html.escape(f['customer_name'] or '')}
              <span class="badge badge-partial" style="margin-left:6px;">{html.escape(f['cat'])}</span>
              <div style="font-size:12.5px;margin-top:4px;line-height:1.7;">
                {html.escape(f.get('remarks') or '')}<br>
                {('<b>Collected on ' + fmt_date(f.get('recv_date')) + ' by ' + html.escape(f.get('recv_by') or '') + (' · ' + html.escape(f['recv_extra']) if f.get('recv_extra') else '') + '</b><br>') if f.get('recv_by') else ''}
                <span style="color:var(--muted);">Follow-up date {fmt_date(f['follow_up_date'])} · marked done by <b>{html.escape(f.get('ack_requested_by') or '')}</b>
                on {fmt_date((f.get('ack_requested_at') or '')[:10])}{(' · note: ' + html.escape(f['ack_note'])) if f.get('ack_note') else ''}</span>
              </div>
            </div>
            <div style="display:flex;gap:8px;align-items:flex-start;flex-wrap:wrap;">
              {'<span style="font-size:12px;color:var(--muted);">You marked this, so someone else must acknowledge it.</span>' if own else f'''
              <form method="POST" action="/ack/followup/{f['followup_id']}" onsubmit="return confirm('Acknowledge and close this follow-up?')">
                <input type="hidden" name="action" value="ack"><button class="btn btn-success btn-sm">✅ Acknowledge</button></form>
              <form method="POST" action="/ack/followup/{f['followup_id']}" onsubmit="return getReason(this)">
                <input type="hidden" name="action" value="reject"><input type="hidden" name="reason" class="reason_inp">
                <button class="btn btn-danger btn-sm">❌ Not done</button></form>'''}
            </div>
          </div></div>"""
    c.execute("""SELECT cl.*, le.loan_number, le.customer_name FROM LoanClosure cl
                 JOIN LoanEntry le ON le.id=cl.loan_id WHERE cl.status='AckPending' ORDER BY cl.closure_id ASC""")
    closures = [dict(r) for r in c.fetchall()]
    cl_cards = ""
    for k in closures:
        its = closure_items(k["closure_id"])
        recorders = {i["recorded_by"] for i in its} | {k["ack_requested_by"]}
        own = me in recorders
        lines = "".join(
            f'<div style="padding:3px 0;"><b>{closure_item_title(i)}</b> — returned on '
            f'{fmt_date(i["returned_on"])} · handed over by <b>{html.escape(i.get("handed_by") or "")}</b>'
            f'{(" · " + html.escape(i["note"])) if i.get("note") else ""}</div>' for i in its)
        cl_cards += f"""<div class="card" style="margin-bottom:10px;">
          <div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;">
            <div>
              <b style="color:var(--accent);font-size:15px;">{html.escape(k['loan_number'])}</b> — {html.escape(k['customer_name'] or '')}
              <span class="badge badge-partial" style="margin-left:6px;">{'Pre-closure' if k['kind'] == 'PreClosure' else 'Regular closing'}</span>
              <div style="font-size:12.5px;margin-top:6px;line-height:1.7;">{lines}
                <span style="color:var(--muted);">Sent by <b>{html.escape(k.get('ack_requested_by') or '')}</b> on {fmt_date((k.get('ack_requested_at') or '')[:10])}</span></div>
            </div>
            <div style="display:flex;gap:8px;align-items:flex-start;flex-wrap:wrap;">
              {'<span style="font-size:12px;color:var(--muted);">You recorded this hand-over, so someone else must acknowledge it.</span>' if own else f'''
              <form method="POST" action="/ack/closure/{k['closure_id']}" onsubmit="return confirm('Acknowledge the hand-over and CLOSE this loan?')">
                <input type="hidden" name="action" value="ack"><button class="btn btn-success btn-sm">✅ Acknowledge &amp; close loan</button></form>
              <form method="POST" action="/ack/closure/{k['closure_id']}" onsubmit="return getReason(this)">
                <input type="hidden" name="action" value="reject"><input type="hidden" name="reason" class="reason_inp">
                <button class="btn btn-danger btn-sm">❌ Not correct</button></form>'''}
            </div>
          </div></div>"""
    c.execute("""SELECT sz.*, le.loan_number, le.customer_name, le.customer_mobile FROM Seizures sz
                 JOIN LoanEntry le ON le.id=sz.loan_id WHERE sz.status='AckPending' ORDER BY sz.seizure_id ASC""")
    seizures = [dict(r) for r in c.fetchall()]
    sz_cards = ""
    for z in seizures:
        its = seizure_items(z["seizure_id"])
        own = me in ({i["recorded_by"] for i in its} | {z["ack_requested_by"]})
        lines = "".join(
            f'<div style="padding:3px 0;"><b>{seizure_item_title(i)}</b> — received on {fmt_date(i["received_on"])} · by '
            f'<b>{html.escape(i.get("received_by") or "")}</b>{(" · " + html.escape(i["note"])) if i.get("note") else ""}</div>' for i in its)
        if own:
            actions = '<span style="font-size:12px;color:var(--muted);">You recorded this, so someone else must acknowledge it.</span>'
        else:
            actions = f"""
              <form method="POST" action="/ack/seizure/{z['seizure_id']}" onsubmit="return szAck(this)" style="min-width:280px;">
                <input type="hidden" name="action" value="ack">
                <label style="display:flex;gap:8px;align-items:center;font-size:13px;margin-bottom:4px;cursor:pointer;">
                  <input type="checkbox" name="details_checked" value="1" style="width:auto;min-height:0;margin:0;"> Seizure details checked</label>
                <label style="display:flex;gap:8px;align-items:center;font-size:13px;margin-bottom:6px;cursor:pointer;">
                  <input type="checkbox" name="legal_checked" value="1" style="width:auto;min-height:0;margin:0;"> Legal issues checked</label>
                <textarea name="legal_note" rows="2" placeholder="Legal issues / note (optional)" style="font-size:12px;width:100%;margin-bottom:6px;"></textarea>
                <button class="btn btn-success btn-sm">✅ Acknowledge</button></form>
              <form method="POST" action="/ack/seizure/{z['seizure_id']}" onsubmit="return getReason(this)">
                <input type="hidden" name="action" value="reject"><input type="hidden" name="reason" class="reason_inp">
                <button class="btn btn-danger btn-sm">❌ Not correct</button></form>"""
        sz_cards += f"""<div class="card" style="margin-bottom:10px;border-left:5px solid var(--red);">
          <div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;">
            <div style="flex:1;min-width:260px;">
              <b style="color:var(--accent);font-size:15px;">{html.escape(z['loan_number'])}</b> — {html.escape(z['customer_name'] or '')}
              <span class="badge badge-rejected" style="margin-left:6px;">Vehicle seized</span>
              <div style="font-size:12.5px;margin-top:6px;line-height:1.7;">
                <b>Seized on:</b> {fmt_date(z.get('seized_date'))} · <b>Kept at:</b> {html.escape(z.get('place') or '—')}<br>
                <b>Reason:</b> {html.escape(z.get('reason') or '—')}<br>
                <b>Written off:</b> <b style="color:var(--red);">{fmt_inr(z.get('written_off') or 0)}</b> — {html.escape(z.get('writeoff_reason') or '')}<br>
                <span style="color:var(--muted);">Approved by {html.escape(z.get('approved_by') or '')} · requested by {html.escape(z.get('requested_by') or '')}</span>
                <div style="margin-top:6px;">{lines}</div>
                <span style="color:var(--muted);">Sent by <b>{html.escape(z.get('ack_requested_by') or '')}</b> on {fmt_date((z.get('ack_requested_at') or '')[:10])}</span>
              </div>
            </div>
            <div style="display:flex;gap:8px;align-items:flex-start;flex-wrap:wrap;flex-direction:column;">{actions}</div>
          </div></div>"""
    content = f"""
    <h1>🔎 Acknowledgements</h1>
    <p style="font-size:12.5px;color:var(--muted);margin-bottom:12px;">Second-level cross-check: confirm that the payment or follow-up
       shown is correct. Financial decisions (loans, pre-closures, penalties) stay with the admin.</p>
    <h3 style="margin:6px 0 10px;">💳 Payments awaiting acknowledgement ({len(pays)})</h3>
    {pay_cards or '<div class="card"><p style="text-align:center;color:var(--muted);">No payments waiting.</p></div>'}
    <h3 style="margin:18px 0 10px;">📞 Follow-ups awaiting acknowledgement ({len(fus)})</h3>
    {fu_cards or '<div class="card"><p style="text-align:center;color:var(--muted);">No follow-ups waiting.</p></div>'}
    <h3 style="margin:18px 0 10px;">🔒 Loan closing — key &amp; documents returned ({len(closures)})</h3>
    {cl_cards or '<div class="card"><p style="text-align:center;color:var(--muted);">No loan closings waiting.</p></div>'}
    <h3 style="margin:18px 0 10px;">🚫 Vehicle seizures — check details &amp; legal issues ({len(seizures)})</h3>
    {sz_cards or '<div class="card"><p style="text-align:center;color:var(--muted);">No vehicle seizures waiting.</p></div>'}
    <script>
    function getReason(form){{
      const r=prompt('Reason:'); if(!r) return false;
      form.querySelector('.reason_inp').value=r; return true;
    }}
    function szAck(form){{
      if(!form.details_checked.checked){{ alert('Tick "Seizure details checked" first.'); return false; }}
      if(!form.legal_checked.checked){{ alert('Tick "Legal issues checked" first.'); return false; }}
      return confirm('Acknowledge the seizure? The loan will move to Closed Loans as Seized.');
    }}
    </script>"""
    return page("Acknowledgements", content, "ack")

@app.route("/ack/payment/<int:pp_id>", methods=["POST"])
@login_required
@role_required(*ACK_ROLES)
def ack_payment(pp_id):
    try:
        if request.form.get("action") == "ack":
            flash(acknowledge_payment(pp_id, session.get("username","")), "success")
        else:
            reject_payment(pp_id, request.form.get("reason",""), session.get("username",""))
            flash("Payment rejected; it was not recorded on the EMI.", "success")
    except Exception as e:
        flash(str(e), "danger")
    return redirect(url_for("acknowledgements"))

@app.route("/ack/followup/<int:followup_id>", methods=["POST"])
@login_required
@role_required(*ACK_ROLES)
def ack_followup(followup_id):
    try:
        if request.form.get("action") == "ack":
            acknowledge_followup(followup_id, session.get("username",""))
            flash("Follow-up acknowledged and closed.", "success")
        else:
            reject_followup_ack(followup_id, request.form.get("reason",""), session.get("username",""))
            flash("Follow-up sent back as not done.", "success")
    except Exception as e:
        flash(str(e), "danger")
    return redirect(url_for("acknowledgements"))

# ── Penalty approval (admin) ───────────────────────────────────────────────────
@app.route("/penalty/approve/<int:penalty_id>", methods=["POST"])
@login_required
@role_required("superadmin","admin")
def penalty_approve(penalty_id):
    try:
        final = approve_penalty(penalty_id, request.form.get("rate",""), session.get("username",""))
        c = get_cur(); c.execute("SELECT merged_emi_id FROM Penalties WHERE penalty_id=?", (penalty_id,))
        merged = (c.fetchone() or {"merged_emi_id": None})["merged_emi_id"]
        if final <= 0:
            flash("Penalty waived (per-day amount was 0).", "success")
        elif merged:
            c.execute("SELECT installment_no FROM EMI WHERE emi_id=?", (merged,))
            flash(f"Penalty approved: {fmt_inr(final)}. It was added to installment {c.fetchone()['installment_no']}'s amount and is collected with that EMI.", "success")
        else:
            flash("Penalty approved: " + fmt_inr(final) + ". No EMI is left, so a penalty-collection follow-up was added.", "success")
    except Exception as e:
        flash(str(e), "danger")
    return redirect(url_for("approval"))

@app.route("/closure/approve/<int:closure_id>", methods=["POST"])
@login_required
@role_required("superadmin","admin")
def closure_approve(closure_id):
    rates = {}
    for k, v in request.form.items():
        if k.startswith("rate_") and k[5:].isdigit(): rates[int(k[5:])] = v
    try:
        approve_closure(closure_id, rates, session.get("username",""))
        flash("Closing approved. Penalties (if any) become collection tasks due within a day; "
              "the key and document return starts once they are collected.", "success")
    except Exception as e:
        flash(str(e), "danger")
    return redirect(url_for("approval"))

@app.route("/closure/item/<int:item_id>", methods=["POST"])
@login_required
@role_required("superadmin","admin","manager","fieldpia")
def closure_item_save(item_id):
    f = request.form
    try:
        record_closure_item(item_id, f.get("returned_on",""), f.get("handed_by",""), f.get("note",""), session.get("username",""))
        flash("Recorded.", "success")
    except Exception as e:
        flash(str(e), "danger")
    return redirect(url_for("emis", loan_id=int(f.get("loan_id", 0) or 0)) + "#closing")

@app.route("/closure/send_ack/<int:closure_id>", methods=["POST"])
@login_required
@role_required("superadmin","admin","manager","fieldpia")
def closure_send_ack(closure_id):
    loan_id = int(request.form.get("loan_id", 0) or 0)
    try:
        request_closure_ack(closure_id, session.get("username",""))
        flash("Sent for acknowledgement. The loan closes once it is acknowledged.", "success")
    except Exception as e:
        flash(str(e), "danger")
    return redirect(url_for("emis", loan_id=loan_id) + "#closing")

@app.route("/ack/closure/<int:closure_id>", methods=["POST"])
@login_required
@role_required(*ACK_ROLES)
def ack_closure(closure_id):
    try:
        if request.form.get("action") == "ack":
            acknowledge_closure(closure_id, session.get("username",""))
            flash("Acknowledged. The loan is now closed.", "success")
        else:
            reject_closure_ack(closure_id, request.form.get("reason",""), session.get("username",""))
            flash("Sent back to the return step.", "success")
    except Exception as e:
        flash(str(e), "danger")
    return redirect(url_for("acknowledgements"))

# ── Vehicle seizure routes ─────────────────────────────────────────────────────
def _back_to_emis(loan_id, anchor="seizure"):
    return redirect(url_for("emis", loan_id=int(loan_id or 0)) + f"#{anchor}")

@app.route("/seizure/request/<int:loan_id>", methods=["POST"])
@login_required
@role_required("superadmin","admin","manager","fieldpia")
def seizure_request(loan_id):
    f = request.form
    try:
        request_seizure(loan_id, f.get("reason",""), f.get("seized_date",""), f.get("place",""),
                        f.get("writeoff_reason",""), session.get("username",""))
        flash("Seizure request sent to the admin for approval.", "success")
    except Exception as e:
        flash(str(e), "danger")
    return _back_to_emis(loan_id)

@app.route("/seizure/approve/<int:seizure_id>", methods=["POST"])
@login_required
@role_required("superadmin","admin")
def seizure_approve(seizure_id):
    try:
        wo = approve_seizure(seizure_id, request.form.get("writeoff_reason",""), session.get("username",""))
        flash(f"Seizure approved. {fmt_inr(wo)} was written off and the pending EMIs are closed. "
              "The key and RC now have to be recorded.", "success")
    except Exception as e:
        flash(str(e), "danger")
    return redirect(url_for("approval"))

@app.route("/seizure/reject/<int:seizure_id>", methods=["POST"])
@login_required
@role_required("superadmin","admin")
def seizure_reject(seizure_id):
    try:
        reject_seizure(seizure_id, request.form.get("reason",""), session.get("username",""))
        flash("Seizure request rejected.", "success")
    except Exception as e:
        flash(str(e), "danger")
    return redirect(url_for("approval"))

@app.route("/seizure/item/<int:item_id>", methods=["POST"])
@login_required
@role_required("superadmin","admin","manager","fieldpia")
def seizure_item_save(item_id):
    f = request.form
    try:
        record_seizure_item(item_id, f.get("received_on",""), f.get("received_by",""), f.get("note",""), session.get("username",""))
        flash("Recorded.", "success")
    except Exception as e:
        flash(str(e), "danger")
    return _back_to_emis(f.get("loan_id", 0))

@app.route("/seizure/send_ack/<int:seizure_id>", methods=["POST"])
@login_required
@role_required("superadmin","admin","manager","fieldpia")
def seizure_send_ack(seizure_id):
    try:
        request_seizure_ack(seizure_id, session.get("username",""))
        flash("Sent to the Account Manager for acknowledgement.", "success")
    except Exception as e:
        flash(str(e), "danger")
    return _back_to_emis(request.form.get("loan_id", 0))

@app.route("/ack/seizure/<int:seizure_id>", methods=["POST"])
@login_required
@role_required(*ACK_ROLES)
def ack_seizure(seizure_id):
    f = request.form
    try:
        if f.get("action") == "ack":
            acknowledge_seizure(seizure_id, session.get("username",""), f.get("details_checked") == "1",
                                f.get("legal_checked") == "1", f.get("legal_note",""))
            flash("Acknowledged. The seizure is complete and the loan is in Closed Loans.", "success")
        else:
            reject_seizure_ack(seizure_id, f.get("reason",""), session.get("username",""))
            flash("Sent back to the key / RC step.", "success")
    except Exception as e:
        flash(str(e), "danger")
    return redirect(url_for("acknowledgements"))

@app.route("/seizure/reopen/<int:seizure_id>", methods=["POST"])
@login_required
@role_required("superadmin","admin")
def seizure_reopen(seizure_id):
    loan_id = int(request.form.get("loan_id", 0) or 0)
    try:
        reopen_seizure(seizure_id, request.form.get("reason",""), session.get("username",""))
        flash("Loan reopened. The EMIs are back to their earlier status and the write-off is reversed.", "success")
    except Exception as e:
        flash(str(e), "danger")
    return _back_to_emis(loan_id, "")

@app.route("/settings/followup_days", methods=["POST"])
@login_required
@role_required("superadmin")
def settings_followup_days():
    try:
        vals = {k: int(request.form.get("days_" + k) or handover_days(k)) for k in HANDOVER_LABELS}
        if any(v < 1 or v > 365 for v in vals.values()): raise ValueError
    except ValueError:
        flash("Enter whole numbers between 1 and 365.", "danger")
        return redirect(url_for("users"))
    for k, v in vals.items(): set_setting(f"followup_days_{k}", v)
    flash("Saved: follow-up days — " + ", ".join(f"{HANDOVER_LABELS[k]} {v}" for k, v in vals.items()) + ".", "success")
    return redirect(url_for("users"))

@app.route("/settings/police_fine", methods=["POST"])
@login_required
@role_required("superadmin")
def settings_police_fine():
    try:
        v = float(request.form.get("max_fine", ""))
        if v < 0: raise ValueError
    except ValueError:
        flash("Enter a valid amount (0 or more).", "danger")
        return redirect(url_for("users"))
    set_setting("police_fine_max", int(v) if v == int(v) else v)
    flash(f"Saved: the police fine limit is now {fmt_inr(v)}.", "success")
    return redirect(url_for("users"))

@app.route("/settings/seizure", methods=["POST"])
@login_required
@role_required("superadmin")
def settings_seizure():
    try:
        n = int(request.form.get("min_overdue", ""))
        if n < 1 or n > 60: raise ValueError
        set_setting("seizure_min_overdue", n)
        flash(f"Saved: the Vehicle Seized option now appears from {n} overdue EMI(s).", "success")
    except ValueError:
        flash("Enter a whole number between 1 and 60.", "danger")
    return redirect(url_for("users"))

@app.route("/penalty/reject/<int:penalty_id>", methods=["POST"])
@login_required
@role_required("superadmin","admin")
def penalty_reject(penalty_id):
    try:
        reject_penalty(penalty_id, request.form.get("reason",""), session.get("username",""))
        flash("Penalty rejected.", "success")
    except Exception as e:
        flash(str(e), "danger")
    return redirect(url_for("approval"))

@app.route("/followup/reschedule/<int:followup_id>", methods=["POST"])
@login_required
def followup_reschedule(followup_id):
    nxt   = request.form.get("next") or url_for("followups")
    fdate = request.form.get("follow_up_date","").strip()
    if not fdate:
        flash("Please pick the new follow-up date.","danger")
        return redirect(nxt)
    try:
        reschedule_follow_up(followup_id, fdate, request.form.get("remarks","").strip(),
                             session.get("username",""))
        flash("New follow-up added.","success")
    except Exception as e:
        flash(str(e),"danger")
    return redirect(nxt)

# ── Follow Up (all customers, all loans) ────────────────────────────────────────
@app.route("/followup")
@login_required
def followups():
    q = request.args.get("q","")
    cat = request.args.get("cat","")
    if cat not in FU_CATEGORIES: cat = ""
    items = list_follow_ups(q, cat)
    today = date.today()
    q_url = urlquote(q); cat_url = urlquote(cat)
    back_url = f"/followup?q={q_url}&cat={cat_url}"
    rows = doc_rows = ""
    for r in items:
        is_doc = r["category"] in DOC_FU_CATEGORIES
        fu_date = parse_date(r["follow_up_date"])
        days_left = (fu_date - today).days
        row_cls = ""
        if r["status"] == "Resolved":
            status_badge = '<span class="badge badge-closed">✅ Resolved</span>'
        elif r["status"] == "Rescheduled":
            status_badge = '<span class="badge badge-closed">🔁 Rescheduled</span>'
        elif r["status"] == "AwaitingAck":
            status_badge = (f'<span class="badge badge-partial">⏳ Awaiting acknowledgement</span><br>'
                            f'<span style="font-size:11px;color:var(--muted);">marked done by {html.escape(r.get("ack_requested_by") or "")}</span>')
        elif fu_date < today:
            status_badge = '<span class="badge badge-overdue">⏰ Missed</span>'
            row_cls = "row-overdue"
        elif days_left <= 2:
            status_badge = '<span class="badge badge-pending">📅 Pending</span>'
            row_cls = "row-upcoming"
        else:
            status_badge = '<span class="badge badge-pending">📅 Pending</span>'
        is_open = r["status"] == "Pending"
        direct = session.get("role", "") in DIRECT_ROLES
        if direct:
            resolve_label = {"penalty": "✔ Collected"}.get(r.get("item"), "✔ Received" if r.get("item") in FU_ITEM_COLUMNS else "✔ Resolve")
            resolve_tip = "Closes this follow-up"
        else:
            resolve_label = "✔ Mark done"
            resolve_tip = "Goes to an Account Manager / admin for acknowledgement before it closes"
        recv_fields = ""
        if r.get("item") in FU_ITEM_COLUMNS:
            recv_fields = (f'<input type="date" name="recv_date" value="{today.isoformat()}" max="{today.isoformat()}" required title="When collected" '
                           f'style="width:128px;font-size:12px;padding:4px 5px;">'
                           f'<input name="recv_by" placeholder="Collected by *" required style="width:120px;font-size:12px;padding:4px 5px;">'
                           + ('<input name="recv_extra" placeholder="Cheque no(s)" style="width:110px;font-size:12px;padding:4px 5px;">' if r.get("item") == "cheque" else ""))
        resolve_btn = "" if not is_open else f"""
          <form method="POST" action="/followup/resolve/{r['followup_id']}" style="display:inline-flex;gap:4px;flex-wrap:wrap;align-items:center;">
            <input type="hidden" name="next" value="{back_url}">
            {recv_fields}
            <button class="btn btn-sm btn-success" title="{resolve_tip}">{resolve_label}</button>
          </form>"""
        reschedule_form = "" if not (is_open and r["category"] != "Loans") else f"""
          <form method="POST" action="/followup/reschedule/{r['followup_id']}" style="display:flex;gap:4px;align-items:center;">
            <input type="hidden" name="next" value="{back_url}">
            <input type="date" name="follow_up_date" min="{today.isoformat()}" required
                   style="width:130px;font-size:12px;padding:5px 6px;" title="New follow-up date">
            <button class="btn btn-sm btn-amber">🔁 Reschedule</button>
          </form>"""
        pay_btn = (f'<a class="btn btn-sm btn-danger" href="/billing/new/{r["loan_id"]}">🧾 Billing</a>'
                   if r["category"] == "Loans" and session.get("role","") in BILLING_ROLES else "")
        money_cells = "" if is_doc else f"""
          <td>{('₹{:,.2f}'.format(r['emi_amount'])) if r.get('emi_amount') is not None else '—'}</td>
          <td>{fmt_date(r.get('oldest_due'))}</td>
          <td style="text-align:center;">{r['pending_dues']}</td>
          <td><b style="color:var(--red);">₹{r['overdue_amount']:,.2f}</b></td>
          <td><b>₹{r['outstanding']:,.2f}</b></td>
          <td>{fmt_date(r.get('last_paid_date'))}</td>"""
        row_html = f"""<tr class="{row_cls}">
          <td><b><a href="/emis/{r['loan_id']}" style="color:var(--accent);">{r['loan_number']}</a></b></td>
          <td><span class="badge badge-partial">{html.escape(r['category'])}</span></td>
          <td>{r['customer_name']}</td>
          <td>{customer_numbers_html(r)}{('<br><span style="font-size:11px;color:var(--muted);">🛡️ Guarantor</span><br>' + guarantor_numbers_html(r)) if guarantor_numbers_html(r) != '—' else ''}</td>
          <td>{vehicle_html(r)}</td>{money_cells}
          <td style="white-space:normal;max-width:180px;">{r.get('customer_address') or '—'}</td>
          <td style="white-space:normal;max-width:180px;">{r.get('customer_permanent_address') or '—'}</td>
          <td>{_location_link(r.get('customer_location'))}</td>
          <td>{_location_link(r.get('guarantor_location'))}</td>
          <td>{fmt_date(r['follow_up_date'])}</td>
          <td style="white-space:normal;max-width:220px;">{r['remarks']}{('<br><span style="font-size:11.5px;color:var(--red);">' + html.escape(r['ack_note']) + '</span>') if r.get('ack_note') and r['status'] == 'Pending' else ''}</td>
          <td>{status_badge}</td>
          <td>{r.get('created_by') or ''}</td>
          <td style="white-space:nowrap;display:flex;gap:6px;flex-wrap:wrap;">{pay_btn}{resolve_btn}{reschedule_form}</td>
        </tr>"""
        if is_doc: doc_rows += row_html
        else: rows += row_html
    legend = """
    <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:10px;font-size:12px;">
      <span style="display:flex;align-items:center;gap:4px;">
        <span style="width:14px;height:14px;background:#fee2e2;border-left:3px solid #dc2626;display:inline-block;"></span> Missed (date passed)
      </span>
      <span style="display:flex;align-items:center;gap:4px;">
        <span style="width:14px;height:14px;background:#fef9c3;border-left:3px solid #d97706;display:inline-block;"></span> Due within 2 days
      </span>
    </div>"""
    head = ('<tr><th>Loan #</th><th>Category</th><th>Customer</th><th>Mobile</th><th>Vehicle</th>{money}'
            '<th>Current Address</th><th>Permanent Address</th><th>Location</th><th>Guarantor Location</th>'
            '<th>Follow-up Date</th><th>Remarks</th><th>Status</th><th>By</th><th>Action</th></tr>')
    money_head = ('<th>EMI Amt</th><th>Oldest Due Date</th><th>Pending Dues</th><th>Overdue Amt</th>'
                  '<th>Outstanding</th><th>Last Paid</th>')
    def fu_table(title, head_html, body, cols):
        return ((f'<h3 style="margin:14px 0 8px;">{title}</h3>' if title else '')
                + f'<div class="table-wrap"><table>{head_html}'
                + (body or f'<tr><td colspan="{cols}" style="text-align:center;color:var(--muted);">No follow-ups recorded yet.</td></tr>')
                + '</table></div>')
    show_money = cat not in DOC_FU_CATEGORIES
    show_docs = cat in ("",) + DOC_FU_CATEGORIES
    money_table = fu_table("💰 Loans &amp; Penalty Collection" if show_docs else "", head.format(money=money_head), rows, 20) if show_money else ""
    doc_table = fu_table("🔑 Key Collection &amp; 📄 Proof &amp; Documents" if show_money else "", head.format(money=""), doc_rows, 14) if show_docs else ""
    tabs = "".join(
        f'<a href="/followup?q={q_url}&cat={urlquote(name)}" class="btn btn-sm" '
        f'style="{"background:var(--accent);color:#fff;" if cat==name else "background:var(--surface2);color:var(--text);"}">{label}</a>'
        for name, label in (("", "All"), ("Loans", "💰 Loans"), ("Key Collection", "🔑 Key Collection"),
                            ("Proof & Documents", "📄 Proof & Documents"), ("Penalty Collection", "⚖️ Penalty Collection")))
    content = f"""
    <h1>📞 Follow Up</h1>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px;">{tabs}</div>
    <form method="GET" style="margin-bottom:12px;display:flex;gap:8px;flex-wrap:wrap;">
      <input type="hidden" name="cat" value="{html.escape(cat)}">
      <input name="q" value="{q}" placeholder="Search loan / customer / mobile…" style="max-width:280px;">
      <button class="btn btn-primary btn-sm">Search</button>
      <a href="/followup/export/csv?q={q_url}&cat={cat_url}" class="btn btn-success btn-sm">📊 Export Excel (CSV)</a>
      <a href="/followup/export/pdf?q={q_url}&cat={cat_url}" class="btn btn-danger btn-sm">📄 Export PDF</a>
    </form>
    <div class="card">
      <p style="font-size:12px;color:var(--muted);margin-bottom:8px;">
        Loan collection follow-ups (saved from the Alerts page) plus the automatic Key Collection and
        Proof &amp; Documents follow-ups created when a loan is submitted with Key / RC / Documents = No, and
        Penalty Collection tasks created when an admin approves a late-payment penalty. Marking a follow-up done
        sends it to an Account Manager / admin for acknowledgement before it closes.
        Rows are ordered by follow-up date. <b>Overdue Amt</b> = already past due; <b>Outstanding</b> = total balance
        of the loan still to be collected; <b>Oldest Due Date</b> = earliest unpaid EMI.
      </p>
      {legend}
      {money_table}{doc_table}
    </div>"""
    return page("Follow Up", content, "followup")

@app.route("/followup/export/csv")
@login_required
def followup_export_csv():
    q = request.args.get("q","")
    items = list_follow_ups(q, request.args.get("cat",""))
    today = date.today()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Loan Number","Category","Customer","Mobile","Customer Other Numbers","Guarantor Numbers",
                      "Vehicle","EMI Amount","Oldest Due Date","Pending Dues","Overdue Amount","Outstanding Amount","Last Paid Date",
                      "Current Address","Permanent Address","Location","Guarantor Location",
                      "Follow-up Date","Remarks","Status","Created By"])
    for r in items:
        fu_date = parse_date(r["follow_up_date"])
        if r["status"] in ("Resolved","Rescheduled"): status = r["status"]
        elif r["status"] == "AwaitingAck": status = "Awaiting acknowledgement"
        elif fu_date < today: status = "Missed"
        else: status = "Pending"
        writer.writerow([
            r["loan_number"], r["category"], r.get("customer_name") or "", r.get("customer_mobile") or "",
            contact_numbers_text(r.get("customer_extra_numbers")),
            "; ".join(x for x in [
                f"{r.get('guarantor_mobile')} (Primary - {r.get('guarantor_name') or 'Guarantor'})" if r.get("guarantor_mobile") else "",
                contact_numbers_text(r.get("guarantor_extra_numbers"))] if x),
            vehicle_label(r),
            f"{r['emi_amount']:.2f}" if r.get("emi_amount") is not None else "",
            fmt_date(r.get("oldest_due"), ""), r["pending_dues"],
            f"{r['overdue_amount']:.2f}", f"{r['outstanding']:.2f}", fmt_date(r.get("last_paid_date"), ""),
            r.get("customer_address") or "", r.get("customer_permanent_address") or "", r.get("customer_location") or "", r.get("guarantor_location") or "",
            fmt_date(r["follow_up_date"]), r.get("remarks") or "",
            status, r.get("created_by") or ""
        ])
    return send_file(io.BytesIO(buf.getvalue().encode("utf-8-sig")), as_attachment=True,
                      download_name="followups.csv", mimetype="text/csv")

@app.route("/followup/export/pdf")
@login_required
def followup_export_pdf():
    q = request.args.get("q","")
    if not REPORTLAB_AVAILABLE:
        flash("reportlab not installed. Run: pip install reportlab","danger")
        return redirect(url_for("followups", q=q))
    items = list_follow_ups(q, request.args.get("cat",""))
    path = os.path.join(tempfile.gettempdir(), "tfc_followup_report.pdf")
    try:
        generate_followup_pdf(path, items)
        return send_file(path, as_attachment=True, download_name="followups.pdf", mimetype="application/pdf")
    except Exception as e:
        flash(str(e),"danger")
        return redirect(url_for("followups", q=q))

# ── Closed / Rejected ──────────────────────────────────────────────────────────
@app.route("/closed")
@login_required
def closed():
    q = request.args.get("q",""); ll = list_closed_loans(q)
    def closure_label(l):
        sz = l.get("seizure")
        if sz:
            return (f'<span class="badge badge-rejected">🚫 Seized</span><br>'
                    f'<span style="font-size:11px;color:var(--muted);">Written off {fmt_inr(sz.get("written_off") or 0)} · {html.escape(sz.get("writeoff_reason") or "")}</span><br>'
                    f'<a href="/emis/{l["loan_id"]}" style="font-size:11.5px;">View EMIs</a>')
        pc = l.get("preclosure")
        if not pc: return "Regular"
        return (f'<span class="badge badge-partial">⏩ Pre-closed</span><br>'
                f'<span style="font-size:11px;color:var(--muted);">Settled ₹{float(pc["settlement_amount"] or 0):,.2f}{(" (incl. penalty " + fmt_inr(pc["penalty_amount"]) + ")") if float(pc.get("penalty_amount") or 0) > 0 else ""} · '
                + (f'interest {fmt_inr(pc["further_interest"])} · {int(pc.get("waived_months") or 0)} month(s) waived'
                   if pc.get("further_interest") is not None else
                   f'rate {float(pc["original_rate"] or 0)*100:.2f}% → {float(pc["new_rate"] or 0)*100:.2f}%')
                + f' · bill {html.escape(str(pc["bill_number"] or ""))}</span>')
    def handover_cell(l):
        z = l.get("seizure")
        if z:
            its = l["seizure_items"]
            chips = " ".join(f'{SEIZURE_ITEM_INFO[i["item"]][0]}{"✔" if i["status"] == "Received" else "✖"}' for i in its)
            det = "".join(
                f'<tr><td>{seizure_item_title(i)}</td><td>{fmt_date(i["received_on"])}</td>'
                f'<td>{html.escape(i.get("received_by") or "")}</td><td>{html.escape(i.get("recorded_by") or "")}</td>'
                f'<td>{html.escape(i.get("note") or "")}</td></tr>' for i in its)
            body = (f'<div style="font-size:13px;line-height:1.8;margin-bottom:8px;"><b>Seized on:</b> {fmt_date(z.get("seized_date"))} · '
                    f'<b>Kept at:</b> {html.escape(z.get("place") or "—")}<br><b>Reason:</b> {html.escape(z.get("reason") or "—")}<br>'
                    f'<b>Written off:</b> {fmt_inr(z.get("written_off") or 0)} — {html.escape(z.get("writeoff_reason") or "")}</div>'
                    f'<div class="table-wrap"><table><tr><th>Item</th><th>Received on</th><th>Received by</th><th>Recorded by</th><th>Note</th></tr>{det}</table></div>'
                    f'<div style="font-size:12px;color:var(--muted);margin-top:8px;">Approved by {html.escape(z.get("approved_by") or "—")} · '
                    f'acknowledged by <b>{html.escape(z.get("acked_by") or "—")}</b> on {fmt_date((z.get("acked_at") or "")[:10])} · '
                    f'seizure details checked ✔ · legal issues checked ✔{(" — " + html.escape(z["legal_note"])) if z.get("legal_note") else ""}</div>')
            title = html.escape(f"{l['loan_number']} — vehicle seizure", quote=True)
            return (f'<span style="white-space:nowrap;">{chips}</span> '
                    f'<button type="button" class="btn btn-sm btn-primary" onclick="showHandover(\'ho{l["loan_id"]}\',\'{title}\')">View</button>'
                    f'<div id="ho{l["loan_id"]}" hidden>{body}</div>')
        k = l.get("closure")
        if not k: return '<span style="color:var(--muted);">—</span>'
        its = l["closure_items"]
        chips = " ".join(f'{CLOSURE_ITEM_INFO[i["item"]][0]}{"✔" if i["status"] == "Returned" else "✖"}' for i in its)
        det = "".join(
            f'<tr><td>{closure_item_title(i)}</td><td>{fmt_date(i["returned_on"])}</td>'
            f'<td>{html.escape(i.get("handed_by") or "")}</td><td>{html.escape(i.get("recorded_by") or "")}</td>'
            f'<td>{html.escape(i.get("note") or "")}</td></tr>' for i in its)
        body = (f'<div class="table-wrap"><table><tr><th>Item</th><th>Returned on</th><th>Handed over by</th><th>Recorded by</th><th>Note</th></tr>{det}</table></div>'
                f'<div style="font-size:12px;color:var(--muted);margin-top:8px;">{"Pre-closure" if k["kind"] == "PreClosure" else "Regular closing"} · '
                f'approved by {html.escape(k.get("approved_by") or "—")} · hand-over acknowledged by <b>{html.escape(k.get("acked_by") or "—")}</b> '
                f'on {fmt_date((k.get("acked_at") or "")[:10])}</div>')
        title = html.escape(f"{l['loan_number']} — key & documents handed over", quote=True)
        return (f'<span style="white-space:nowrap;">{chips}</span> '
                f'<button type="button" class="btn btn-sm btn-primary" onclick="showHandover(\'ho{l["loan_id"]}\',\'{title}\')">View</button>'
                f'<div id="ho{l["loan_id"]}" hidden>{body}</div>')
    rows = "".join(f"""<tr>
        <td><b>{l['loan_number']}</b></td><td>{l['customer_name']}</td>
        <td>{vehicle_html(l)}</td><td>₹{l['loan_amount']:,.2f}</td>
        <td>{fmt_date(l['closure_date'])}</td><td>{closure_label(l)}</td><td>{handover_cell(l)}</td></tr>""" for l in ll)
    content = f"""
    <h1>🔒 Closed Loans</h1>
    <form method="GET" style="margin-bottom:12px;display:flex;gap:8px;">
      <input name="q" value="{q}" placeholder="Search…" style="max-width:240px;">
      <button class="btn btn-primary btn-sm">Search</button>
    </form>
    <div class="card"><div class="table-wrap"><table>
      <tr><th>Loan #</th><th>Customer</th><th>Vehicle</th><th>Amount</th><th>Closed On</th><th>Closure</th><th>Key &amp; documents</th></tr>
      {rows or '<tr><td colspan="7" style="text-align:center;color:var(--muted);">No closed loans</td></tr>'}
    </table></div></div>
    <div class="fu-modal-overlay" id="hoModal" onclick="if(event.target===this)this.classList.remove('open')">
      <div class="fu-modal" style="max-width:640px;">
        <h3 id="hoTitle">Key &amp; documents handed over</h3>
        <div id="hoBody" style="font-size:13px;margin-top:8px;"></div>
        <div style="margin-top:12px;text-align:right;"><button type="button" class="btn btn-primary btn-sm" onclick="document.getElementById('hoModal').classList.remove('open')">Close</button></div>
      </div>
    </div>
    <script>
    function showHandover(id, title){{
      document.getElementById('hoTitle').textContent = title;
      document.getElementById('hoBody').innerHTML = document.getElementById(id).innerHTML;
      document.getElementById('hoModal').classList.add('open');
    }}
    </script>"""
    return page("Closed Loans", content, "closed")

@app.route("/rejected")
@login_required
def rejected():
    q = request.args.get("q",""); ll = list_rejected_loans(q)
    rows = "".join(f"""<tr>
        <td><b>{l['loan_number']}</b></td><td>{l['customer_name']}</td>
        <td>{l['reason']}</td><td>{fmt_date((l.get('created_at') or '')[:10])}</td></tr>""" for l in ll)
    content = f"""
    <h1>❌ Rejected Loans</h1>
    <form method="GET" style="margin-bottom:12px;display:flex;gap:8px;">
      <input name="q" value="{q}" placeholder="Search…" style="max-width:240px;">
      <button class="btn btn-primary btn-sm">Search</button>
    </form>
    <div class="card"><div class="table-wrap"><table>
      <tr><th>Loan #</th><th>Customer</th><th>Reason</th><th>Date</th></tr>
      {rows or '<tr><td colspan="4" style="text-align:center;color:var(--muted);">No rejected loans</td></tr>'}
    </table></div></div>"""
    return page("Rejected Loans", content, "rejected")

# ── Calculator ─────────────────────────────────────────────────────────────────
@app.route("/calculator", methods=["GET"])
@login_required
def calculator():
    content = f"""
    <h1>🧮 Loan Calculator</h1>

    <div class="card">
      <p style="color:var(--muted);font-size:13px;margin-bottom:14px;">
        Fixed flat rate — interest applied on full principal for entire tenure (finance company method).
        Results update <b>instantly</b> as you type — no page reload needed.
      </p>

      <!-- Calculator Mode -->
      <div class="form-group full" style="margin-bottom:16px;">
        <label>🧮 Calculator Mode</label>
        <select id="c_mode" onchange="c_switchMode(this.value)"
                style="max-width:480px;padding:10px 12px;border-radius:8px;
                       border:1.5px solid var(--border);background:var(--surface);
                       color:var(--text);font-size:14px;">
          <option value="rate">Amount + Interest Rate + Tenure → Calculate EMI</option>
          <option value="emi">Amount + EMI Amount + Tenure → Calculate Interest Rate</option>
          <option value="tenure">Amount + Interest Rate + EMI → Calculate Tenure</option>
        </select>
      </div>

      <div class="form-grid">
        <!-- Loan Amount — always visible -->
        <div class="form-group">
          <label>Loan Amount (₹) *</label>
          <input type="number" id="c_amt" min="1" step="0.01" placeholder="e.g. 50000" oninput="c_calc()">
        </div>

        <!-- Interest Rate — hidden in 'emi' mode -->
        <div class="form-group" id="c_rate_grp">
          <label id="c_rate_lbl">Interest Rate (% p.a.) *</label>
          <input type="number" id="c_rate" min="0.01" step="0.01" placeholder="e.g. 24" oninput="c_calc()">
          <small id="c_rate_note" style="color:var(--accent);font-size:11px;display:none;">
            ← Auto-calculated from Amount + EMI + Tenure
          </small>
        </div>

        <!-- EMI Amount — hidden in 'rate' mode -->
        <div class="form-group" id="c_emi_grp" style="display:none;">
          <label id="c_emi_lbl">EMI Amount (₹) *</label>
          <input type="number" id="c_emi_inp" min="1" step="0.01" placeholder="e.g. 2500" oninput="c_calc()">
          <small id="c_emi_note" style="color:var(--accent);font-size:11px;display:none;">
            ← Auto-calculated from Amount + Rate + Tenure
          </small>
        </div>

        <!-- Tenure — hidden in 'tenure' mode -->
        <div class="form-group" id="c_tenure_grp">
          <label id="c_tenure_lbl">Tenure (Months) *</label>
          <input type="number" id="c_tenure" min="1" max="360" placeholder="e.g. 12" oninput="c_calc()">
          <small id="c_tenure_note" style="color:var(--accent);font-size:11px;display:none;">
            ← Auto-calculated from Amount + Rate + EMI
          </small>
        </div>
      </div>

      <button class="btn btn-primary" onclick="c_calc()" style="margin-top:4px;">Calculate</button>
      <button class="btn" onclick="c_reset()"
              style="background:var(--surface2);color:var(--text);margin-top:4px;margin-left:8px;">
        🔄 Reset
      </button>
    </div>

    <!-- Result panel -->
    <div id="c_result" style="display:none;">
      <div class="calc-result" id="c_main_result">
        <div class="lbl" id="c_res_label">Monthly EMI</div>
        <div class="big-val" id="c_res_bigval">—</div>
        <div class="calc-summary">
          <div class="item"><div class="val" id="c_r_principal">—</div><div class="lbl">Principal</div></div>
          <div class="item"><div class="val" id="c_r_interest">—</div><div class="lbl">Total Interest</div></div>
          <div class="item"><div class="val" id="c_r_total">—</div><div class="lbl">Total Payable</div></div>
          <div class="item"><div class="val" id="c_r_emi">—</div><div class="lbl">Monthly EMI</div></div>
          <div class="item"><div class="val" id="c_r_rate">—</div><div class="lbl">Rate p.a.</div></div>
          <div class="item"><div class="val" id="c_r_tenure">—</div><div class="lbl">Tenure</div></div>
        </div>
      </div>
      <div class="card" style="margin-top:12px;">
        <h2>📅 EMI Schedule</h2>
        <div class="table-wrap">
          <table id="c_schedule_table">
            <thead><tr><th>#</th><th>EMI</th><th>Principal</th><th>Interest</th><th>Balance</th></tr></thead>
            <tbody id="c_sched_body"></tbody>
          </table>
        </div>
      </div>
    </div>

    <div class="card" style="background:#fef3c7;border-color:#fde68a;margin-top:12px;">
      <b>📌 Flat Rate Formulas used:</b><br>
      <code>EMI = (Principal + Principal × Rate% × Tenure÷12) ÷ Tenure</code><br>
      <code>Rate = ((EMI × Tenure − Principal) ÷ (Principal × Tenure÷12)) × 100</code><br>
      <code>Tenure = Total Payable ÷ EMI &nbsp;[iterative, nearest month]</code>
    </div>

    <script>
    function c_fmt(v){{
      return '₹'+parseFloat(v).toLocaleString('en-IN',{{minimumFractionDigits:2,maximumFractionDigits:2}});
    }}

    function c_switchMode(mode){{
      const rateGrp   = document.getElementById('c_rate_grp');
      const emiGrp    = document.getElementById('c_emi_grp');
      const tenureGrp = document.getElementById('c_tenure_grp');
      const rateInp   = document.getElementById('c_rate');
      const emiInp    = document.getElementById('c_emi_inp');
      const tenInp    = document.getElementById('c_tenure');
      const rateLbl   = document.getElementById('c_rate_lbl');
      const emiLbl    = document.getElementById('c_emi_lbl');
      const tenureLbl = document.getElementById('c_tenure_lbl');
      const rateNote  = document.getElementById('c_rate_note');
      const tenureNote= document.getElementById('c_tenure_note');

      // Clear all values and reset readonly
      [rateInp, emiInp, tenInp].forEach(el=>{{ el.value=''; el.readOnly=false; el.style.background=''; }});
      rateNote.style.display = 'none';
      tenureNote.style.display = 'none';

      if(mode === 'rate'){{
        // Inputs: Amount + Rate + Tenure   Output: EMI
        rateGrp.style.display   = 'block';
        emiGrp.style.display    = 'none';
        tenureGrp.style.display = 'block';
        rateLbl.textContent   = 'Interest Rate (% p.a.) *';
        tenureLbl.textContent = 'Tenure (Months) *';

      }} else if(mode === 'emi'){{
        // Inputs: Amount + EMI + Tenure    Output: Interest Rate (shown read-only)
        rateGrp.style.display   = 'block';   // show rate field — but read-only as output
        emiGrp.style.display    = 'block';
        tenureGrp.style.display = 'block';
        rateInp.readOnly = true;
        rateInp.style.background = 'var(--surface2)';
        rateLbl.textContent  = '📊 Interest Rate (% p.a.) — Auto Calculated';
        emiLbl.textContent   = 'EMI Amount (₹) *';
        tenureLbl.textContent= 'Tenure (Months) *';
        rateNote.style.display = 'block';

      }} else if(mode === 'tenure'){{
        // Inputs: Amount + Rate + EMI      Output: Tenure (shown read-only)
        rateGrp.style.display   = 'block';
        emiGrp.style.display    = 'block';
        tenureGrp.style.display = 'block';
        tenInp.readOnly = true;
        tenInp.style.background = 'var(--surface2)';
        rateLbl.textContent   = 'Interest Rate (% p.a.) *';
        emiLbl.textContent    = 'EMI Amount (₹) *';
        tenureLbl.textContent = '📊 Tenure (Months) — Auto Calculated';
        tenureNote.style.display = 'block';
      }}

      document.getElementById('c_result').style.display = 'none';
    }}

    function c_calc(){{
      const mode   = document.getElementById('c_mode').value;
      const amt    = parseFloat(document.getElementById('c_amt').value)||0;
      const rateEl = document.getElementById('c_rate');
      const emiEl  = document.getElementById('c_emi_inp');
      const tenEl  = document.getElementById('c_tenure');

      let rate, emi, tenure, interest, total;

      if(mode === 'rate'){{
        rate   = parseFloat(rateEl.value)||0;
        tenure = parseInt(tenEl.value)||0;
        if(amt<=0||rate<=0||tenure<=0) return;
        interest = amt*(rate/100)*(tenure/12);
        total    = amt+interest;
        emi      = total/tenure;

      }} else if(mode === 'emi'){{
        emi    = parseFloat(emiEl.value)||0;
        tenure = parseInt(tenEl.value)||0;
        if(amt<=0||emi<=0||tenure<=0) return;
        total    = emi*tenure;
        interest = total-amt;
        if(interest<0){{
          document.getElementById('c_result').style.display='none'; return;
        }}
        rate = (interest/(amt*(tenure/12)))*100;
        // Show back-calculated rate
        rateEl.value = rate.toFixed(4);

      }} else if(mode === 'tenure'){{
        rate = parseFloat(rateEl.value)||0;
        emi  = parseFloat(emiEl.value)||0;
        if(amt<=0||rate<=0||emi<=0) return;
        // Total = Principal*(1 + rate/100 * T/12); EMI = Total/T
        // EMI*T = P + P*r*T/12  → T*(EMI - P*r/12) = P → T = P/(EMI - P*r/12)
        const rDec = rate/100;
        const perMonth = amt*rDec/12;
        if(emi <= perMonth){{
          document.getElementById('c_result').style.display='none'; return;
        }}
        tenure   = Math.round(amt/(emi-perMonth));
        if(tenure<=0||tenure>600) return;
        interest = amt*(rate/100)*(tenure/12);
        total    = amt+interest;
        emi      = total/tenure;
        // Show back-calculated tenure
        tenEl.value = tenure;
      }}

      // Populate result panel
      document.getElementById('c_res_label').textContent =
        mode==='emi' ? 'Back-Calculated Interest Rate' :
        mode==='tenure' ? 'Calculated Tenure' : 'Monthly EMI';
      document.getElementById('c_res_bigval').textContent =
        mode==='emi'    ? rate.toFixed(2)+'% p.a.' :
        mode==='tenure' ? tenure+' months'          : c_fmt(emi);

      document.getElementById('c_r_principal').textContent = c_fmt(amt);
      document.getElementById('c_r_interest').textContent  = c_fmt(interest);
      document.getElementById('c_r_total').textContent     = c_fmt(total);
      document.getElementById('c_r_emi').textContent       = c_fmt(emi);
      document.getElementById('c_r_rate').textContent      = rate.toFixed(2)+'%';
      document.getElementById('c_r_tenure').textContent    = tenure+' months';

      // Build schedule
      const body = document.getElementById('c_sched_body');
      body.innerHTML = '';
      for(let i=1;i<=tenure;i++){{
        const bal = Math.max(amt-(amt/tenure)*i,0);
        body.innerHTML += `<tr>
          <td>${{i}}</td>
          <td>${{c_fmt(emi)}}</td>
          <td>${{c_fmt(amt/tenure)}}</td>
          <td>${{c_fmt(interest/tenure)}}</td>
          <td>${{c_fmt(bal)}}</td>
        </tr>`;
      }}

      document.getElementById('c_result').style.display = 'block';
    }}

    function c_reset(){{
      ['c_amt','c_rate','c_emi_inp','c_tenure'].forEach(id=>{{
        const el=document.getElementById(id);
        el.value=''; el.readOnly=false; el.style.background='';
      }});
      document.getElementById('c_mode').value='rate';
      c_switchMode('rate');
      document.getElementById('c_result').style.display='none';
    }}
    // Init on page load — ensure correct fields shown for default mode
    document.addEventListener('DOMContentLoaded', ()=>{{ c_switchMode('rate'); }});
    </script>
    """
    return page("Calculator", content, "calculator")

# ── Report ─────────────────────────────────────────────────────────────────────
@app.route("/report", methods=["GET","POST"])
@login_required
@role_required("superadmin","admin","manager","viewer","assocmgr")
def report():
    if request.method == "POST":
        if not REPORTLAB_AVAILABLE:
            flash("reportlab not installed. Run: pip install reportlab","danger")
            return redirect(url_for("report"))
        path = os.path.join(tempfile.gettempdir(), "tfc_loan_report.pdf")
        try:
            generate_pdf(path)
            return send_file(path, as_attachment=True, download_name="tfc_loan_report.pdf", mimetype="application/pdf")
        except Exception as e:
            flash(str(e),"danger")
    tl,tla,tr,tp = get_kpi_totals(); counts = get_loan_summary_counts()
    content = f"""
    <h1>📊 Report</h1>
    <div class="kpi-grid" style="margin-bottom:16px;">
      <div class="kpi"><div class="val">{counts['total']}</div><div class="lbl">Total Loans</div></div>
      <div class="kpi"><div class="val">₹{tla:,.0f}</div><div class="lbl">Disbursed</div></div>
      <div class="kpi" style="border-color:var(--green)"><div class="val" style="color:var(--green)">₹{tr:,.0f}</div><div class="lbl">Collected</div></div>
      <div class="kpi" style="border-color:var(--red)"><div class="val" style="color:var(--red)">₹{tp:,.0f}</div><div class="lbl">Outstanding</div></div>
    </div>
    <div class="card">
      <form method="POST">
        <button class="btn btn-primary">📥 Download PDF Report</button>
      </form>
      {"<p style='color:var(--red);margin-top:8px;font-size:13px;'>reportlab not installed — PDF unavailable.</p>" if not REPORTLAB_AVAILABLE else ""}
    </div>"""
    return page("Report", content, "report")

# ── Users ──────────────────────────────────────────────────────────────────────
@app.route("/users", methods=["GET","POST"])
@login_required
@role_required("admin","superadmin")
def users():
    is_super = session.get("role") == "superadmin"
    me = session.get("username", "")
    if request.method == "POST":
        uname = request.form["username"].strip(); pw = request.form["password"]; role_u = request.form["role"]
        c = get_cur()
        c.execute("SELECT role FROM Users WHERE username=?", (uname,))
        existing = c.fetchone()
        if role_u not in ROLES:
            flash("Unknown role.","danger")
        elif not is_super and role_u == "superadmin":
            flash("Only a Super Admin can create a Super Admin.","danger")
        else:
            if existing and not is_super:
                role_u = existing["role"]      # only a Super Admin may change an existing user's role
            c.execute("INSERT INTO Users (username,pw_hash,role,created_at) VALUES (?,?,?,?) ON CONFLICT(username) DO UPDATE SET pw_hash=?,role=?",
                      (uname,hash_pw(pw),role_u,datetime.now(timezone.utc).isoformat(),hash_pw(pw),role_u))
            get_db().commit(); flash(f"User '{uname}' saved.","success")
    c = get_cur(); c.execute("SELECT * FROM Users ORDER BY user_id")
    ul = [dict(r) for r in c.fetchall()]
    def role_cell(u):
        if not is_super: return ""
        if u["username"] == me:
            return '<td style="font-size:12px;color:var(--muted);">(you — cannot change your own role)</td>'
        opts = "".join(f'<option value="{r}" {"selected" if r == u["role"] else ""}>{ROLES[r]["label"]}</option>' for r in ROLES)
        return (f'<td><form method="POST" action="/users/role" style="display:flex;gap:6px;align-items:center;" '
                f'onsubmit="return confirm(\'Change the role of {html.escape(u["username"])}?\')">'
                f'<input type="hidden" name="username" value="{html.escape(u["username"])}">'
                f'<select name="role" style="font-size:12px;padding:4px 6px;">{opts}</select>'
                f'<button class="btn btn-sm btn-amber">Change role</button></form></td>')
    urows = "".join(f"""<tr><td>{u['username']}</td>
        <td><span class="badge badge-{u['role']}">{ROLES.get(u['role'],{}).get('label', u['role'].title())}</span></td>
        <td>{fmt_date((u.get('created_at') or '')[:10])}</td>{role_cell(u)}</tr>""" for u in ul)
    rrws = ""
    for r,p in ROLES.items():
        def ck(k,p=p): return "✅" if p.get(k) else "❌"
        rrws += f"""<tr>
            <td><span class="badge badge-{r}">{p['label']}</span></td>
            <td>{ck('can_add')}</td><td>{ck('can_approve')}</td>
            <td>{ck('can_pay')}</td><td>{ck('can_ack')}</td><td>{ck('can_report')}</td>
        </tr>"""
    seizure_rule = ""
    if is_super:
        seizure_rule = f"""<div class="card" style="margin-bottom:12px;">
      <h2>🚫 Seizure rule</h2>
      <form method="POST" action="/settings/seizure" style="display:flex;gap:10px;align-items:end;flex-wrap:wrap;">
        <div class="form-group"><label>The "Vehicle Seized" option appears on a loan from this many overdue EMIs</label>
          <input type="number" name="min_overdue" value="{seizure_threshold()}" min="1" max="60" required style="max-width:140px;"></div>
        <button class="btn btn-primary">Save</button>
      </form>
      <p style="font-size:12px;color:var(--muted);margin-top:6px;">Seizing is always optional: it only makes the button available. Currently: {seizure_threshold()} or more overdue EMIs.</p>
    </div>"""
    followup_rule = ""
    if is_super:
        fields = "".join(
            f'<div class="form-group"><label>{HANDOVER_LABELS[k]} — follow-up after (days)</label>'
            f'<input type="number" name="days_{k}" value="{handover_days(k)}" min="1" max="365" required style="max-width:130px;"></div>'
            for k in HANDOVER_LABELS)
        followup_rule = f"""<div class="card" style="margin-bottom:12px;">
      <h2>📞 Follow-up timing</h2>
      <form method="POST" action="/settings/followup_days" style="display:flex;gap:14px;align-items:end;flex-wrap:wrap;">
        {fields}
        <button class="btn btn-primary">Save</button>
      </form>
      <p style="font-size:12px;color:var(--muted);margin-top:6px;">When a new loan is entered with Key / RC / Proof &amp; Documents = No, the follow-up is set this many days after the loan date. Existing follow-ups keep their dates.</p>
    </div>"""
    fine_rule = ""
    if is_super:
        fine_rule = f"""<div class="card" style="margin-bottom:12px;">
      <h2>🚓 Police fine limit</h2>
      <form method="POST" action="/settings/police_fine" style="display:flex;gap:10px;align-items:end;flex-wrap:wrap;">
        <div class="form-group"><label>A new loan cannot be registered with a police fine above (₹)</label>
          <input type="number" name="max_fine" value="{police_fine_limit():g}" min="0" step="1" required style="max-width:160px;"></div>
        <button class="btn btn-primary">Save</button>
      </form>
      <p style="font-size:12px;color:var(--muted);margin-top:6px;">Currently {fmt_inr(police_fine_limit())}. The amount entered for each vehicle is shown to the approver.</p>
    </div>"""
    content = f"""
    <h1>⚙️ User Management</h1>
    {seizure_rule}
    {followup_rule}
    {fine_rule}
    <div class="form-grid">
      <div class="card">
        <h2>Add / Update User</h2>
        <form method="POST">
          <div class="form-group" style="margin-bottom:10px;"><label>Username</label><input name="username" required></div>
          <div class="form-group" style="margin-bottom:10px;"><label>Password</label><input type="password" name="password" required></div>
          <div class="form-group" style="margin-bottom:14px;"><label>Role</label>
            <select name="role">{"".join(f'<option value="{r}">{ROLES[r]["label"]}</option>' for r in ROLES)}</select>
          </div>
          <button class="btn btn-primary">Save User</button>
        </form>
      </div>
      <div class="card">
        <h2>Role Permissions</h2>
        <div class="table-wrap"><table>
          <tr><th>Role</th><th>ADD</th><th>APPROVE</th><th>PAY</th><th>ACKNOWLEDGE</th><th>REPORT</th></tr>
          {rrws}
        </table></div>
      </div>
    </div>
    <div class="card">
      <h2>All Users</h2>
      <div class="table-wrap"><table>
        <tr><th>Username</th><th>Role</th><th>Created</th>{'<th>Change role</th>' if is_super else ''}</tr>{urows}
      </table></div>
      {'<p style="font-size:12px;color:var(--muted);margin-top:8px;">A role change applies to that user within about a minute, without them logging in again.</p>' if is_super else ''}
    </div>"""
    return page("Users", content, "users")

@app.route("/users/role", methods=["POST"])
@login_required
@role_required("superadmin")
def users_change_role():
    uname = request.form.get("username","").strip(); new_role = request.form.get("role","")
    c = get_cur()
    c.execute("SELECT role FROM Users WHERE username=?", (uname,))
    row = c.fetchone()
    if new_role not in ROLES:
        flash("Unknown role.","danger")
    elif not row:
        flash("User not found.","danger")
    elif uname == session.get("username"):
        flash("You cannot change your own role.","danger")
    elif row["role"] == new_role:
        flash(f"'{uname}' is already {ROLES[new_role]['label']}.","info")
    else:
        if row["role"] == "superadmin":
            c.execute("SELECT COUNT(*) as n FROM Users WHERE role='superadmin'")
            if c.fetchone()["n"] <= 1:
                flash("There must always be at least one Super Admin.","danger")
                return redirect(url_for("users"))
        c.execute("UPDATE Users SET role=? WHERE username=?", (new_role, uname))
        get_db().commit()
        flash(f"'{uname}' is now {ROLES[new_role]['label']} (was {ROLES.get(row['role'],{}).get('label', row['role'])}).","success")
    return redirect(url_for("users"))

# ── API ────────────────────────────────────────────────────────────────────────
@app.route("/api/chart/monthly")
@login_required
def api_monthly():
    m,a = get_monthly_paid_series(); return jsonify({"labels":m,"data":a})

@app.route("/api/chart/breakdown")
@login_required
def api_breakdown():
    bd = get_loan_type_breakdown()
    return jsonify({"labels":[r["vehicle_type"] for r in bd],"data":[r["cnt"] for r in bd]})

# ── CHATBOT ────────────────────────────────────────────────────────────────────
def _chatbot_search_loans(term):
    c = get_cur()
    term = term.strip()

    # 1) Fast path: exact loan number match (indexed, instant)
    c.execute("SELECT * FROM LoanEntry WHERE loan_number = ? LIMIT 6", (term,))
    rows = c.fetchall()
    if rows:
        return [dict(r) for r in rows]

    # 2) Fast path: exact mobile number match (10-digit numeric input)
    if term.isdigit() and len(term) == 10:
        c.execute("SELECT * FROM LoanEntry WHERE customer_mobile = ? LIMIT 6", (term,))
        rows = c.fetchall()
        if rows:
            return [dict(r) for r in rows]

    # 3) Prefix match on loan_number (e.g. "LN-2026" -> uses index range scan)
    if re.match(r"^[A-Za-z]{1,4}-?\d", term):
        c.execute("SELECT * FROM LoanEntry WHERE loan_number LIKE ? ORDER BY created_at DESC LIMIT 6", (f"{term}%",))
        rows = c.fetchall()
        if rows:
            return [dict(r) for r in rows]

    # 4) Broad fallback search (only reached if nothing matched above)
    q = f"%{term}%"
    c.execute("""SELECT * FROM LoanEntry
                 WHERE customer_name LIKE ? OR customer_mobile LIKE ?
                    OR vehicle_number LIKE ? OR vehicle_model LIKE ?
                    OR engine_number LIKE ? OR chassis_number LIKE ?
                    OR guarantor_name LIKE ? OR guarantor_mobile LIKE ?
                    OR loan_number LIKE ?
                 ORDER BY created_at DESC LIMIT 6""",
              (q,q,q,q,q,q,q,q,q))
    return [dict(r) for r in c.fetchall()]

def _na(val, fallback="-"):
    return val if val not in (None, "", "None") else fallback

def _chatbot_loan_summary(loan, detailed=False):
    lid = loan["id"]
    emis = get_emis_for_loan(lid)
    total = len(emis)
    paid  = len([e for e in emis if e["status"]=="Paid"])
    outstanding = sum(float(e.get("remaining_amount") or e["emi_amount"]) for e in emis if e["status"] not in ("Paid","PreClosed","Seized"))
    next_due = next((e for e in emis if e["status"] in ("Pending","Partial","Overdue")), None)

    lines = []
    lines.append(f"📋 Loan: {loan['loan_number']}  ({loan['status']})")
    lines.append(f"👤 Customer: {_na(loan.get('customer_name'))}  |  📱 {_na(loan.get('customer_mobile'))}")
    lines.append(f"🚗 Vehicle: {_na(vehicle_label(loan))} — {_na(loan.get('vehicle_number'))} ({_na(loan.get('vehicle_type'))})")
    lines.append(f"💰 Loan Amount: {fmt_inr(loan.get('loan_amount',0))}  |  Rate: {float(loan.get('interest_rate',0))*100:.1f}%  |  Tenure: {loan.get('tenure','-')}m")
    if total:
        lines.append(f"💳 EMIs: {paid}/{total} paid  |  Outstanding: {fmt_inr(outstanding)}")
        if next_due:
            rem = float(next_due.get("remaining_amount") or next_due["emi_amount"])
            lines.append(f"⏳ Next Due: Installment #{next_due['installment_no']} on {fmt_date(next_due['due_date'])} — {fmt_inr(rem)} ({next_due['status']})")
    else:
        lines.append("💳 EMI schedule not generated yet (loan pending approval).")
    if detailed:
        if loan.get("customer_address"): lines.append(f"🏠 Address: {loan['customer_address']}")
        if loan.get("guarantor_name"): lines.append(f"🛡️ Guarantor: {loan['guarantor_name']} ({_na(loan.get('guarantor_mobile'))})")
        if loan.get("remarks"): lines.append(f"📝 Remarks: {loan['remarks']}")
    return "\n".join(lines)


# ── FUZZY TYPO CORRECTION ────────────────────────────────────────────────────
import difflib

CHATBOT_VOCAB = [
    "loan","loans","pending","overdue","upcoming","today","summary","week","weekly",
    "month","monthly","profit","collection","collections","collected","outstanding",
    "total","high","highest","top","amount","customer","customers","active","closed",
    "rejected","approved","approval","insights","insight","emi","emis","due","this",
    "last","income","revenue","disbursed","disburse","balance","vehicle","mobile",
    "number","status","late","delayed","payment","payments","how","many","show","give",
    "list","my","all","of","do","i","have","about","upcoming","days","day","what","is",
    "hi","hey","help","menu","hello",
    "average","avg","customers","vehicle","type","two","four","wheeler","commercial",
    "highest","lowest","interest","rate","worst","reloan","new","applications","next","this","last","quarter","profit","overall","collected","disbursed","compare","comparison","get","will","end","now","from","till","year",
    "follow","followup","missed","resolved","completed","remarks","promise","promised","collect","salary",
]

CHATBOT_ALIASES = {
    "qtr": "quarter", "qtrs": "quarters", "qtor": "quarter", "qtator": "quarter",
    "quator": "quarter", "qaurter": "quarter", "quartar": "quarter", "qty": "quarter",
    "comparision": "comparison", "disburshed": "disbursed", "distibuted": "disbursed", "distributed": "disbursed",
    "yr": "year", "yrs": "years", "wk": "week", "wks": "weeks",
    "mo": "month", "mos": "months", "nxt": "next",
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12",
}

def _normalize_query(text):
    """Best-effort spelling correction against known chatbot vocabulary,
    so typos like 'pendng', 'ovrdue', 'tp 3', 'hihg amout' still match intents."""
    words = re.findall(r"[a-zA-Z]+|\d+", text.lower())
    fixed = []
    for w in words:
        if w in CHATBOT_ALIASES:
            fixed.append(CHATBOT_ALIASES[w]); continue
        if w.isdigit() or len(w) < 2 or w in CHATBOT_VOCAB:
            fixed.append(w); continue
        match = difflib.get_close_matches(w, CHATBOT_VOCAB, n=1, cutoff=0.74)
        fixed.append(match[0] if match else w)
    return " ".join(fixed)


def _add_months(d, n):
    month = d.month - 1 + n
    year = d.year + month // 12
    month = month % 12 + 1
    return date(year, month, 1)

def _profit_period_range(today, rel, unit, n=1):
    """Return (start_date, end_date) inclusive for 'this/next week/month/year/quarter',
    optionally spanning N periods (e.g. 'next 2 months')."""
    if unit == "week":
        start = today - timedelta(days=today.weekday())  # Monday of this week
        if rel == "next":
            start += timedelta(days=7)
        elif rel == "last":
            start -= timedelta(days=7*n)
        end = start + timedelta(days=7*n - 1)
        return start, end

    if unit == "month":
        if rel == "this":
            start = today.replace(day=1)
        elif rel == "last":
            start = _add_months(today.replace(day=1), -n)
        else:
            start = _add_months(today.replace(day=1), 1)
        end_month_start = _add_months(start, n)
        end = end_month_start - timedelta(days=1)
        return start, end

    if unit == "quarter":
        cur_q_start_month = ((today.month - 1) // 3) * 3 + 1  # 1,4,7,10
        start = date(today.year, cur_q_start_month, 1)
        if rel == "next":
            start = _add_months(start, 3)
        elif rel == "last":
            start = _add_months(start, -3)
        end_start = _add_months(start, 3*n)
        end = end_start - timedelta(days=1)
        return start, end

    # year
    if rel == "this":
        yr = today.year
    elif rel == "last":
        yr = today.year - 1
    else:
        yr = today.year + 1
    start = date(yr, 1, 1)
    end = date(yr + n - 1, 12, 31)
    return start, end


def _profit_for_range(start, end):
    """Compute profit (interest-portion) stats for EMI installments with
    due_date in [start, end]. If start/end are None, computes across ALL EMIs."""
    c = get_cur()
    if start is None:
        c.execute("""SELECT e.emi_amount, e.amount_paid, e.status, le.loan_amount,
                            (SELECT COUNT(*) FROM EMI e2 WHERE e2.loan_id=e.loan_id) as n_inst
                     FROM EMI e JOIN LoanEntry le ON le.id=e.loan_id""")
    else:
        c.execute("""SELECT e.emi_amount, e.amount_paid, e.status, le.loan_amount,
                            (SELECT COUNT(*) FROM EMI e2 WHERE e2.loan_id=e.loan_id) as n_inst
                     FROM EMI e JOIN LoanEntry le ON le.id=e.loan_id
                     WHERE e.due_date>=? AND e.due_date<=?""",
                  (start.isoformat(), end.isoformat()))
    rows = c.fetchall()

    total_profit = 0.0
    collected_profit = 0.0
    for r in rows:
        n_inst = r["n_inst"] or 1
        principal_share = float(r["loan_amount"]) / n_inst
        interest_share = float(r["emi_amount"]) - principal_share
        total_profit += interest_share
        if r["status"] == "Paid":
            collected_profit += interest_share
        elif r["status"] == "Partial" and r["emi_amount"]:
            paid_ratio = float(r["amount_paid"] or 0) / float(r["emi_amount"])
            collected_profit += interest_share * paid_ratio

    return {
        "n_installments": len(rows),
        "total_profit": total_profit,
        "collected_profit": collected_profit,
        "pending_profit": total_profit - collected_profit,
    }


def _chatbot_intent(msg, low):
    """Return a reply string for analytical / conversational intents, or None if it's a search query."""
    c = get_cur()
    today = date.today()

    # ── Greeting / on-open ──
    if msg == "__greet__":
        uname = session.get("username","there")
        role = session.get("role","")
        return (f"👋 Hello {uname}! I'm Thendralla, your AI assistant.\n\n"
                f"You're logged in as {role}. Ask me things like:\n"
                f"• \"How many loans do I have\"\n"
                f"• \"Today summary\"\n"
                f"• \"Upcoming EMI\"\n"
                f"• \"Overdue loans\"\n"
                f"• \"This week insights\"\n"
                f"• \"High amount pending loans top 3\"\n"
                f"• \"How many customers\", \"average loan amount\"\n"
                f"• \"Loans by vehicle type\", \"highest interest rate\"\n"
                f"• \"Most overdue customer\", \"new loans this month\"\n"
                f"• \"This year how much will get collected\"\n"
                f"• \"Compare this month and last month profit\"\n"
                f"• \"Follow ups today\", \"missed follow ups\", \"follow up summary\"\n"
                f"• Or just type a loan number / customer name / vehicle number.")

    if any(g == low for g in ["hi","hello","hey","help","hii","hlo","menu"]) or "what can you do" in low:
        uname = session.get("username","")
        return (f"👋 Hi {uname}! Ask me about:\n"
                f"• Loan counts & status (\"how many loans\")\n"
                f"• Today's summary, this week's insights\n"
                f"• Upcoming / overdue EMIs\n"
                f"• Collections / outstanding amounts\n"
                f"• Top high-amount loans (e.g. \"top 5 high amount loans\")\n"
                f"• Follow-ups (\"follow ups today\", \"missed follow ups\")\n"
                f"• Or search by loan number, customer name, vehicle number, mobile.")

    # ── Total loan count / "how many loans" ──
    if re.search(r"how many loan|total loan|no\.? of loan|number of loan|my loans?$|all loans?$", low):
        counts = get_loan_summary_counts()
        return (f"📊 You have {counts['total']} loan(s) in total:\n"
                + f"• Pending Approval: {counts['pending']}\n"
                + f"• Active (Approved): {counts['approved']}\n"
                + f"• Closed: {counts['closed']}\n"
                + f"• Rejected: {counts['rejected']}\n"
                + f"• Overdue EMIs: {counts['overdue']}\n"
                + f"• Upcoming EMIs (10d): {counts['upcoming']}")

    # ── Today summary ──
    if "today" in low and ("summary" in low or "today" == low.strip() or "give me today" in low):
        today_iso = today.isoformat()
        c.execute("SELECT COUNT(*) as n, COALESCE(SUM(remaining_amount),0) as amt FROM EMI WHERE due_date=? AND status IN ('Pending','Partial','Overdue')",(today_iso,))
        due_row = c.fetchone()
        c.execute("SELECT COUNT(*) as n, COALESCE(SUM(amount_paid),0) as amt FROM EMI WHERE date(paid_at)=? AND status='Paid'",(today_iso,))
        paid_row = c.fetchone()
        c.execute("SELECT COUNT(*) as n FROM LoanEntry WHERE date(created_at)=?",(today_iso,))
        new_row = c.fetchone()
        overdue_count = len(get_overdue_emis())
        return (f"📅 Today's Summary ({today_iso}):\n"
                f"• EMIs due today: {due_row['n']} — {fmt_inr(due_row['amt'])}\n"
                f"• Collected today: {paid_row['n']} EMI(s) — {fmt_inr(paid_row['amt'])}\n"
                f"• New loan applications today: {new_row['n']}\n"
                f"• Total overdue EMIs (all time): {overdue_count}")

    # ── Upcoming EMI ──
    if "upcoming" in low or "due soon" in low or "next emi" in low:
        upcoming = get_upcoming_emis()
        if not upcoming:
            return "✅ No EMIs are due in the next 10 days."
        grouped = group_alerts_by_loan(upcoming)
        lines = [f"⏳ Upcoming EMIs (next {UPCOMING_DAYS} days) — {len(grouped)} loan(s):\n"]
        for g in grouped[:8]:
            days_left = (parse_date(g["oldest_due"]) - today).days
            lines.append(f"• {g['loan_number']} ({g['customer_name']}) — {fmt_inr(g['total_due'])} due {fmt_date(g['oldest_due'])} (in {days_left}d)")
        if len(grouped) > 8:
            lines.append(f"...and {len(grouped)-8} more. Check the Alerts page for full list.")
        return "\n".join(lines)

    # ── Overdue ──
    # ── Customer with most overdue (check BEFORE general overdue intent) ──
    if "most overdue" in low or "worst customer" in low or "highest overdue" in low:
        overdue = get_overdue_emis()
        if not overdue:
            return "✅ No overdue EMIs — every customer is up to date!"
        grouped = group_alerts_by_loan(overdue)
        grouped.sort(key=lambda g: g["total_due"], reverse=True)
        top = grouped[0]
        return (f"🔴 Customer with the highest overdue amount:\n"
                f"• {top['loan_number']} — {top['customer_name']}\n"
                f"• Overdue: {fmt_inr(top['total_due'])} across {top['emi_count']} EMI(s), oldest due {fmt_date(top['oldest_due'])}")

    if "overdue" in low or "late payment" in low or "delayed" in low:
        overdue = get_overdue_emis()
        if not overdue:
            return "✅ No overdue EMIs! All customers are up to date."
        grouped = group_alerts_by_loan(overdue)
        total_amt = sum(g["total_due"] for g in grouped)
        lines = [f"🔴 Overdue — {len(grouped)} loan(s), total {fmt_inr(total_amt)}:\n"]
        for g in grouped[:8]:
            days_overdue = (today - parse_date(g["oldest_due"])).days
            lines.append(f"• {g['loan_number']} ({g['customer_name']}) — {fmt_inr(g['total_due'])}, {days_overdue}d overdue")
        if len(grouped) > 8:
            lines.append(f"...and {len(grouped)-8} more. Check the Alerts page for full list.")
        return "\n".join(lines)

    # ── Top N high-amount pending loans ──
    # Matches: "High amount pending loans top 3", "loans top 5", "high pending loans top 2",
    # "top 5 pending loans", "highest outstanding loans", etc.
    if "top" in low and ("loan" in low or "pending" in low or "amount" in low or "outstanding" in low) \
       or ("high" in low and "loan" in low):
        m = re.search(r"top\s*(\d+)", low)
        n = int(m.group(1)) if m else 3
        n = max(1, min(n, 20))

        c.execute("""SELECT le.id, le.loan_number, le.customer_name, le.status, le.loan_amount,
                            COALESCE(SUM(CASE WHEN e.status NOT IN ('Paid','PreClosed','Seized') THEN e.remaining_amount ELSE 0 END),0) as outstanding
                     FROM LoanEntry le LEFT JOIN EMI e ON e.loan_id=le.id
                     WHERE le.status NOT IN ('Closed','Rejected')
                     GROUP BY le.id""")
        rows = c.fetchall()

        ranked = []
        for r in rows:
            amt = float(r["loan_amount"]) if r["status"] == "PendingApproval" else float(r["outstanding"] or 0)
            ranked.append((amt, r))
        ranked.sort(key=lambda x: x[0], reverse=True)
        top = ranked[:n]

        if not top:
            return "📊 No active loans found to rank."

        lines = [f"💰 Top {len(top)} High-Amount Loan(s):\n"]
        for amt, r in top:
            label = "Loan Amount (Pending Approval)" if r["status"] == "PendingApproval" else "Outstanding"
            lines.append(f"• {r['loan_number']} — {_na(r['customer_name'])} ({r['status']}) — {fmt_inr(amt)} {label}")
        return "\n".join(lines)

    # ── Period COMPARISON: "this month and last month ... comparison" ──
    if "compar" in low:
        unit = next((u for u in ["quarter","month","year","week"] if u in low), None)
        if unit:
            if "profit" in low:
                metric = "profit"
            elif "disburs" in low:
                metric = "disbursed"
            elif "collect" in low:
                metric = "collected"
            elif "loan" in low:
                metric = "loans"
            else:
                metric = "profit"

            this_s, this_e = _profit_period_range(today, "this", unit)
            last_s, last_e = _profit_period_range(today, "last", unit)
            unit_label = unit.capitalize()

            def _period_metric(start, end):
                if metric == "profit":
                    r = _profit_for_range(start, end)
                    return r["total_profit"], r["collected_profit"]
                if metric == "disbursed" or metric == "loans":
                    c.execute("SELECT COUNT(*) as n, COALESCE(SUM(loan_amount),0) as amt FROM LoanEntry WHERE date(created_at)>=? AND date(created_at)<=?",
                              (start.isoformat(), end.isoformat()))
                    row = c.fetchone()
                    return row["amt"], row["n"]
                # collected (expected collections = full EMI amounts due)
                c.execute("SELECT COALESCE(SUM(emi_amount),0) as amt, COUNT(*) as n FROM EMI WHERE due_date>=? AND due_date<=?",
                          (start.isoformat(), end.isoformat()))
                row = c.fetchone()
                return row["amt"], row["n"]

            this_val, this_extra = _period_metric(this_s, this_e)
            last_val, last_extra = _period_metric(last_s, last_e)
            diff = this_val - last_val
            pct = (diff / last_val * 100) if last_val else (100.0 if this_val else 0.0)
            arrow = "📈 up" if diff > 0 else ("📉 down" if diff < 0 else "➡️ flat")

            if metric == "profit":
                return (f"📊 {unit_label}ly Profit Comparison:\n"
                        f"• This {unit_label} ({fmt_date(this_s)} to {fmt_date(this_e)}): {fmt_inr(this_val)} "
                        f"(collected: {fmt_inr(this_extra)})\n"
                        f"• Last {unit_label} ({fmt_date(last_s)} to {fmt_date(last_e)}): {fmt_inr(last_val)} "
                        f"(collected: {fmt_inr(last_extra)})\n\n"
                        f"{arrow} by {fmt_inr(abs(diff))} ({abs(pct):.1f}%)")
            elif metric in ("disbursed","loans"):
                label = "New Loans / Disbursed Amount" if metric=="loans" else "Disbursed Amount"
                return (f"📊 {unit_label}ly {label} Comparison:\n"
                        f"• This {unit_label}: {fmt_inr(this_val)} across {this_extra} loan(s)\n"
                        f"• Last {unit_label}: {fmt_inr(last_val)} across {last_extra} loan(s)\n\n"
                        f"{arrow} by {fmt_inr(abs(diff))} ({abs(pct):.1f}%)")
            else:
                return (f"📊 {unit_label}ly Expected Collections Comparison:\n"
                        f"• This {unit_label}: {fmt_inr(this_val)} ({this_extra} EMI installments due)\n"
                        f"• Last {unit_label}: {fmt_inr(last_val)} ({last_extra} EMI installments due)\n\n"
                        f"{arrow} by {fmt_inr(abs(diff))} ({abs(pct):.1f}%)")


    # ── Period-based / overall PROFIT projection ──
    # Profit = interest portion of EMI installments due in the period
    # (EMI amount minus the principal share, where principal share = loan_amount / total_installments)
    if "profit" in low:
        # "next 2 months profit", "next 3 weeks profit", etc. (N before the unit)
        m = re.search(r"(this|next|last)\s+(\d+)?\s*(week|month|year|quarter)s?", low)

        # "last month" + profit/collection => handled by the dedicated
        # Last-Month-Collections intent below; don't intercept it here.
        is_last_month = m and m.group(1) == "last" and m.group(3) == "month" and not m.group(2)

        if m and not is_last_month:
            rel = m.group(1)
            n = int(m.group(2)) if m.group(2) else 1
            unit = m.group(3)
            n = max(1, min(n, 24))
            start, end = _profit_period_range(today, rel, unit, n)

            unit_label = unit.capitalize()
            if unit == "week":
                period_label = f"{rel.capitalize()} Week" if n == 1 else f"{rel.capitalize()} {n} Weeks"
            elif unit == "month" and n == 1:
                period_label = start.strftime("%B %Y")
            elif unit == "quarter":
                q_num = (start.month - 1)//3 + 1
                period_label = f"Q{q_num} {start.year}" if n == 1 else f"{rel.capitalize()} {n} Quarters"
            elif unit == "year" and n == 1:
                period_label = str(start.year)
            else:
                period_label = f"{rel.capitalize()} {n} {unit_label}s"

            result = _profit_for_range(start, end)
            if result["n_installments"] == 0:
                return (f"📈 Profit Projection — {period_label}:\n"
                        f"No EMIs are due in this period, so no profit is expected.")
            return (f"📈 Profit Projection — {period_label} ({fmt_date(start)} to {fmt_date(end)}):\n"
                    f"• EMI installments due: {result['n_installments']}\n"
                    f"• Total expected profit (interest portion): {fmt_inr(result['total_profit'])}\n"
                    f"• Already collected: {fmt_inr(result['collected_profit'])}\n"
                    f"• Still pending: {fmt_inr(result['pending_profit'])}\n\n"
                    f"ℹ️ Profit here = EMI amount minus the principal share of each installment "
                    f"(loan amount ÷ total installments). This is your interest income, not gross collections.")

        # Bare / overall: "profit", "all profit", "total profit", "overall profit"
        if not is_last_month and ("collection" not in low and "income" not in low and "revenue" not in low):
            result = _profit_for_range(None, None)
            if result["n_installments"] == 0:
                return "📈 No EMI schedule found yet, so there's no profit to project."
            return (f"📈 Overall Profit (all loans, all time):\n"
                    f"• Total EMI installments: {result['n_installments']}\n"
                    f"• Total expected profit (interest portion): {fmt_inr(result['total_profit'])}\n"
                    f"• Already collected: {fmt_inr(result['collected_profit'])}\n"
                    f"• Still pending: {fmt_inr(result['pending_profit'])}\n\n"
                    f"ℹ️ This is your total interest income across every loan — past, present and future installments.\n"
                    f"💡 Tip: ask \"this month profit\", \"next quarter profit\", \"last quarter profit\", "
                    f"\"next 2 months profit\", etc. for a specific period.")

    # ── Expected collections for a period: "this/next/last X how much will get collected" ──
    if ("collect" in low or "get" in low) and "profit" not in low:
        m = re.search(r"(this|next|last)\s+(\d+)?\s*(week|month|year|quarter)s?", low)
        if m:
            rel = m.group(1)
            n = int(m.group(2)) if m.group(2) else 1
            unit = m.group(3)
            n = max(1, min(n, 24))
            start, end = _profit_period_range(today, rel, unit, n)

            c.execute("""SELECT COUNT(*) as n,
                                COALESCE(SUM(emi_amount),0) as total,
                                COALESCE(SUM(CASE WHEN status='Paid' THEN amount_paid
                                                   WHEN status='Partial' THEN amount_paid
                                                   ELSE 0 END),0) as collected
                         FROM EMI WHERE due_date>=? AND due_date<=?""",
                      (start.isoformat(), end.isoformat()))
            row = c.fetchone()
            total = row["total"] or 0
            collected = row["collected"] or 0
            pending = total - collected

            if unit == "month" and n == 1:
                period_label = start.strftime("%B %Y")
            elif unit == "quarter" and n == 1:
                q_num = (start.month - 1)//3 + 1
                period_label = f"Q{q_num} {start.year}"
            elif unit == "year" and n == 1:
                period_label = str(start.year)
            else:
                period_label = f"{rel.capitalize()} {n} {unit.capitalize()}{'s' if n>1 else ''}"

            if row["n"] == 0:
                return f"💰 Expected Collections — {period_label}:\nNo EMIs are due in this period."

            return (f"💰 Expected Collections — {period_label} ({fmt_date(start)} to {fmt_date(end)}):\n"
                    f"• EMI installments due: {row['n']}\n"
                    f"• Total expected collection: {fmt_inr(total)}\n"
                    f"• Already collected: {fmt_inr(collected)}\n"
                    f"• Still pending: {fmt_inr(pending)}\n\n"
                    f"ℹ️ This is the full EMI amount (principal + interest), not just profit.")

    # ── Disbursed amount for a period: "last year how much disbursed" ──
    if "disburs" in low:
        m = re.search(r"(this|next|last)\s+(\d+)?\s*(week|month|year|quarter)s?", low)
        if m:
            rel = m.group(1)
            n = int(m.group(2)) if m.group(2) else 1
            unit = m.group(3)
            n = max(1, min(n, 24))
            start, end = _profit_period_range(today, rel, unit, n)

            c.execute("SELECT COUNT(*) as n, COALESCE(SUM(loan_amount),0) as amt FROM LoanEntry WHERE date(created_at)>=? AND date(created_at)<=?",
                      (start.isoformat(), end.isoformat()))
            row = c.fetchone()

            if unit == "month" and n == 1:
                period_label = start.strftime("%B %Y")
            elif unit == "quarter" and n == 1:
                q_num = (start.month - 1)//3 + 1
                period_label = f"Q{q_num} {start.year}"
            elif unit == "year" and n == 1:
                period_label = str(start.year)
            else:
                period_label = f"{rel.capitalize()} {n} {unit.capitalize()}{'s' if n>1 else ''}"

            return (f"💰 Disbursed — {period_label} ({fmt_date(start)} to {fmt_date(end)}):\n"
                    f"• {row['n']} loan(s) disbursed, totaling {fmt_inr(row['amt'])}")

    # ── This week insights ──
    if "this week" in low or "weekly" in low or "week insight" in low:
        week_ago = (today - timedelta(days=7)).isoformat()
        today_iso = today.isoformat()
        c.execute("SELECT COUNT(*) as n, COALESCE(SUM(amount_paid),0) as amt FROM EMI WHERE status='Paid' AND date(paid_at)>=? AND date(paid_at)<=?",(week_ago,today_iso))
        coll = c.fetchone()
        c.execute("SELECT COUNT(*) as n FROM LoanEntry WHERE date(created_at)>=? AND date(created_at)<=?",(week_ago,today_iso))
        new_loans = c.fetchone()
        c.execute("SELECT COUNT(*) as n, COALESCE(SUM(remaining_amount),0) as amt FROM EMI WHERE due_date>=? AND due_date<=? AND status IN ('Pending','Partial','Overdue')",(week_ago,today_iso))
        due_week = c.fetchone()
        c.execute("SELECT COUNT(*) as n FROM LoanEntry WHERE status='Approved' AND date(created_at)>=? AND date(created_at)<=?",(week_ago,today_iso))
        approved_week = c.fetchone()
        return (f"📈 This Week's Insights (last 7 days):\n"
                f"• Collections: {coll['n']} EMI(s) — {fmt_inr(coll['amt'])}\n"
                f"• New loan applications: {new_loans['n']}\n"
                f"• Loans approved: {approved_week['n']}\n"
                f"• EMIs due (this window): {due_week['n']} — {fmt_inr(due_week['amt'])}")

    # ── Last month profit / collections (actual cash collected) ──
    if "last month" in low and ("profit" in low or "collection" in low or "income" in low or "revenue" in low):
        first_of_this_month = today.replace(day=1)
        last_month_end = first_of_this_month - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        c.execute("SELECT COUNT(*) as n, COALESCE(SUM(amount_paid),0) as amt FROM EMI WHERE status='Paid' AND date(paid_at)>=? AND date(paid_at)<=?",
                  (last_month_start.isoformat(), last_month_end.isoformat()))
        row = c.fetchone()
        c.execute("SELECT COALESCE(SUM(extra_interest),0) as ei FROM EMI WHERE status='Paid' AND date(paid_at)>=? AND date(paid_at)<=?",
                  (last_month_start.isoformat(), last_month_end.isoformat()))
        extra = c.fetchone()["ei"] or 0
        return (f"💰 Last Month ({last_month_start.strftime('%B %Y')}) Collections:\n"
                f"• EMIs collected: {row['n']}\n"
                f"• Total collected (EMI amounts): {fmt_inr(row['amt'])}\n"
                f"• Extra/late interest collected: {fmt_inr(extra)}\n\n"
                f"ℹ️ This is total cash collected (principal + interest), not net profit after expenses.")

    # ── Outstanding / Collections totals ──
    if "outstanding" in low or "total due" in low or "pending amount" in low:
        tl,tla,tr,tp = get_kpi_totals()
        return f"⏳ Total Outstanding across all loans: {fmt_inr(tp)}\n💰 Total Disbursed: {fmt_inr(tla)}"

    if "total collect" in low or "how much collected" in low or "collections" in low:
        tl,tla,tr,tp = get_kpi_totals()
        return f"✅ Total Collected so far: {fmt_inr(tr)}"

    if "disburs" in low:
        tl,tla,tr,tp = get_kpi_totals()
        return f"💰 Total Disbursed: {fmt_inr(tla)} across {tl} loan(s)"

    # ── Status-specific counts ──
    if "pending approval" in low or "waiting for approval" in low:
        counts = get_loan_summary_counts()
        return f"⏳ {counts['pending']} loan(s) pending approval."

    if "closed loan" in low or "completed loan" in low:
        counts = get_loan_summary_counts()
        return f"🔒 {counts['closed']} loan(s) closed."

    if "rejected loan" in low:
        counts = get_loan_summary_counts()
        return f"❌ {counts['rejected']} loan(s) rejected."

    if "active loan" in low or "approved loan" in low:
        counts = get_loan_summary_counts()
        return f"✅ {counts['approved']} active loan(s)."

    # ── Total customers ──
    if "how many customer" in low or "total customer" in low or "number of customer" in low:
        c.execute("SELECT COUNT(*) as n FROM Customers")
        n = c.fetchone()["n"] or 0
        c.execute("SELECT COUNT(*) as n FROM Customers WHERE status='Active'")
        active_n = c.fetchone()["n"] or 0
        return f"👥 Total customers: {n}\n• Active: {active_n}\n• Closed: {n - active_n}"

    # ── Average loan amount ──
    if "average loan" in low or "avg loan" in low:
        c.execute("SELECT AVG(loan_amount) as a, COUNT(*) as n FROM LoanEntry")
        row = c.fetchone()
        return f"📐 Average loan amount across {row['n']} loan(s): {fmt_inr(row['a'] or 0)}"

    # ── New loans this month ──
    if "this month" in low and ("new loan" in low or "loan" in low):
        start = today.replace(day=1).isoformat()
        c.execute("SELECT COUNT(*) as n, COALESCE(SUM(loan_amount),0) as amt FROM LoanEntry WHERE date(created_at)>=?", (start,))
        row = c.fetchone()
        return f"🆕 New loan applications this month ({today.strftime('%B %Y')}): {row['n']}\n💰 Total amount applied: {fmt_inr(row['amt'])}"

    # ── Loans by vehicle type ──
    if "vehicle type" in low or "by vehicle" in low or ("two wheeler" in low or "four wheeler" in low or "commercial vehicle" in low):
        c.execute("SELECT vehicle_type, COUNT(*) as n FROM LoanEntry GROUP BY vehicle_type ORDER BY n DESC")
        rows = c.fetchall()
        if not rows: return "🚗 No loans found."
        lines = ["🚗 Loans by Vehicle Type:\n"]
        for r in rows:
            lines.append(f"• {r['vehicle_type'] or 'Unknown'}: {r['n']}")
        return "\n".join(lines)

    # ── Highest / lowest interest rate ──
    if ("highest" in low or "lowest" in low) and ("interest" in low or "rate" in low):
        order = "DESC" if "highest" in low else "ASC"
        c.execute(f"SELECT loan_number, customer_name, interest_rate, loan_amount FROM LoanEntry ORDER BY interest_rate {order} LIMIT 1")
        row = c.fetchone()
        if not row: return "📊 No loans found."
        word = "highest" if order=="DESC" else "lowest"
        return (f"📊 Loan with the {word} interest rate:\n"
                f"• {row['loan_number']} — {_na(row['customer_name'])}\n"
                f"• Rate: {float(row['interest_rate'])*100:.2f}% p.a.  |  Amount: {fmt_inr(row['loan_amount'])}")

    # ── Reloan count ──
    if "reloan" in low:
        c.execute("SELECT COUNT(*) as n FROM LoanEntry WHERE is_reloan=1")
        n = c.fetchone()["n"] or 0
        return f"🔄 Reloans: {n} loan(s) marked as reloan."

    # ── Follow Up ──
    if "follow up" in low or "followup" in low or "follow-up" in low:
        today_s = today.isoformat()
        c.execute("SELECT COUNT(*) as n FROM FollowUp WHERE status='Pending'")
        pending_n = c.fetchone()["n"] or 0
        c.execute("SELECT COUNT(*) as n FROM FollowUp WHERE status='Pending' AND follow_up_date<?", (today_s,))
        missed_n = c.fetchone()["n"] or 0
        c.execute("SELECT COUNT(*) as n FROM FollowUp WHERE status='Pending' AND follow_up_date=?", (today_s,))
        today_n = c.fetchone()["n"] or 0
        c.execute("SELECT COUNT(*) as n FROM FollowUp WHERE status='Resolved'")
        resolved_n = c.fetchone()["n"] or 0

        if "today" in low:
            c.execute("""SELECT f.follow_up_date, f.remarks, le.loan_number, le.customer_name, le.customer_mobile
                         FROM FollowUp f JOIN LoanEntry le ON f.loan_id=le.id
                         WHERE f.status='Pending' AND f.follow_up_date=?
                         ORDER BY le.loan_number""", (today_s,))
            rows = c.fetchall()
            if not rows:
                return "📞 No follow-ups scheduled for today."
            lines = [f"📞 Follow-ups due TODAY — {len(rows)}:\n"]
            for r in rows:
                lines.append(f"• {r['loan_number']} — {_na(r['customer_name'])} ({_na(r['customer_mobile'])}): {r['remarks']}")
            return "\n".join(lines)

        if "missed" in low or ("overdue" in low and "follow" in low):
            c.execute("""SELECT f.follow_up_date, f.remarks, le.loan_number, le.customer_name, le.customer_mobile
                         FROM FollowUp f JOIN LoanEntry le ON f.loan_id=le.id
                         WHERE f.status='Pending' AND f.follow_up_date<?
                         ORDER BY f.follow_up_date ASC""", (today_s,))
            rows = c.fetchall()
            if not rows:
                return "✅ No missed follow-ups — every customer promise date is still current."
            lines = [f"⏰ Missed Follow-ups (promised date already passed) — {len(rows)}:\n"]
            for r in rows[:8]:
                days_late = (today - parse_date(r["follow_up_date"])).days
                lines.append(f"• {r['loan_number']} — {_na(r['customer_name'])} ({_na(r['customer_mobile'])}), {days_late}d late: {r['remarks']}")
            if len(rows) > 8:
                lines.append(f"...and {len(rows)-8} more. Check the Follow Up page for full list.")
            return "\n".join(lines)

        if "resolved" in low or "completed" in low or "done" in low:
            return f"✅ Resolved follow-ups so far: {resolved_n}"

        c.execute("""SELECT f.follow_up_date, f.remarks, le.loan_number, le.customer_name
                     FROM FollowUp f JOIN LoanEntry le ON f.loan_id=le.id
                     WHERE f.status='Pending'
                     ORDER BY f.follow_up_date ASC LIMIT 8""")
        rows = c.fetchall()
        lines = [f"📞 Follow-Up Summary:\n"
                 f"• Pending: {pending_n}\n"
                 f"• Due today: {today_n}\n"
                 f"• Missed (promised date passed): {missed_n}\n"
                 f"• Resolved: {resolved_n}"]
        if rows:
            lines.append("\nUpcoming follow-ups:")
            for r in rows:
                lines.append(f"• {r['loan_number']} — {_na(r['customer_name'])} on {fmt_date(r['follow_up_date'])}: {r['remarks']}")
        return "\n".join(lines)

    return None  # fall through to search


@app.route("/api/chatbot", methods=["POST"])
@login_required
@role_required("admin","superadmin")
def api_chatbot():
    data = request.get_json() or {}
    msg = (data.get("message") or "").strip()
    if not msg:
        return jsonify({"reply": "Please type something — a loan number, customer name, or ask me a question about your loans."})

    low = msg.lower().strip()
    norm = _normalize_query(low) if msg != "__greet__" else low

    intent_reply = _chatbot_intent(msg, norm)
    if intent_reply is not None:
        return jsonify({"reply": intent_reply})

    results = _chatbot_search_loans(msg)

    if not results:
        return jsonify({"reply": (
            f"🔍 I couldn't find any loan, customer, or vehicle matching \"{msg}\", "
            f"and it doesn't look like a question I recognize.\n\n"
            f"Try:\n• A loan number (e.g. LN-2026-01)\n• A customer or vehicle name\n"
            f"• \"How many loans do I have\"\n• \"Today summary\" / \"Upcoming EMI\" / \"Overdue loans\" / \"This week insights\""
        )})

    if len(results) == 1:
        reply = _chatbot_loan_summary(results[0], detailed=True)
        return jsonify({"reply": reply})

    lines = [f"🔎 Found {len(results)} matches for \"{msg}\":\n"]
    for loan in results:
        lines.append(f"• {loan['loan_number']} — {_na(loan.get('customer_name'))} ({loan['status']})")
    lines.append("\nType the exact loan number above for full details.")
    return jsonify({"reply": "\n".join(lines)})

# ── SMS Alert APIs ─────────────────────────────────────────────────────────────
@app.route("/api/sms/overdue", methods=["POST"])
@login_required
@role_required("admin","manager")
def api_sms_overdue():
    results, total = send_bulk_overdue_sms()
    return jsonify({"sent": total, "total": len(results), "results": results,
                    "sms_enabled": _sms_enabled()})

@app.route("/api/sms/upcoming", methods=["POST"])
@login_required
@role_required("admin","manager")
def api_sms_upcoming():
    results, total = send_bulk_upcoming_sms()
    return jsonify({"sent": total, "total": len(results), "results": results,
                    "sms_enabled": _sms_enabled()})

@app.route("/api/sms/single", methods=["POST"])
@login_required
@role_required("admin","manager")
def api_sms_single():
    """Send a custom SMS to one customer mobile number."""
    data   = request.get_json()
    mobile = data.get("mobile","").strip()
    msg    = data.get("message","").strip()
    if not mobile or not msg:
        return jsonify({"ok": False, "info": "Mobile and message required"})
    ok, info = _send_sms(mobile, msg)
    return jsonify({"ok": ok, "info": info, "sms_enabled": _sms_enabled()})

@app.route("/sms_test", methods=["POST"])
@login_required
@role_required("admin","manager")
def sms_test():
    """Test SMS to a specific number."""
    data   = request.get_json() or {}
    mobile = str(data.get("mobile","")).strip()
    ok, info = _send_sms(mobile, f"Test SMS from Thendralla Fincorp. Your app SMS is working! -TFC")
    return jsonify({"ok": ok, "info": info, "sms_enabled": _sms_enabled(),
                    "api_key_set": bool(_get_sms_key())})

@app.route("/sms_settings")
@login_required
@role_required("admin")
def sms_settings():
    """SMS configuration status page."""
    enabled = _sms_enabled()
    key_set = bool(SMS_CONFIG["api_key"])
    content = f"""
    <h1>📱 SMS Notification Settings</h1>

    <!-- Status Card -->
    <div class="card" style="border-left:4px solid {'var(--green)' if enabled else 'var(--red)'};">
      <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;">
        <div>
          <h2>{'✅ SMS Active' if enabled else '❌ SMS Not Configured'}</h2>
          <p style="color:var(--muted);font-size:13px;margin-top:4px;">
            {'API Key is set. SMS will be sent automatically on loan events.' if enabled else
             'Set FAST2SMS_KEY in app.py or Render environment to enable SMS.'}
          </p>
        </div>
      </div>
    </div>

    <!-- Test SMS Tool -->
    <div class="card">
      <h2>🧪 Test SMS</h2>
      <p style="color:var(--muted);font-size:13px;margin-bottom:12px;">
        Enter any 10-digit mobile number to send a test SMS and verify your setup works.
      </p>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
        <input id="test_mobile" type="tel" placeholder="10-digit mobile number"
               maxlength="10" style="max-width:220px;"
               oninput="this.value=this.value.replace(/[^0-9]/g,'').slice(0,10)">
        <button class="btn btn-primary" onclick="testSMS()">📤 Send Test SMS</button>
      </div>
      <div id="test_result" style="margin-top:12px;display:none;"></div>
    </div>

    <!-- Setup Instructions -->
    <div class="card">
      <h2>🔧 How to Set Up SMS (Fast2SMS)</h2>
      <ol style="margin-left:18px;line-height:2.2;font-size:14px;">
        <li>Go to <a href="https://www.fast2sms.com" target="_blank" style="color:var(--accent);font-weight:700;">fast2sms.com</a>
            → <b>Sign Up</b> (Free — get ₹50 credits instantly)</li>
        <li>Login → <b>Dev API</b> (left menu) → Copy the <b>API Key</b></li>
        <li>Open <b>app.py</b> → Find this line:<br>
            <code style="background:#f1f5fb;padding:4px 8px;border-radius:4px;font-size:12px;display:inline-block;margin:4px 0;">
            "api_key": os.environ.get("FAST2SMS_KEY", ""),</code><br>
            Change to:<br>
            <code style="background:#d1fae5;padding:4px 8px;border-radius:4px;font-size:12px;display:inline-block;margin:4px 0;">
            "api_key": os.environ.get("FAST2SMS_KEY", "YOUR_COPIED_API_KEY"),</code>
        </li>
        <li>Save and <b>restart the app</b></li>
        <li>Come back here → use <b>Test SMS</b> above to verify</li>
      </ol>
      <div style="background:#fef3c7;border-radius:6px;padding:10px;margin-top:8px;font-size:13px;">
        💡 <b>Route used:</b> Fast2SMS <code>v3</code> (Quick SMS — no DLT registration needed for testing).<br>
        For production/bulk, upgrade to DLT route on Fast2SMS panel.
      </div>
    </div>

    <!-- SMS Events Table -->
    <div class="card">
      <h2>📋 Automatic SMS Events</h2>
      <div class="table-wrap"><table>
        <tr><th>Event</th><th>Triggered When</th><th>Recipient</th></tr>
        <tr><td>✅ Loan Approved</td><td>Admin approves a loan</td><td>Customer mobile</td></tr>
        <tr><td>🎉 Loan Closed</td><td>All EMIs are paid</td><td>Customer mobile</td></tr>
        <tr><td>🔴 Overdue Alert</td><td>Click button on Alerts page</td><td>Overdue customers</td></tr>
        <tr><td>🟡 EMI Reminder</td><td>Click button on Alerts page (≤3 days)</td><td>Upcoming customers</td></tr>
      </table></div>
    </div>

    <script>
    function testSMS() {{
      const mob = document.getElementById('test_mobile').value.trim();
      const res = document.getElementById('test_result');
      if (mob.length !== 10) {{
        res.style.display='block';
        res.innerHTML='<div class="alert alert-danger">❌ Enter a valid 10-digit mobile number.</div>';
        return;
      }}
      res.style.display='block';
      res.innerHTML='<div class="alert alert-info">⏳ Sending test SMS to ' + mob + '…</div>';
      fetch('/sms_test', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{mobile: mob}})
      }})
      .then(r => r.json())
      .then(data => {{
        if (!data.api_key_set) {{
          res.innerHTML='<div class="alert alert-danger">❌ API Key not set in app.py. See setup instructions below.</div>';
          return;
        }}
        if (data.ok) {{
          res.innerHTML='<div class="alert alert-success">✅ ' + data.info + '<br><b>Check the mobile for the SMS!</b></div>';
        }} else {{
          res.innerHTML='<div class="alert alert-danger">❌ Failed: ' + data.info +
            '<br><small>Check: Is the API key correct? Does the number have DND? Is your Fast2SMS balance > 0?</small></div>';
        }}
      }})
      .catch(e => {{
        res.innerHTML='<div class="alert alert-danger">❌ Network error: ' + e.message + '</div>';
      }});
    }}
    </script>
    """
    return page("SMS Settings", content, "")


# ══════════════════════════════════════════════════════════════════════════════
#  EMI EDIT (Super Admin only)
# ══════════════════════════════════════════════════════════════════════════════
@app.route("/emi/edit/<int:emi_id>", methods=["GET","POST"])
@login_required
@role_required("superadmin")
def emi_edit(emi_id):
    c = get_cur()
    loan_id = request.args.get("loan_id", type=int) or request.form.get("loan_id", type=int)
    c.execute("SELECT * FROM EMI WHERE emi_id=?", (emi_id,))
    emi = dict(c.fetchone() or {})
    if not emi:
        flash("EMI not found.", "danger")
        return redirect(url_for("customers"))

    if not loan_id:
        loan_id = emi.get("loan_id")

    if request.method == "POST":
        f = request.form
        try:
            new_status = f.get("status","Pending")
            new_due    = f.get("due_date","")
            new_emi    = float(f.get("emi_amount", emi.get("emi_amount",0)))
            new_paid   = float(f.get("amount_paid", emi.get("amount_paid",0)))
            new_remain = float(f.get("remaining_amount", emi.get("remaining_amount",0)))
            new_extra  = float(f.get("extra_interest", emi.get("extra_interest",0)))
            new_bill   = f.get("bill_number","").strip()
            new_paid_at = f.get("paid_at","").strip() or None

            c.execute("""UPDATE EMI SET due_date=?,emi_amount=?,status=?,amount_paid=?,
                         remaining_amount=?,extra_interest=?,bill_number=?,paid_at=?
                         WHERE emi_id=?""",
                      (new_due, new_emi, new_status, new_paid, new_remain,
                       new_extra, new_bill, new_paid_at, emi_id))
            get_db().commit()
            flash("EMI updated successfully!", "success")
            return redirect(url_for("emis", loan_id=loan_id) + f"#emi_{emi_id}")
        except Exception as e:
            flash(f"Error: {e}", "danger")

    c.execute("SELECT loan_number, customer_name FROM LoanEntry WHERE id=?", (loan_id,))
    loan_row = c.fetchone() or {}
    status_options = ["Pending","Paid","Partial","Overdue","PreClosed","Seized"]

    payments = get_payments_for_emi(emi_id)
    inst_no = emi.get('installment_no','?')
    payment_rows = "".join(f"""<tr>
          <td><b>{inst_no}.{i+1}</b></td><td>{fmt_inr(p.get('amount') or 0)}</td>
          <td>{fmt_inr(p.get('extra_interest') or 0)}</td>
          <td>{p.get('bill_number') or '—'}</td>
          <td>{fmt_date((p.get('paid_at') or '')[:10], '')}</td>
          <td>{delay_badge(emi.get('due_date',''), (p.get('paid_at') or '')[:10]) if p.get('paid_at') else '—'}</td>
          <td>{p.get('paid_by') or '—'}</td>
        </tr>""" for i, p in enumerate(payments))
    half_date = half_paid_date(emi, payments) if payments else None
    if payments and (half_date or emi.get("status") == "Paid"):
        closed_txt = f"<b>✅ {inst_no} (Closed)</b> — " if emi.get("status") == "Paid" else ""
        half_txt = (f"½ half of the EMI crossed on <b>{fmt_date(half_date)}</b> — {delay_badge(emi.get('due_date',''), half_date)}"
                    f" — Late Payment Days: <b>{max(0, (parse_date(half_date) - parse_date(emi.get('due_date',''))).days)}</b>"
                    if half_date else "half of the EMI not crossed yet")
        payment_rows += f"""<tr>
          <td colspan="7" style="color:var(--green);">{closed_txt}{half_txt}</td>
        </tr>"""
    payment_history_card = f"""
    <div class="card" style="margin-bottom:12px;">
      <h3 style="font-size:14px;margin-bottom:10px;">💳 Payment History <span style="font-size:11px;font-weight:400;color:var(--muted);">(each payment recorded separately — Ref # = installment.payment)</span></h3>
      <div class="table-wrap"><table>
        <tr><th>Ref #</th><th>Amount</th><th>Extra Interest</th><th>Bill No</th><th>Paid On</th><th>Payment Status</th><th>Paid By</th></tr>
        {payment_rows or '<tr><td colspan="7" style="text-align:center;color:var(--muted);">No payments recorded yet for this installment.</td></tr>'}
      </table></div>
    </div>"""

    content = f"""
    <h1>✏️ Edit EMI #{emi.get('installment_no','')} — {dict(loan_row).get('loan_number','')}</h1>
    <div class="alert alert-warning">⚠️ <b>Super Admin Edit:</b> Direct database update. Use carefully.</div>
    {payment_history_card}
    <div class="card">
    <form method="POST">
      <input type="hidden" name="loan_id" value="{loan_id}">
      <div class="form-grid">
        <div class="form-group">
          <label>Installment #</label>
          <input value="{emi.get('installment_no','')}" readonly>
        </div>
        <div class="form-group">
          <label>Due Date</label>
          <input type="date" name="due_date" value="{emi.get('due_date','')}">
        </div>
        <div class="form-group">
          <label>EMI Amount (₹)</label>
          <input type="number" name="emi_amount" value="{emi.get('emi_amount',0)}" step="0.01" min="0">
        </div>
        <div class="form-group">
          <label>Amount Paid (₹)</label>
          <input type="number" name="amount_paid" value="{float(emi.get('amount_paid') or 0)}" step="0.01" min="0">
        </div>
        <div class="form-group">
          <label>Remaining Amount (₹)</label>
          <input type="number" name="remaining_amount" value="{float(emi.get('remaining_amount') or emi.get('emi_amount',0))}" step="0.01" min="0">
        </div>
        <div class="form-group">
          <label>Extra Interest (₹)</label>
          <input type="number" name="extra_interest" value="{float(emi.get('extra_interest') or 0)}" step="0.01" min="0">
        </div>
        <div class="form-group">
          <label>Status</label>
          <select name="status">
            {''.join(f'<option value="{s}" {"selected" if emi.get("status")==s else ""}>{s}</option>' for s in status_options)}
          </select>
        </div>
        <div class="form-group">
          <label>Bill Number</label>
          <input name="bill_number" value="{emi.get('bill_number') or ''}">
        </div>
        <div class="form-group">
          <label>Paid At (datetime)</label>
          <input name="paid_at" value="{(emi.get('paid_at') or '')[:19]}" placeholder="YYYY-MM-DDTHH:MM:SS">
        </div>
      </div>
      <div style="margin-top:18px;display:flex;gap:10px;flex-wrap:wrap;">
        <button type="submit" class="btn btn-primary">💾 Save EMI Changes</button>
        <a href="/emis/{loan_id}" class="btn" style="background:var(--surface2);color:var(--text);">Cancel</a>
      </div>
    </form>
    </div>"""
    return page("Edit EMI", content, "emis")


# ══════════════════════════════════════════════════════════════════════════════
#  DATABASE MANAGER (Super Admin only)
# ══════════════════════════════════════════════════════════════════════════════
DB_TABLES = ["LoanEntry","Customers","EMI","RejectedLoans","ClosedLoans","Users","FollowUp"]

@app.route("/database")
@login_required
@role_required("superadmin")
def database_view():
    tbl = request.args.get("table", DB_TABLES[0])
    if tbl not in DB_TABLES:
        tbl = DB_TABLES[0]

    c = get_cur()
    # Get columns
    c.execute(f"PRAGMA table_info({tbl})")
    cols = [r[1] if isinstance(r, (list,tuple)) else r["name"] for r in c.fetchall()]

    # Get rows
    search = request.args.get("q","")
    if search and cols:
        like_clause = " OR ".join([f"{col} LIKE ?" for col in cols])
        params = tuple(f"%{search}%" for _ in cols)
        c.execute(f"SELECT * FROM {tbl} WHERE {like_clause} ORDER BY rowid DESC LIMIT 500", params)
    else:
        c.execute(f"SELECT * FROM {tbl} ORDER BY rowid DESC LIMIT 500")
    rows = c.fetchall()

    # Build table counts
    table_counts = {}
    for t in DB_TABLES:
        try:
            c.execute(f"SELECT COUNT(*) FROM {t}")
            r = c.fetchone()
            table_counts[t] = r[0] if isinstance(r,(list,tuple)) else list(r.values())[0]
        except:
            table_counts[t] = "?"

    # Table tabs
    tab_html = ""
    for t in DB_TABLES:
        active_cls = "btn-primary" if t==tbl else ""
        tab_html += f'<a href="/database?table={t}" class="btn btn-sm {active_cls}" style="{"" if t==tbl else "background:var(--surface2);color:var(--text);"}">{t} <span style="font-size:11px;opacity:.7;">({table_counts.get(t,0)})</span></a>'

    # Header row
    th_html = "".join(f"<th>{col}</th>" for col in cols) + "<th>Actions</th>"

    # Data rows
    tr_html = ""
    pk_col = cols[0] if cols else "rowid"
    for row in rows:
        row_dict = dict(zip(cols, [row[i] if isinstance(row,(list,tuple)) else row[col] for i,col in enumerate(cols)]))
        pk_val = row_dict.get(pk_col,"")
        td_html = "".join(f'<td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="{str(row_dict.get(col,"") or "")}">{str(row_dict.get(col,"") or "")}</td>' for col in cols)
        tr_html += f"""<tr>
            {td_html}
            <td style="white-space:nowrap;">
              <a class="btn btn-sm btn-amber" href="/database/edit/{tbl}/{pk_val}">✏️ Edit</a>
              <a class="btn btn-sm btn-danger" href="/database/delete/{tbl}/{pk_val}"
                 onclick="return confirm('Delete this row permanently?')">🗑️</a>
            </td>
        </tr>"""

    if not tr_html:
        tr_html = f'<tr><td colspan="{len(cols)+1}" style="text-align:center;color:var(--muted);">No data found</td></tr>'

    content = f"""
    <h1>🗄️ Database Manager</h1>
    <div class="alert alert-warning">⚠️ <b>Super Admin Only:</b> Direct database access. All changes are permanent and immediate.</div>

    <!-- Table Selector -->
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px;">
      {tab_html}
    </div>

    <!-- Download Buttons -->
    <div class="card" style="margin-bottom:16px;">
      <h2 style="margin-bottom:10px;">📥 Download Database</h2>
      <div style="display:flex;gap:8px;flex-wrap:wrap;">
        <a href="/database/download/db" class="btn btn-primary">⬇️ SQLite .db File</a>
        <a href="/database/download/sql" class="btn btn-primary">⬇️ SQL Dump</a>
        <a href="/database/download/zip" class="btn btn-primary">⬇️ All CSVs as ZIP</a>
        <a href="/database/download/csv?table={tbl}" class="btn btn-success">⬇️ Current Table CSV ({tbl})</a>
      </div>
    </div>

    <!-- Search + Table Data -->
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:12px;">
        <h2 style="margin:0;">📋 {tbl} <span style="font-size:13px;color:var(--muted);font-weight:400;">({table_counts.get(tbl,0)} rows)</span></h2>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
          <form method="GET" style="display:flex;gap:6px;align-items:center;">
            <input type="hidden" name="table" value="{tbl}">
            <input name="q" value="{search}" placeholder="Search all columns…" style="width:200px;">
            <button class="btn btn-sm btn-primary">Search</button>
          </form>
          <a href="/database/add/{tbl}" class="btn btn-sm btn-success">➕ Add Row</a>
        </div>
      </div>
      <div class="table-wrap">
        <table>
          <tr>{th_html}</tr>
          {tr_html}
        </table>
      </div>
      <p style="font-size:11px;color:var(--muted);margin-top:8px;">Showing up to 500 rows. Use search to filter.</p>
    </div>"""
    return page("Database", content, "database")


@app.route("/database/edit/<table>/<pk>", methods=["GET","POST"])
@login_required
@role_required("superadmin")
def database_edit_row(table, pk):
    if table not in DB_TABLES:
        flash("Invalid table.","danger"); return redirect(url_for("database_view"))
    c = get_cur()
    c.execute(f"PRAGMA table_info({table})")
    col_info = c.fetchall()
    cols = [r[1] if isinstance(r,(list,tuple)) else r["name"] for r in col_info]
    pk_col = cols[0] if cols else "rowid"

    c.execute(f"SELECT * FROM {table} WHERE {pk_col}=?", (pk,))
    row = c.fetchone()
    if not row:
        flash("Row not found.","danger"); return redirect(url_for("database_view", table=table))
    row_dict = dict(zip(cols, [row[i] if isinstance(row,(list,tuple)) else row[col] for i,col in enumerate(cols)]))

    if request.method == "POST":
        f = request.form
        updates = [f"{col}=?" for col in cols if col != pk_col]
        vals = [f.get(col,"") for col in cols if col != pk_col]
        vals.append(pk)
        try:
            c.execute(f"UPDATE {table} SET {', '.join(updates)} WHERE {pk_col}=?", vals)
            get_db().commit()
            # ── Cascade: if editing LoanEntry, sync Customers table too ──
            if table == "LoanEntry":
                loan_id = pk
                # Recalculate EMI if loan_amount, interest_rate, tenure or custom_emi_amount changed
                new_amt    = f.get("loan_amount","")
                new_rate   = f.get("interest_rate","")
                new_tenure = f.get("tenure","")
                new_custom = f.get("custom_emi_amount","")
                new_name   = f.get("customer_name","")
                new_vtype  = f.get("vehicle_type","")
                try:
                    amt    = float(new_amt)
                    rate   = float(new_rate)
                    tenure = int(new_tenure)
                    interest = amt * (rate/100) * (tenure/12)
                    total    = amt + interest
                    if new_custom and float(new_custom) > 0:
                        emi = float(new_custom)
                    else:
                        emi = total / tenure
                    c2 = get_cur()
                    c2.execute("""UPDATE Customers SET name=?,vehicle_type=?,loan_amount=?,emi_amount=?
                                  WHERE loan_id=?""",
                               (new_name, new_vtype, amt, round(emi,2), loan_id))
                    get_db().commit()
                except: pass
            flash(f"Row updated in {table}.", "success")
            return redirect(url_for("database_view", table=table))
        except Exception as e:
            flash(f"Error: {e}", "danger")

    fields_html = ""
    for col in cols:
        readonly = ' readonly style="background:var(--surface2);color:var(--muted);"' if col == pk_col else ""
        val = str(row_dict.get(col,"") or "")
        fields_html += f"""<div class="form-group">
          <label>{col}</label>
          <input name="{col}" value="{val.replace('"','&quot;')}"{readonly}>
        </div>"""

    content = f"""
    <h1>✏️ Edit Row — {table}</h1>
    <div class="alert alert-warning">⚠️ <b>Super Admin:</b> Direct database row edit.</div>
    <div class="card">
      <form method="POST">
        <div class="form-grid">{fields_html}</div>
        <div style="margin-top:18px;display:flex;gap:10px;">
          <button type="submit" class="btn btn-primary">💾 Save</button>
          <a href="/database?table={table}" class="btn" style="background:var(--surface2);color:var(--text);">Cancel</a>
        </div>
      </form>
    </div>"""
    return page("Edit DB Row", content, "database")


@app.route("/database/add/<table>", methods=["GET","POST"])
@login_required
@role_required("superadmin")
def database_add_row(table):
    if table not in DB_TABLES:
        flash("Invalid table.","danger"); return redirect(url_for("database_view"))

    # For LoanEntry, redirect superadmin to the proper loan entry form
    if table == "LoanEntry":
        flash("ℹ️ Use the New Loan form below to add a loan — it handles calculations, EMI generation, and approvals correctly.","info")
        return redirect(url_for("add_loan"))

    c = get_cur()
    c.execute(f"PRAGMA table_info({table})")
    col_info = c.fetchall()
    cols = [r[1] if isinstance(r,(list,tuple)) else r["name"] for r in col_info]
    pk_col = cols[0] if cols else "rowid"

    if request.method == "POST":
        f = request.form
        insert_cols = [col for col in cols if col != pk_col]
        vals = [f.get(col,"") or None for col in insert_cols]
        placeholders = ",".join(["?" for _ in insert_cols])
        try:
            c.execute(f"INSERT INTO {table} ({','.join(insert_cols)}) VALUES ({placeholders})", vals)
            get_db().commit()
            # Cascade: if adding a Customers row, nothing extra needed
            flash(f"Row added to {table}.", "success")
            return redirect(url_for("database_view", table=table))
        except Exception as e:
            flash(f"Error: {e}", "danger")

    # Build smart form — highlight auto fields
    fields_html = ""
    auto_cols = {"created_at","status","paid_at"}
    for col in cols:
        if col == pk_col: continue
        hint = ""
        default_val = ""
        if col in auto_cols:
            hint = f' <span style="font-size:10px;color:var(--muted);">(auto-filled by app)</span>'
        if col == "status":
            default_val = "Pending"
        if col == "created_at":
            from datetime import datetime, timezone
            default_val = datetime.now(timezone.utc).isoformat()
        fields_html += f"""<div class="form-group">
          <label>{col}{hint}</label>
          <input name="{col}" value="{default_val}">
        </div>"""

    content = f"""
    <h1>➕ Add Row — {table}</h1>
    <div class="alert alert-warning">⚠️ <b>Direct DB insert.</b> No business logic runs — EMI schedule will NOT be auto-generated here. Only use for reference/lookup tables.</div>
    <div class="card">
      <form method="POST">
        <div class="form-grid">{fields_html}</div>
        <div style="margin-top:18px;display:flex;gap:10px;">
          <button type="submit" class="btn btn-success">➕ Insert Row</button>
          <a href="/database?table={table}" class="btn" style="background:var(--surface2);color:var(--text);">Cancel</a>
        </div>
      </form>
    </div>"""
    return page("Add DB Row", content, "database")


@app.route("/database/delete/<table>/<pk>")
@login_required
@role_required("superadmin")
def database_delete_row(table, pk):
    if table not in DB_TABLES:
        flash("Invalid table.","danger"); return redirect(url_for("database_view"))
    c = get_cur()
    c.execute(f"PRAGMA table_info({table})")
    col_info = c.fetchall()
    cols = [r[1] if isinstance(r,(list,tuple)) else r["name"] for r in col_info]
    pk_col = cols[0] if cols else "rowid"
    try:
        c.execute(f"DELETE FROM {table} WHERE {pk_col}=?", (pk,))
        get_db().commit()
        flash(f"Row deleted from {table}.", "success")
    except Exception as e:
        flash(f"Error: {e}", "danger")
    return redirect(url_for("database_view", table=table))


@app.route("/database/download/<fmt>")
@login_required
@role_required("superadmin")
def database_download(fmt):
    if fmt == "db":
        # Send the raw SQLite file
        if TURSO_URL:
            flash("Turso (cloud) DB: direct .db download not available. Use SQL dump instead.","warning")
            return redirect(url_for("database_view"))
        return send_file(DB_FILE, as_attachment=True,
                         download_name="vehicle_loans.db",
                         mimetype="application/octet-stream")

    elif fmt == "sql":
        # Generate SQL dump
        buf = io.StringIO()
        if not TURSO_URL:
            import sqlite3 as _sq
            conn2 = _sq.connect(DB_FILE)
            for line in conn2.iterdump():
                buf.write(line + "\n")
            conn2.close()
        else:
            # Turso — dump via SELECT
            buf.write("-- SQL Dump (Turso cloud DB)\n")
            c = get_cur()
            for tbl in DB_TABLES:
                try:
                    c.execute(f"PRAGMA table_info({tbl})")
                    col_info = c.fetchall()
                    cols = [r[1] if isinstance(r,(list,tuple)) else r["name"] for r in col_info]
                    c.execute(f"SELECT * FROM {tbl}")
                    rows = c.fetchall()
                    buf.write(f"\n-- Table: {tbl}\n")
                    for row in rows:
                        vals = [row[i] if isinstance(row,(list,tuple)) else row[col] for i,col in enumerate(cols)]
                        escaped = ["NULL" if v is None else f"'{str(v).replace(chr(39), chr(39)+chr(39))}'" for v in vals]
                        buf.write(f"INSERT INTO {tbl} ({','.join(cols)}) VALUES ({','.join(escaped)});\n")
                except: pass
        sql_bytes = buf.getvalue().encode("utf-8")
        return send_file(io.BytesIO(sql_bytes), as_attachment=True,
                         download_name="vehicle_loans_dump.sql",
                         mimetype="text/plain")

    elif fmt == "zip":
        # All tables as CSVs in a ZIP
        zip_buf = io.BytesIO()
        c = get_cur()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for tbl in DB_TABLES:
                try:
                    c.execute(f"PRAGMA table_info({tbl})")
                    col_info = c.fetchall()
                    cols = [r[1] if isinstance(r,(list,tuple)) else r["name"] for r in col_info]
                    c.execute(f"SELECT * FROM {tbl}")
                    rows = c.fetchall()
                    csv_buf = io.StringIO()
                    writer = csv.writer(csv_buf)
                    writer.writerow(cols)
                    for row in rows:
                        vals = [row[i] if isinstance(row,(list,tuple)) else row[col] for i,col in enumerate(cols)]
                        writer.writerow(vals)
                    zf.writestr(f"{tbl}.csv", csv_buf.getvalue())
                except: pass
        zip_buf.seek(0)
        return send_file(zip_buf, as_attachment=True,
                         download_name="vehicle_loans_all_tables.zip",
                         mimetype="application/zip")

    elif fmt == "csv":
        tbl = request.args.get("table", DB_TABLES[0])
        if tbl not in DB_TABLES: tbl = DB_TABLES[0]
        c = get_cur()
        c.execute(f"PRAGMA table_info({tbl})")
        col_info = c.fetchall()
        cols = [r[1] if isinstance(r,(list,tuple)) else r["name"] for r in col_info]
        c.execute(f"SELECT * FROM {tbl}")
        rows = c.fetchall()
        csv_buf = io.StringIO()
        writer = csv.writer(csv_buf)
        writer.writerow(cols)
        for row in rows:
            vals = [row[i] if isinstance(row,(list,tuple)) else row[col] for i,col in enumerate(cols)]
            writer.writerow(vals)
        return send_file(io.BytesIO(csv_buf.getvalue().encode("utf-8")),
                         as_attachment=True,
                         download_name=f"{tbl}.csv",
                         mimetype="text/csv")

    flash("Unknown format.", "danger")
    return redirect(url_for("database_view"))


# ── Users ──────────────────────────────────────────────────────────────────────
init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
