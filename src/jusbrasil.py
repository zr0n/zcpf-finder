"""Scraping da página pública de consulta processual do Jusbrasil.

Separado da interface: a tela só chama :func:`scrape_jusbrasil_by_cpf` e exibe o dict.
"""

from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import quote

import aiohttp

JUSBRASIL_BUSCA_URL = "https://www.jusbrasil.com.br/consulta-processual/busca"


def url_busca(termo: str) -> str:
    """Monta a URL da busca processual do Jusbrasil para abrir no navegador.

    O Jusbrasil protege a página com Cloudflare (acesso por script recebe 403),
    então para CNPJ a integração é só abrir a busca no navegador do usuário.

    Args:
        termo (str): CPF/CNPJ (pontuação é removida) ou nome.

    Returns:
        str: URL da busca.
    """
    digitos = re.sub(r"\D", "", termo)
    consulta = digitos if len(digitos) in (11, 14) else termo.strip()
    return f"{JUSBRASIL_BUSCA_URL}?q={quote(consulta)}"


async def scrape_jusbrasil_by_cpf(cpf: str, timeout: int = 30) -> dict:
    """Faz scraping da página pública do Jusbrasil para um CPF.

    Extrai o JSON embutido em ``<script id="__NEXT_DATA__">`` (SSR do Next.js)
    e devolve os perfis com nome, idade, estados, processos, tribunais e anos.

    Args:
        cpf (str): CPF (com ou sem pontuação).
        timeout (int): Timeout em segundos.

    Returns:
        dict: ``{"url": str, "profiles": list[dict], "error": str | None}``.
    """
    cpf_clean = re.sub(r"\D", "", cpf)
    url = f"{JUSBRASIL_BUSCA_URL}?q={cpf_clean}"

    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
    }
    result = {"url": url, "profiles": [], "error": None}

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status != 200:
                    result["error"] = f"HTTP {resp.status}"
                    return result
                html = await resp.text()
    except asyncio.TimeoutError:
        result["error"] = "Timeout na consulta ao Jusbrasil"
        return result
    except Exception as e:
        result["error"] = f"Erro de rede: {e}"
        return result

    # Extrai o __NEXT_DATA__
    try:
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if not m:
            result["error"] = "Estrutura __NEXT_DATA__ não encontrada (site pode ter mudado)"
            return result
        data = json.loads(m.group(1))
    except Exception as e:
        result["error"] = f"Erro ao parsear JSON: {e}"
        return result

    # Navega até os registros
    try:
        page_props = data.get("props", {}).get("pageProps", {})
        root_query = page_props.get("__APOLLO_STATE__", {}).get("ROOT_QUERY", {})
        data_root = root_query.get("root", {})

        content = None
        for _key, val in data_root.items():
            if isinstance(val, dict) and isinstance(val.get("content"), dict):
                content = val["content"]
                break

        if not content:
            # Sem content = provavelmente sem resultados
            return result

        components = content.get("components", [])
        if not components:
            return result

        inner = components[0].get("components", [])
        for item in inner:
            if not isinstance(item, dict):
                continue
            fields = item.get("fields", {})
            if not fields.get("url"):
                continue

            tribunais = ", ".join(
                f"{c.get('court', '?')} ({c.get('count', 0)})"
                for c in (fields.get("courts") or [])
            )
            anos = ", ".join(
                f"{y.get('year', '?')} ({y.get('count', 0)})"
                for y in (fields.get("years") or [])
            )

            result["profiles"].append({
                "nome": fields.get("display_name") or fields.get("identification_name") or "",
                "cpf_mascarado": fields.get("identification_number", ""),
                "idade": fields.get("age", ""),
                "estados": ", ".join(fields.get("all_states") or []),
                "processos": fields.get("lawsuit_count", 0),
                "tribunais": tribunais,
                "anos": anos,
                "url": fields.get("url", ""),
            })
    except Exception as e:
        result["error"] = f"Erro ao extrair perfis: {e}"

    return result
