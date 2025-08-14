# -*- coding: utf--8 -*-
import base64
import datetime
import os
import traceback
import uuid

import pytz
from email.mime.text import MIMEText
from flask import Flask, render_template_string, request
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# --- 設定エリア ---
# 必要なAPIの権限（スコープ）
SCOPES = [
    'https://www.googleapis.com/auth/calendar.readonly',
    'https://www.googleapis.com/auth/calendar.events', # 自身のカレンダーに書き込むために必要
    'https://www.googleapis.com/auth/gmail.send'
]

# ★★★★★ 必ず設定してください ★★★★★
# 予約通知を受け取る社内担当者のメールアドレス
TO_EMAIL_ADDRESS = 'keiichiro.yoshino@bizreach.co.jp'
# その他の固定の招待者がいればメールアドレスを記入
# FIXED_ATTENDEE_EMAIL = 'another-fixed-email@example.com'
# ★★★★★ 設定ここまで ★★★★★

# 面談の時間（分）
MEETING_DURATION_MINUTES = 60
# 候補時間を探す間隔（分）
TIME_STEP_MINUTES = 30
# 業務開始時間
WORKDAY_START_HOUR = 10
# 業務終了時間
WORKDAY_END_HOUR = 19
# 営業日 (0:月曜日, 1:火曜日, ... 6:日曜日)
WORKDAYS = [0, 1, 2, 3, 4]
# 何日先まで候補を探すか
SEARCH_DAYS_AHEAD = 7
# タイムゾーン
TIMEZONE = 'Asia/Tokyo'
# サービスアカウントのキーファイル名
SERVICE_ACCOUNT_FILE = 'schedule-adjustment-service-account-key.json'
# --- 設定エリアここまで ---

app = Flask(__name__)

