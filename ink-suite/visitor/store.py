"""Transactional, persistent visitor data. No network score ingestion."""
from contextlib import contextmanager
from datetime import datetime, timezone
import csv
import hashlib
import html
import io
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import unicodedata
import uuid
import zipfile

GAMES={'sword':'御剑山河','fight':'格斗','fruit':'水果忍者','koi':'鲤跃龙门','taiko':'鼓动山河','platformer':'超级玛丽','runner':'地铁跑酷','kart':'赛车'}
TOKEN=re.compile(r'^[A-Za-z0-9_-]{43}$')
IDENT=re.compile(r'^[a-f0-9]{32}$')
FIELDS=('score','best_combo','combo','time','elapsed','duration','result','hp','enemy_hp','hits','blocks',
        'perfect_blocks','fruits_cut','bombs_hit','missed','rings','pearls','lives','grade','rank','accuracy',
        'difficulty','practice','judgment','mode','counts','move_counts','kills','graze','power','end_reason',
        'distance','total_notes','strikes','empty_strikes','bonus_score','coins','completed_stages','enemies_defeated','level_index','deaths','pipes_used',
        'position','lap','total_laps','best_lap','finish_time','drifts','items_used','boosts','collisions')

def now():return datetime.now(timezone.utc).isoformat(timespec='seconds')
def dumps(value):return json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':'))

def name_value(value):
    if not isinstance(value,str):raise ValueError('请输入名字')
    value=unicodedata.normalize('NFKC',value)
    if any(unicodedata.category(c).startswith('C') for c in value):raise ValueError('名字不能含控制符')
    value=value.strip()
    if not 1<=len(value)<=24 or any(unicodedata.category(c).startswith('C') or c in '<>&' for c in value):
        raise ValueError('名字需为 1–24 个字，不能含控制符或 < > &')
    return value

def summary(value):
    """Only bounded report fields; poses, images and diagnostics never enter DB."""
    if not isinstance(value,dict):raise ValueError('Invalid game summary')
    output={}
    for key in FIELDS:
        item=value.get(key)
        if isinstance(item,(int,float)) and not isinstance(item,bool):
            if not math.isfinite(item) or abs(item)>1e12:raise ValueError('Invalid numeric score')
            output[key]=round(item,4)
        elif isinstance(item,str) and len(item)<=80:output[key]=item
        elif key in ('counts','move_counts') and isinstance(item,dict) and len(item)<=40:
            output[key]={str(k)[:40]:v for k,v in item.items() if isinstance(v,int) and not isinstance(v,bool) and 0<=v<=1e9}
    return output

def rules(game,data):
    if game=='taiko':return '/'.join(str(data.get(k,d)) for k,d in (('difficulty','easy'),('practice','full'),('judgment','relaxed')))
    if game=='fruit':return str(data.get('mode','arcade'))
    if game=='platformer' and data.get('mode')=='thextech-aod':return 'thextech-aod'
    return 'standard'

def rules_label(value):
    labels={'easy':'简单','normal':'普通','hard':'困难','full':'完整曲目','intro':'开篇练习',
            'alternate':'交替练习','finale':'终章练习','relaxed':'体感判定','strict':'严谨判定',
            'arcade':'进阶玩法','classic':'经典玩法','standard':'标准玩法','thextech-aod':'TheXTech 冒险'}
    return ' · '.join(labels.get(part,part) for part in value.split('/'))

def score(game,data):
    if game=='fight':
        # A documented outcome-first score, never mixed with another game.
        result=data.get('result','')
        return max(0,({'胜':10000,'win':10000,'平':5000,'draw':5000}.get(result,0))+
                   int(data.get('hp',0))*10+int(data.get('hits',0))*20+int(data.get('perfect_blocks',0))*30)
    return max(0,int(data.get('score',0)))

