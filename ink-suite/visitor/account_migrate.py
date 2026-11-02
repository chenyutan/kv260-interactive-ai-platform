"""Explicit one-time account cutover. Never invoked by website or startup.

Call after quiescing the app during deployment. Old anonymous play sessions,
including the current one, remain on disk but cannot be claimed by new users.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

CORE_TABLES=('sessions','meta','artworks','rounds','photo_jobs')
MESSAGE='这是升级前的匿名记录。请在板端结束本次游玩，再重新扫码绑定新记录。'

def _core_snapshot(db):
    result={}
    for table in CORE_TABLES:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():continue
        columns=[r[1] for r in db.execute('PRAGMA table_info('+table+')')]
        digest=hashlib.sha256();count=0
        for row in db.execute('SELECT * FROM '+table+' ORDER BY rowid'):
            digest.update(json.dumps(list(row),ensure_ascii=False,separators=(',',':'),default=str).encode());digest.update(b'\n');count+=1
        result[table]=dict(columns=columns,rows=count,sha256=digest.hexdigest())
    return result

def seal_legacy(database,seal_id='accounts-20261024',undo=False):
    path=Path(database)
    if not path.is_file() or path.is_symlink() or path.absolute()!=path.resolve():
        raise ValueError('Expected existing database without symlink ancestors')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',seal_id):raise ValueError('Invalid seal id')
    db=sqlite3.connect(path,timeout=8)
    try:
        db.execute('PRAGMA foreign_keys=ON')
        for table in ('sessions','meta'):
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
                raise ValueError('Not an existing visitor database')
        before=_core_snapshot(db)
        db.executescript('''
        CREATE TABLE IF NOT EXISTS account_legacy_sessions(
          session_id TEXT PRIMARY KEY REFERENCES sessions(id),seal_id TEXT NOT NULL,sealed TEXT NOT NULL,reason TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS account_migrations(
          id TEXT PRIMARY KEY,created TEXT NOT NULL,mode TEXT NOT NULL,sealed_count INTEGER NOT NULL);
        ''')
        with db:
            db.execute('BEGIN IMMEDIATE')
            prior=db.execute('SELECT sealed_count FROM account_migrations WHERE id=?',(seal_id,)).fetchone()
            if undo:
                count=db.execute('DELETE FROM account_legacy_sessions WHERE seal_id=?',(seal_id,)).rowcount
                db.execute('DELETE FROM account_migrations WHERE id=?',(seal_id,))
                repeated=prior is None
            elif prior:
                count=prior[0];repeated=True
            else:
                bound_table=bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='account_sessions'").fetchone())
                query=('SELECT s.id FROM sessions s LEFT JOIN account_sessions o ON o.session_id=s.id WHERE o.session_id IS NULL'
                       if bound_table else 'SELECT id FROM sessions')
                stamp=datetime.now(timezone.utc).isoformat(timespec='seconds');count=0
                for (ident,) in db.execute(query).fetchall():
                    count+=db.execute('INSERT OR IGNORE INTO account_legacy_sessions VALUES(?,?,?,?)',
                                      (ident,seal_id,stamp,'pre-account anonymous history')).rowcount
                db.execute('INSERT INTO account_migrations VALUES(?,?,?,?)',(seal_id,stamp,'seal-anonymous',count))
                repeated=False
            after=_core_snapshot(db)
            if before!=after:raise RuntimeError('Core visitor data changed during account cutover')
        return dict(passed=True,seal_id=seal_id,action='undo' if undo else 'seal',
                    sealed_count=count,repeated=repeated,core_preserved=True,core_tables=after,
                    deletes_user_data=False,clears_canvas=False)
    finally:db.close()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True)
    parser.add_argument('--seal-id',default='accounts-20261024')
    parser.add_argument('--undo',action='store_true',help='Deployment rollback only; removes this seal metadata')
    args=parser.parse_args()
    print(json.dumps(seal_legacy(args.database,args.seal_id,args.undo),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
