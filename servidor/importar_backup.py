"""Importa ou restaura um backup completo do ISE no banco do servidor.

Aceita os dois arquivos ISE_BACKUP_*.json: o baixado pelo botão "Baixar backup completo"
do site (versão do Claude) e o gerado pelo backup automático deste servidor.

    python importar_backup.py ISE_BACKUP_2026-10-22_1353.json            # só registros que ainda não existem
    python importar_backup.py ISE_BACKUP_2026-10-22_1353.json --substituir  # restauração: sobrescreve

Antes de gravar, confere o SHA-256 de cada registro. Se algum não conferir, nada é gravado.
A restauração é uma decisão do administrador (diretriz 3.6) e fica registrada na auditoria.
"""
import argparse
import hashlib
import json
import sys
import time

from banco import conectar, gravar_doc

COLECOES = ("inspecoes", "fotos", "aprovacoes", "config")


def compacto(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("arquivo")
    ap.add_argument("--substituir", action="store_true", help="sobrescreve registros que já existem")
    ap.add_argument("--banco", help="caminho do SQLite (padrão: ISE_BANCO ou dados/ise.sqlite3)")
    a = ap.parse_args()

    p = json.load(open(a.arquivo, encoding="utf-8"))
    regs = p.get("integridade", {}).get("registros", {})
    erros = []
    for c in COLECOES:
        for i, d in p.get("colecoes", {}).get(c, {}).items():
            h = regs.get(f"{c}/{i}")
            if h and hashlib.sha256(compacto(d).encode()).hexdigest() != h:
                erros.append(f"{c}/{i}")
            elif not h:
                erros.append(f"{c}/{i} (sem resumo de integridade)")
    if erros:
        sys.exit(f"Integridade não confere em {len(erros)} registro(s): {', '.join(erros[:10])}. Nada foi importado.")
    if p.get("fotosReferenciadasAusentes"):
        print(f"Atenção: o arquivo já indicava {len(p['fotosReferenciadasAusentes'])} foto(s) ausente(s) no banco de origem.")

    con = conectar(a.banco)
    gravados = pulados = 0
    for c in COLECOES:
        for i, d in p.get("colecoes", {}).get(c, {}).items():
            path = f"{c}/{i}"
            if c == "config" and i == "backup":
                continue  # situação do backup é sempre a deste servidor
            existe = con.execute("SELECT 1 FROM docs WHERE path = ? AND data IS NOT NULL", (path,)).fetchone()
            if existe and not a.substituir:
                pulados += 1
                continue
            gravar_doc(con, path, d, "importacao")
            gravados += 1
    con.execute("INSERT INTO auditoria (ts, usuario, op, alvo, ip, detalhe) VALUES (?, 'importacao', ?, ?, '', ?)",
                (time.time(), "restauracao" if a.substituir else "importacao", p.get("nome", a.arquivo),
                 f"{gravados} gravados, {pulados} já existiam"))
    print(f"{p.get('nome', a.arquivo)}: {gravados} registro(s) gravado(s), {pulados} já existiam e foram mantidos.")


if __name__ == "__main__":
    main()
