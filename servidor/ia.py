"""Análise do Claude para as não conformidades (substitui a análise da plataforma do Claude).

O site monta o prompt com as regras das diretrizes e espera de volta um JSON.
A chave fica só no servidor (ANTHROPIC_API_KEY), nunca no navegador.
"""
import json
import os
import re

import anthropic

MODELO = os.environ.get("ISE_MODELO", "claude-opus-5-5")
_cliente = None


class ErroIA(Exception):
    pass


def cliente():
    global _cliente
    if _cliente is None:
        _cliente = anthropic.Anthropic(timeout=120.0, max_retries=2)
    return _cliente


def _imagem(data_url):
    m = re.match(r"data:(image/(?:jpeg|png|webp|gif));base64,(.+)$", data_url, re.S)
    if not m:
        raise ErroIA("imagem inválida")
    return {"type": "image", "source": {"type": "base64", "media_type": m.group(1), "data": m.group(2)}}


def _extrai_json(texto):
    texto = texto.strip()
    texto = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto)
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        ini, fim = texto.find("{"), texto.rfind("}")
        if ini >= 0 and fim > ini:
            try:
                return json.loads(texto[ini:fim + 1])
            except json.JSONDecodeError:
                pass
    raise ErroIA("resposta sem JSON válido")


def analisar(prompt, imagens=()):
    conteudo = [_imagem(i) for i in imagens] + [{"type": "text", "text": prompt}]
    try:
        r = cliente().beta.messages.create(
            model=MODELO,
            max_tokens=16000,
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": conteudo}],
        )
    except anthropic.AuthenticationError:
        raise ErroIA("chave da API do Claude inválida")
    except anthropic.RateLimitError:
        raise ErroIA("limite de uso da API do Claude atingido; tente de novo em instantes")
    except anthropic.APIStatusError as e:
        raise ErroIA(f"erro da API do Claude ({e.status_code})")
    except anthropic.APIConnectionError:
        raise ErroIA("sem conexão com a API do Claude")
    if r.stop_reason == "refusal":
        raise ErroIA("o Claude recusou esta análise")
    texto = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
    return _extrai_json(texto)