class VisitorStore:
    def __init__(self,directory):
        self.root=Path(directory).resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.art_root=self.root/'artworks';self.art_root.mkdir(exist_ok=True)
        self.path=self.root/'visitors.sqlite3'
        with self.connect() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,token TEXT UNIQUE NOT NULL,name TEXT,
              status TEXT NOT NULL,started TEXT NOT NULL,ended TEXT,finish_key TEXT,finish_art_hash TEXT);
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS artworks(id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES sessions(id),
              sha TEXT NOT NULL,title TEXT NOT NULL,created TEXT NOT NULL,marks INTEGER NOT NULL,UNIQUE(session_id,sha));
            CREATE TABLE IF NOT EXISTS native_art_sources(
              session_id TEXT NOT NULL REFERENCES sessions(id),name TEXT NOT NULL,
              art_id TEXT NOT NULL REFERENCES artworks(id),PRIMARY KEY(session_id,name));
            CREATE TABLE IF NOT EXISTS rounds(id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES sessions(id),
              game TEXT NOT NULL,rules TEXT NOT NULL,status TEXT NOT NULL,started TEXT NOT NULL,updated TEXT NOT NULL,
              ended TEXT,data TEXT NOT NULL,score INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS round_board ON rounds(game,rules,status,score DESC);
            CREATE TABLE IF NOT EXISTS health_practices(
              id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES sessions(id),
              course_id TEXT NOT NULL,created TEXT NOT NULL,updated TEXT NOT NULL,data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS health_owner ON health_practices(session_id,course_id,updated);
            ''')
        self.current()

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=8);db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON');db.execute('PRAGMA busy_timeout=8000')
        try:
            with db:yield db
        finally:db.close()

    def _new(self,db):
        ident=uuid.uuid4().hex;token=secrets.token_urlsafe(32)
        db.execute('INSERT INTO sessions(id,token,status,started) VALUES(?,?,?,?)',(ident,token,'active',now()))
        db.execute("INSERT OR REPLACE INTO meta VALUES('active_session',?)",(ident,))
        return ident

    def current(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT s.* FROM sessions s JOIN meta m ON s.id=m.value WHERE m.key='active_session'").fetchone()
            if row is None or row['status']=='archived':
                row=db.execute('SELECT * FROM sessions WHERE id=?',(self._new(db),)).fetchone()
            return dict(row)

    def by_token(self,token):
        if not isinstance(token,str) or not TOKEN.fullmatch(token):raise KeyError(token)
        with self.connect() as db:row=db.execute('SELECT * FROM sessions WHERE token=?',(token,)).fetchone()
        if row is None:raise KeyError(token)
        return dict(row)

    def owned_tokens_for_session(self,token):
        """Native clear scope: this account's sessions, or this anonymous play.

        Native callers already hold the current in-process play credential.
        HTTP callers must additionally pass the portal's login/owner check.
        """
        session=self.by_token(token)
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='account_sessions'").fetchone():return [token]
            row=db.execute('SELECT account_id FROM account_sessions WHERE session_id=?',(session['id'],)).fetchone()
            if row is None:return [token]
            return [r['token'] for r in db.execute('''SELECT s.token FROM sessions s
                JOIN account_sessions o ON o.session_id=s.id WHERE o.account_id=?''',(row['account_id'],))]

    def has_account_owner(self,token):
        """An explicit claim, never inferred from an anonymous play token."""
        session=self.by_token(token)
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='account_sessions'").fetchone():return False
            return db.execute('SELECT 1 FROM account_sessions WHERE session_id=?',(session['id'],)).fetchone() is not None

    def record_health(self,session_id,report):
        """Index a saved engine report under the owner captured at session start.

        The report's UUID is idempotent and can never move to another visitor.
        Raw reports and their disk paths are deliberately not stored here.
        """
        from ai_assistant.health import sanitize_report
        if not isinstance(session_id,str) or not IDENT.fullmatch(session_id):raise ValueError('Invalid captured visitor')
        payload=sanitize_report(report);stamp=now()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM sessions WHERE id=?',(session_id,)).fetchone():raise KeyError(session_id)
            previous=db.execute('SELECT session_id,course_id FROM health_practices WHERE id=?',(payload['practice_id'],)).fetchone()
            if previous and (previous['session_id']!=session_id or previous['course_id']!=payload['course_id']):
                raise ValueError('Practice owner cannot change')
            db.execute('''INSERT INTO health_practices VALUES(?,?,?,?,?,?)
              ON CONFLICT(id) DO UPDATE SET updated=excluded.updated,data=excluded.data''',
              (payload['practice_id'],session_id,payload['course_id'],stamp,stamp,dumps(payload)))
        return dict(saved=True,practice_id=payload['practice_id'],course_id=payload['course_id'])

    def health_summary(self,token,course_id='baduanjin'):
        """Only explicit account ownership, or the current anonymous play.

        Never infer ownership from legacy health report files or timestamps.
        """
        from ai_assistant.health import course,summarize
        course(course_id)
        tokens=self.owned_tokens_for_session(token)
        identifiers=[self.by_token(value)['id'] for value in tokens]
        placeholders=','.join('?' for _ in identifiers)
        with self.connect() as db:
            where='session_id IN ('+placeholders+') AND course_id=?'
            total=db.execute('SELECT COUNT(*) FROM health_practices WHERE '+where,(*identifiers,course_id)).fetchone()[0]
            rows=db.execute('SELECT data FROM health_practices WHERE '+where+' ORDER BY updated DESC,id DESC LIMIT 50',
                            (*identifiers,course_id)).fetchall()
        return summarize([json.loads(row['data']) for row in rows],total,course_id)

    def rename(self,token,value):
        name=name_value(value);session=self.by_token(token)
        with self.connect() as db:db.execute('UPDATE sessions SET name=? WHERE id=?',(name,session['id']))
        return self.view(token)

    def add_art(self,session_id,png,marks=0,title='身随墨动 · 水墨画', *, source_name=None):
        if not isinstance(png,bytes) or not png.startswith(b'\x89PNG\r\n\x1a\n') or not 8<=len(png)<=30_000_000:
            raise ValueError('Invalid PNG artwork')
        if source_name is not None and not re.fullmatch(r'ink-\d{8}-\d{6}-\d{6}\.png', source_name):
            raise ValueError('Invalid native artwork name')
        digest=hashlib.sha256(png).hexdigest()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            session=db.execute('SELECT status FROM sessions WHERE id=?',(session_id,)).fetchone()
            if session is None or session['status']=='archived':raise ValueError('Visitor session is closed')
            if source_name is not None:
                active=db.execute("SELECT value FROM meta WHERE key='active_session'").fetchone()
                if session['status']!='active' or not active or active['value']!=session_id:
                    raise ValueError('本次游玩已结束，作品仅保存在板卡')
                linked=db.execute('SELECT a.id,a.sha FROM native_art_sources n JOIN artworks a ON a.id=n.art_id '
                                  'WHERE n.session_id=? AND n.name=?',(session_id,source_name)).fetchone()
                if linked:
                    if linked['sha']!=digest:raise ValueError('作品文件名已对应另一幅画')
                    return linked['id']
            row=db.execute('SELECT id FROM artworks WHERE session_id=? AND sha=?',(session_id,digest)).fetchone()
            if row:
                ident=row['id']
            else:
                ident=uuid.uuid4().hex;path=self.art_root/(ident+'.png');temporary=path.with_suffix('.tmp')
                with temporary.open('wb') as stream:stream.write(png);stream.flush();os.fsync(stream.fileno())
                temporary.replace(path)
                db.execute('INSERT INTO artworks VALUES(?,?,?,?,?,?)',(ident,session_id,digest,title,now(),max(0,int(marks))))
            if source_name is not None:
                db.execute('INSERT INTO native_art_sources VALUES(?,?,?)',(session_id,source_name,ident))
            return ident

    def record(self,session_id,round_id,game,data,status='active',started=None):
        if game not in GAMES or not IDENT.fullmatch(round_id) or status not in ('active','completed','interrupted'):
            raise ValueError('Invalid trusted game result')
        data=summary(data);stamp=now()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            session=db.execute('SELECT status FROM sessions WHERE id=?',(session_id,)).fetchone()
            if session is None or session['status']!='active':return False
            old=db.execute('SELECT session_id,status FROM rounds WHERE id=?',(round_id,)).fetchone()
            if old and (old['session_id']!=session_id or old['status']!='active'):return False
            db.execute('''INSERT INTO rounds VALUES(?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(id) DO UPDATE SET status=excluded.status,updated=excluded.updated,ended=excluded.ended,
              data=excluded.data,score=excluded.score,rules=excluded.rules''',
              (round_id,session_id,game,rules(game,data),status,started or stamp,stamp,
               stamp if status!='active' else None,dumps(data),score(game,data)))
        return True

    def recover_rounds(self):
        """Host startup occurs before spawning children; previous live rounds were interrupted."""
        with self.connect() as db:
            return db.execute("UPDATE rounds SET status='interrupted',ended=?,updated=? WHERE status='active'",(now(),now())).rowcount

    def replay_pending(self):
        """Only locally written retry files; called before closing/recovering rounds."""
        restored=0
        for path in self.root.glob('pending-*.json'):
            if path.is_symlink() or path.stat().st_size>100000:raise ValueError('Invalid pending result')
            job=json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(job,list) or len(job)!=6:raise ValueError('Invalid pending result')
            self.record(*job);path.unlink();restored+=1
        return restored

    def begin_finish(self,session_id,finish_key,art_hash,expected_art_count=None,*,expected_view=None,ended=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM sessions WHERE id=?',(session_id,)).fetchone()
            if row is None:raise KeyError(session_id)
            if row['status']!='active':return dict(row)
            if expected_art_count is not None:
                count=db.execute('SELECT COUNT(*) FROM artworks WHERE session_id=?',(session_id,)).fetchone()[0]
                if count!=expected_art_count:raise RuntimeError('刚有新的作品保存，请重试归档')
            # BEGIN IMMEDIATE excludes writers while we compare the ZIP's source snapshot.
            if expected_view is not None and self.view(row['token'])!=expected_view:
                raise RuntimeError('刚有新的作品、成绩或信息更新，请重试归档')
            ended=ended or now()
            db.execute("UPDATE sessions SET status='ending',finish_key=?,finish_art_hash=?,ended=? WHERE id=?",(finish_key,art_hash,ended,session_id))
            db.execute("UPDATE rounds SET status='interrupted',ended=? WHERE session_id=? AND status='active'",(ended,session_id))
            return dict(db.execute('SELECT * FROM sessions WHERE id=?',(session_id,)).fetchone())

    def complete_finish(self,session_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT status FROM sessions WHERE id=?',(session_id,)).fetchone()
            if not row:raise KeyError(session_id)
            if row['status']=='active':raise ValueError('Finish was not requested')
            if row['status']=='ending':
                db.execute("UPDATE sessions SET status='archived',ended=COALESCE(ended,?) WHERE id=?",(now(),session_id));self._new(db)
        return self.current()

    def view(self,token):
        session=self.by_token(token)
        with self.connect() as db:
            art=[dict(r) for r in db.execute('SELECT id,title,created,marks FROM artworks WHERE session_id=? ORDER BY created DESC,id DESC',(session['id'],))]
            rounds=[]
            for row in db.execute('SELECT * FROM rounds WHERE session_id=? ORDER BY started,id',(session['id'],)):
                value=dict(row);value.pop('session_id');value['data']=json.loads(value['data']);value['title']=GAMES[value['game']];value['rules_label']=rules_label(value['rules']);rounds.append(value)
        return dict(name=session['name'] or '游客',status=session['status'],started=session['started'],ended=session['ended'],
                    artworks=art,rounds=rounds,games=GAMES,ranking_note='仅完成局入榜；太鼓按难度、练习段和判定规则分榜，水果按玩法分榜。格斗排名分 = 胜10000/平5000 + 剩余体力×10 + 命中×20 + 完美格挡×30。')

    def artwork(self,token,ident):
        session=self.by_token(token)
        if not IDENT.fullmatch(ident):raise KeyError(ident)
        with self.connect() as db:row=db.execute('SELECT * FROM artworks WHERE id=? AND session_id=?',(ident,session['id'])).fetchone()
        if row is None:raise KeyError(ident)
        path=self.art_root/(ident+'.png')
        if path.is_symlink() or path.resolve().parent!=self.art_root:raise KeyError(ident)
        return dict(row),path.read_bytes()

    def leaderboard(self,game=None,cohort=None,limit=20,registered_only=False):
        if game is not None and game not in GAMES:raise ValueError('Unknown game')
        with self.connect() as db:
            if registered_only:
                rows=db.execute('''SELECT r.game,r.rules,r.score,r.ended,r.id,a.name FROM rounds r
                  JOIN sessions s ON s.id=r.session_id JOIN account_sessions o ON s.id=o.session_id
                  JOIN accounts a ON a.id=o.account_id WHERE r.status='completed'
                  ORDER BY r.score DESC,r.ended,r.id''').fetchall()
            else:
                rows=db.execute('''SELECT r.game,r.rules,r.score,r.ended,r.id,s.name FROM rounds r
                  JOIN sessions s ON s.id=r.session_id WHERE r.status='completed'
                  ORDER BY r.score DESC,r.ended,r.id''').fetchall()
        groups={}
        for row in rows:
            if game and row['game']!=game or cohort and row['rules']!=cohort:continue
            key=(row['game'],row['rules']);bucket=groups.setdefault(key,[])
            if len(bucket)>=limit:continue
            bucket.append(dict(rank=len(bucket)+1,name=row['name'] or '游客',score=row['score'],completed_at=row['ended']))
        return [dict(game=g,title=GAMES[g],rules=c,rules_label=rules_label(c),items=v) for (g,c),v in sorted(groups.items())]

    def report(self,token,format='html',final=False,view=None,*,ended=None):
        self.by_token(token)
        view=self.view(token) if view is None else json.loads(json.dumps(view,ensure_ascii=False))
        if final:
            ended=ended or now()
            view.update(status='archived',ended=ended)
            for row in view['rounds']:
                if row['status']=='active':
                    row.update(status='interrupted',ended=ended)
        if format=='json':return json.dumps(view,ensure_ascii=False,allow_nan=False,indent=2).encode('utf-8')
        if format=='csv':
            output=io.StringIO();writer=csv.writer(output);writer.writerow(['游客','游戏','规则','状态','得分','开始时间','结束时间','详情'])
            for row in view['rounds']:
                values=[view['name'],row['title'],row['rules'],row['status'],row['score'],row['started'],row['ended'] or '',dumps(row['data'])]
                # Spreadsheet formula injection is possible even with CSV quoting.
                writer.writerow(["'"+v if isinstance(v,str) and v[:1] in '=+-@' else v for v in values])
            return ('\ufeff'+output.getvalue()).encode('utf-8')
        if format=='zip':
            output=io.BytesIO()
            with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
                for kind in ('html','json','csv'):archive.writestr('report.'+kind,self.report(token,kind,view=view))
                for item in view['artworks']:archive.writestr('artworks/'+item['id']+'.png',self.artwork(token,item['id'])[1])
            return output.getvalue()
        if format!='html':raise ValueError('Unknown report format')
        e=lambda value:html.escape(str(value))
        state_labels={'active':'进行中','completed':'已完成','interrupted':'中途退出或中断'}
        rows=''.join('<tr>'+''.join('<td>'+e(v)+'</td>' for v in (r['title'],r['rules_label'],state_labels[r['status']],r['score'],r['ended'] or '进行中',dumps(r['data'])))+'</tr>' for r in view['rounds'])
        return ('''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>墨境成绩报告</title>
          <body><h1>墨境 · '''+e(view['name'])+''' 的游玩报告</h1><p>开始：'''+e(view['started'])+'　结束：'+e(view['ended'] or '尚在游玩')+'</p><p>作品 '+str(len(view['artworks']))+' 幅；完成 '+str(sum(r['status']=='completed' for r in view['rounds']))+' 局。</p><p>'+e(view['ranking_note'])+'</p><table border="1" cellpadding="8"><thead><tr><th>游戏</th><th>规则</th><th>状态</th><th>排名分</th><th>结束时间</th><th>各项成绩</th></tr></thead><tbody>'+rows+'</tbody></table><p>中途退出与中断记录保留在报告中，不进入排行榜。保存此文件可离线查看，也可用浏览器打印成 PDF。</p></body></html>').encode('utf-8')
