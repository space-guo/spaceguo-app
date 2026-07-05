"""
github_report.py - Firebase Firestore -> 텔레그램 종합 일일 보고
GitHub Actions에서 실행됨

환경 변수:
  FIREBASE_SA_JSON   : Firebase 서비스 계정 JSON
  TELEGRAM_BOT_TOKEN : 텔레그램 봇 토큰
  TELEGRAM_CHAT_ID   : 텔레그램 채팅 ID
"""

import os, sys, json, time, requests
from datetime import datetime, timedelta, timezone, date
from collections import defaultdict

KST = timezone(timedelta(hours=9))
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]


# Firebase 초기화
def init_firebase():
    import firebase_admin
    from firebase_admin import credentials, firestore
    sa_dict = json.loads(os.environ["FIREBASE_SA_JSON"])
    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(sa_dict))
    return firestore.client()


# 중복 발송 방지
def already_sent_today(db):
    today_str = datetime.now(KST).strftime("%Y-%m-%d")
    return db.collection("report_log").document(today_str).get().exists

def mark_sent_today(db):
    today_str = datetime.now(KST).strftime("%Y-%m-%d")
    db.collection("report_log").document(today_str).set({
        "sent_at": datetime.now(KST).isoformat(),
        "run_by": "github_actions"
    })


# 텔레그램 발송 (재시도 3회)
def send_telegram(message, retries=3):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(url, json={
                "chat_id": CHAT_ID,
                "text": message,
                "parse_mode": "HTML"
            }, timeout=15)
            if resp.status_code == 200:
                print(f"텔레그램 발송 성공 (시도 {attempt})")
                return True
            print(f"시도 {attempt} 실패: {resp.status_code} {resp.text}")
        except Exception as e:
            print(f"시도 {attempt} 예외: {e}")
        if attempt < retries:
            time.sleep(5 * attempt)
    print("최종 발송 실패")
    sys.exit(1)


# 날짜 유틸
def to_date_kst(val):
    if val is None:
        return None
    try:
        if hasattr(val, 'astimezone'):
            return val.astimezone(KST).date()
        if hasattr(val, '_seconds'):
            return datetime.fromtimestamp(val._seconds, tz=KST).date()
    except Exception:
        pass
    return None

def parse_date_str(s):
    try:
        return datetime.strptime(str(s), "%Y-%m-%d").date() if s else None
    except Exception:
        return None

def days_until(d):
    return (d - datetime.now(KST).date()).days if d else None

def site_name(val):
    if isinstance(val, dict):
        return val.get("name", str(val))
    return str(val) if val else "미분류"

def fmt_money(n):
    return f"{int(n):,}원"


# Firebase 데이터 읽기
def get_expenses(db, days=7):
    from google.cloud.firestore_v1.base_query import FieldFilter
    cutoff = datetime.now(KST) - timedelta(days=days)
    return [d.to_dict() for d in db.collection("expenses").where(filter=FieldFilter("date", ">=", cutoff)).stream()]

def get_incomes(db, days=7):
    from google.cloud.firestore_v1.base_query import FieldFilter
    cutoff = datetime.now(KST) - timedelta(days=days)
    return [d.to_dict() for d in db.collection("incomes").where(filter=FieldFilter("date", ">=", cutoff)).stream()]

def get_schedules(db):
    return [d.to_dict() for d in db.collection("schedules").stream()]

def get_orders(db):
    return [d.to_dict() for d in db.collection("orders").stream()]