# --- HTMLテンプレート ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>日程調整候補</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; max-width: 600px; margin: 2em auto; padding: 0 1em; background-color: #f9f9f9; }
        h1 { color: #2c3e50; }
        .day-section { margin-bottom: 2em; background-color: white; padding: 1em 1.5em; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        h2 { border-bottom: 2px solid #ecf0f1; padding-bottom: 0.5em; font-size: 1.2em; color: #34495e; }
        .slots { display: flex; flex-wrap: wrap; gap: 0.8em; }
        .slot-link { text-decoration: none; }
        .slot-button {
            background-color: #3498db; color: white; border: none; padding: 0.8em 1.2em;
            border-radius: 5px; cursor: pointer; font-size: 1em; transition: all 0.2s ease;
            display: block; width: 100%; text-align: center;
        }
        .slot-button:hover { background-color: #2980b9; transform: translateY(-2px); }
        .no-slots { color: #7f8c8d; }
    </style>
</head>
<body>
    <h1>打ち合わせの候補日時</h1>
    <p>ご希望の日時を選択してください。</p>
    {% if slots_by_day %}
        {% for day, slots in slots_by_day.items() %}
            <div class="day-section">
                <h2>{{ day }}</h2>
                <div class="slots">
                    {% for slot in slots %}
                        <a href="/confirm?time={{ slot.isoformat() }}&calendar={{ calendar_id }}" class="slot-link">
                            <button class="slot-button">{{ slot.strftime('%H:%M') }}</button>
                        </a>
                    {% endfor %}
                </div>
            </div>
        {% endfor %}
    {% else %}
        <p class="no-slots">申し訳ありませんが、現在ご案内できる候補時間がありません。</p>
    {% endif %}
</body>
</html>
"""
HTML_CONFIRM_PROMPT_TEMPLATE = """
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <title>予約内容の確認</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; padding: 2em 1em; background-color: #f9f9f9; }
        .container { max-width: 500px; margin: 0 auto; background-color: white; padding: 2em; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        h1 { color: #2c3e50; text-align: center; }
        p { color: #34495e; font-size: 1.2em; line-height: 1.6; text-align: center; }
        strong { color: #2980b9; font-size: 1.3em; }
        .form-group { margin-bottom: 1.5em; }
        .form-group label { display: block; margin-bottom: 0.5em; color: #34495e; font-weight: bold; }
        .form-group input { width: 100%; padding: 0.8em; border: 1px solid #ccc; border-radius: 4px; font-size: 1em; box-sizing: border-box; }
        .actions { margin-top: 2em; display: flex; justify-content: center; gap: 1em; }
        .button {
            text-decoration: none; color: white; border: none; padding: 0.8em 2em;
            border-radius: 5px; cursor: pointer; font-size: 1em; transition: all 0.2s ease;
        }
        .submit-button { background-color: #27ae60; }
        .submit-button:hover { background-color: #229954; }
        .cancel-link { background-color: #c0392b; text-align: center; }
        .cancel-link:hover { background-color: #a93226; }
    </style>
</head>
<body>
    <div class="container">
        <h1>予約内容の入力</h1>
        <p>以下の日時で予約します。<br><strong>{{ formatted_time }}</strong></p>
        <p>お名前とメールアドレスを入力してください。</p>
        <form action="/create_event" method="GET">
            <input type="hidden" name="time" value="{{ iso_time }}">
            <input type="hidden" name="calendar" value="{{ calendar_id }}">
            <div class="form-group">
                <label for="name">お名前</label>
                <input type="text" id="name" name="name" required>
            </div>
            <div class="form-group">
                <label for="email">メールアドレス</label>
                <input type="email" id="email" name="email" required>
            </div>
            <div class="actions">
                <button type="submit" class="button submit-button">この内容で予約する</button>
                <a href="/?calendar={{ calendar_id }}" class="button cancel-link">キャンセル</a>
            </div>
        </form>
    </div>
</body>
</html>
"""
HTML_CONFIRMATION_TEMPLATE = """
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <title>予約完了</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; text-align: center; padding: 4em 1em; background-color: #f9f9f9; }
        .container { max-width: 500px; margin: 0 auto; background-color: white; padding: 2em; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        h1 { color: #27ae60; }
        p { color: #34495e; font-size: 1.1em; }
        a { color: #3498db; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🗓️ 予約が完了しました！</h1>
        <p><strong>{{ event_time }}</strong> にて、カレンダーに予定を登録しました。</p>
        <p>関係者の皆様に、Googleカレンダーの招待と確認メールを送信しましたので、ご確認ください。</p>
        <p><a href="/?calendar={{ calendar_id }}">別の日時を選び直す</a></p>
    </div>
</body>
</html>
"""

# --- 補助関数 ---
def get_credentials():
    """サービスアカウントの認証情報を取得する"""
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return creds

def find_available_slots(service, calendar_id):
    """指定されたカレンダーの空き時間スロットを見つける"""
    tz = pytz.timezone(TIMEZONE)
    now_utc = datetime.datetime.now(pytz.utc)
    start_search_time = now_utc + datetime.timedelta(hours=1)

    time_min_utc_str = start_search_time.isoformat()
    time_max_utc_str = (start_search_time + datetime.timedelta(days=SEARCH_DAYS_AHEAD)).isoformat()
    body = {"timeMin": time_min_utc_str, "timeMax": time_max_utc_str, "timeZone": 'UTC', "items": [{'id': calendar_id}]}
    events_result = service.freebusy().query(body=body).execute()
    busy_times_utc = events_result['calendars'][calendar_id]['busy']

    busy_slots = [{'start': datetime.datetime.fromisoformat(b['start']), 'end': datetime.datetime.fromisoformat(b['end'])} for b in busy_times_utc]
    available_slots = []

    current_time = start_search_time.astimezone(tz)
    current_time += datetime.timedelta(minutes=TIME_STEP_MINUTES - current_time.minute % TIME_STEP_MINUTES)

    time_max = datetime.datetime.fromisoformat(time_max_utc_str).astimezone(tz)

    while current_time < time_max:
        if current_time.weekday() not in WORKDAYS:
            current_time = (current_time + datetime.timedelta(days=1)).replace(hour=WORKDAY_START_HOUR, minute=0, second=0, microsecond=0)
            continue

        slot_end_time = current_time + datetime.timedelta(minutes=MEETING_DURATION_MINUTES)
        if not (current_time.time() >= datetime.time(WORKDAY_START_HOUR) and slot_end_time.time() <= datetime.time(WORKDAY_END_HOUR)):
             current_time += datetime.timedelta(minutes=TIME_STEP_MINUTES)
             if current_time.hour >= WORKDAY_END_HOUR:
                 current_time = (current_time + datetime.timedelta(days=1)).replace(hour=WORKDAY_START_HOUR, minute=0, second=0, microsecond=0)
             continue

        current_time_utc = current_time.astimezone(pytz.utc)
        slot_end_time_utc = slot_end_time.astimezone(pytz.utc)

        is_free = all(current_time_utc >= b['end'] or slot_end_time_utc <= b['start'] for b in busy_slots)

        if is_free:
            available_slots.append(current_time)

        current_time += datetime.timedelta(minutes=TIME_STEP_MINUTES)

    return available_slots

def create_message(sender, to, subject, message_text):
    """Gmail送信用メッセージを作成する"""
    message = MIMEText(message_text, 'plain', 'utf-8')
    message['to'] = to
    message['from'] = sender
    message['subject'] = subject
    encoded_message = base64.urlsafe_b64encode(message.as_bytes())
    return {'raw': encoded_message.decode()}


# --- Flaskルート ---
@app.route('/')
def show_schedule_page():
    """候補時間選択ページを表示する"""
    try:
        calendar_id = request.args.get('calendar', 'primary')
        creds = get_credentials()
        service = build('calendar', 'v3', credentials=creds)
        available_slots = find_available_slots(service, calendar_id)

        slots_by_day = {}
        for slot in available_slots:
            day_str = slot.strftime('%Y年%m月%d日(%a)')
            if day_str not in slots_by_day:
                slots_by_day[day_str] = []
            slots_by_day[day_str].append(slot)

        return render_template_string(HTML_TEMPLATE, slots_by_day=slots_by_day, calendar_id=calendar_id)
    except Exception as e:
        error_details = traceback.format_exc()
        print(error_details)
        return f"<h1>エラーが発生しました</h1><hr><pre>{error_details}</pre>"

@app.route('/confirm')
def confirm_event():
    """予約者情報入力ページを表示する"""
    try:
        time_str = request.args.get('time')
        calendar_id = request.args.get('calendar', 'primary')

        if not time_str:
            return "エラー: 時間が選択されていません。", 400

        dt_obj = datetime.datetime.fromisoformat(time_str.replace(' ', '+'))
        formatted_time = dt_obj.strftime('%Y年%m月%d日(%a) %H:%M')

        return render_template_string(HTML_CONFIRM_PROMPT_TEMPLATE, formatted_time=formatted_time, iso_time=time_str, calendar_id=calendar_id)
    except Exception as e:
        error_details = traceback.format_exc()
        print(error_details)
        return f"<h1>エラーが発生しました</h1><hr><pre>{error_details}</pre>"

@app.route('/create_event')
def create_event():
    """カレンダーイベント作成、メール送信を実行する"""
    try:
        time_str = request.args.get('time')
        calendar_id = request.args.get('calendar', 'primary') # 担当社員のメールアドレス
        student_name = request.args.get('name')
        student_email = request.args.get('email')

        if not all([time_str, student_name, student_email]):
            return "エラー: 必要な情報（時間、氏名、メールアドレス）が不足しています。", 400

        creds = get_credentials()
        calendar_service = build('calendar', 'v3', credentials=creds)
        gmail_service = build('gmail', 'v1', credentials=creds)

        employee_name = calendar_id # 担当者名はカレンダーID（メールアドレス）とする

        start_time = datetime.datetime.fromisoformat(time_str.replace(' ', '+'))
        end_time = start_time + datetime.timedelta(minutes=MEETING_DURATION_MINUTES)

        attendees = [
            {'email': student_email},
            {'email': calendar_id},
        ]
        if 'FIXED_ATTENDEE_EMAIL' in globals():
            attendees.append({'email': FIXED_ATTENDEE_EMAIL})

        event = {
            'summary': f'【面談】{student_name}様（担当: {employee_name}）',
            'description': f'{student_name}様との面談です。\n担当: {employee_name}\nこの予定はPythonツールによって自動登録されました。',
            'start': {'dateTime': start_time.isoformat(), 'timeZone': TIMEZONE},
            'end': {'dateTime': end_time.isoformat(), 'timeZone': TIMEZONE},
            'attendees': attendees,
            'conferenceData': {
                'createRequest': {
                    'requestId': uuid.uuid4().hex,
                    'conferenceSolutionKey': {'type': 'hangoutsMeet'}
                }
            }
        }

        print("DEBUG: これからカレンダーイベントを作成します...")
        created_event = calendar_service.events().insert(
            calendarId='primary',
            body=event,
            conferenceDataVersion=1,
            sendNotifications=True
        ).execute()
        print("DEBUG: カレンダーイベントの作成に成功しました。")

        meet_link = created_event.get('hangoutLink', '（Meetリンクは作成されませんでした）')
        formatted_time = start_time.strftime('%Y年%m月%d日(%a) %H:%M')

        sender_email = creds.service_account_email
        
        subject_student = f"【予約完了】{formatted_time}からの面談のご案内"
        body_text_student = f"""{student_name}様\n\nこの度は、面談にご予約いただきありがとうございます。\n以下の内容でご予約を承りました。\n\n担当者: {employee_name}\n日時: {formatted_time}\n接続先URL: {meet_link}\n\n当日はどうぞよろしくお願いいたします。"""
        message_student = create_message(sender_email, student_email, subject_student, body_text_student)
        
        print("DEBUG: これから予約者にメールを送信します...")
        gmail_service.users().messages().send(userId='me', body=message_student).execute()
        print("DEBUG: 予約者へのメール送信に成功しました。")

        subject_internal = f"【面談予約通知】{student_name}様 - {formatted_time}"
        body_text_internal = f"""担当者様\n\n以下の日程で、{student_name}様との面談が予約されました。\n\n日時: {formatted_time}\n氏名: {student_name}\nメールアドレス: {student_email}\n担当社員: {employee_name}\n接続先URL: {meet_link}\n\nGoogleカレンダーに招待が送信されています。"""
        message_internal = create_message(sender_email, TO_EMAIL_ADDRESS, subject_internal, body_text_internal)
        
        print("DEBUG: これから社内担当者にメールを送信します...")
        gmail_service.users().messages().send(userId='me', body=message_internal).execute()
        print("DEBUG: 社内担当者へのメール送信に成功しました。")

        return render_template_string(HTML_CONFIRMATION_TEMPLATE, event_time=formatted_time, calendar_id=calendar_id)

    except Exception as e:
        error_details = traceback.format_exc()
        print(error_details)
        return f"<h1>イベント作成中にエラーが発生しました</h1><hr><pre>{error_details}</pre>"

if __name__ == '__main__':
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        print(f"エラー: サービスアカウントのキーファイル '{SERVICE_ACCOUNT_FILE}' が見つかりません。")
    else:
        print("アプリケーションを起動します...")
        print(f"ローカルテスト用に http://127.0.0.1:8080/ で起動します。")
        print("ブラウザで開いてください。")
        print("カレンダーを指定する場合は ?calendar=your_email@example.com をURLの末尾に追加します。")
        app.run(host='0.0.0.0', port=8080, debug=False)

