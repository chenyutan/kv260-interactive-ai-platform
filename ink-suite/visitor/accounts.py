"""Local account ownership and revocable browser logins.

The original visitor data stays in place. Ownership is additive and can only
be claimed for the current active play session, using its scanned credential.
"""
import hashlib
import hmac
import re
import secrets
import time
import unicodedata
import uuid
from .store import TOKEN,name_value,now

COOKIE='ink_account'
MAX_AGE=7*24*60*60
ITERATIONS=310000

class AccountError(Exception):
    def __init__(self,message,status=400,code='invalid_account'):
        super().__init__(message);self.message=message;self.status=status;self.code=code

def username(value):
    if not isinstance(value,str):raise AccountError('请输入用户名。')
    value=unicodedata.normalize('NFKC',value).strip()
    if not 3<=len(value)<=32 or not all(c.isalnum() or c in '_.-' for c in value):
        raise AccountError('用户名需为3–32个字，仅支持文字、字母、数字、点、短横线或下划线。')
    return value,value.casefold()

def password(value):
    if not isinstance(value,str) or not 8<=len(value)<=128 or '\x00' in value:
        raise AccountError('密码需为8–128个字符。')
    return value

def derive(value,salt):
    return hashlib.pbkdf2_hmac('sha256',value.encode('utf8'),bytes.fromhex(salt),ITERATIONS).hex()