# 보고서 생성
def build_report(db):
    now      = datetime.now(KST)
    today    = now.date()
    weekdays = ['월','화','수','목','금','토','일']
    weekday  = weekdays[now.weekday()]

    expenses_7d = get_expenses(db, 7)
    incomes_7d  = get_incomes(db, 7)
    schedules   = get_schedules(db)
    orders      = get_orders(db)

    week_start     = today - timedelta(days=today.weekday())
    expenses_today = [e for e in expenses_7d if to_date_kst(e.get("date")) == today]
    incomes_today  = [i for i in incomes_7d  if to_date_kst(i.get("date")) == today]
    expenses_week  = [e for e in expenses_7d if (to_date_kst(e.get("date")) or date.min) >= week_start]
    incomes_week   = [i for i in incomes_7d  if (to_date_kst(i.get("date")) or date.min) >= week_start]

    # 공정 분류
    active_scheds = [s for s in schedules if int(s.get("progress", 0)) < 100]
    overdue_scheds, today_scheds, soon_scheds = [], [], []
    for s in active_scheds:
        end = parse_date_str(s.get("endDate", ""))
        if end is None: continue
        d = days_until(end)
        if d is None: continue
        if d < 0:   overdue_scheds.append((d, s))
        elif d == 0: today_scheds.append((d, s))
        elif d <= 3: soon_scheds.append((d, s))
    overdue_scheds.sort(key=lambda x: x[0])
    soon_scheds.sort(key=lambda x: x[0])

    # 발주 분류
    pending_orders = [o for o in orders if o.get("status") == "pending"]
    ordered_orders = [o for o in orders if o.get("status") == "ordered"]
    urgent_orders  = []
    for o in orders:
        if o.get("status") == "arrived": continue
        dd = parse_date_str(o.get("deliveryDate", ""))
        d  = days_until(dd)
        if d is not None and d <= 3:
            urgent_orders.append((d, o))
    urgent_orders.sort(key=lambda x: x[0])

    lines = []
    lines.append(f"\U0001f3e0 <b>공간구오 일일 보고</b>")
    lines.append(f"\U0001f4c5 {now.strftime('%Y년 %m월 %d일')} ({weekday}요일) {now.strftime('%H:%M')}")

    # 긴급 경고
    warnings = []
    for d, s in overdue_scheds:
        warnings.append(f"\U0001f534 공정 기한 초과 ({abs(d)}일): {s.get('procName','미지정')} / {site_name(s.get('site',''))} (~{s.get('endDate','')})")
    for d, s in today_scheds:
        warnings.append(f"\U0001f7e0 공정 마감 오늘: {s.get('procName','미지정')} / {site_name(s.get('site',''))}")
    for d, s in soon_scheds:
        warnings.append(f"\U0001f7e1 공정 마감 D-{d}: {s.get('procName','미지정')} / {site_name(s.get('site',''))}")
    for d, o in urgent_orders:
        oname  = o.get("name","")
        vendor = o.get("vendor","거래처미상")
        dd_str = o.get("deliveryDate","")
        if d < 0:
            warnings.append(f"\U0001f534 납품 기한 초과 ({abs(d)}일): {oname} — {vendor} (~{dd_str})")
        elif d == 0:
            warnings.append(f"\U0001f7e0 납품 예정 오늘: {oname} — {vendor}")
        else:
            warnings.append(f"\U0001f7e1 납품 예정 D-{d}: {oname} — {vendor}")

    if warnings:
        lines.append("")
        lines.append("⚠️ <b>긴급 확인 필요</b>")
        for w in warnings:
            lines.append(f"  {w}")

    # 오늘의 브리핑
    lines.append("")
    lines.append("\U0001f4c5 <b>오늘의 브리핑</b>")
    exp_today_sum = sum(int(e.get("amount",0)) for e in expenses_today)
    inc_today_sum = sum(int(i.get("amount",0)) for i in incomes_today)
    if not expenses_today and not incomes_today:
        lines.append("  · 오늘 등록된 내역 없음")
    else:
        if inc_today_sum: lines.append(f"  · 수입: {fmt_money(inc_today_sum)} ({len(incomes_today)}건)")
        if exp_today_sum:
            lines.append(f"  · 지출: {fmt_money(exp_today_sum)} ({len(expenses_today)}건)")
            cat_today = defaultdict(int)
            for e in expenses_today:
                cat_today[e.get("category","기타")] += int(e.get("amount",0))
            for cat, amt in sorted(cat_today.items(), key=lambda x: -x[1])[:3]:
                lines.append(f"    ├ {cat}: {fmt_money(amt)}")
    if today_scheds:
        lines.append(f"  · 오늘 마감 공정: {len(today_scheds)}건")
        for _, s in today_scheds:
            lines.append(f"    └ {s.get('procName','미지정')} / {site_name(s.get('site',''))}")

    # 이번 주 브리핑
    exp_week_sum = sum(int(e.get("amount",0)) for e in expenses_week)
    inc_week_sum = sum(int(i.get("amount",0)) for i in incomes_week)
    days_passed  = today.weekday() + 1
    lines.append("")
    lines.append(f"\U0001f4ca <b>이번 주 브리핑</b> (월~{weekday}, {days_passed}일차)")
    if not expenses_week and not incomes_week:
        lines.append("  · 이번 주 내역 없음")
    else:
        if inc_week_sum: lines.append(f"  · 수입: {fmt_money(inc_week_sum)} ({len(incomes_week)}건)")
        if exp_week_sum:
            lines.append(f"  · 지출: {fmt_money(exp_week_sum)} ({len(expenses_week)}건)")
            cat_week = defaultdict(int)
            for e in expenses_week:
                cat_week[e.get("category","기타")] += int(e.get("amount",0))
            for cat, amt in sorted(cat_week.items(), key=lambda x: -x[1])[:3]:
                pct = int(amt / exp_week_sum * 100) if exp_week_sum else 0
                lines.append(f"    ├ {cat}: {fmt_money(amt)} ({pct}%)")
            site_week = defaultdict(int)
            for e in expenses_week:
                site_week[site_name(e.get("site","미분류"))] += int(e.get("amount",0))
            if len(site_week) > 1:
                top_sites = sorted(site_week.items(), key=lambda x: -x[1])[:2]
                lines.append("  · 현장별: " + " / ".join(f"{s} {fmt_money(a)}" for s,a in top_sites))

    # 공정 현황
    lines.append("")
    lines.append(f"\U0001f528 <b>공정 현황</b> (진행 중 {len(active_scheds)}건)")
    if not active_scheds:
        lines.append("  · 진행 중인 공정 없음")
    else:
        def sort_end(s):
            e = parse_date_str(s.get("endDate",""))
            return e if e else date.max
        for s in sorted(active_scheds, key=sort_end)[:8]:
            pct    = int(s.get("progress",0))
            name   = s.get("procName","미지정")
            sname  = site_name(s.get("site",""))
            end    = parse_date_str(s.get("endDate",""))
            d      = days_until(end)
            filled = int(pct / 10)
            bar    = chr(0x2588)*filled + chr(0x2591)*(10-filled)
            if d is None:      deadline = ""
            elif d < 0:        deadline = f" D+{abs(d)}초과"
            elif d == 0:       deadline = " D-Day"
            else:              deadline = f" D-{d}"
            lines.append(f"  [{bar}] {pct}% {name}/{sname}{deadline}")
        if len(active_scheds) > 8:
            lines.append(f"  +{len(active_scheds)-8}건 더...")

    # 발주 현황
    lines.append("")
    lines.append("\U0001f4e6 <b>발주 현황</b>")
    if not pending_orders and not ordered_orders:
        lines.append("  · 진행 중인 발주 없음")
    else:
        if pending_orders:
            total_amt = sum(int(o.get("amount",0)) for o in pending_orders if o.get("amount"))
            amt_str   = f" / 합계 {fmt_money(total_amt)}" if total_amt else ""
            lines.append(f"  · 발주 대기: {len(pending_orders)}건{amt_str}")
            for o in pending_orders[:4]:
                dd = parse_date_str(o.get("deliveryDate",""))
                d  = days_until(dd)
                dd_suffix = f" (D-{d})" if d is not None and d >= 0 else (f" (D+{abs(d)} 초과)" if d is not None else "")
                lines.append(f"    · {o.get('name','')} ({o.get('vendor','')}){dd_suffix}")
            if len(pending_orders) > 4:
                lines.append(f"    +{len(pending_orders)-4}건 더...")
        if ordered_orders:
            lines.append(f"  · 입고 대기: {len(ordered_orders)}건")
            for o in ordered_orders[:4]:
                dd = parse_date_str(o.get("deliveryDate",""))
                d  = days_until(dd)
                dd_str = "" if d is None else (f" (D+{abs(d)} 초과)" if d < 0 else (" (오늘 입고)" if d == 0 else f" (D-{d})"))
                lines.append(f"    · {o.get('name','')} — {o.get('vendor','')}{dd_str}")
            if len(ordered_orders) > 4:
                lines.append(f"    +{len(ordered_orders)-4}건 더...")

    # 손익 요약
    exp_total = sum(int(e.get("amount",0)) for e in expenses_7d)
    inc_total = sum(int(i.get("amount",0)) for i in incomes_7d)
    net_7d    = inc_total - exp_total
    sign      = "+" if net_7d >= 0 else ""
    net_emoji = "\U0001f4c8" if net_7d >= 0 else "\U0001f4c9"
    lines.append("")
    lines.append("\U0001f4b0 <b>손익 요약 (최근 7일)</b>")
    lines.append(f"  · 수입: {fmt_money(inc_total)}")
    lines.append(f"  · 지출: {fmt_money(exp_total)}")
    lines.append(f"  · 순이익: {net_emoji} <b>{sign}{fmt_money(net_7d)}</b>")

    # 푸터
    lines.append("")
    lines.append(f"-- 공간구오 보고봇 | {now.strftime('%H:%M')} 자동발송")
    return "\n".join(lines)


# 실행
if __name__ == "__main__":
    print(f"[{datetime.now(KST).isoformat()}] 보고 시작")
    db = init_firebase()

    if already_sent_today(db):
        print("오늘 이미 발송됨 - 스킵")
        sys.exit(0)

    report = build_report(db)
    print(report[:300])
    send_telegram(report)
    mark_sent_today(db)
    print("완료")
