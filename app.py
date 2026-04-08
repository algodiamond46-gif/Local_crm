from __future__ import annotations
import csv, io, json, os, sqlite3, re
from contextlib import closing
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any
from flask import (Flask, g, jsonify, redirect, render_template, request,
                   url_for, flash, abort)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "crm.db"

app = Flask(__name__)
app.config["SECRET_KEY"] = "diamond-local-crm-2026"

import logging
logging.basicConfig(level=logging.INFO)

@app.errorhandler(500)
def handle_500(e):
    app.logger.error(f"500 error: {e}")
    return f"<h1>שגיאה</h1><pre>{e}</pre>", 500

# ── Constants ───────────────────────────────────────────────────────
PIPELINE_STAGES = [
    "ליד חדש", "ניסיון קשר", "שיחה ראשונה",
    "מתעניין", "הצעה נשלחה", "נסגר", "לא נסגר",
]
INTEREST_LEVELS = ["נמוכה", "בינונית", "גבוהה"]
SOURCES = ["Facebook", "Instagram", "אתר", "וואטסאפ", "טלפון", "הפניה", "אחר"]
BUDGET_OPTIONS = ["עד ₪3,000", "₪3,000–₪7,000", "₪7,000–₪13,000", "מעל ₪13,000"]
READINESS_OPTIONS = ["מיד — אני מוכן היום", "השבוע", "רק מתעניין בשלב הזה"]
EXPERIENCE_OPTIONS = ["אין", "בסיסי", "מנוסה"]
PACKAGES = [
    {"id": "basic", "name": "סטארט", "price": 3900},
    {"id": "pro", "name": "פרו", "price": 6900},
    {"id": "vip", "name": "VIP", "price": 12900},
    {"id": "custom", "name": "מותאם אישית", "price": 0},
]
PAYMENT_METHODS = ["העברה בנקאית", "אשראי", "PayBox", "ביט", "מזומן", "אחר"]
DEBT_STATUSES = ["ממתין", "שולם חלקית", "שולם", "באיחור"]

