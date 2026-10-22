"""Backup diário do ISE no Google Drive (diretriz 3).

Executa sozinho às 23h (America/Sao_Paulo) quando ISE_BACKUP_AUTOMATICO=1, ou à mão:
    python backup_drive.py

O que faz, nesta ordem:
 1. Lê todos os registros do banco e monta ISE_BACKUP_AAAA-MM-DD_HHMM.json, completo e
    restaurável (inspeções, respostas, NCs, ações, histórico, fotos, assinaturas, aprovações e
    configurações), com o SHA-256 de cada registro. O formato é o mesmo do botão
    "Baixar backup completo" do site, e o importar_backup.py restaura os dois.
 2. Envia o arquivo à pasta "01 — Backup Diário" e confere o MD5 calculado pelo próprio Drive.
 3. Envia as fotos e assinaturas novas, uma a uma, às pastas 03 e 07, também conferidas.
 4. No primeiro backup íntegro de cada mês, copia o arquivo para "02 — Backup Mensal".
 5. Só depois de tudo conferido, manda para a lixeira do Drive os backups diários com mais de
    30 dias e os mensais com mais de 12 meses. Fotos e assinaturas nunca são apagadas.
 6. Grava o registro da execução na pasta 09, na tabela "backups" e no aviso do site.

Uma falha nunca substitui a última cópia íntegra: o arquivo com problema é renomeado como
INCOMPLETO e a limpeza da retenção não roda.
"""
import base64
import datetime
import fcntl
import hashlib
import json
import os
import re
import sys
import time
import traceback
from zoneinfo import ZoneInfo

FUSO = ZoneInfo("America/Sao_Paulo")
VERSAO = "servidor-python-1"
PASTAS = {"01": "01 — Backup Diário", "02": "02 — Backup Mensal", "03": "03 — Fotografias e Evidências",
          "04": "04 — Relatórios", "05": "05 — Configurações e Checklists", "06": "06 — Histórico de Ações",
          "07": "07 — Assinaturas", "08": "08 — Auditoria", "09": "09 — Registros de Backup"}
RETENCAO_DIARIA = int(os.environ.get("ISE_RETENCAO_DIARIA_DIAS", "30"))
RETENCAO_MENSAL = int(os.environ.get("ISE_RETENCAO_MENSAL_MESES", "12"))
TENTATIVAS = 3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from banco import BANCO, conectar, gravar_doc  # noqa: E402


