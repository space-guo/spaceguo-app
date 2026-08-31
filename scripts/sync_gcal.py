# -*- coding: utf-8 -*-
"""
공간구오 앱 일정(myEvents) → 구글 캘린더 단방향 동기화
알림: 전날 20:00 / 당일 07:00 / 시작 1시간 전
"""
import os, json, re, datetime
import firebase_admin
from firebase_admin import credentials, firestore
from google.oauth2 import service_account
from googleapiclient.discovery import build

CAL_ID = os.environ.get('GCAL_ID', 'space.guo24@gmail.com')
TZ = 'Asia/Seoul'
TAG = 'spaceguo'
PAST_DAYS = 30
FUTURE_DAYS = 400

sa = json.loads(os.environ['FIREBASE_SA_JSON'])
firebase_admin.initialize_app(credentials.Certificate(sa))
db = firestore.client()

gcred = service_account.Credentials.from_service_account_info(
    sa, scopes=['https://www.googleapis.com/auth/calendar'])
svc = build('calendar', 'v3', credentials=gcred, cache_discovery=False)


def p_date(s):
    m = re.match(r'(\d{4})\D+(\d{1,2})\D+(\d{1,2})', str(s or ''))
    if not m:
        return None
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def p_time(s):
    m = re.match(r'(\d{1,2}):(\d{2})', str(s or ''))
    if not m:
        return None
    try:
        return datetime.time(int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


def reminders(start_dt, all_day):
    base = start_dt if not all_day else datetime.datetime.combine(
        start_dt.date(), datetime.time(9, 0))
    out = []
    if not all_day:
        out.append(60)
    d7 = datetime.datetime.combine(base.date(), datetime.time(7, 0))
    d20 = datetime.datetime.combine(base.date() - datetime.timedelta(days=1),
                                    datetime.time(20, 0))
    for d in (d7, d20):
        mins = int((base - d).total_seconds() // 60)
        if 0 < mins <= 40320:
            out.append(mins)
    out = sorted(set(out))[:5]
    return [{'method': 'popup', 'minutes': m} for m in out]


def build_body(ev):
    d = p_date(ev.get('date'))
    if not d:
        return None
    t = p_time(ev.get('time'))
    end_d = p_date(ev.get('endDate')) or d

    title = (ev.get('title') or '일정').strip()
    if ev.get('type') == 'todo':
        title = ('✔ ' if ev.get('done') else '☐ ') + title

    body = {
        'summary': title,
        'description': (ev.get('note') or '') + '\n\n— 공간구오 앱에서 자동 동기화',
        'extendedProperties': {'private': {'source': TAG, 'appId': ev['id']}},
    }

    if t:
        start = datetime.datetime.combine(d, t)
        end = start + datetime.timedelta(hours=1)
        body['start'] = {'dateTime': start.isoformat(), 'timeZone': TZ}
        body['end'] = {'dateTime': end.isoformat(), 'timeZone': TZ}
        body['reminders'] = {'useDefault': False,
                             'overrides': reminders(start, False)}
    else:
        start = datetime.datetime.combine(d, datetime.time(0, 0))
        body['start'] = {'date': d.isoformat()}
        body['end'] = {'date': (end_d + datetime.timedelta(days=1)).isoformat()}
        body['reminders'] = {'useDefault': False,
                             'overrides': reminders(start, True)}
    return body


def main():
    today = datetime.date.today()
    lo = today - datetime.timedelta(days=PAST_DAYS)
    hi = today + datetime.timedelta(days=FUTURE_DAYS)

    events = []
    for doc in db.collection('myEvents').stream():
        v = doc.to_dict() or {}
        v['id'] = doc.id
        d = p_date(v.get('date'))
        if d and lo <= d <= hi:
            events.append(v)
    print('앱 일정 %d건 (기간 내)' % len(events))

    existing, token = {}, None
    while True:
        r = svc.events().list(calendarId=CAL_ID,
                              privateExtendedProperty='source=' + TAG,
                              maxResults=250, pageToken=token,
                              showDeleted=False).execute()
        for it in r.get('items', []):
            aid = it.get('extendedProperties', {}).get('private', {}).get('appId')
            if aid:
                existing[aid] = it
        token = r.get('nextPageToken')
        if not token:
            break
    print('구글 기존 %d건' % len(existing))

    created = updated = deleted = skipped = 0
    seen = set()

    for ev in events:
        body = build_body(ev)
        if not body:
            skipped += 1
            continue
        seen.add(ev['id'])
        cur = existing.get(ev['id'])
        try:
            if cur:
                svc.events().update(calendarId=CAL_ID, eventId=cur['id'],
                                    body=body).execute()
                updated += 1
            else:
                svc.events().insert(calendarId=CAL_ID, body=body).execute()
                created += 1
        except Exception as e:
            print('  경고 %s: %s' % (ev.get('title'), e))
            skipped += 1

    for aid, it in existing.items():
        if aid in seen:
            continue
        try:
            svc.events().delete(calendarId=CAL_ID, eventId=it['id']).execute()
            deleted += 1
        except Exception as e:
            print('  삭제 실패 %s: %s' % (aid, e))

    print('완료: 생성 %d / 수정 %d / 삭제 %d / 건너뜀 %d'
          % (created, updated, deleted, skipped))


if __name__ == '__main__':
    main()