# ── Interactive Sales Script ────────────────────────────────────────
SCRIPT_TREE = {
    "start": {
        "say": "היי {name}, פה {agent} מ-Diamond Algo. ראיתי שנרשמת לקבל פרטים על מערכת המסחר האוטומטי שלנו. יש לך רגע?",
        "options": [
            {"label": "כן, מעוניין", "next": "interested", "note": "מעוניין, פתוח לשמוע"},
            {"label": "מה זה בדיוק?", "next": "explain", "note": "צריך הסבר, לא מכיר"},
            {"label": "אין לי זמן עכשיו", "next": "reschedule", "note": "ביקש לחזור אליו"},
            {"label": "לא מעוניין", "next": "objection_no", "note": "סירב בפתיחה"},
        ]
    },
    "explain": {
        "say": "בקצרה — אנחנו מספקים כלי מסחר אוטומטי שעובד 24/7 בשוק ההון. הבוט מבצע עסקאות לפי אלגוריתמים מוכחים, בלי שאתה צריך לשבת מול המסך. זה מתאים גם למי שאין לו ניסיון. מעניין אותך לשמוע עוד?",
        "options": [
            {"label": "כן, ספר עוד", "next": "interested", "note": "התעניין אחרי הסבר"},
            {"label": "כמה זה עולה?", "next": "pricing", "note": "שאל על מחיר — אות חיובי"},
            {"label": "נשמע טוב מדי", "next": "objection_scam", "note": "חשדן, צריך להרגיע"},
            {"label": "לא, תודה", "next": "soft_close", "note": "סירב אחרי הסבר"},
        ]
    },
    "interested": {
        "say": "מעולה! אני רוצה להבין מה המצב שלך כדי שאתאים לך את הפתרון הכי מדויק. מה הניסיון שלך בשוק ההון?",
        "options": [
            {"label": "אין לי ניסיון", "next": "budget_q", "note": "אין ניסיון במסחר", "tag": "exp:אין"},
            {"label": "קצת, בסיסי", "next": "budget_q", "note": "ניסיון בסיסי", "tag": "exp:בסיסי"},
            {"label": "מנוסה, סוחר כמה שנים", "next": "budget_q", "note": "מנוסה במסחר", "tag": "exp:מנוסה"},
        ]
    },
    "budget_q": {
        "say": "מה התקציב שאתה חושב להשקיע בכלי מסחר מקצועי?",
        "options": [
            {"label": "עד 3,000 ₪", "next": "timeline_q", "note": "תקציב: עד 3K", "tag": "budget:עד ₪3,000"},
            {"label": "3,000–7,000 ₪", "next": "timeline_q", "note": "תקציב: 3-7K", "tag": "budget:₪3,000–₪7,000"},
            {"label": "7,000–13,000 ₪", "next": "timeline_q", "note": "תקציב: 7-13K", "tag": "budget:₪7,000–₪13,000"},
            {"label": "מעל 13,000 ₪", "next": "timeline_q", "note": "תקציב: 13K+", "tag": "budget:מעל ₪13,000"},
        ]
    },
    "timeline_q": {
        "say": "מתי היית רוצה להתחיל?",
        "options": [
            {"label": "מיד, אני מוכן", "next": "close_hot", "note": "רוצה להתחיל מיד!", "tag": "ready:מיד"},
            {"label": "השבוע", "next": "close_warm", "note": "רוצה להתחיל השבוע", "tag": "ready:השבוע"},
            {"label": "צריך לחשוב על זה", "next": "objection_think", "note": "צריך לחשוב", "tag": "ready:לא בטוח"},
        ]
    },
    "pricing": {
        "say": "יש 3 מסלולים: סטארט ב-₪3,900 — כולל גישה מלאה לכלי + קורס, פרו ב-₪6,900 — כולל גם ליווי אישי ושיחות זום, ו-VIP ב-₪12,900 — ליווי צמוד ל-3 חודשים עם מנטור אישי. רוב הלקוחות הולכים על פרו. איזה מסלול מדבר אליך?",
        "options": [
            {"label": "סטארט נשמע טוב", "next": "close_warm", "note": "מתעניין בחבילת סטארט", "tag": "pkg:basic"},
            {"label": "פרו מעניין", "next": "close_hot", "note": "מתעניין בחבילת פרו!", "tag": "pkg:pro"},
            {"label": "VIP", "next": "close_hot", "note": "מתעניין ב-VIP!", "tag": "pkg:vip"},
            {"label": "יקר לי", "next": "objection_price", "note": "התנגדות מחיר"},
            {"label": "צריך לחשוב", "next": "objection_think", "note": "רוצה לחשוב"},
        ]
    },
    "objection_price": {
        "say": "אני מבין. תחשוב על זה ככה — הכלי עובד 24/7 ויכול להחזיר את ההשקעה תוך חודש-חודשיים. זה לא הוצאה, זה השקעה בעסק שעובד בשבילך. יש גם אפשרות לתשלומים. מה אם נתחיל עם סטארט ותראה תוצאות?",
        "options": [
            {"label": "אוקיי, בוא ננסה", "next": "close_warm", "note": "הסכים אחרי התנגדות מחיר ✅"},
            {"label": "יש תשלומים?", "next": "installments", "note": "מעוניין בתשלומים"},
            {"label": "עדיין יקר", "next": "soft_close", "note": "עדיין יקר לו, לחזור אליו"},
        ]
    },
    "installments": {
        "say": "בטח! אפשר לפרוס עד 3 תשלומים בלי ריבית. ככה זה יוצא סביר מאוד. רוצה שנסגור?",
        "options": [
            {"label": "כן, בוא נסגור", "next": "close_hot", "note": "סגר עם תשלומים! ✅"},
            {"label": "צריך לחשוב", "next": "objection_think", "note": "רוצה לחשוב גם עם תשלומים"},
        ]
    },
    "objection_think": {
        "say": "לגמרי, אני מבין שזה החלטה. רק שים לב — יש לנו מבצע שנגמר בסוף השבוע. מה הדבר העיקרי שמעכב אותך? אולי אני יכול לעזור.",
        "options": [
            {"label": "אני צריך לדבר עם אשתי/בן זוג", "next": "spouse", "note": "צריך אישור בן/בת זוג"},
            {"label": "אני לא בטוח שזה עובד", "next": "objection_scam", "note": "לא בטוח שזה עובד"},
            {"label": "אוקיי, בוא נסגור", "next": "close_hot", "note": "סגר! הפרגון של הדחיפות עבד ✅"},
            {"label": "לא, אחזור אליך", "next": "soft_close", "note": "ביקש לחזור, follow up"},
        ]
    },
    "spouse": {
        "say": "מובן לגמרי. מה אם אני שולח לך סרטון קצר שמסביר על המערכת, ככה תוכל להראות לבן/בת הזוג? ואני אחזור אליך מחר לבדוק.",
        "options": [
            {"label": "כן, שלח לי", "next": "followup_set", "note": "לשלוח סרטון + follow up מחר"},
            {"label": "אני אחזור בעצמי", "next": "soft_close", "note": "אמר שיחזור, follow up עוד יומיים"},
        ]
    },
    "objection_scam": {
        "say": "שאלה לגיטימית. אנחנו עובדים עם מעל 50 לקוחות פעילים, אפשר לראות תוצאות בזמן אמת. אני יכול לשלוח לך צילומי מסך של תוצאות מלקוחות — בלי שמות כמובן. גם יש לנו ערבות החזר כספי ל-30 יום.",
        "options": [
            {"label": "30 יום ערבות? אוקיי", "next": "close_warm", "note": "הערבות שכנעה אותו ✅"},
            {"label": "שלח לי הוכחות", "next": "followup_set", "note": "לשלוח הוכחות + follow up"},
            {"label": "עדיין לא בטוח", "next": "soft_close", "note": "חשדן, לנסות שוב בעוד שבוע"},
        ]
    },
    "objection_no": {
        "say": "אוקיי, בסדר גמור. רק מתוך סקרנות — מה גרם לך להירשם מלכתחילה?",
        "options": [
            {"label": "סתם התעניינתי", "next": "soft_close", "note": "רק סקרנות, ליד קר"},
            {"label": "רציתי הכנסה נוספת", "next": "explain", "note": "רוצה הכנסה נוספת — לנסות שוב!"},
            {"label": "סגור", "next": "dead", "note": "לא מעוניין בכלל"},
        ]
    },
    "reschedule": {
        "say": "לא נורא! מתי הכי נוח לדבר?",
        "options": [
            {"label": "היום בערב", "next": "followup_set", "note": "לחזור היום בערב", "tag": "fu:היום"},
            {"label": "מחר", "next": "followup_set", "note": "לחזור מחר", "tag": "fu:מחר"},
            {"label": "תתקשר בשבוע הבא", "next": "followup_set", "note": "follow up שבוע הבא", "tag": "fu:שבוע"},
        ]
    },
    "close_hot": {
        "say": "🔥 מעולה! בוא נסגור את זה. אני שולח לך עכשיו לינק לתשלום + אני מתאם איתך שיחת התקנה. מה השעה הכי נוחה לך?",
        "options": [
            {"label": "סגר! ✅", "next": "done_won", "note": "סגירה! 🎉"},
            {"label": "ביקש לחשוב עוד", "next": "soft_close", "note": "התקרר ברגע האחרון"},
        ],
        "is_closing": True
    },
    "close_warm": {
        "say": "אחלה. אני שולח לך עכשיו את כל הפרטים בוואטסאפ כולל לינק לתשלום. יש שאלות אחרונות?",
        "options": [
            {"label": "לא, הכל ברור — סוגר", "next": "done_won", "note": "סגירה! 🎉"},
            {"label": "יש עוד שאלות", "next": "pricing", "note": "עוד שאלות לפני סגירה"},
            {"label": "אחזור אליך", "next": "soft_close", "note": "עדיין מתלבט"},
        ],
        "is_closing": True
    },
    "followup_set": {
        "say": "מעולה, רשמתי. אני חוזר אליך. שיהיה לך יום טוב! 💪",
        "options": [
            {"label": "סיום שיחה ✅", "next": "done_followup", "note": "נקבע follow up"},
        ],
        "is_end": True
    },
    "soft_close": {
        "say": "בסדר, אני שולח לך הודעה בוואטסאפ עם כל המידע. אם תהיה לך שאלה — אני כאן. שיהיה טוב!",
        "options": [
            {"label": "סיום שיחה ✅", "next": "done_followup", "note": "סיום רך, follow up בעוד יומיים"},
        ],
        "is_end": True
    },
    "dead": {
        "say": "תודה על הזמן, {name}. אם תשנה דעה — אני כאן. בהצלחה!",
        "options": [
            {"label": "סיום ✅", "next": "done_dead", "note": "ליד מת"},
        ],
        "is_end": True
    },
    "done_won": {"say": "🎉 סגירה מוצלחת!", "is_final": True, "result": "won"},
    "done_followup": {"say": "📋 נקבע follow up", "is_final": True, "result": "followup"},
    "done_dead": {"say": "❌ ליד נסגר", "is_final": True, "result": "dead"},
}

