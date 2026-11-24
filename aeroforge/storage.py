"""Durable local tracking. Runtime data belongs on an ext4 volume, outside Git."""
from __future__ import annotations
import datetime as dt
import os
import pathlib
import sqlite3
import json


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def home() -> pathlib.Path:
    return pathlib.Path(os.environ.get('AEROFORGE_STATE', '~/.local/share/aeroforge')).expanduser().resolve()


def connect() -> sqlite3.Connection:
    root=home()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == 'posix':
        os.chmod(root, 0o700)
    con=sqlite3.connect(root/'aeroforge.sqlite',timeout=15)
    con.row_factory=sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    con.execute('PRAGMA busy_timeout=15000')
    con.execute('PRAGMA journal_mode=WAL')
    con.executescript('''
       CREATE TABLE IF NOT EXISTS repositories (
           full_name TEXT PRIMARY KEY, visibility TEXT NOT NULL,
           default_branch TEXT, archived INTEGER NOT NULL DEFAULT 0,
           source TEXT NOT NULL, scanned_at TEXT NOT NULL);
       CREATE TABLE IF NOT EXISTS workflows (
           repo TEXT NOT NULL, path TEXT NOT NULL, name TEXT NOT NULL,
           enabled INTEGER NOT NULL DEFAULT 1, sha256 TEXT, triggers TEXT NOT NULL,
           scanned_at TEXT NOT NULL, PRIMARY KEY(repo,path),
           FOREIGN KEY(repo) REFERENCES repositories(full_name));
       CREATE TABLE IF NOT EXISTS findings (
           id TEXT PRIMARY KEY, repo TEXT NOT NULL, path TEXT NOT NULL,
           rule TEXT NOT NULL, severity TEXT NOT NULL, confidence TEXT NOT NULL,
           summary TEXT NOT NULL, recommendation TEXT NOT NULL,
           scanned_at TEXT NOT NULL,
           FOREIGN KEY(repo) REFERENCES repositories(full_name));
       CREATE TABLE IF NOT EXISTS jobs (
           id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
           target TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
           created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
           report_dir TEXT, outcome TEXT);
       CREATE TABLE IF NOT EXISTS events (
           id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL,
           at TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL,
           FOREIGN KEY(job_id) REFERENCES jobs(id));
       CREATE TABLE IF NOT EXISTS scans (
           id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL,
           owner TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT,
           repos_scanned INTEGER NOT NULL DEFAULT 0,
           workflows_scanned INTEGER NOT NULL DEFAULT 0,
           errors INTEGER NOT NULL DEFAULT 0, partial INTEGER NOT NULL DEFAULT 1);
    ''')
    con.commit()
    return con


def upsert_repository(con,repo:dict,source='github'):
    name=repo['full_name']
    visibility=repo.get('visibility') or ('private' if repo.get('private') else 'public')
    con.execute('''INSERT INTO repositories(full_name,visibility,default_branch,archived,source,scanned_at)
        VALUES(?,?,?,?,?,?) ON CONFLICT(full_name) DO UPDATE SET
        visibility=excluded.visibility,default_branch=excluded.default_branch,
        archived=excluded.archived,source=excluded.source,scanned_at=excluded.scanned_at''',
        (name,visibility,repo.get('default_branch'),int(bool(repo.get('archived'))),source,now()))


def update_workflow(con, repo:str, path:str, name:str, enabled:bool, digest:str, triggers:list, findings:list):
    con.execute('''INSERT INTO workflows(repo,path,name,enabled,sha256,triggers,scanned_at)
        VALUES (?,?,?,?,?,?,?) ON CONFLICT(repo,path) DO UPDATE SET
        name=excluded.name,enabled=excluded.enabled,sha256=excluded.sha256,
        triggers=excluded.triggers,scanned_at=excluded.scanned_at''',
        (repo,path,name,int(enabled),digest,json.dumps(triggers),now()))
    con.execute('DELETE FROM findings WHERE repo=? AND path=?',(repo,path))
    for f in findings:
        con.execute('''INSERT INTO findings(id,repo,path,rule,severity,confidence,summary,recommendation,scanned_at)
                       VALUES(?,?,?,?,?,?,?,?,?)''',(f['id'],repo,path,f['rule'],f['severity'],f['confidence'],
                                  f['summary'],f['recommendation'],now()))


def stats(con):
    return {t:con.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('repositories','workflows','findings','jobs')}
