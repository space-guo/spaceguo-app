"""
github_report.py — Firebase Firestore → 텔레그램 종합 일일 보고
GitHub Actions에서 실행됨 (컴퓨터 불필요)

환경 변수:
  FIREBASE_SA_JSON   : Firebase 서비스 계정 JSON 전체 내용
  TELEGRAM_BOT_TOKEN : 텔레그램 봇 토큰
  TELEGRAM_CHAT_ID   : 텔레그램 채팅 ID
"""

import os, sys, json, requests
from datetime import datetime, timedelta, timezone, date

KST = timezone(timedelta(hours=9))

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]


# ── Firebase 초기화 ──────────────────────────────────────
def init_firebase():
    import firebase_admin
    from firebase_admin import credentials, firestore

    sa_json = os.environ["FIREBASE_SA_JSON"]
    sa_dict = json.loads(sa_json)

    if not firebase_admin._apps:
        cred = credentials.Certificate(sa_dict)
        firebase_admin.initialize_app(cred)
    return firestore.client()


# ── 텔레그램 발송 ────────────────────────────────────────
def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    })
    if resp.status_code == 200:
        print("✓ 텔레그램 발송 성공")
    else:
        print(f"✗ 발송 실패: {resp.status_code} {resp.text}")
        sys.exit(1)


# ── 날짜 유틸 ────────────────────────────────────────────
def to_date_kst(val):
    """Firebase Timestamp 또는 datetime → date (KST)"""
    if val is None:
        return None
    try:
        # Firestore Timestamp / DatetimeWithNanoseconds (datetime 서브클래스)
        if hasattr(val, 'astimezone'):
            return val.astimezone(KST).date()
        # 혹시 raw Timestamp 객체인 경우
        if hasattr(val, '_seconds'):
            return datetime.fromtimestamp(val._seconds, tz=KST).date()
    except Exception:
        pass
    return None


def parse_date_str(s):
    """'YYYY-MM-DD' 문자열 → date"""
    if not s:
        return None
    try:
        return datetime.strptime(str(s), "%Y-%m-%d").date()
    except Exception:
        return None


def days_until(d):
    """date까지 남은 일수 (음수 = 지남)"""
    if d is None:
        return None
    today = datetime.now(KST).date()
    return (d - today).days


def site_name(val):
    """site 필드 → 문자열"""
    if isinstance(val, dict):
        return val.get("name", str(val))
    return str(val) if val else "미분류"


# ── Firebase 데이터 읽기 ─────────────────────────────────
def get_expenses(db, days=7):
    from google.cloud.firestore_v1.base_query import FieldFilter
    cutoff = datetime.now(KST) - timedelta(days=days)
    docs = db.collection("expenses").where(filter=FieldFilter("date", ">=", cutoff)).stream()
    return [d.to_dict() for d in docs]


def get_incomes(db, days=7):
    from google.cloud.firestore_v1.base_query import FieldFilter
    cutoff = datetime.now(KST) - timedelta(days=days)
    docs = db.collection("incomes").where(filter=FieldFilter("date", ">=", cutoff)).stream()
    return [d.to_dict() for d in docs]


def get_schedules(db):
    docs = db.collection("schedules").stream()
    return [d.to_dict() for d in docs]


def get_orders(db):
    docs = db.collection("orders").stream()
    return [d.to_dict() for d in docs]


# ── 보고서 생성 ──────────────────────────────────────────
def build_report(db):
    now   = datetime.now(KST)
    today = now.date()
    weekdays = ['월', '화', '수', '목', '금', '토', '일']
    weekday  = weekdays[now.weekday()]

    # ── 데이터 수집 ──
    expenses_7d = get_expenses(db, days=7)
    incomes_7d  = get_incomes(db, days=7)
    schedules   = get_schedules(db)
    orders      = get_orders(db)

    # 오늘 내역
    expenses_today = [e for e in expenses_7d if to_date_kst(e.get("date")) == today]
    incomes_today  = [i for i in incomes_7d  if to_date_kst(i.get("date")) == today]

    # 이번 주 내역 (월~오늘)
    week_start    = today - timedelta(days=today.weekday())
    expenses_week = [e for e in expenses_7d if (to_date_kst(e.get("date")) or date.min) >= week_start]
    incomes_week  = [i for i in incomes_7d  if (to_date_kst(i.get("date")) or date.min) >= week_start]

    # 공정 분류
    active_scheds  = [s for s in schedules if int(s.get("progress", 0)) < 100]
    overdue_scheds = []  # 마감일 지남
    urgent_scheds  = []  # 마감 3일 이내
    for s in active_scheds:
        end = parse_date_str(s.get("endDate", ""))
        if end is None:
            continue
        d = days_until(end)
        if d < 0:
            overdue_scheds.append((d, s))
        elif d <= 3:
            urgent_scheds.append((d, s))

    overdue_scheds.sort(key=lambda x: x[0])
    urgent_scheds.sort(key=lambda x: x[0])

    # 발주 분류
    pending_orders = [o for o in orders if o.get("status") == "pending"]
    ordered_orders = [o for o in orders if o.get("status") == "ordered"]
    urgent_orders  = []  # 납품 예정 3일 이내 (미입고)
    for o in orders:
        if o.get("status") == "arrived":
            continue
        dd = parse_date_str(o.get("deliveryDate", ""))
        if dd is None:
            continue
        d = days_until(dd)
        if d <= 3:
            urgent_orders.append((d, o))
    urgent_orders.sort(key=lambda x: x[0])

    # ──── 보고서 작성 ────
    lines = []
    lines.append("🏠 <b>공간구오 일일 보고</b>")
    lines.append(f"📅 {now.strftime('%Y년 %m월 %d일')} ({weekday}요일) {now.strftime('%H:%M')}")

    # ── ⚠️ 긴급 경고 ──
    warnings = []

    for d, s in overdue_scheds:
        name = s.get("procName", "미지정")
        sname = site_name(s.get("site", ""))
        end  = s.get("endDate", "")
        warnings.append(f"🔴 공정 기한 초과 ({abs(d)}일): {name} / {sname} (~{end})")

    for d, o in urgent_orders:
        oname  = o.get("name", "")
        vendor = o.get("vendor", "거래처미상")
        dd_str = o.get("deliveryDate", "")
        if d < 0:
            warnings.append(f"🔴 납품 기한 초과 ({abs(d)}일): {oname} — {vendor} (