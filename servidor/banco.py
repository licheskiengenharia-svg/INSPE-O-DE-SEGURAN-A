"""Banco do ISE: registros (docs), contas, auditoria e controle do backup.

Com DATABASE_URL definida, usa PostgreSQL (por exemplo, o plano gratuito do Neon).
Sem ela, usa um arquivo SQLite local (ISE_BANCO), bom para testes e servidores com disco.
O resto do código escreve SQL com "?" e usa só o que os dois bancos aceitam.
"""
import atexit
import contextlib
import json
import os
import sqlite3
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
URL = os.environ.get("DATABASE_URL", "").strip()
BANCO = os.environ.get("ISE_BANCO", str(RAIZ / "dados" / "ise.sqlite3"))
POSTGRES = URL.startswith(("postgres://", "postgresql://"))
MAX_DOC = 2 * 1024 * 1024  # 2 MB por registro (a plataforma do Claude aceitava 256 KB)

TABELAS = """
CREATE TABLE IF NOT EXISTS docs (
  path TEXT PRIMARY KEY, colecao TEXT NOT NULL, data TEXT, versao INTEGER NOT NULL DEFAULT 1,
  seq BIGINT NOT NULL, atualizado_em {real} NOT NULL, atualizado_por TEXT);
CREATE INDEX IF NOT EXISTS docs_seq ON docs(seq);
CREATE TABLE IF NOT EXISTS contador (id INTEGER PRIMARY KEY CHECK (id = 1), seq BIGINT NOT NULL);
INSERT INTO contador (id, seq) VALUES (1, 0) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS usuarios (
  id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, nome TEXT NOT NULL, senha TEXT NOT NULL,
  papel TEXT NOT NULL CHECK (papel IN ('admin', 'tst')), ativo INTEGER NOT NULL DEFAULT 1, criado_em {real} NOT NULL);
CREATE TABLE IF NOT EXISTS auditoria (
  id {serial}, ts {real} NOT NULL, usuario TEXT, op TEXT NOT NULL, alvo TEXT, ip TEXT, detalhe TEXT);
CREATE TABLE IF NOT EXISTS backups (
  id {serial}, inicio {real}, fim {real}, nome TEXT, ok INTEGER, registros INTEGER,
  arquivos INTEGER, erro TEXT, drive_id TEXT, md5 TEXT);
CREATE TABLE IF NOT EXISTS drive_arquivos (sha256 TEXT PRIMARY KEY, drive_id TEXT NOT NULL, nome TEXT, enviado_em {real})
"""
ESQUEMA = TABELAS.format(real="DOUBLE PRECISION", serial="BIGSERIAL PRIMARY KEY") if POSTGRES \
    else TABELAS.format(real="REAL", serial="INTEGER PRIMARY KEY AUTOINCREMENT")

if POSTGRES:
    import psycopg
    from psycopg_pool import ConnectionPool
    ErroIntegridade = (sqlite3.IntegrityError, psycopg.IntegrityError)
else:
    ErroIntegridade = (sqlite3.IntegrityError,)


class Linha(tuple):
    """Linha que aceita r[0] e r["coluna"], como sqlite3.Row."""
    def __new__(cls, nomes, valores):
        o = super().__new__(cls, valores)
        o._nomes = nomes
        return o

    def __getitem__(self, k):
        return super().__getitem__(self._nomes.index(k) if isinstance(k, str) else k)

    def keys(self):
        return list(self._nomes)


def _fabrica_linhas(cursor):
    nomes = [c.name for c in cursor.description or []]
    return lambda valores: Linha(nomes, valores)


_pool = None
_esquema_pronto = False


@atexit.register
def _fechar_pool():
    if _pool is not None:
        _pool.close()


class Conexao:
    """Interface única para SQLite e PostgreSQL."""

    def __init__(self, caminho=None):
        global _pool, _esquema_pronto
        self.pg = POSTGRES and not caminho
        if self.pg:
            if _pool is None:
                _pool = ConnectionPool(URL, min_size=1, max_size=5, open=True, check=ConnectionPool.check_connection,
                                       kwargs={"autocommit": True, "row_factory": _fabrica_linhas})
            self._ctx = _pool.connection()
            self.c = self._ctx.__enter__()
        else:
            caminho = caminho or BANCO
            Path(caminho).parent.mkdir(parents=True, exist_ok=True)
            self.c = sqlite3.connect(caminho, timeout=30, isolation_level=None, check_same_thread=False)
            self.c.row_factory = sqlite3.Row
            self.c.execute("PRAGMA journal_mode=WAL")
        if not _esquema_pronto or not self.pg:
            for cmd in ESQUEMA.split(";"):
                if cmd.strip():
                    self.c.execute(cmd)
            _esquema_pronto = _esquema_pronto or self.pg

    def execute(self, sql, params=()):
        if self.pg:
            return self.c.execute(sql.replace("?", "%s"), tuple(params))
        return self.c.execute(sql, tuple(params))

    @contextlib.contextmanager
    def transacao(self):
        if self.pg:
            with self.c.transaction():
                yield
        else:
            self.c.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.c.execute("COMMIT")
            except BaseException:
                self.c.execute("ROLLBACK")
                raise

    def close(self):
        if self.pg:
            self._ctx.__exit__(None, None, None)
        else:
            self.c.close()


def conectar(caminho=None):
    return Conexao(caminho)


def gravar_doc(con, path, data, usuario):
    """Grava (ou apaga, com data=None) um registro e devolve o número de sequência."""
    col = path.split("/")[0]
    corpo = None if data is None else json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if corpo is not None and len(corpo.encode()) > MAX_DOC:
        raise ValueError("registro maior que o limite")
    with con.transacao():
        seq = con.execute("UPDATE contador SET seq = seq + 1 WHERE id = 1 RETURNING seq").fetchone()[0]
        con.execute(
            """INSERT INTO docs (path, colecao, data, versao, seq, atualizado_em, atualizado_por)
               VALUES (?, ?, ?, 1, ?, ?, ?)
               ON CONFLICT(path) DO UPDATE SET data = excluded.data, versao = docs.versao + 1,
                 seq = excluded.seq, atualizado_em = excluded.atualizado_em, atualizado_por = excluded.atualizado_por""",
            (path, col, corpo, seq, time.time(), usuario),
        )
    return seq
