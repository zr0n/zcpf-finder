"""Testes da montagem de URL do Jusbrasil (sem rede)."""

from __future__ import annotations

import pytest

from jusbrasil import JUSBRASIL_BUSCA_URL, url_busca, url_busca_pessoa


def test_url_busca_cnpj_remove_pontuacao():
    assert url_busca("41.909.672/0001-00") == f"{JUSBRASIL_BUSCA_URL}?q=41909672000100"


def test_url_busca_cpf_remove_pontuacao():
    assert url_busca("123.456.789-09") == f"{JUSBRASIL_BUSCA_URL}?q=12345678909"


def test_url_busca_nome_e_codificado():
    assert url_busca("  Auto Mecânica Pains ") == f"{JUSBRASIL_BUSCA_URL}?q=Auto%20Mec%C3%A2nica%20Pains"


# ── url_busca_pessoa: termo usado pela opção "Jusbrasil" da Busca de pessoa ──

def test_url_busca_pessoa_prefere_cpf_completo():
    assert url_busca_pessoa("123.456.789-09", "Maria Silva").endswith("?q=12345678909")


@pytest.mark.parametrize("cpf", ["", "456.789", "XXX.456.789-XX"])
def test_url_busca_pessoa_sem_cpf_completo_usa_nome(cpf):
    assert url_busca_pessoa(cpf, "  Maria   Aparecida ").endswith("?q=Maria%20Aparecida")


@pytest.mark.parametrize("nome", ["", "Maria"])
def test_url_busca_pessoa_sem_cpf_e_nome_insuficiente_da_erro(nome):
    with pytest.raises(ValueError):
        url_busca_pessoa("456.789", nome)