class Accounts:
    def __init__(self,store):
        self.store=store
        with store.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS accounts(
              id TEXT PRIMARY KEY,username TEXT NOT NULL,username_key TEXT UNIQUE NOT NULL,
              name TEXT NOT NULL,salt TEXT NOT NULL,password_hash TEXT NOT NULL,created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS account_logins(
              digest TEXT PRIMARY KEY,account_id TEXT NOT NULL REFERENCES accounts(id),expires REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS account_login_expiry ON account_logins(expires);
            CREATE TABLE IF NOT EXISTS account_sessions(
              session_id TEXT PRIMARY KEY REFERENCES sessions(id),
              account_id TEXT NOT NULL REFERENCES accounts(id),bound TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS account_session_owner ON account_sessions(account_id,bound);
            CREATE TABLE IF NOT EXISTS account_legacy_sessions(
              session_id TEXT PRIMARY KEY REFERENCES sessions(id),seal_id TEXT NOT NULL,
              sealed TEXT NOT NULL,reason TEXT NOT NULL);
            ''')

    @staticmethod
    def public(row):return dict(id=row['id'],username=row['username'],name=row['name'])

    def register(self,values):
        if not isinstance(values,dict) or set(values)-{'username','password','name'}:
            raise AccountError('注册信息格式无效。')
        label,key=username(values.get('username'));secret=password(values.get('password'))
        try:display=name_value(values.get('name') or label[:24])
        except ValueError as exc:raise AccountError(str(exc)) from None
        salt=secrets.token_hex(16);digest=derive(secret,salt)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM accounts WHERE username_key=?',(key,)).fetchone():
                raise AccountError('用户名已注册，请登录或换一个用户名。',409,'username_taken')
            ident=uuid.uuid4().hex
            db.execute('INSERT INTO accounts VALUES(?,?,?,?,?,?,?)',(ident,label,key,display,salt,digest,now()))
            return self.public(db.execute('SELECT * FROM accounts WHERE id=?',(ident,)).fetchone())

    def login(self,values):
        if not isinstance(values,dict) or set(values)!={'username','password'}:
            raise AccountError('登录信息格式无效。')
        _,key=username(values.get('username'));secret=password(values.get('password'))
        with self.store.connect() as db:
            row=db.execute('SELECT * FROM accounts WHERE username_key=?',(key,)).fetchone()
        # Unknown accounts still perform the same bounded password derivation.
        candidate=derive(secret,row['salt'] if row else '00'*16)
        if row is None or not hmac.compare_digest(candidate,row['password_hash']):
            raise AccountError('用户名或密码不正确。',401,'login_failed')
        return self.public(row)

    def issue(self,account_id):
        token=secrets.token_urlsafe(32);digest=hashlib.sha256(token.encode()).hexdigest()
        with self.store.connect() as db:
            db.execute('DELETE FROM account_logins WHERE expires<=?',(time.time(),))
            db.execute('INSERT INTO account_logins VALUES(?,?,?)',(digest,account_id,time.time()+MAX_AGE))
        return token

    def resolve(self,token):
        if not isinstance(token,str) or not TOKEN.fullmatch(token):return None
        digest=hashlib.sha256(token.encode()).hexdigest()
        with self.store.connect() as db:
            row=db.execute('SELECT a.* FROM accounts a JOIN account_logins l ON a.id=l.account_id WHERE l.digest=? AND l.expires>?',
                           (digest,time.time())).fetchone()
        return self.public(row) if row else None

    def logout(self,token):
        if isinstance(token,str) and TOKEN.fullmatch(token):
            with self.store.connect() as db:
                db.execute('DELETE FROM account_logins WHERE digest=?',(hashlib.sha256(token.encode()).hexdigest(),))

    def sessions(self,account_id,offset=0,limit=50):
        if not isinstance(offset,int) or not isinstance(limit,int) or offset<0 or not 1<=limit<=50:
            raise AccountError('记录页码无效。')
        current=self.store.current()['id']
        with self.store.connect() as db:
            rows=db.execute('''SELECT s.token,s.name,s.status,s.started,s.ended,s.id FROM sessions s
                JOIN account_sessions o ON s.id=o.session_id WHERE o.account_id=?
                ORDER BY s.started DESC,s.id DESC LIMIT ? OFFSET ?''',(account_id,limit+1,offset)).fetchall()
        return dict(sessions=[dict(token=r['token'],name=r['name'] or '我的游玩',status=r['status'],started=r['started'],
                                   ended=r['ended'],current=r['id']==current) for r in rows[:limit]],
                    offset=offset,limit=limit,has_more=len(rows)>limit)

    def state(self,account):
        if account is None:return dict(authenticated=False)
        sessions=self.sessions(account['id'])['sessions']
        return dict(authenticated=True,account=account,sessions=sessions,
                    active_token=next((r['token'] for r in sessions if r['current'] and r['status']=='active'),None))

    def rename(self,account,value):
        try:display=name_value(value)
        except ValueError as exc:raise AccountError(str(exc)) from None
        with self.store.connect() as db:db.execute('UPDATE accounts SET name=? WHERE id=?',(display,account['id']))
        return display

    def authorize(self,account,token):
        if account is None:raise AccountError('请先登录账户。',401,'login_required')
        session=self.store.by_token(token)
        with self.store.connect() as db:
            row=db.execute('SELECT account_id FROM account_sessions WHERE session_id=?',(session['id'],)).fetchone()
            legacy=bool(db.execute('SELECT 1 FROM account_legacy_sessions WHERE session_id=?',(session['id'],)).fetchone())
        if row is None:
            if legacy:raise AccountError('这是升级前的匿名记录。请在板端结束本次游玩，再重新扫码绑定新记录。',409,'legacy_session')
            raise AccountError('请先用当前二维码关联本次游玩。',409,'session_unclaimed')
        if row['account_id']!=account['id']:
            raise AccountError('这次游玩已属于其他账户，请使用自己的记录或重新扫码。',403,'not_owner')
        return session

    def claim(self,account,token):
        if account is None:raise AccountError('请先登录账户。',401,'login_required')
        if not isinstance(token,str) or not TOKEN.fullmatch(token):raise AccountError('二维码无效，请重新扫码。')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT s.*,o.account_id FROM sessions s LEFT JOIN account_sessions o ON s.id=o.session_id WHERE s.token=?',(token,)).fetchone()
            if row is None:raise KeyError(token)
            if row['account_id'] is not None:
                if row['account_id']!=account['id']:raise AccountError('本次游玩已被其他账户关联，请重新扫码。',403,'already_claimed')
                return dict(claimed=True,token=token,repeated=True)
            if db.execute('SELECT 1 FROM account_legacy_sessions WHERE session_id=?',(row['id'],)).fetchone():
                raise AccountError('这是升级前的匿名记录。请在板端结束本次游玩，再重新扫码绑定新记录。',409,'legacy_session')
            active=db.execute("SELECT value FROM meta WHERE key='active_session'").fetchone()
            if row['status']!='active' or not active or active['value']!=row['id']:
                raise AccountError('旧的匿名记录不能关联，请扫描主菜单当前二维码。',409,'claim_expired')
            db.execute('INSERT INTO account_sessions VALUES(?,?,?)',(row['id'],account['id'],now()))
            db.execute('UPDATE sessions SET name=? WHERE id=?',(account['name'],row['id']))
        return dict(claimed=True,token=token,repeated=False)
