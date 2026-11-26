"""Testes da fila de CNPJ, da extração de CNPJs de texto e do ServicoCnpj (sem rede e sem Tk)."""

from __future__ import annotations

import time

import pytest

from cnpj_ws import CnpjNaoEncontrado, LimiteExcedido, extrair_cnpjs, meio_cpf, montar_empresa
from fila_cnpj import FilaCnpj
from ui.servico_cnpj import ServicoCnpj

A, B, C, D = "27865757000102", "11222333000181", "41909672000100", "43871668000117"


# ── extrair_cnpjs ───────────────────────────────────────────────────────────

def test_extrair_cnpjs_formatos_misturados_e_repetidos():
    texto = f"cnpj;nome\n27.865.757/0001-02;Globo\n{B}, {A}\n{C} 43871668000117"
    validos, invalidos = extrair_cnpjs(texto)
    assert validos == [A, B, C, D]
    assert invalidos == []


def test_extrair_cnpjs_recupera_zero_a_esquerda_e_aponta_invalidos():
    # 01.222.333/0001-28 colado como número (Excel apagou o zero inicial).
    validos, invalidos = extrair_cnpjs("1222333000128\n27865757000103")
    assert validos == ["01222333000128"]
    assert invalidos == ["27865757000103"]


def test_extrair_cnpjs_ignora_cpf_e_telefone():
    validos, invalidos = extrair_cnpjs("CPF 12345678909, tel (37) 33235200, cep 35582-000")
    assert validos == [] and invalidos == []


def test_socio_pj_so_com_raiz_e_descartado_quando_vem_completo():
    # Caso real da API (AMP Comercio): a mesma holding veio com o CNPJ completo e só com a raiz.
    emp = montar_empresa({"socios": [
        {"nome": "AJE PARTICIPACOES LTDA", "cpf_cnpj_socio": "43871668", "qualificacao_socio": {"descricao": "Sócio"}},
        {"nome": "AJE PARTICIPACOES LTDA", "cpf_cnpj_socio": D, "qualificacao_socio": {"descricao": "Sócio"}},
    ]})
    assert [(s["nome"], s["cnpj"]) for s in emp.socios] == [("AJE PARTICIPACOES LTDA", D)]


def test_socio_pj_so_com_raiz_vira_cnpj_da_matriz():
    emp = montar_empresa({"socios": [{"nome": "AJE", "cpf_cnpj_socio": "43871668"}]})
    assert emp.socios[0]["cnpj"] == D                  # 43.871.668/0001-17
    assert emp.socios[0]["documento"] == "43.871.668 (raiz)"


@pytest.mark.parametrize("documento, esperado", [
    ("***.682.476-**", "682476"),
    ("***682476**", "682476"),
    ("43.871.668/0001-17", ""),   # CNPJ não é CPF mascarado
    ("43.871.668 (raiz)", ""),
    ("—", ""),
    (None, ""),
])
def test_meio_cpf(documento, esperado):
    assert meio_cpf(documento) == esperado


def test_socio_pj_tem_cnpj_seguivel_e_pf_nao():
    emp = montar_empresa({"socios": [
        {"nome": "HOLDING", "cpf_cnpj_socio": D},
        {"nome": "PESSOA", "cpf_cnpj_socio": "07561099738"},
    ]})
    assert [s["cnpj"] for s in emp.socios] == [D, ""]


# ── FilaCnpj ────────────────────────────────────────────────────────────────

def _cb(*_a):
    return None


def test_fila_junta_repetidos_e_respeita_prioridade():
    fila = FilaCnpj()
    assert fila.adicionar(A, _cb, "lote")
    assert fila.adicionar(B, _cb, "lote")
    assert not fila.adicionar(A, _cb, "rede")           # mesmo CNPJ: junta, não duplica
    fila.adicionar(B, _cb, "consulta", prioridade=True)  # já existe: sobe para a frente
    assert len(fila) == 2
    primeira = fila.proxima()
    assert primeira.digitos == B and primeira.donos() == {"lote", "consulta"}


