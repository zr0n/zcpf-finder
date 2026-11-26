"""Testes da montagem de URL do Jusbrasil (sem rede)."""

from __future__ import annotations

from jusbrasil import JUSBRASIL_BUSCA_URL, url_busca


def test_url_busca_cnpj_remove_pontuacao():
    assert url_busca("41.909.672/0001-00") == f"{JUSBRASIL_BUSCA_URL}?q=41909672000100"


def test_url_busca_cpf_remove_pontuacao():
    assert url_busca("123.456.789-09") == f"{JUSBRASIL_BUSCA_URL}?q=12345678909"


def test_url_busca_nome_e_codificado():
    assert url_busca("  Auto Mecânica Pains ") == f"{JUSBRASIL_BUSCA_URL}?q=Auto%20Mec%C3%A2nica%20Pains"