# ── WhatsApp Message Templates ──────────────────────────────────────
WA_TEMPLATES = {
    "intro": "היי {name}! 👋\nפה Diamond Algo.\nראיתי שנרשמת לקבל פרטים על מערכת המסחר האוטומטי שלנו.\n\nיש לך רגע לשיחה קצרה? 📞",
    "followup": "היי {name}! 👋\nדיברנו לפני כמה ימים על Diamond Algo.\nרציתי לבדוק — חשבת על זה?\n\nאם יש שאלות, אני כאן 🙂",
    "payment": "היי {name}! 🎉\nשמח שהחלטת להצטרף ל-Diamond Algo!\n\nהנה הלינק לתשלום: [לינק]\n\nאחרי התשלום אני מתאם איתך שיחת התקנה.\nשאלות? אני כאן 💪",
    "debt": "היי {name},\nרציתי להזכיר שיש יתרה פתוחה של ₪{amount}.\n\nאשמח אם תוכל/י להסדיר 🙏\nתודה!",
    "post_call": "היי {name}! 👋\nתודה על השיחה היום.\n\n{summary}\n\nאם תהיה שאלה — אני כאן.\nDiamond Algo 💎",
}

# ── Lead Classification ─────────────────────────────────────────────
HEAT_ORDER = {"🔥 חם": 0, "🟡 פושר": 1, "❄️ קר": 2}

def classify_lead(budget, readiness, experience):
    is_cold_ready = readiness == "רק מתעניין בשלב הזה"
    hot_budget = budget in ("₪3,000–₪7,000", "₪7,000–₪13,000", "מעל ₪13,000")
    hot_ready = readiness == "מיד — אני מוכן היום"
    experienced = experience == "מנוסה"
    if is_cold_ready and not hot_budget:
        return "❄️ קר", "נמוכה", "ליד חדש"
    if hot_budget and hot_ready:
        return "🔥 חם", "גבוהה", "מתעניין"
    if hot_budget and experienced:
        return "🔥 חם", "גבוהה", "מתעניין"
    if hot_ready and experienced:
        return "🔥 חם", "גבוהה", "ניסיון קשר"
    if hot_budget and is_cold_ready:
        return "🟡 פושר", "בינונית", "ניסיון קשר"
    return "🟡 פושר", "בינונית", "ניסיון קשר"


# ── Database ─────────────────────────────────────────────────────────
def get_db():
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db

@app.teardown_appcontext
def close_db(_):
    db = g.pop("db", None)
    if db: db.close()