def compacto(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def sha256_txt(txt):
    return hashlib.sha256(txt.encode()).hexdigest()


def montar_pacote(con, agora):
    """Retrato completo do banco, no mesmo formato do backup baixado pelo site."""
    colecoes = {"inspecoes": {}, "fotos": {}, "aprovacoes": {}, "config": {}}
    for r in con.execute("SELECT path, data FROM docs WHERE data IS NOT NULL ORDER BY path"):
        col, did = r["path"].split("/", 1)
        if col in colecoes:
            colecoes[col][did] = json.loads(r["data"])
    refs = sorted(set(re.findall(r"db:([A-Za-z0-9]+)", compacto(colecoes["inspecoes"]))))
    integridade = {f"{c}/{i}": sha256_txt(compacto(d)) for c, docs in colecoes.items() for i, d in docs.items()}
    nome = f"ISE_BACKUP_{agora:%Y-%m-%d_%H%M}"
    return {"nome": nome, "geradoEm": agora.isoformat(timespec="seconds"), "fuso": "America/Sao_Paulo",
            "versaoSistema": VERSAO, "tipo": "completo-automatico",
            "contagem": {c: len(d) for c, d in colecoes.items()},
            "fotosReferenciadasAusentes": [i for i in refs if i not in colecoes["fotos"]],
            "integridade": {"algoritmo": "SHA-256 do JSON de cada registro", "registros": integridade},
            "colecoes": colecoes}


def conferir_pacote(texto):
    """Relê o arquivo gerado e confere cada registro antes de enviar."""
    p = json.loads(texto)
    for k, h in p["integridade"]["registros"].items():
        c, i = k.split("/", 1)
        if sha256_txt(compacto(p["colecoes"][c][i])) != h:
            raise RuntimeError(f"registro {k} não confere no arquivo gerado")
    return p


class Drive:
    def __init__(self):
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        cred = os.environ.get("ISE_DRIVE_CREDENCIAL", "")
        self.raiz = os.environ.get("ISE_DRIVE_PASTA", "")
        if not cred or not self.raiz:
            raise RuntimeError("backup não configurado: falta ISE_DRIVE_CREDENCIAL ou ISE_DRIVE_PASTA")
        info = json.loads(cred) if cred.lstrip().startswith("{") else json.load(open(cred, encoding="utf-8"))
        cr = service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/drive"])
        self.s = build("drive", "v3", credentials=cr, cache_discovery=False)
        self.pastas = {}

    def listar(self, pai, nome_inicia=""):
        q = f"'{pai}' in parents and trashed = false"
        if nome_inicia:
            q += f" and name contains '{nome_inicia}'"
        itens, token = [], None
        while True:
            r = self.s.files().list(q=q, fields="nextPageToken, files(id, name, createdTime, mimeType, md5Checksum)",
                                    supportsAllDrives=True, includeItemsFromAllDrives=True, pageSize=1000, pageToken=token).execute()
            itens += r.get("files", [])
            token = r.get("nextPageToken")
            if not token:
                return itens

    def pasta(self, codigo):
        if codigo not in self.pastas:
            achada = [f for f in self.listar(self.raiz) if f["mimeType"] == "application/vnd.google-apps.folder" and f["name"].startswith(codigo)]
            if achada:
                self.pastas[codigo] = achada[0]["id"]
            else:
                f = self.s.files().create(body={"name": PASTAS[codigo], "mimeType": "application/vnd.google-apps.folder", "parents": [self.raiz]},
                                          fields="id", supportsAllDrives=True).execute()
                self.pastas[codigo] = f["id"]
        return self.pastas[codigo]

    def enviar(self, pai, nome, conteudo, mime):
        """Envia e confere o MD5 que o Drive calculou. Tenta de novo até TENTATIVAS vezes."""
        from googleapiclient.http import MediaInMemoryUpload
        esperado = hashlib.md5(conteudo).hexdigest()
        ultimo = ""
        for n in range(1, TENTATIVAS + 1):
            try:
                f = self.s.files().create(body={"name": nome, "parents": [pai]},
                                          media_body=MediaInMemoryUpload(conteudo, mimetype=mime, resumable=True),
                                          fields="id, md5Checksum, size", supportsAllDrives=True).execute()
                if f.get("md5Checksum") == esperado and int(f.get("size", -1)) == len(conteudo):
                    return f["id"], esperado
                ultimo = f"MD5 no Drive {f.get('md5Checksum')} difere do esperado {esperado}"
                self.s.files().update(fileId=f["id"], body={"name": "INCOMPLETO_" + nome}, supportsAllDrives=True).execute()
            except Exception as e:  # rede, cota, permissão
                ultimo = f"{type(e).__name__}: {e}"
            time.sleep(2 ** n)
        raise RuntimeError(f"{nome}: falhou após {TENTATIVAS} tentativas ({ultimo})")

    def copiar(self, origem, pai, nome, md5):
        f = self.s.files().copy(fileId=origem, body={"name": nome, "parents": [pai]}, fields="id, md5Checksum", supportsAllDrives=True).execute()
        if f.get("md5Checksum") != md5:
            raise RuntimeError(f"cópia mensal {nome} não confere")
        return f["id"]

    def lixeira(self, fid):
        self.s.files().update(fileId=fid, body={"trashed": True}, supportsAllDrives=True).execute()


def tipo_imagem(fid, inspecoes_txt):
    return "07" if re.search(r'"assinatura[A-Za-z]*":"db:' + re.escape(fid) + '"', inspecoes_txt) else "03"


def executar(banco=None, drive=None, agora=None):
    """Roda um backup completo. Devolve o dicionário de status gravado no site."""
    banco = banco or BANCO
    trava = open(banco + ".backup.lock", "w")
    try:
        fcntl.flock(trava, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return {"ok": False, "erro": "outro backup já está em andamento"}
    con = conectar(banco)
    agora = agora or datetime.datetime.now(FUSO)
    inicio = time.time()
    bid = con.execute("INSERT INTO backups (inicio, ok) VALUES (?, 0)", (inicio,)).lastrowid
    log, enviados, erros = [f"Backup iniciado: {agora:%d/%m/%Y %H:%M:%S} (America/Sao_Paulo)", f"Versão: {VERSAO}"], 0, []
    status = {"ok": False}
    pacote = None
    try:
        drive = drive or Drive()
        pacote = montar_pacote(con, agora)
        texto = compacto(pacote)
        conferir_pacote(texto)
        cont = pacote["contagem"]
        log.append("Registros: " + ", ".join(f"{c} {n}" for c, n in cont.items()))
        if pacote["fotosReferenciadasAusentes"]:
            log.append(f"Atenção: {len(pacote['fotosReferenciadasAusentes'])} foto(s) citadas nas inspeções não existem no banco.")
        dados = texto.encode()
        fid, md5 = drive.enviar(drive.pasta("01"), pacote["nome"] + ".json", dados, "application/json")
        enviados += 1
        log.append(f"Arquivo diário {pacote['nome']}.json enviado e conferido (MD5 {md5}, {len(dados)} bytes).")

        insp_txt = compacto(pacote["colecoes"]["inspecoes"])
        for fid_foto, doc in pacote["colecoes"]["fotos"].items():
            m = re.match(r"data:(image/[a-z+]+);base64,(.+)$", doc.get("data", ""), re.S)
            if not m:
                erros.append(f"foto {fid_foto} sem imagem reconhecível")
                continue
            bruto = base64.b64decode(m.group(2))
            sha = hashlib.sha256(bruto).hexdigest()
            if con.execute("SELECT 1 FROM drive_arquivos WHERE sha256 = ?", (sha,)).fetchone():
                continue
            pasta = tipo_imagem(fid_foto, insp_txt)
            ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}.get(m.group(1), "img")
            nome = f"{'ASSINATURA' if pasta == '07' else 'FOTO'}_{fid_foto}.{ext}"
            did, _ = drive.enviar(drive.pasta(pasta), nome, bruto, m.group(1))
            con.execute("INSERT OR REPLACE INTO drive_arquivos VALUES (?, ?, ?, ?)", (sha, did, nome, time.time()))
            enviados += 1
        log.append(f"Fotos e assinaturas novas enviadas e conferidas: {enviados - 1}.")

        mes = f"ISE_BACKUP_{agora:%Y-%m}"
        if not drive.listar(drive.pasta("02"), mes):
            drive.copiar(fid, drive.pasta("02"), pacote["nome"] + ".json", md5)
            log.append(f"Cópia mensal criada em 02 — Backup Mensal ({agora:%m/%Y}).")

        if erros:
            raise RuntimeError("; ".join(erros))

        # retenção: só depois de um backup novo íntegro
        limite_d = agora - datetime.timedelta(days=RETENCAO_DIARIA)
        limite_m = agora - datetime.timedelta(days=RETENCAO_MENSAL * 31)
        removidos = 0
        for codigo, limite in (("01", limite_d), ("02", limite_m)):
            for f in drive.listar(drive.pasta(codigo), "ISE_BACKUP_"):
                criado = datetime.datetime.fromisoformat(f["createdTime"].replace("Z", "+00:00"))
                if criado < limite and f["id"] != fid:
                    drive.lixeira(f["id"])
                    removidos += 1
        log.append(f"Retenção: {removidos} backup(s) antigo(s) movido(s) para a lixeira do Drive "
                   f"(diários > {RETENCAO_DIARIA} dias, mensais > {RETENCAO_MENSAL} meses).")
        fim = time.time()
        status = {"ok": True, "automatico": True, "quando": agora.strftime("%d/%m/%Y %H:%M"), "ts": int(fim * 1000),
                  "nome": pacote["nome"], "registros": cont["inspecoes"], "arquivos": cont["fotos"], "erro": ""}
        con.execute("UPDATE backups SET fim = ?, nome = ?, ok = 1, registros = ?, arquivos = ?, drive_id = ?, md5 = ? WHERE id = ?",
                    (fim, pacote["nome"], cont["inspecoes"], cont["fotos"], fid, md5, bid))
        log.append("Situação: concluído. Verificação de integridade: OK.")
    except Exception as e:
        msg = str(e) or type(e).__name__
        log.append(f"Situação: FALHA. {msg}")
        log.append("A última cópia íntegra continua no Drive; a limpeza da retenção não foi feita.")
        traceback.print_exc()
        con.execute("UPDATE backups SET fim = ?, nome = ?, ok = 0, erro = ? WHERE id = ?",
                    (time.time(), pacote["nome"] if pacote else "", msg[:500], bid))
        anterior = con.execute("SELECT data FROM docs WHERE path = 'config/backup'").fetchone()
        ant = json.loads(anterior["data"]) if anterior and anterior["data"] else {}
        status = {"ok": False, "automatico": True, "erro": msg[:300], "falhaEm": agora.strftime("%d/%m/%Y %H:%M"),
                  "quando": ant.get("quando", "") if ant.get("ok") else "", "ultimoIntegro": ant.get("nome", "") if ant.get("ok") else ""}
    finally:
        log.append(f"Término: {datetime.datetime.now(FUSO):%d/%m/%Y %H:%M:%S}")
        try:
            if drive and pacote:
                drive.enviar(drive.pasta("09"), pacote["nome"] + "_registro.txt", ("\n".join(log) + "\n").encode(), "text/plain")
        except Exception as e:
            print("registro na pasta 09 falhou:", e, file=sys.stderr)
        gravar_doc(con, "config/backup", status, "sistema-backup")
        con.close()
        fcntl.flock(trava, fcntl.LOCK_UN)
        trava.close()
        print("\n".join(log))
    return status


if __name__ == "__main__":
    r = executar()
    sys.exit(0 if r.get("ok") else 1)
