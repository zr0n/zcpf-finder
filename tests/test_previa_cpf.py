"""Testes da prévia de interpretação da máscara de CPF (aba de CPF)."""

from __future__ import annotations

import pytest

from ui.aba_cpf import descrever_entrada
from ui.widgets import formatar_numero


@pytest.mark.parametrize("entrada, esperado", [
    ("456789", ("XXX.456.789-XX", 1_000)),
    ("456.789", ("XXX.456.789-XX", 1_000)),
    ("123.456.789-09", ("123.456.789-09", 1)),
    ("XXX.456.789.XX", ("XXX.456.789-XX", 1_000)),
    ("4XXXXXXXXXX", ("4XX.XXX.XXX-XX", 10 ** 8)),
    ("1234", ("123.4XX.XXX-XX", 10 ** 5)),  # outro tamanho sem X → prefixo
])
def test_descrever_entrada(entrada, esperado):
    assert descrever_entrada(entrada) == esperado


@pytest.mark.parametrize("entrada", ["", "   ", "abc", "123456789012"])
def test_descrever_entrada_invalida(entrada):
    assert descrever_entrada(entrada) is None


def test_formatar_numero():
    assert formatar_numero(0) == "0"
    assert formatar_numero(1_000_000) == "1.000.000"