def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, phone TEXT, email TEXT,
            source TEXT DEFAULT 'אתר',
            stage TEXT DEFAULT 'ליד חדש',
            interest_level TEXT DEFAULT 'בינונית',
            notes TEXT DEFAULT '', call_summary TEXT DEFAULT '',
            follow_up_date TEXT DEFAULT '', follow_up_time TEXT DEFAULT '',
            last_contact_date TEXT DEFAULT '', closed_status TEXT DEFAULT '',
            budget TEXT DEFAULT '', readiness TEXT DEFAULT '',
            experience TEXT DEFAULT '', heat_label TEXT DEFAULT '',
            goal TEXT DEFAULT '', meta_id TEXT DEFAULT '',
            lead_date TEXT DEFAULT '', package TEXT DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS interactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL, interaction_type TEXT DEFAULT 'שיחה',
            summary TEXT NOT NULL, created_at TEXT NOT NULL,
            FOREIGN KEY (lead_id) REFERENCES leads(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL,
            amount REAL NOT NULL, method TEXT DEFAULT '',
            notes TEXT DEFAULT '', payment_date TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (lead_id) REFERENCES leads(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS debts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL,
            amount REAL NOT NULL, paid REAL DEFAULT 0,
            due_date TEXT DEFAULT '', status TEXT DEFAULT 'ממתין',
            notes TEXT DEFAULT '', created_at TEXT NOT NULL,
            FOREIGN KEY (lead_id) REFERENCES leads(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            description TEXT NOT NULL, category TEXT DEFAULT 'כללי',
            amount REAL NOT NULL, expense_date TEXT NOT NULL,
            notes TEXT DEFAULT '', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """)
    # migrate leads
    cols = {r[1] for r in db.execute("PRAGMA table_info(leads)").fetchall()}
    for col, td in {"budget":"TEXT DEFAULT ''","readiness":"TEXT DEFAULT ''",
                     "experience":"TEXT DEFAULT ''","heat_label":"TEXT DEFAULT ''",
                     "goal":"TEXT DEFAULT ''","meta_id":"TEXT DEFAULT ''",
                     "lead_date":"TEXT DEFAULT ''","package":"TEXT DEFAULT ''"}.items():
        if col not in cols:
            db.execute(f"ALTER TABLE leads ADD COLUMN {col} {td}")
    db.commit(); db.close()

def now_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
def today_iso():
    return date.today().isoformat()

def fetchall(q, p=()):
    return get_db().execute(q, p).fetchall()
def fetchone(q, p=()):
    return get_db().execute(q, p).fetchone()
def execute(q, p=()):
    db = get_db(); cur = db.execute(q, p); db.commit(); return cur.lastrowid

LEAD_COLS = ("name,phone,email,source,stage,interest_level,notes,call_summary,"
             "follow_up_date,follow_up_time,last_contact_date,closed_status,"
             "budget,readiness,experience,heat_label,goal,meta_id,lead_date,package,"
             "created_at,updated_at")
INSERT_SQL = f"INSERT INTO leads ({LEAD_COLS}) VALUES ({','.join('?' for _ in LEAD_COLS.split(','))})"


# ══════════════════════════════════════════════════════════════════════
#  ROUTES — DASHBOARD
# ══════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    leads = fetchall("SELECT * FROM leads ORDER BY datetime(created_at) DESC")
    grouped = {s: [] for s in PIPELINE_STAGES}
    for l in leads:
        grouped.setdefault(l["stage"], []).append(l)

    due = fetchall("SELECT * FROM leads WHERE follow_up_date != '' AND stage NOT IN ('נסגר','לא נסגר') ORDER BY follow_up_date, follow_up_time")

    # Monthly revenue
    month_start = date.today().replace(day=1).isoformat()
    revenue_row = fetchone("SELECT COALESCE(SUM(amount),0) as total FROM payments WHERE payment_date >= ?", (month_start,))
    monthly_revenue = revenue_row["total"] if revenue_row else 0

    total_debt = fetchone("SELECT COALESCE(SUM(amount - paid),0) as total FROM debts WHERE status != 'שולם'")
    open_debt = total_debt["total"] if total_debt else 0

    counts = {
        "all": len(leads),
        "won": sum(1 for l in leads if l["stage"]=="נסגר"),
        "lost": sum(1 for l in leads if l["stage"]=="לא נסגר"),
        "hot": sum(1 for l in leads if _is_hot(l)),
        "revenue": monthly_revenue,
        "debt": open_debt,
    }
    return render_template("index.html", grouped=grouped, stages=PIPELINE_STAGES,
                           counts=counts, due_today=due, leads=leads)

def _is_hot(l):
    try: return l["heat_label"] == "🔥 חם"
    except: return l["interest_level"] == "גבוהה"


# ══════════════════════════════════════════════════════════════════════
#  ROUTES — LEADS CRUD
# ══════════════════════════════════════════════════════════════════════

@app.route("/lead/new", methods=["GET","POST"])
def lead_new():
    if request.method == "POST":
        f = request.form; ts = now_iso()
        budget=f.get("budget",""); readiness=f.get("readiness",""); experience=f.get("experience","")
        heat_label, interest, stage = classify_lead(budget, readiness, experience)
        if f.get("interest_level"): interest = f["interest_level"]
        if f.get("stage"): stage = f["stage"]
        execute(INSERT_SQL, (
            f.get("name","").strip() or "ללא שם", f.get("phone","").strip(),
            f.get("email","").strip(), f.get("source","אתר"), stage, interest,
            f.get("notes","").strip(), f.get("call_summary","").strip(),
            f.get("follow_up_date",""), f.get("follow_up_time",""),
            f.get("last_contact_date",""), f.get("closed_status",""),
            budget, readiness, experience, heat_label, "", "", "",
            f.get("package",""), ts, ts))
        flash("הליד נשמר בהצלחה")
        return redirect(url_for("index"))
    return render_template("lead_form.html", lead=None, stages=PIPELINE_STAGES,
        interest_levels=INTEREST_LEVELS, sources=SOURCES,
        budget_options=BUDGET_OPTIONS, readiness_options=READINESS_OPTIONS,
        experience_options=EXPERIENCE_OPTIONS, packages=PACKAGES)

@app.route("/lead/<int:lid>")
def lead_detail(lid):
    lead = fetchone("SELECT * FROM leads WHERE id=?", (lid,))
    if not lead: flash("הליד לא נמצא"); return redirect(url_for("index"))
    interactions = fetchall("SELECT * FROM interactions WHERE lead_id=? ORDER BY datetime(created_at) DESC", (lid,))
    payments = fetchall("SELECT * FROM payments WHERE lead_id=? ORDER BY payment_date DESC", (lid,))
    debts = fetchall("SELECT * FROM debts WHERE lead_id=? ORDER BY due_date", (lid,))
    paid_total = sum(p["amount"] for p in payments)
    return render_template("lead_detail.html", lead=lead, interactions=interactions,
        payments=payments, debts=debts, paid_total=paid_total,
        stages=PIPELINE_STAGES, interest_levels=INTEREST_LEVELS, sources=SOURCES,
        budget_options=BUDGET_OPTIONS, readiness_options=READINESS_OPTIONS,
        experience_options=EXPERIENCE_OPTIONS, packages=PACKAGES,
        payment_methods=PAYMENT_METHODS, wa_templates=WA_TEMPLATES,
        debt_statuses=DEBT_STATUSES, today=today_iso(),
        script_tree=json.dumps(SCRIPT_TREE, ensure_ascii=False))

@app.route("/lead/<int:lid>/edit", methods=["POST"])
def lead_edit(lid):
    f = request.form
    budget=f.get("budget",""); readiness=f.get("readiness",""); experience=f.get("experience","")
    heat_label, _, _ = classify_lead(budget, readiness, experience)
    execute("""UPDATE leads SET name=?,phone=?,email=?,source=?,stage=?,interest_level=?,notes=?,
        call_summary=?,follow_up_date=?,follow_up_time=?,last_contact_date=?,closed_status=?,
        budget=?,readiness=?,experience=?,heat_label=?,package=?,updated_at=? WHERE id=?""",
        (f.get("name","").strip() or "ללא שם", f.get("phone","").strip(),
         f.get("email","").strip(), f.get("source","אתר"),
         f.get("stage","ליד חדש"), f.get("interest_level","בינונית"),
         f.get("notes","").strip(), f.get("call_summary","").strip(),
         f.get("follow_up_date",""), f.get("follow_up_time",""),
         f.get("last_contact_date",""), f.get("closed_status","").strip(),
         budget, readiness, experience, heat_label,
         f.get("package",""), now_iso(), lid))
    flash("הליד עודכן")
    return redirect(url_for("lead_detail", lid=lid))

@app.route("/lead/<int:lid>/interaction", methods=["POST"])
def add_interaction(lid):
    execute("INSERT INTO interactions (lead_id,interaction_type,summary,created_at) VALUES (?,?,?,?)",
        (lid, request.form.get("interaction_type","שיחה"),
         request.form.get("summary","").strip(), now_iso()))
    execute("UPDATE leads SET last_contact_date=?, updated_at=? WHERE id=?", (today_iso(), now_iso(), lid))
    flash("האינטראקציה נוספה")
    return redirect(url_for("lead_detail", lid=lid))

@app.route("/lead/<int:lid>/delete", methods=["POST"])
def delete_lead(lid):
    execute("DELETE FROM interactions WHERE lead_id=?", (lid,))
    execute("DELETE FROM payments WHERE lead_id=?", (lid,))
    execute("DELETE FROM debts WHERE lead_id=?", (lid,))
    execute("DELETE FROM leads WHERE id=?", (lid,))
    flash("הליד נמחק")
    return redirect(url_for("index"))

@app.route("/leads/mass-delete", methods=["POST"])
def mass_delete():
    ids = request.form.getlist("lead_ids")
    if not ids:
        flash("לא נבחרו לידים"); return redirect(url_for("index"))
    placeholders = ",".join("?" for _ in ids)
    execute(f"DELETE FROM interactions WHERE lead_id IN ({placeholders})", tuple(ids))
    execute(f"DELETE FROM payments WHERE lead_id IN ({placeholders})", tuple(ids))
    execute(f"DELETE FROM debts WHERE lead_id IN ({placeholders})", tuple(ids))
    execute(f"DELETE FROM leads WHERE id IN ({placeholders})", tuple(ids))
    flash(f"נמחקו {len(ids)} לידים")
    return redirect(url_for("index"))


# ══════════════════════════════════════════════════════════════════════
#  ROUTES — PAYMENTS & DEBTS
# ══════════════════════════════════════════════════════════════════════

@app.route("/lead/<int:lid>/payment", methods=["POST"])
def add_payment(lid):
    f = request.form
    amount = float(f.get("amount", 0))
    execute("INSERT INTO payments (lead_id,amount,method,notes,payment_date,created_at) VALUES (?,?,?,?,?,?)",
        (lid, amount, f.get("method",""), f.get("notes","").strip(),
         f.get("payment_date","") or today_iso(), now_iso()))
    # Update stage to closed if not already
    lead = fetchone("SELECT stage FROM leads WHERE id=?", (lid,))
    if lead and lead["stage"] != "נסגר":
        execute("UPDATE leads SET stage='נסגר', updated_at=? WHERE id=?", (now_iso(), lid))
    flash(f"תשלום של ₪{amount:,.0f} נרשם")
    return redirect(url_for("lead_detail", lid=lid))

@app.route("/lead/<int:lid>/debt", methods=["POST"])
def add_debt(lid):
    f = request.form
    execute("INSERT INTO debts (lead_id,amount,paid,due_date,status,notes,created_at) VALUES (?,?,0,?,?,?,?)",
        (lid, float(f.get("amount",0)), f.get("due_date",""), "ממתין",
         f.get("notes","").strip(), now_iso()))
    flash("חוב נרשם")
    return redirect(url_for("lead_detail", lid=lid))

@app.route("/debt/<int:did>/update", methods=["POST"])
def update_debt(did):
    f = request.form
    debt = fetchone("SELECT * FROM debts WHERE id=?", (did,))
    if not debt: abort(404)
    paid = float(f.get("paid", debt["paid"]))
    status = f.get("status", debt["status"])
    if paid >= debt["amount"]: status = "שולם"
    execute("UPDATE debts SET paid=?, status=?, notes=? WHERE id=?",
        (paid, status, f.get("notes", debt["notes"]), did))
    flash("חוב עודכן")
    return redirect(url_for("lead_detail", lid=debt["lead_id"]))


# ══════════════════════════════════════════════════════════════════════
#  ROUTES — PIPELINE & REVENUE
# ══════════════════════════════════════════════════════════════════════

@app.route("/pipeline")
def pipeline():
    leads = fetchall("SELECT * FROM leads WHERE stage='נסגר' ORDER BY datetime(updated_at) DESC")
    payments = fetchall("SELECT p.*, l.name FROM payments p JOIN leads l ON p.lead_id=l.id ORDER BY p.payment_date DESC")
    # Monthly stats
    months = {}
    for p in payments:
        m = p["payment_date"][:7]  # YYYY-MM
        months[m] = months.get(m, 0) + p["amount"]
    month_labels = sorted(months.keys())[-6:]  # last 6 months
    month_data = [months.get(m, 0) for m in month_labels]

    this_month = date.today().strftime("%Y-%m")
    monthly_total = months.get(this_month, 0)
    all_time = sum(p["amount"] for p in payments)

    return render_template("pipeline.html", leads=leads, payments=payments,
        monthly_total=monthly_total, all_time=all_time,
        month_labels=json.dumps(month_labels, ensure_ascii=False),
        month_data=json.dumps(month_data),
        packages=PACKAGES)


# ══════════════════════════════════════════════════════════════════════
#  ROUTES — BUSINESS MANAGEMENT
# ══════════════════════════════════════════════════════════════════════

@app.route("/business")
def business():
    debts = fetchall("""SELECT d.*, l.name, l.phone FROM debts d
        JOIN leads l ON d.lead_id = l.id ORDER BY d.status, d.due_date""")
    expenses = fetchall("SELECT * FROM expenses ORDER BY expense_date DESC LIMIT 50")
    payments = fetchall("SELECT p.*, l.name FROM payments p JOIN leads l ON p.lead_id=l.id ORDER BY p.payment_date DESC LIMIT 50")

    this_month = date.today().strftime("%Y-%m")
    month_revenue = sum(p["amount"] for p in payments if p["payment_date"][:7]==this_month)
    month_expenses = sum(e["amount"] for e in expenses if e["expense_date"][:7]==this_month)
    open_debts = sum(d["amount"]-d["paid"] for d in debts if d["status"]!="שולם")

    return render_template("business.html", debts=debts, expenses=expenses,
        payments=payments, month_revenue=month_revenue, month_expenses=month_expenses,
        open_debts=open_debts, profit=month_revenue-month_expenses,
        debt_statuses=DEBT_STATUSES, payment_methods=PAYMENT_METHODS)

@app.route("/expense/add", methods=["POST"])
def add_expense():
    f = request.form
    execute("INSERT INTO expenses (description,category,amount,expense_date,notes,created_at) VALUES (?,?,?,?,?,?)",
        (f.get("description","").strip(), f.get("category","כללי"),
         float(f.get("amount",0)), f.get("expense_date","") or today_iso(),
         f.get("notes","").strip(), now_iso()))
    flash("הוצאה נרשמה")
    return redirect(url_for("business"))

@app.route("/expense/<int:eid>/delete", methods=["POST"])
def delete_expense(eid):
    execute("DELETE FROM expenses WHERE id=?", (eid,))
    flash("הוצאה נמחקה")
    return redirect(url_for("business"))


# ══════════════════════════════════════════════════════════════════════
#  ROUTES — CSV UPLOAD
# ══════════════════════════════════════════════════════════════════════

def _clean_meta(val):
    return val.strip().strip('"').strip("_").replace("_"," ").strip()

def _clean_phone(raw):
    p = raw.strip().strip('"')
    if p.startswith("p:"): p = p[2:]
    if p.startswith("+972"): p = "0" + p[4:]
    elif p.startswith("972"): p = "0" + p[3:]
    return p

@app.route("/upload", methods=["GET","POST"])
def upload_leads():
    if request.method == "GET":
        return render_template("upload.html")
    file = request.files.get("csv_file")
    if not file or not file.filename:
        flash("לא נבחר קובץ"); return redirect(url_for("upload_leads"))

    raw_bytes = file.read()
    text = None
    for enc in ["utf-16","utf-16-le","utf-16-be","utf-8-sig","utf-8","cp1255"]:
        try: text = raw_bytes.decode(enc); break
        except: continue
    if not text:
        flash("לא הצלחתי לקרוא — שמור כ-UTF-8"); return redirect(url_for("upload_leads"))
    if text and text[0]=="\ufeff": text = text[1:]

    delim = "\t" if "\t" in text.split("\n",1)[0] else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    if not reader.fieldnames:
        flash("קובץ ריק"); return redirect(url_for("upload_leads"))

    fmap = {f.lower().strip().strip('"'): f for f in reader.fieldnames}
    def find_col(cs):
        for c in cs:
            for k,v in fmap.items():
                if c in k: return v
        return None

    col_first=find_col(["first name","first_name","שם פרטי","שם_פרטי"])
    col_last=find_col(["last_name","last name","שם משפחה"])
    col_name=find_col(["name","שם מלא","full_name"]) if not col_first else None
    col_phone=find_col(["phone","טלפון","mobile","נייד"])
    col_email=find_col(["email","אימייל"])
    col_budget=find_col(["כמה_אתה_מוכן","budget","תקציב","מוכן_להשקיע"])
    col_readiness=find_col(["מתי_אתה_רוצה","readiness","מוכנות","מתי"])
    col_experience=find_col(["ניסיון","experience"])
    col_goal=find_col(["מטרה","goal","המטרה"])
    col_platform=find_col(["platform"])
    col_notes=find_col(["notes","הערות"])
    col_meta_id=find_col(["id"])
    col_date=find_col(["created_time","created"])

    existing_ids = set()
    try:
        for r in fetchall("SELECT meta_id FROM leads WHERE meta_id!=''"):
            existing_ids.add(r["meta_id"])
    except: pass

    imported, skipped, results = 0, 0, []
    for row in reader:
        if col_first:
            first = (row.get(col_first,"") or "").strip().strip('"')
            last = (row.get(col_last,"") or "").strip().strip('"') if col_last else ""
            name = f"{first} {last}".strip() or "ליד מיובא"
        elif col_name:
            name = (row.get(col_name,"") or "").strip().strip('"') or "ליד מיובא"
        else: name = "ליד מיובא"

        phone = _clean_phone(row.get(col_phone,"") if col_phone else "")
        email = (row.get(col_email,"") if col_email else "").strip().strip('"')
        meta_id = (row.get(col_meta_id,"") if col_meta_id else "").strip().strip('"')
        if meta_id and meta_id in existing_ids: skipped += 1; continue
        existing_ids.add(meta_id)

        plat = (row.get(col_platform,"") if col_platform else "").strip().strip('"').lower()
        source = {"fb":"Facebook","ig":"Instagram"}.get(plat, "Facebook")
        lead_date = (row.get(col_date,"") if col_date else "").strip().strip('"')

        budget = _match_budget(_clean_meta(row.get(col_budget,"") if col_budget else ""))
        readiness = _match_readiness(_clean_meta(row.get(col_readiness,"") if col_readiness else ""))
        experience = _match_experience(_clean_meta(row.get(col_experience,"") if col_experience else ""))
        goal = _clean_meta(row.get(col_goal,"") if col_goal else "")
        notes = f"מטרה: {goal}" if goal else ""

        heat_label, interest, stage = classify_lead(budget, readiness, experience)
        ts = now_iso()
        execute(INSERT_SQL, (name, phone, email, source, stage, interest, notes, "",
                             "", "", "", "", budget, readiness, experience,
                             heat_label, goal, meta_id, lead_date, "", ts, ts))
        imported += 1
        results.append({"name":name,"phone":phone,"heat_label":heat_label,
                         "interest":interest,"stage":stage,"budget":budget,
                         "readiness":readiness,"experience":experience,"goal":goal})

    results.sort(key=lambda r: HEAT_ORDER.get(r["heat_label"],9))
    msg = f"יובאו {imported} לידים!"
    if skipped: msg += f" ({skipped} כפילויות דולגו)"
    flash(msg)
    return render_template("upload_results.html", results=results, total=imported, skipped=skipped)

def _match_budget(raw):
    if not raw: return ""
    if "3,000" in raw and "7,000" in raw: return "₪3,000–₪7,000"
    if "7,000" in raw and "13,000" in raw: return "₪7,000–₪13,000"
    if "מעל" in raw or "13,000" in raw.replace(" ",""): return "מעל ₪13,000"
    if "עד" in raw: return "עד ₪3,000"
    digits = "".join(c for c in raw if c.isdigit())
    try: num = int(digits) if digits else 0
    except: num = 0
    if num > 0:
        if num<3000: return "עד ₪3,000"
        if num<7000: return "₪3,000–₪7,000"
        if num<13000: return "₪7,000–₪13,000"
        return "מעל ₪13,000"
    return raw

def _match_readiness(raw):
    if not raw: return ""
    r = raw.lower()
    if any(k in r for k in ["מיד","היום","עכשיו","מוכן היום"]): return "מיד — אני מוכן היום"
    if any(k in r for k in ["שבוע","week"]): return "השבוע"
    if any(k in r for k in ["מתעניין","בשלב","אולי"]): return "רק מתעניין בשלב הזה"
    return raw

def _match_experience(raw):
    if not raw: return ""
    r = raw.lower()
    if any(k in r for k in ["מנוסה","experienced","שנים","מקצועי"]): return "מנוסה"
    if any(k in r for k in ["בסיסי","beginner","מתחיל","basic"]): return "בסיסי"
    if any(k in r for k in ["אין","no","none"]): return "אין"
    return raw


# ══════════════════════════════════════════════════════════════════════
#  ROUTES — WEBHOOK (Meta integration)
# ══════════════════════════════════════════════════════════════════════

@app.route("/webhook/meta", methods=["GET","POST"])
def webhook_meta():
    """Meta Lead Ads webhook endpoint."""
    if request.method == "GET":
        # Verification challenge
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        verify_token = fetchone("SELECT value FROM settings WHERE key='webhook_token'")
        vt = verify_token["value"] if verify_token else "diamond-algo-2026"
        if mode == "subscribe" and token == vt:
            return challenge, 200
        return "Forbidden", 403

    # POST — incoming lead
    data = request.get_json(silent=True) or {}
    try:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                lead_data = change.get("value", {})
                name = ""
                phone = ""
                email = ""
                for fd in lead_data.get("field_data", []):
                    n = fd.get("name","").lower()
                    v = fd.get("values",[""])[0]
                    if "name" in n or "שם" in n: name = v
                    elif "phone" in n or "טלפון" in n: phone = _clean_phone(v)
                    elif "email" in n or "אימייל" in n: email = v
                if not name: name = "ליד מ-Meta"
                ts = now_iso()
                execute(INSERT_SQL, (name, phone, email, "Facebook", "ליד חדש", "בינונית",
                                     "", "", "", "", "", "", "", "", "", "", "", "", "", "", ts, ts))
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True})

@app.route("/webhook/setup")
def webhook_setup():
    token = fetchone("SELECT value FROM settings WHERE key='webhook_token'")
    vt = token["value"] if token else "diamond-algo-2026"
    return render_template("webhook_setup.html", verify_token=vt,
        webhook_url=request.host_url.rstrip("/") + "/webhook/meta")

@app.route("/webhook/setup/save", methods=["POST"])
def webhook_save_token():
    token = request.form.get("token","").strip() or "diamond-algo-2026"
    execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('webhook_token', ?)", (token,))
    flash("הטוקן נשמר")
    return redirect(url_for("webhook_setup"))


# ══════════════════════════════════════════════════════════════════════
#  ROUTES — WHATSAPP
# ══════════════════════════════════════════════════════════════════════

@app.route("/lead/<int:lid>/whatsapp")
def whatsapp_send(lid):
    lead = fetchone("SELECT * FROM leads WHERE id=?", (lid,))
    if not lead: abort(404)
    template = request.args.get("template", "intro")
    tmpl = WA_TEMPLATES.get(template, WA_TEMPLATES["intro"])
    phone = lead["phone"].lstrip("0")
    if not phone.startswith("972"): phone = "972" + phone
    msg = tmpl.format(name=lead["name"], amount="", summary="")
    import urllib.parse
    url = f"https://wa.me/{phone}?text={urllib.parse.quote(msg)}"
    return redirect(url)


# ══════════════════════════════════════════════════════════════════════
#  API
# ══════════════════════════════════════════════════════════════════════

@app.route("/api/leads", methods=["POST"])
def api_create_lead():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "ליד חדש").strip()
    phone = (data.get("phone") or "").strip()
    email = (data.get("email") or "").strip()
    source = (data.get("source") or "Facebook").strip()
    notes = (data.get("notes") or "").strip()
    ts = now_iso()
    lid = execute(INSERT_SQL, (name, phone, email, source, "ליד חדש", "בינונית",
                               notes, "", "", "", "", "", "", "", "", "", "", "", "", "", ts, ts))
    return jsonify({"ok": True, "lead_id": lid})

@app.route("/api/lead/<int:lid>/stage", methods=["POST"])
def api_update_stage(lid):
    data = request.get_json(silent=True) or {}
    stage = data.get("stage","")
    if stage not in PIPELINE_STAGES:
        return jsonify({"ok":False,"error":"bad stage"}), 400
    execute("UPDATE leads SET stage=?, updated_at=? WHERE id=?", (stage, now_iso(), lid))
    return jsonify({"ok": True})

@app.route("/api/script-save", methods=["POST"])
def api_script_save():
    """Save script results to lead."""
    data = request.get_json(silent=True) or {}
    lid = data.get("lead_id")
    notes = data.get("notes","")
    result = data.get("result","")
    if not lid: return jsonify({"ok":False}), 400

    # Add interaction
    execute("INSERT INTO interactions (lead_id,interaction_type,summary,created_at) VALUES (?,?,?,?)",
        (lid, "שיחה", notes, now_iso()))
    execute("UPDATE leads SET call_summary=?, last_contact_date=?, updated_at=? WHERE id=?",
        (notes, today_iso(), now_iso(), lid))

    if result == "won":
        execute("UPDATE leads SET stage='נסגר', updated_at=? WHERE id=?", (now_iso(), lid))
    elif result == "dead":
        execute("UPDATE leads SET stage='לא נסגר', updated_at=? WHERE id=?", (now_iso(), lid))
    elif result == "followup":
        tomorrow = (date.today() + timedelta(days=2)).isoformat()
        execute("UPDATE leads SET stage='ניסיון קשר', follow_up_date=?, updated_at=? WHERE id=?",
            (tomorrow, now_iso(), lid))

    return jsonify({"ok": True})


init_db()  # ensure DB exists on startup

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    debug = os.environ.get("RENDER") is None
    app.run(host="0.0.0.0", port=port, debug=debug)
