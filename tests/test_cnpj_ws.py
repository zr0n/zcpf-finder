"""Testes do cliente CNPJ.ws — sem rede (a abertura de URL é injetada)."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from cnpj_ws import (
    CnpjInvalido,
    CnpjNaoEncontrado,
    ErroCnpj,
    LimiteExcedido,
    LimitadorConsultas,
    cnpj_valido,
    consultar_cnpj,
    formatar_cep,
    formatar_cnpj,
    formatar_data,
    formatar_moeda,
    formatar_telefone,
    mascarar_documento,
    montar_empresa,
)

CNPJ_GLOBO = "27865757000102"

EXEMPLO = {
    "razao_social": "GLOBO COMUNICACAO E PARTICIPACOES S/A",
    "capital_social": "6983568523.86",
    "atualizado_em": "2021-07-20T05:41:44.884Z",
    "porte": {"id": "05", "descricao": "Demais"},
    "natureza_juridica": {"id": "2054", "descricao": "Sociedade Anônima Fechada"},
    "socios": [{
        "cpf_cnpj_socio": "***486498**", "nome": "FULANO DE TAL", "tipo": "Pessoa Física",
        "data_entrada": "2021-03-15", "faixa_etaria": "51 a 60 anos",
        "qualificacao_socio": {"id": 10, "descricao": "Diretor "},
    }],
    "simples": None,
    "estabelecimento": {
        "cnpj": CNPJ_GLOBO, "tipo": "Matriz", "nome_fantasia": "GLOBO",
        "situacao_cadastral": "Ativa", "data_situacao_cadastral": "2005-11-03",
        "data_inicio_atividade": "1986-01-31", "tipo_logradouro": "RUA", "logradouro": "LOPES QUINTAS",
        "numero": "303", "complemento": None, "bairro": "JARDIM BOTANICO", "cep": "22460901",
        "ddd1": "21", "telefone1": "21554551", "ddd2": None, "telefone2": None,
        "ddd_fax": "21", "fax": "25408773", "email": None,
        "atividade_principal": {"id": "6021700", "subclasse": "6021-7/00",
                                "descricao": "Atividades de televisão aberta"},
        "atividades_secundarias": [{"id": "5911102", "subclasse": "5911-1/02",
                                    "descricao": "Produção de filmes para publicidade"}],
        "estado": {"sigla": "RJ"}, "cidade": {"nome": "Rio de Janeiro"},
        "inscricoes_estaduais": [
            {"inscricao_estadual": "84295760", "ativo": True, "atualizado_em": "2021-08-13T15:11:08Z",
             "estado": {"sigla": "RJ"}},
            {"inscricao_estadual": "84347353", "ativo": False, "atualizado_em": None, "estado": {"sigla": "RJ"}},
        ],
    },
}


# ── Validação e formatação ──────────────────────────────────────────────────

@pytest.mark.parametrize("cnpj", [CNPJ_GLOBO, "27.865.757/0001-02", "11.222.333/0001-81"])
def test_cnpj_valido_aceita_cnpjs_corretos(cnpj):
    assert cnpj_valido(cnpj)


@pytest.mark.parametrize("cnpj", ["", "123", "27865757000103", "11111111111111", "278657570001021", None])
def test_cnpj_valido_rejeita_invalidos(cnpj):
    assert not cnpj_valido(cnpj)


def test_formatadores():
    assert formatar_cnpj(CNPJ_GLOBO) == "27.865.757/0001-02"
    assert formatar_cnpj("123") == "123"  # incompleto: devolve como veio
    assert formatar_moeda("6983568523.86") == "R$ 6.983.568.523,86"
    assert formatar_moeda(None) == "—"
    assert formatar_moeda("abc") == "—"
    assert formatar_data("2021-07-20T05:41:44.884Z") == "20/07/2021"
    assert formatar_data(None) == "—"
    assert formatar_cep("22460901") == "22460-901"
    assert formatar_telefone("21", "21554551") == "(21) 2155-4551"
    assert formatar_telefone("11", "987654321") == "(11) 98765-4321"
    assert formatar_telefone("21", None) is None


@pytest.mark.parametrize("entrada, esperado", [
    ("07561099738", "***.610.997-**"),       # CPF completo vindo da API → mascarado
    ("***486498**", "***.486.498-**"),       # já mascarado → só formata
    ("12345678000195", "12.345.678/0001-95"),  # sócio pessoa jurídica → CNPJ formatado
    (None, "—"),
])
def test_mascarar_documento(entrada, esperado):
    assert mascarar_documento(entrada) == esperado


def test_montar_empresa_remove_socio_duplicado():
    socio = EXEMPLO["socios"][0]
    emp = montar_empresa({**EXEMPLO, "socios": [socio, dict(socio), {**socio, "nome": "OUTRO"}]})
    assert [s["nome"] for s in emp.socios] == ["FULANO DE TAL", "OUTRO"]


# ── Montagem da empresa ─────────────────────────────────────────────────────

def test_montar_empresa_com_exemplo_da_documentacao():
    emp = montar_empresa(EXEMPLO)
    assert emp.cnpj == "27.865.757/0001-02"
    assert emp.ativa
    assert emp.capital_social == "R$ 6.983.568.523,86"
    assert emp.endereco == "RUA LOPES QUINTAS, 303 — JARDIM BOTANICO"
    assert emp.cidade_uf == "Rio de Janeiro / RJ"
    assert emp.telefones == ["(21) 2155-4551", "(21) 2540-8773 (fax)"]
    assert emp.email == "—"
    assert emp.simples == "—"  # "simples": null não quebra
    assert emp.socios[0]["qualificacao"] == "Diretor"  # espaço sobrando da API removido
    assert [a["tipo"] for a in emp.atividades] == ["Principal", "Secundária"]
    assert [i["ativa"] for i in emp.inscricoes] == ["Sim", "Não"]
    assert emp.inscricoes[1]["atualizado"] == "—"


def test_montar_empresa_com_json_vazio_nao_quebra():
    emp = montar_empresa({})
    assert emp.razao_social == "—"
    assert emp.cidade_uf == "—"
    assert not emp.ativa
    assert emp.socios == [] and emp.atividades == [] and emp.inscricoes == []


# ── Consulta HTTP (abertura de URL falsa) ───────────────────────────────────

class _RespostaFalsa(io.BytesIO):
    def __init__(self, corpo: bytes, headers: dict):
        super().__init__(corpo)
        self.headers = headers


def _abrir_ok(corpo: dict, restantes: str = "2"):
    def abrir(req, timeout):
        assert req.full_url.endswith(CNPJ_GLOBO)  # pontuação removida antes de chamar a API
        return _RespostaFalsa(json.dumps(corpo).encode(), {"x-ratelimit-remaining": restantes})
    return abrir


def _abrir_erro(codigo: int, corpo: dict | None = None, headers: dict | None = None):
    def abrir(req, timeout):
        raise urllib.error.HTTPError(req.full_url, codigo, "erro", headers or {},
                                     io.BytesIO(json.dumps(corpo or {}).encode()))
    return abrir


def test_consultar_sucesso_devolve_empresa_e_restantes():
    resp = consultar_cnpj("27.865.757/0001-02", abrir=_abrir_ok(EXEMPLO))
    assert resp.empresa.razao_social.startswith("GLOBO")
    assert resp.restantes == 2


def test_consultar_cnpj_invalido_nao_chama_api():
    def abrir(*_a, **_k):
        raise AssertionError("não deveria chamar a API")
    with pytest.raises(CnpjInvalido):
        consultar_cnpj("27865757000103", abrir=abrir)


@pytest.mark.parametrize("codigo, erro", [(400, CnpjInvalido), (404, CnpjNaoEncontrado), (500, ErroCnpj)])
def test_consultar_mapeia_erros_http(codigo, erro):
    with pytest.raises(erro) as exc:
        consultar_cnpj(CNPJ_GLOBO, abrir=_abrir_erro(codigo, {"detalhes": "mensagem da API"}))
    assert "mensagem da API" in str(exc.value)


def test_consultar_429_usa_tempo_do_cabecalho():
    with pytest.raises(LimiteExcedido) as exc:
        consultar_cnpj(CNPJ_GLOBO, abrir=_abrir_erro(429, headers={"x-ratelimit-reset": "42"}))
    assert exc.value.segundos == 42


def test_consultar_falha_de_rede():
    def abrir(*_a, **_k):
        raise urllib.error.URLError("sem internet")
    with pytest.raises(ErroCnpj, match="sem internet"):
        consultar_cnpj(CNPJ_GLOBO, abrir=abrir)


def test_consultar_resposta_nao_json():
    def abrir(req, timeout):
        return _RespostaFalsa(b"<html>manutencao</html>", {})
    with pytest.raises(ErroCnpj, match="não é JSON"):
        consultar_cnpj(CNPJ_GLOBO, abrir=abrir)


# ── Limitador ───────────────────────────────────────────────────────────────

class _Relogio:
    def __init__(self):
        self.agora = 1000.0

    def __call__(self):
        return self.agora


def test_limitador_bloqueia_na_quarta_consulta_e_libera_apos_janela():
    relogio = _Relogio()
    lim = LimitadorConsultas(limite=3, janela=60, relogio=relogio)
    for _ in range(3):
        assert lim.espera() == 0
        lim.registrar()
        relogio.agora += 10
    assert lim.restantes() == 0
    assert lim.espera() == 30  # 1ª consulta foi há 30s → faltam 30s
    relogio.agora += 30
    assert lim.espera() == 0 and lim.restantes() == 1


def test_limitador_bloqueio_forcado_por_429():
    relogio = _Relogio()
    lim = LimitadorConsultas(limite=3, janela=60, relogio=relogio)
    lim.bloquear(45)
    assert lim.espera() == 45 and lim.restantes() == 0
    relogio.agora += 45
    assert lim.espera() == 0 and lim.restantes() == 3
