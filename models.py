from app import db # app.pyで作成したdbオブジェクトをインポート
from flask_login import UserMixin

class User(UserMixin, db.Model):
    __tablename__ = 'users' # テーブル名

    id = db.Column(db.Integer, primary_key=True)
    google_id = db.Column(db.String(100), unique=True, nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=True)
    refresh_token = db.Column(db.String(500), nullable=True)