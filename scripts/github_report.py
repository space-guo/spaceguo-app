"""
github_report.py — Firebase Firestore → 텔레그램 일일 보고
GitHub Actions에서 실행됨 (컴퓨터 불필요)

환경 변수:
  FIREBASE_SA_JSON   : Firebase 서비스 계정 JSON 전체 내용
  TELEGRAM_BOT_TOKEN : 텔레그램 봇 토큰
  TELEGRAM_CHAT_ID   : 텔레그램 채팅 ID
"""

import os, sys, json, requests
from datetime import datetime, timedelta, timezone

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


# ── Firebase 데이터 읽기 ─────────────────────────────────
def get_recent_expenses(db, days=7):
    """최근 N일 지출 합계"""
    from google.cloud.firestore_v1.base_query import FieldFilter
    now = datetime.now(KST)
    cutoff = now - timedelta(days=days)

    docs = (
        db.collection("expenses")
        .where(filter=FieldFilter("date", ">=", cutoff))
        .stream()
    )
    items = [d.to_dict() for d in docs]
    total = sum(int(i.get("amount", 0)) for i in items)
    return total, len(items), items


def get_recent_incomes(db, days=7):
    """최근 N일 수입 합계"""
    from google.cloud.firestore_v1.base_query import FieldFilter
    now = datetime.now(KST)
    cutoff = now - timedelta(days=days)

    docs = (
        db.collection("incomes")
        .where(filter=FieldFilter("date", ">=", cutoff))
        .stream()
    )
    items = [d.to_dict() for d in docs]
    total = sum(int(i.get("amount", 0)) for i in items)
    return total, len(items)


def get_active_schedules(db):
    """진행 중인 공정 (progress < 100)"""
    docs = db.collection("schedules").stream()
    items = [d.to_dict() for d in docs]
    active = [i for i in items if int(i.get("progress", 0)) < 100]
    active.sort(key=lambda x: int(x.get("progress", 0)), reverse=True)
    return active


def get_sites(db):
    """현장 목록"""
    docs = db.collection("settings").document("sites").get()
    if docs.exists:
        return docs.to_dict().get("sites", [])
    # fallback: sites 컬렉션
    docs2 = db.collection("sites").stream()
    return [d.to_dict() for d in docs2]


# ── 보고서 생성 ──────────────────────────────────────────
def build_report(db):
    now = datetime.now(KST)
    date_str = now.strftime("%Y년 %m월 %d일")
    weekdays = ['월', '화', '수', '목', '금', '토', '일']
    weekday  = weekdays[now.weekday()]

    expense_total, exp_cnt, exp_items = get_recent_expenses(db, days=7)
    income_total,  inc_cnt            = get_recent_incomes(db, days=7)
    active_scheds                     = get_active_schedules(db)

    lines = [
        "🏠 <b>공간구오 일일 보고</b>",
        f"📅 {date_str} ({weekday}요일)\n",
    ]

    # ── 재무 ──
    lines.append("💰 <b>최근 7일 재무</b>")
    if income_total == 0 and expense_total == 0:
        lines.append("  · 기록 없음")
    else:
        if income_total > 0:
            lines.append(f"  · 수입: {income_total:,}원 ({inc_cnt}건)")
        if expense_total > 0:
            lines.append(f"  · 지출: {expense_total:,}원 ({exp_cnt}건)")
            # 카테고리별 상위 3개
            cat_sum = {}
            for item in exp_items:
                cat = item.get("category", "기타")
                cat_sum[cat] = cat_sum.get(cat, 0) + int(item.get("amount", 0))
            top = sorted(cat_sum.items(), key=lambda x: x[1], reverse=True)[:3]
            for cat, amt in top:
                lines.append(f"    ↳ {cat}: {amt:,}원")
        net  = income_total - expense_total
        sign = "+" if net >= 0 else ""
        lines.append(f"  · 순이익: <b>{sign}{net:,}원</b>")
    lines.append("")

    # ── 공정 ──
    lines.append("🔨 <b>진행 중인 공정</b>")
    if not active_scheds:
        lines.append("  · 진행 중인 공정 없음")
    else:
        lines.append(f"  · 총 {len(active_scheds)}건")
        for s in active_scheds[:5]:
            pct  = int(s.get("progress", 0))
            name = s.get("procName", "미지정")
            site_raw = s.get("site", "")
            site = site_raw.get("name", str(site_raw)) if isinstance(site_raw, dict) else str(site_raw)
            bar  = "█" * (pct // 20) + "░" * (5 - pct // 20)
            lines.append(f"  · [{bar}] {pct}% {name} / {site}")
        if len(active_scheds) > 5:
            lines.append(f"  · +{len(active_scheds)-5}건 더...")
    lines.append("")

    lines.append("─────────────────")
    lines.append(f"🤖 {now.strftime('%H:%M')} KST | GitHub Actions 자동보고")

    return "\n".join(lines)


# ── 메인 ────────────────────────────────────────────────
def main():
    if "--test" in sys.argv:
        msg = (
            "✅ <b>공간구오 보고봇 연결 테스트 성공!</b>\n\n"
            "GitHub Actions에서 매일 오전 7:00(KST)에\n"
            "일일 보고가 자동으로 전송됩니다.\n"
            "컴퓨터를 꺼도 작동해요! 🎉"
        )
        send_telegram(msg)
        return

    print("Firebase 연결 중...")
    db = init_firebase()
    print("보고서 생성 중...")
    report = build_report(db)
    print("=== 발송 내용 ===")
    print(report)
    print("=================")
    send_telegram(report)


if __name__ == "__main__":
    main()
