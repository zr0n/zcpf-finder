"""Testes do motor de busca de CPF (geração de candidatos, dígitos, match de nome, parser).

Sem rede: query_term é exercitado com uma sessão aiohttp falsa.
"""

from __future__ import annotations

import asyncio

import pytest

import cpf_finder as c


# ── Dígitos verificadores e formatação ──────────────────────────────────────

def test_build_cpf_calcula_dv_correto():
    assert c._build_cpf("123456789") == "12345678909"
    assert c._build_cpf("390533447") == "39053344705"


def test_build_cpf_rejeita_todos_digitos_iguais():
    # 111.111.111-11 etc. têm DV válido na fórmula, mas são CPFs inválidos de fato.
    for d in "0123456789":
        assert c._build_cpf(d * 9) is None


def test_fmt_poe_pontuacao():
    assert c.fmt("12345678909") == "123.456.789-09"


# ── generate_candidates: cada regra ─────────────────────────────────────────

def test_cpf_completo_sem_x_retorna_ele_mesmo():
    assert c.generate_candidates("390.533.447-05") == ["39053344705"]


def test_cpf_completo_com_dv_invalido_ainda_consulta(capsys):
    # Regra: avisa, mas usa o valor digitado (não "corrige").
    assert c.generate_candidates("39053344700") == ["39053344700"]
    assert "DV inválido" in capsys.readouterr().out


def test_meio_do_cpf_gera_1000_candidatos():
    candidatos = c.generate_candidates("456789")
    assert len(candidatos) == 1000
    # O "meio" (posições 4–9) é preservado em todos.
    assert all(x[3:9] == "456789" for x in candidatos)
    assert len(set(candidatos)) == 1000


def test_mascara_com_um_x_gera_dez_candidatos():
    candidatos = c.generate_candidates("12345678X")
    assert len(candidatos) == 10
    assert all(x.startswith("12345678") for x in candidatos)


def test_mascara_explicita_respeita_posicao():
    # X no começo → todos terminam (nos 9 primeiros) com o mesmo miolo fixo.
    candidatos = c.generate_candidates("X2345678X")
    assert len(candidatos) == 100
    assert all(x[1:8] == "2345678" for x in candidatos)


def test_entrada_vazia_levanta():
    with pytest.raises(ValueError):
        c.generate_candidates("   ")


def test_mais_de_11_digitos_levanta():
    with pytest.raises(ValueError, match="Muitos caracteres"):
        c.generate_candidates("123456789012")


def test_pontuacao_e_ignorada():
    assert c.generate_candidates("390.533.447-05") == c.generate_candidates("39053344705")


# ── names_match ─────────────────────────────────────────────────────────────

def test_match_estrito_exige_todas_as_palavras_da_busca():
    # Todas as palavras de A precisam estar em B.
    assert c.names_match("Maria Silva", "Maria Aparecida Silva")
    assert not c.names_match("Maria Aparecida Silva", "Maria Silva")


def test_match_ignora_acento_e_caixa():
    assert c.names_match("JOSÉ", "jose")


def test_match_fonetico_z_s_e_y_i():
    assert c.names_match("Souza", "Sousa")
    assert c.names_match("Thaynara", "Tainara")


def test_match_frouxo_aceita_duas_palavras_em_comum():
    assert c.names_match("Ana Paula Souza", "Ana Paula Lima", loose=True)
    assert not c.names_match("Ana Paula Souza", "Ana Paula Lima", loose=False)


def test_match_nome_vazio_e_falso():
    assert not c.names_match("", "Maria")
    assert not c.names_match("Maria", "")


# ── _parse_json ─────────────────────────────────────────────────────────────

def test_parse_json_extrai_campos_e_titula_nome():
    total, regs = c._parse_json({
        "totalRegistros": "3",
        "registros": [{"nome": "joão da silva", "cpfNis": "***.123.456-**",
                       "skPessoa": 7, "descricaoRelacoesGovernoFederal": "Bolsa Família"}],
    })
    assert total == 3
    assert regs[0] == {"skPessoa": 7, "nome": "João Da Silva",
                       "cpf_parcial": "***.123.456-**", "relacao": "Bolsa Família"}


def test_parse_json_vazio_nao_quebra():
    assert c._parse_json({}) == (0, [])
    assert c._parse_json({"totalRegistros": None, "registros": None}) == (0, [])


# ── query_term: sessão aiohttp falsa (sem rede) ─────────────────────────────

class _RespFalsa:
    def __init__(self, status, payload=None, exc=None):
        self.status = status
        self._payload = payload
        self._exc = exc

    async def __aenter__(self):
        if self._exc:
            raise self._exc
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self, content_type=None):
        return self._payload


class _SessaoFalsa:
    """Devolve respostas de uma fila; registra quantas vezes foi chamada."""

    def __init__(self, respostas):
        self._respostas = list(respostas)
        self.chamadas = 0

    def get(self, url, params=None, timeout=None):
        self.chamadas += 1
        item = self._respostas.pop(0)
        if isinstance(item, Exception):
            return _RespFalsa(0, exc=item)
        return item


def _rodar(sessao):
    sem = asyncio.Semaphore(1)
    return asyncio.run(c.query_term(sessao, "456789", 1, 10, sem))


def test_query_term_sucesso():
    payload = {"totalRegistros": 1, "registros": [
        {"nome": "ana", "cpfNis": "x", "skPessoa": 1, "descricaoRelacoesGovernoFederal": ""}]}
    sessao = _SessaoFalsa([_RespFalsa(200, payload)])
    total, regs = _rodar(sessao)
    assert total == 1 and regs[0]["nome"] == "Ana" and sessao.chamadas == 1


def test_query_term_repete_apos_429_e_depois_sucede():
    payload = {"totalRegistros": 0, "registros": []}
    sessao = _SessaoFalsa([_RespFalsa(429), _RespFalsa(200, payload)])
    total, regs = _rodar(sessao)
    assert (total, regs) == (0, [])
    assert sessao.chamadas == 2  # tentou de novo após o 429


def test_query_term_desiste_apos_tres_tentativas():
    sessao = _SessaoFalsa([_RespFalsa(503), _RespFalsa(503), _RespFalsa(503)])
    assert _rodar(sessao) == (0, [])
    assert sessao.chamadas == 3


def test_query_term_trata_excecao_de_rede():
    # Mesmo tipo que query_term captura (em 3.10 difere do TimeoutError embutido).
    sessao = _SessaoFalsa([asyncio.TimeoutError(), asyncio.TimeoutError(), asyncio.TimeoutError()])
    assert _rodar(sessao) == (0, [])
    assert sessao.chamadas == 3


def _rodar_status(sessao):
    sem = asyncio.Semaphore(1)
    return asyncio.run(c.query_term_status(sessao, "456789", 1, 10, sem))


def test_query_term_status_distingue_sem_resultado_de_falha():
    # 200 com zero registros: NÃO é falha.
    ok = _SessaoFalsa([_RespFalsa(200, {"totalRegistros": 0, "registros": []})])
    assert _rodar_status(ok) == (0, [], False)
    # 3× 503: falhou.
    falha = _SessaoFalsa([_RespFalsa(503), _RespFalsa(503), _RespFalsa(503)])
    assert _rodar_status(falha) == (0, [], True)
