# -*- coding: utf-8 -*-
import base64
import datetime
import os
import traceback
import uuid

import pytz
from email.mime.text import MIMEText
from flask import Flask, render_template_string, request
from google.oauth2 import service_account # 変更
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# --- 設定エリア ---
# 必要なAPIの権限（スコープ）
SCOPES = [
    'https://www.googleapis.com/auth/calendar.readonly',
    'https://www.googleapis.com/auth/calendar.events',
    'https://www.googleapis.com/auth/gmail.send'
]

# ★★★★★ 必ず設定してください ★★★★★
# 予約通知を受け取る社内担当者のメールアドレス
TO_EMAIL_ADDRESS = 'keiichiro.yoshino@bizreach.co.jp' 
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
# --- 設定エリアここまで ---

app = Flask(__name__)

# --- HTMLテンプレート ---
# (HTML部分は変更ないため、ここでは省略します。お手元のコードのままで大丈夫です)
HTML_TEMPLATE = """ ... """
HTML_CONFIRM_PROMPT_TEMPLATE = """ ... """
HTML_CONFIRMATION_TEMPLATE = """ ... """

# --- 補助関数 ---
def get_credentials():
    """サービスアカウントの認証情報を取得する"""
    
    # ★★★★★ ここにダウンロードしたサービスアカウントのJSONファイル名を設定 ★★★★★
    SERVICE_ACCOUNT_FILE = 'schedule-adjustment-service-account-key.json' # あなたが保存したJSONファイル名
    # ★★★★★ 設定ここまで ★★★★★
    
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
        if not (current_time.replace(hour=WORKDAY_START_HOUR, minute=0) <= current_time and slot_end_time.time() <= datetime.time(WORKDAY_END_HOUR)):
             current_time = (current_time + datetime.timedelta(days=1)).replace(hour=WORKDAY_START_HOUR, minute=0, second=0, microsecond=0)
             continue
        
        current_time_utc = current_time.astimezone(pytz.utc)
        slot_end_time_utc = slot_end_time.astimezone(pytz.utc)

        is_free = all(max(current_time_utc, b['start']) >= min(slot_end_time_utc, b['end']) for b in busy_slots)

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
        calendar_id = request.args.get('calendar', 'primary')
        student_name = request.args.get('name')
        student_email = request.args.get('email')

        if not all([time_str, student_name, student_email]):
            return "エラー: 必要な情報（時間、氏名、メールアドレス）が不足しています。", 400
        
        creds = get_credentials()
        calendar_service = build('calendar', 'v3', credentials=creds)
        gmail_service = build('gmail', 'v1', credentials=creds)

        # 担当社員名を取得
        try:
            calendar_info = calendar_service.calendars().get(calendarId=calendar_id).execute()
            employee_name = calendar_info.get('summary', calendar_id)
        except Exception:
            employee_name = calendar_id 
        
        start_time = datetime.datetime.fromisoformat(time_str.replace(' ', '+'))
        end_time = start_time + datetime.timedelta(minutes=MEETING_DURATION_MINUTES)
        
        # カレンダーイベントの作成
        event = {
            'summary': f'【面談】{student_name}様',
            'description': f'{student_name}様との面談です。\n担当: {employee_name}\nこの予定はPythonツールによって自動登録されました。',
            'start': {'dateTime': start_time.isoformat(), 'timeZone': TIMEZONE},
            'end': {'dateTime': end_time.isoformat(), 'timeZone': TIMEZONE},
            'attendees': [
                {'email': student_email},
                {'email': 'rookie@bizreach.co.jp'} # ★ここに追加したい固定のメールアドレスを記入
            ],
            'conferenceData': {
                'createRequest': {
                    'requestId': uuid.uuid4().hex,
                    'conferenceSolutionKey': {'type': 'hangoutsMeet'}
                }
            }
        }
        
        created_event = calendar_service.events().insert(
            calendarId=calendar_id, 
            body=event, 
            conferenceDataVersion=1,
            sendNotifications=True
        ).execute()
        
        meet_link = created_event.get('hangoutLink', '（Meetリンクは作成されませんでした）')
        formatted_time = start_time.strftime('%Y年%m月%d日(%a) %H:%M')
        
        # メール送信
        # サービスアカウント自身のメールアドレスを差出人とする
        sender_email = creds.service_account_email

        # 学生への通知メール
        subject_student = f"【予約完了】{formatted_time}からの面談のご案内"
        body_text_student = f"""{student_name}様\n\nこの度は、面談にご予約いただきありがとうございます。\n以下の内容でご予約を承りました。\n\n担当者: {employee_name}\n日時: {formatted_time}\n接続先URL: {meet_link}\n\n当日はどうぞよろしくお願いいたします。"""
        message_student = create_message(sender_email, student_email, subject_student, body_text_student)
        gmail_service.users().messages().send(userId='me', body=message_student).execute()

        # 社内担当者への通知メール
        subject_internal = f"【面談予約通知】{student_name}様 - {formatted_time}"
        body_text_internal = f"""担当者様\n\n以下の日程で、{student_name}様との面談が予約されました。\n\n日時: {formatted_time}\n氏名: {student_name}\nメールアドレス: {student_email}\n担当社員: {employee_name}\n接続先URL: {meet_link}\n\nGoogleカレンダーにも予定が登録されています。"""
        message_internal = create_message(sender_email, TO_EMAIL_ADDRESS, subject_internal, body_text_internal)
        gmail_service.users().messages().send(userId='me', body=message_internal).execute()

        return render_template_string(HTML_CONFIRMATION_TEMPLATE, event_time=formatted_time, calendar_id=calendar_id)
        
    except Exception as e:
        error_details = traceback.format_exc()
        print(error_details)
        return f"<h1>イベント作成中にエラーが発生しました</h1><hr><pre>{error_details}</pre>"

if __name__ == '__main__':
    # サービスアカウントのキーファイルがあるかだけ確認
    if not os.path.exists('service-account-key.json'): # ★get_credentials内のファイル名と合わせる
        print("エラー: サービスアカウントのキーファイルが見つかりません。")
    else:
        print("アプリケーションを起動します...")
        print("ブラウザで http://127.0.0.1:5000/ を開いてください。")
        print("特定のカレンダーを指定する場合は ?calendar=your_email@example.com をURLの末尾に追加します。")
        app.run(debug=True, port=5000)

