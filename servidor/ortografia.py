"""Correção ortográfica básica, sem custo, para quando a API do Claude não está configurada.

É conservadora de propósito: só troca palavras que não existem no dicionário de português
e só quando a troca é segura (acento esquecido, uma letra a mais, a menos ou trocada).
Não mexe em nomes próprios, siglas, códigos, números nem nos termos do checklist.
A revisão completa (concordância, pontuação, sentido) continua sendo a do Claude.
"""
import functools
import re
import unicodedata
from pathlib import Path

# erros frequentes nos registros dos TSTs que a distância de edição não resolve sozinha
TROCAS = {
    "paca": "placa", "pacas": "placas", "obtuida": "obstruída", "obtuido": "obstruído", "obtruida": "obstruída",
    "obistruida": "obstruída", "maquin": "máquina", "maquina": "máquina", "maquinas": "máquinas",
    "sinalisação": "sinalização", "sinalisacao": "sinalização", "sinalizacao": "sinalização",
    "estintor": "extintor", "esitintor": "extintor", "oleo": "óleo",
    "nao": "não", "tambem": "também", "proximo": "próximo", "proxima": "próxima", "area": "área", "areas": "áreas",
    "eletrica": "elétrica", "eletrico": "elétrico", "protecao": "proteção", "manutencao": "manutenção",
    "inspecao": "inspeção", "condicao": "condição", "situacao": "situação",
    "empilhadera": "empilhadeira", "corrimao": "corrimão", "funcionario": "funcionário", "funcionarios": "funcionários",
}
# termos da fundição e da segurança do trabalho que o dicionário comum não conhece
TERMOS = """macharia rebarbação rebarbar jateamento jateadora sopradora fundição fusão vazamento cadinho panela
moldagem moldes macho machos areia cura savelli quebra canal ferramentaria expedição pcp almoxarifado usinagem
empilhadeira empilhadeiras paleteira talha talhas ponte rolante epi epis epc epcs nr sesmt cipa tst tsts pgr pcmso
loto bloqueio etiquetagem sinalização obstruída obstruído obstruídas obstruídos desobstruída desobstruído
extintor extintores hidrante hidrantes sprinkler guarda-corpo rodapé enclausuramento intertravamento
"""


def _sem_acento(t):
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")


def _distancia(a, b):
    if abs(len(a) - len(b)) > 1:
        return 2
    ant = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        atual = [i]
        for j, cb in enumerate(b, 1):
            atual.append(min(ant[j] + 1, atual[j - 1] + 1, ant[j - 1] + (ca != cb)))
        ant = atual
    return ant[-1]


@functools.lru_cache(maxsize=1)
def _dicionario():
    from spellchecker import SpellChecker
    sp = SpellChecker(language="pt", distance=2)
    extras = set(TERMOS.split()) | set(TROCAS.values())
    site = Path(__file__).resolve().parent.parent / "index.html"
    if site.exists():  # perguntas do checklist, setores e normas cadastrados no site
        extras |= {w.lower() for w in re.findall(r"[A-Za-zÀ-ÿ]{4,}", site.read_text(encoding="utf-8"))}
    sp.word_frequency.load_words(sorted(extras))
    return sp


def _corrige_palavra(p, inicio_frase):
    if len(p) < 3 or not p.isalpha() or (p[0].isupper() and not inicio_frase and not p.isupper()):
        return p  # nome próprio ou de equipamento no meio da frase (Unibox, Savelli)
    if p[0].isupper() and p[1:].lower() != p[1:] and not p.isupper():
        return p  # caixa mista: IUnibox
    if p.isupper() and len(p) <= 5:  # siglas: EPI, NR, CIPA
        return p
    baixa = p.lower()
    if baixa in TROCAS:
        nova = TROCAS[baixa]
    else:
        sp = _dicionario()
        if baixa in sp:
            return p
        if len(baixa) < 5:
            return p
        nova = sp.correction(baixa)
        if not nova or nova == baixa:
            return p
        sa, sn = _sem_acento(baixa), _sem_acento(nova)
        # só aceita trocas seguras: acentuação, ou uma letra de diferença
        if not (sa == sn or _distancia(sa, sn) == 1):
            return p
    if p[0].isupper():
        nova = nova[0].upper() + nova[1:]
    return nova


def corrigir(texto):
    t = re.sub(r"[ \t]+", " ", texto.strip())
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    t = re.sub(r"([,;:])(?=[^\s\d])", r"\1 ", t)
    t = re.sub(r"[A-Za-zÀ-ÿ]+", lambda m: _corrige_palavra(m.group(0), not t[:m.start()].strip() or t[:m.start()].rstrip()[-1] in ".!?"), t)
    t = re.sub(r"([.!?]\s+)([a-zà-ÿ])", lambda m: m.group(1) + m.group(2).upper(), t)
    if t and t[0].islower():
        t = t[0].upper() + t[1:]
    return t


def corrigir_textos(textos):
    return {k: corrigir(v) if isinstance(v, str) else v for k, v in textos.items()}
