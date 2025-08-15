import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy

# --- アプリケーションの基本設定 ---
app = Flask(__name__)

# セッション情報を暗号化するための秘密鍵。Renderの環境変数で設定します。
# 必ず'your_secret_key'から変更してください。
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your_secret_key')

# データベースの接続設定。Renderの環境変数から接続URLを取得します。
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# データベースオブジェクトの初期化
db = SQLAlchemy(app)

# --- ルート（URLの定義） ---
@app.route('/')
def index():
    return "アプリケーションのセットアップが完了しました！"

# --- 実行 ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)