def test_fila_cancelar_por_dono_preserva_pedidos_de_outros():
    fila = FilaCnpj()
    fila.adicionar(A, _cb, "lote")
    fila.adicionar(B, _cb, "lote")
    fila.adicionar(B, _cb, "rede")
    assert fila.cancelar("lote") == 1                    # A sai; B fica porque a rede ainda quer
    assert fila.pendentes() == 1 and fila.pendentes("rede") == 1 and fila.pendentes("lote") == 0


def test_fila_devolver_volta_para_frente():
    fila = FilaCnpj()
    fila.adicionar(A, _cb, "x")
    fila.adicionar(B, _cb, "x")
    t = fila.proxima()
    fila.devolver(t)
    assert fila.proxima().digitos == A


# ── ServicoCnpj ─────────────────────────────────────────────────────────────

class _RaizFalsa:
    """Imita o ``after`` do Tk: guarda as chamadas e executa quando ``bombear`` é chamado."""

    def __init__(self):
        self.agendados = []

    def after(self, _ms, fn, *args):
        self.agendados.append((fn, args))

    def after_idle(self, fn, *args):
        self.agendados.append((fn, args))

    def bombear(self, ate, limite_s: float = 3.0):
        fim = time.time() + limite_s
        while not ate() and time.time() < fim:
            pendentes, self.agendados = self.agendados, []
            for fn, args in pendentes:
                fn(*args)
            time.sleep(0.01)
        assert ate(), "condição não satisfeita a tempo"


class _Resp:
    def __init__(self, digitos):
        self.empresa = montar_empresa({"razao_social": f"EMPRESA {digitos}", "estabelecimento": {"cnpj": digitos}})


def test_servico_entrega_resultados_cacheia_e_respeita_limite():
    chamadas = []

    def consultar(d):
        chamadas.append(d)
        return _Resp(d)

    raiz = _RaizFalsa()
    serv = ServicoCnpj(raiz, consultar=consultar)
    recebidos = []
    for d in (A, B, C, D):
        serv.consultar(d, lambda dig, emp, erro: recebidos.append((dig, emp.razao_social, erro)), dono="lote")

    raiz.bombear(lambda: len(recebidos) == 3)
    assert chamadas == [A, B, C]                 # 4ª espera: limite de 3/min
    assert len(serv.fila) == 1 and serv.limitador.espera() > 0
    assert "na fila" in serv.resumo()

    # Pedido de algo já consultado sai do cache, sem nova chamada à API.
    serv.consultar(A, lambda dig, emp, erro: recebidos.append((dig, "cache", erro)), dono="consulta")
    raiz.bombear(lambda: len(recebidos) == 4)
    assert recebidos[-1] == (A, "cache", None) and chamadas == [A, B, C]


def test_servico_429_devolve_para_fila_e_erro_comum_avisa():
    respostas = {A: LimiteExcedido(30), B: CnpjNaoEncontrado("não achei")}

    def consultar(d):
        raise respostas[d]

    raiz = _RaizFalsa()
    serv = ServicoCnpj(raiz, consultar=consultar)
    recebidos = []
    serv.consultar(A, lambda *a: recebidos.append(a), dono="x")
    serv.consultar(B, lambda *a: recebidos.append(a), dono="x")
    raiz.bombear(lambda: len(recebidos) == 0 and serv.limitador.espera() >= 29 and serv.em_andamento is None)
    assert serv.fila.proxima().digitos == A       # A voltou para a frente, não se perdeu
    assert serv.fila.proxima().digitos == B


def test_servico_cancelar_remove_pendentes_do_dono():
    raiz = _RaizFalsa()
    serv = ServicoCnpj(raiz, consultar=lambda d: _Resp(d))
    serv.limitador.bloquear(60)                   # nada sai da fila durante o teste
    for d in (A, B):
        serv.consultar(d, _cb, dono="rede")
    serv.consultar(C, _cb, dono="lote")
    assert serv.cancelar("rede") == 2
    assert serv.fila.pendentes() == 1 and serv.fila.pendentes("lote") == 1


@pytest.mark.parametrize("dono", ["consulta"])
def test_servico_prioridade_fura_fila(dono):
    raiz = _RaizFalsa()
    serv = ServicoCnpj(raiz, consultar=lambda d: _Resp(d))
    serv.limitador.bloquear(60)
    serv.consultar(A, _cb, dono="lote")
    serv.consultar(B, _cb, dono=dono, prioridade=True)
    assert serv.fila.proxima().digitos == B
