"""Banco SQLite do ISE: registros (docs), contas, auditoria e controle do backup."""
import json
import os
import sqlite3
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
BANCO = os.environ.get("ISE_BANCO", str(RAIZ / "dados" / "ise.sqlite3"))
MAX_DOC = 2 * 1024 * 1024  # 2 MB por registro (a plataforma do Claude aceitava 256 KB)

ESQUEMA = """
CREATE TABLE IF NOT EXISTS docs (
  path TEXT PRIMARY KEY, colecao TEXT NOT NULL, data TEXT, versao INTEGER NOT NULL DEFAULT 1,
  seq INTEGER NOT NULL, atualizado_em REAL NOT NULL, atualizado_por TEXT);
CREATE INDEX IF NOT EXISTS docs_seq ON docs(seq);
CREATE TABLE IF NOT EXISTS contador (id INTEGER PRIMARY KEY CHECK (id = 1), seq INTEGER NOT NULL);
INSERT OR IGNORE INTO contador VALUES (1, 0);
CREATE TABLE IF NOT EXISTS usuarios (
  id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, nome TEXT NOT NULL, senha TEXT NOT NULL,
  papel TEXT NOT NULL CHECK (papel IN ('admin', 'tst')), ativo INTEGER NOT NULL DEFAULT 1, criado_em REAL NOT NULL);
CREATE TABLE IF NOT EXISTS auditoria (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, usuario TEXT, op TEXT NOT NULL, alvo TEXT, ip TEXT, detalhe TEXT);
CREATE TABLE IF NOT EXISTS backups (
  id INTEGER PRIMARY KEY AUTOINCREMENT, inicio REAL, fim REAL, nome TEXT, ok INTEGER, registros INTEGER,
  arquivos INTEGER, erro TEXT, drive_id TEXT, md5 TEXT);
CREATE TABLE IF NOT EXISTS drive_arquivos (sha256 TEXT PRIMARY KEY, drive_id TEXT NOT NULL, nome TEXT, enviado_em REAL);
"""


def conectar(caminho=None):
    caminho = caminho or BANCO
    Path(caminho).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(caminho, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(ESQUEMA)
    return con


def gravar_doc(con, path, data, usuario):
    """Grava (ou apaga, com data=None) um registro e devolve a nova versão."""
    col = path.split("/")[0]
    corpo = None if data is None else json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if corpo is not None and len(corpo.encode()) > MAX_DOC:
        raise ValueError("registro maior que o limite")
    con.execute("BEGIN IMMEDIATE")
    try:
        seq = con.execute("UPDATE contador SET seq = seq + 1 WHERE id = 1 RETURNING seq").fetchone()[0]
        con.execute(
            """INSERT INTO docs (path, colecao, data, versao, seq, atualizado_em, atualizado_por)
               VALUES (?, ?, ?, 1, ?, ?, ?)
               ON CONFLICT(path) DO UPDATE SET data = excluded.data, versao = docs.versao + 1,
                 seq = excluded.seq, atualizado_em = excluded.atualizado_em, atualizado_por = excluded.atualizado_por""",
            (path, col, corpo, seq, time.time(), usuario),
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return seq
