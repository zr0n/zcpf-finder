"""Cliente da API pública do CNPJ.ws (https://publica.cnpj.ws).

Regras da API pública:
  - GET /cnpj/{14 dígitos sem pontuação};
  - limite de 3 consultas por minuto (cabeçalhos ``x-ratelimit-*``);
  - 400 = CNPJ inválido, 404 = não encontrado, 429 = limite excedido.

Só usa a biblioteca padrão (urllib): é uma consulta por vez, não precisa de aiohttp.
"""

from __future__ import annotations

import json
import math
import re
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

URL_BASE = "https://publica.cnpj.ws/cnpj/"
LIMITE_POR_MINUTO = 3
TIMEOUT_PADRAO = 20

_HEADERS = {"Accept": "application/json", "User-Agent": "cpf-finder/1.0"}


# ── Erros ────────────────────────────────────────────────────────────────────

class ErroCnpj(Exception):
    """Base dos erros de consulta de CNPJ."""


class CnpjInvalido(ErroCnpj):
    """CNPJ com tamanho errado ou dígitos verificadores incorretos."""


class CnpjNaoEncontrado(ErroCnpj):
    """A API respondeu 404: o CNPJ não está na base."""


class LimiteExcedido(ErroCnpj):
    """A API respondeu 429: passou de 3 consultas por minuto.

    Attributes:
        segundos (int): quanto tempo esperar antes de tentar de novo.
    """

    def __init__(self, segundos: int = 60):
        super().__init__(f"Limite de {LIMITE_POR_MINUTO} consultas por minuto atingido. "
                         f"Aguarde {segundos}s.")
        self.segundos = segundos


# ── Validação e formatação ───────────────────────────────────────────────────

def limpar_cnpj(texto: str) -> str:
    """Remove tudo que não for dígito.

    Args:
        texto (str): CNPJ digitado, com ou sem pontuação.

    Returns:
        str: só os dígitos.
    """
    return re.sub(r"\D", "", texto or "")


def _digito(base: str) -> str:
    pesos = list(range(len(base) - 7, 1, -1)) + list(range(9, 1, -1))
    soma = sum(int(d) * p for d, p in zip(base, pesos))
    resto = soma % 11
    return "0" if resto < 2 else str(11 - resto)


def cnpj_valido(texto: str) -> bool:
    """Confere tamanho e os dois dígitos verificadores do CNPJ.

    Args:
        texto (str): CNPJ com ou sem pontuação.

    Returns:
        bool: True se tiver 14 dígitos, não for repetido (ex.: 111...1) e os DVs baterem.
    """
    d = limpar_cnpj(texto)
    if len(d) != 14 or d == d[0] * 14:
        return False
    dv1 = _digito(d[:12])
    dv2 = _digito(d[:12] + dv1)
    return d[12:] == dv1 + dv2


def formatar_cnpj(texto: str) -> str:
    """Formata como ``00.000.000/0000-00`` (devolve o texto original se não tiver 14 dígitos).

    Args:
        texto (str): CNPJ com ou sem pontuação.

    Returns:
        str: CNPJ formatado.
    """
    d = limpar_cnpj(texto)
    if len(d) != 14:
        return texto
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"


def formatar_moeda(valor) -> str:
    """Formata valor numérico (ou string numérica da API) como ``R$ 1.234,56``.

    Args:
        valor (str | float | None): valor vindo da API (ex.: ``"6983568523.86"``).

    Returns:
        str: valor em reais, ou ``"—"`` se vazio/inválido.
    """
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return "—"
    inteiro, centavos = f"{numero:,.2f}".split(".")
    return f"R$ {inteiro.replace(',', '.')},{centavos}"


def formatar_data(valor: str | None) -> str:
    """Converte ``AAAA-MM-DD`` (ou ISO com hora) para ``DD/MM/AAAA``.

    Args:
        valor (str | None): data da API.

    Returns:
        str: data formatada, ou ``"—"`` se vazia/inválida.
    """
    if not valor:
        return "—"
    try:
        return datetime.strptime(valor[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return valor


def formatar_cep(valor: str | None) -> str:
    """Formata CEP como ``00000-000``.

    Args:
        valor (str | None): CEP só com dígitos.

    Returns:
        str: CEP formatado, ou ``"—"`` se vazio.
    """
    d = limpar_cnpj(valor or "")
    return f"{d[:5]}-{d[5:]}" if len(d) == 8 else (valor or "—")


def formatar_telefone(ddd: str | None, numero: str | None) -> str | None:
    """Monta ``(DD) 0000-0000``; devolve None se não houver número.

    Args:
        ddd (str | None): código de área.
        numero (str | None): telefone sem DDD.

    Returns:
        str | None: telefone formatado.
    """
    if not numero:
        return None
    n = limpar_cnpj(numero)
    corpo = f"{n[:-4]}-{n[-4:]}" if len(n) >= 8 else n
    return f"({ddd}) {corpo}" if ddd else corpo


def mascarar_documento(valor: str | None) -> str:
    """Exibe o documento do sócio sem expor CPF completo.

    A API às vezes devolve o CPF inteiro do sócio pessoa física. Na tela seguimos o
    padrão de publicação da Receita (``***.XXX.XXX-**``); CNPJ de sócio pessoa jurídica
    é dado público e sai formatado normalmente.

    Args:
        valor (str | None): ``cpf_cnpj_socio`` como veio da API.

    Returns:
        str: documento mascarado/formatado, ou ``"—"`` se vazio.
    """
    if not valor:
        return "—"
    if "*" in valor:  # já veio mascarado pela API (ex.: "***486498**")
        d = valor.strip()
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}" if len(d) == 11 else d
    d = limpar_cnpj(valor)
    if len(d) == 11:
        return f"***.{d[3:6]}.{d[6:9]}-**"
    if len(d) == 14:
        return formatar_cnpj(d)
    return valor


_RE_CNPJ_FORMATADO = re.compile(r"(?<!\d)\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}(?!\d)")
_RE_CNPJ_NUMERICO = re.compile(r"(?<![\d./-])\d{12,14}(?![\d./-])")


def extrair_cnpjs(texto: str) -> tuple[list[str], list[str]]:
    """Encontra CNPJs em um texto livre (lista colada, CSV, planilha exportada).

    Aceita ``00.000.000/0000-00`` e só dígitos. Números com 12 ou 13 dígitos ganham
    zeros à esquerda: o Excel costuma "comer" o zero inicial de CNPJs como 01.234.567/...

    Args:
        texto (str): conteúdo colado ou lido de arquivo.

    Returns:
        tuple[list[str], list[str]]: (CNPJs válidos só com dígitos, sem repetição e na
        ordem em que aparecem; trechos que parecem CNPJ mas têm dígito verificador errado).
    """
    achados: list[tuple[int, str]] = []
    for m in _RE_CNPJ_FORMATADO.finditer(texto):
        achados.append((m.start(), m.group()))
    for m in _RE_CNPJ_NUMERICO.finditer(texto):
        achados.append((m.start(), m.group()))

    validos, invalidos, vistos = [], [], set()
    for _pos, bruto in sorted(achados):
        digitos = limpar_cnpj(bruto).zfill(14)
        if digitos in vistos:
            continue
        vistos.add(digitos)
        if cnpj_valido(digitos):
            validos.append(digitos)
        else:
            invalidos.append(bruto)
    return validos, invalidos


def meio_cpf(documento: str | None) -> str:
    """Extrai os 6 dígitos visíveis de um CPF mascarado (``***.682.476-**`` → ``682476``).

    É exatamente o "meio do CPF" que a aba de busca de CPF aceita como entrada.

    Args:
        documento (str | None): documento do sócio como exibido.

    Returns:
        str: 6 dígitos, ou ``""`` se não for um CPF mascarado nesse formato.
    """
    if not documento or "*" not in documento:
        return ""
    digitos = limpar_cnpj(documento)
    return digitos if len(digitos) == 6 else ""


# ── Modelo para a tela ───────────────────────────────────────────────────────

@dataclass
class Empresa:
    """Dados do CNPJ já achatados e formatados para exibição.

    O JSON da API é bem aninhado (estabelecimento → cidade → nome...). Esta classe
    concentra a "tradução" em um lugar só, para a tela não conhecer o formato da API.
    """

    cnpj: str
    razao_social: str
    nome_fantasia: str
    tipo: str
    situacao: str
    data_situacao: str
    motivo_situacao: str
    abertura: str
    porte: str
    natureza_juridica: str
    capital_social: str
    simples: str
    mei: str
    atividade_principal: str
    endereco: str
    cep: str
    cidade_uf: str
    telefones: list[str]
    email: str
    atualizado_em: str
    socios: list[dict] = field(default_factory=list)
    atividades: list[dict] = field(default_factory=list)
    inscricoes: list[dict] = field(default_factory=list)
    bruto: dict = field(default_factory=dict)

    @property
    def ativa(self) -> bool:
        """bool: True se a situação cadastral for "Ativa"."""
        return self.situacao.strip().lower() == "ativa"


def _txt(valor, padrao: str = "—") -> str:
    texto = str(valor).strip() if valor is not None else ""
    return texto or padrao


def _atividade(a: dict | None) -> str:
    if not a:
        return "—"
    return f"{a.get('subclasse') or a.get('id') or ''} — {a.get('descricao') or ''}".strip(" —") or "—"


def _sim_nao(valor) -> str:
    if valor is None or valor == "":
        return "—"
    return "Sim" if str(valor).strip().lower() in ("sim", "s", "true", "1") else "Não"


def cnpj_matriz(raiz: str) -> str:
    """Monta o CNPJ da matriz (ordem 0001) a partir da raiz de 8 dígitos.

    Args:
        raiz (str): 8 primeiros dígitos do CNPJ.

    Returns:
        str: CNPJ completo com dígitos verificadores, ou ``""`` se a raiz não tiver 8 dígitos.
    """
    raiz = limpar_cnpj(raiz)
    if len(raiz) != 8:
        return ""
    base = raiz + "0001"
    dv1 = _digito(base)
    return base + dv1 + _digito(base + dv1)


def _montar_socios(brutos: list[dict]) -> list[dict]:
    """Normaliza a lista de sócios da API.

    Trata duas esquisitices observadas na API real (não aparecem na documentação):
      - o mesmo sócio vem repetido;
      - sócio empresa às vezes vem duas vezes: uma com o CNPJ completo e outra só com a
        raiz (8 dígitos). A versão "só raiz" é descartada quando há a completa.

    Args:
        brutos (list[dict]): ``socios`` como vieram da API.

    Returns:
        list[dict]: sócios prontos para exibir; ``cnpj`` preenchido só para pessoa jurídica.
    """
    socios, vistos = [], set()
    for s in brutos:
        doc = limpar_cnpj(s.get("cpf_cnpj_socio") or "")
        if len(doc) == 14:
            cnpj, documento = doc, formatar_cnpj(doc)
        elif len(doc) == 8:
            # HIPÓTESE: só a raiz veio; assumimos a matriz (/0001), que é quem costuma ser sócia.
            cnpj, documento = cnpj_matriz(doc), f"{doc[:2]}.{doc[2:5]}.{doc[5:]} (raiz)"
        else:
            cnpj, documento = "", mascarar_documento(s.get("cpf_cnpj_socio"))
        socio = {
            # Só sócio pessoa jurídica tem CNPJ "seguível" pela rede de empresas.
            "cnpj": cnpj,
            "so_raiz": len(doc) == 8,
            "nome": _txt(s.get("nome")),
            "qualificacao": _txt((s.get("qualificacao_socio") or {}).get("descricao")),
            "tipo": _txt(s.get("tipo")),
            "documento": documento,
            "entrada": formatar_data(s.get("data_entrada")),
            "faixa_etaria": _txt(s.get("faixa_etaria")),
        }
        chave = (socio["nome"], socio["documento"], socio["qualificacao"], socio["entrada"])
        if chave not in vistos:
            vistos.add(chave)
            socios.append(socio)

    completos = {s["cnpj"][:8] for s in socios if s["cnpj"] and not s["so_raiz"]}
    return [s for s in socios if not (s["so_raiz"] and s["cnpj"][:8] in completos)]


def montar_empresa(dados: dict) -> Empresa:
    """Converte o JSON da API em :class:`Empresa`.

    Campos ausentes viram ``"—"`` em vez de quebrar: a API devolve muitos ``null``.

    Args:
        dados (dict): JSON retornado por ``GET /cnpj/{cnpj}``.

    Returns:
        Empresa: dados prontos para exibir.
    """
    est = dados.get("estabelecimento") or {}
    simples = dados.get("simples") or {}
    cidade = (est.get("cidade") or {}).get("nome")
    uf = (est.get("estado") or {}).get("sigla")

    partes_end = [est.get("tipo_logradouro"), est.get("logradouro")]
    endereco = " ".join(p for p in partes_end if p)
    if est.get("numero"):
        endereco += f", {est['numero']}"
    if est.get("complemento"):
        endereco += f" — {est['complemento']}"
    if est.get("bairro"):
        endereco += f" — {est['bairro']}"
    if not endereco and est.get("nome_cidade_exterior"):
        endereco = est["nome_cidade_exterior"]

    telefones = [t for t in (
        formatar_telefone(est.get("ddd1"), est.get("telefone1")),
        formatar_telefone(est.get("ddd2"), est.get("telefone2")),
    ) if t]
    fax = formatar_telefone(est.get("ddd_fax"), est.get("fax"))
    if fax:
        telefones.append(f"{fax} (fax)")

    simples_txt = _sim_nao(simples.get("simples"))
    if simples.get("data_opcao_simples"):
        simples_txt += f" (desde {formatar_data(simples['data_opcao_simples'])})"
    if simples.get("data_exclusao_simples"):
        simples_txt += f", excluído em {formatar_data(simples['data_exclusao_simples'])}"

    socios = _montar_socios(dados.get("socios") or [])

    atividades = []
    if est.get("atividade_principal"):
        a = est["atividade_principal"]
        atividades.append({"codigo": _txt(a.get("subclasse") or a.get("id")),
                           "descricao": _txt(a.get("descricao")), "tipo": "Principal"})
    for a in est.get("atividades_secundarias") or []:
        atividades.append({"codigo": _txt(a.get("subclasse") or a.get("id")),
                           "descricao": _txt(a.get("descricao")), "tipo": "Secundária"})

    inscricoes = [{
        "inscricao": _txt(i.get("inscricao_estadual")),
        "uf": _txt((i.get("estado") or {}).get("sigla")),
        "ativa": "Sim" if i.get("ativo") else "Não",
        "atualizado": formatar_data(i.get("atualizado_em")),
    } for i in est.get("inscricoes_estaduais") or []]

    return Empresa(
        cnpj=formatar_cnpj(est.get("cnpj") or ""),
        razao_social=_txt(dados.get("razao_social")),
        nome_fantasia=_txt(est.get("nome_fantasia")),
        tipo=_txt(est.get("tipo")),
        situacao=_txt(est.get("situacao_cadastral")),
        data_situacao=formatar_data(est.get("data_situacao_cadastral")),
        motivo_situacao=_txt(est.get("motivo_situacao_cadastral")),
        abertura=formatar_data(est.get("data_inicio_atividade")),
        porte=_txt((dados.get("porte") or {}).get("descricao")),
        natureza_juridica=_txt((dados.get("natureza_juridica") or {}).get("descricao")),
        capital_social=formatar_moeda(dados.get("capital_social")),
        simples=simples_txt,
        mei=_sim_nao(simples.get("mei")),
        atividade_principal=_atividade(est.get("atividade_principal")),
        endereco=_txt(endereco),
        cep=formatar_cep(est.get("cep")),
        cidade_uf=" / ".join(p for p in (cidade, uf) if p) or "—",
        telefones=telefones,
        email=_txt(est.get("email")).lower() if est.get("email") else "—",
        atualizado_em=formatar_data(dados.get("atualizado_em")),
        socios=socios,
        atividades=atividades,
        inscricoes=inscricoes,
        bruto=dados,
    )


# ── Controle do limite de consultas ──────────────────────────────────────────

class LimitadorConsultas:
    """Janela deslizante de N consultas por minuto, para não gastar uma requisição à toa.

    A API já devolve 429 quando passa do limite; controlar do lado de cá evita o erro e
    permite mostrar uma contagem regressiva na tela.
    """

    def __init__(self, limite: int = LIMITE_POR_MINUTO, janela: float = 60.0, relogio=time.monotonic):
        """Cria o limitador.

        Args:
            limite (int): consultas permitidas por janela.
            janela (float): tamanho da janela, em segundos.
            relogio (Callable[[], float]): fonte de tempo (injetável para testes).
        """
        self.limite, self.janela, self.relogio = limite, janela, relogio
        self._consultas: deque[float] = deque()
        self._bloqueado_ate = 0.0

    def _expirar(self, agora: float):
        while self._consultas and agora - self._consultas[0] >= self.janela:
            self._consultas.popleft()

    def espera(self) -> int:
        """Segundos até poder consultar de novo (0 = pode agora).

        Returns:
            int: tempo de espera arredondado para cima.
        """
        agora = self.relogio()
        self._expirar(agora)
        espera = self._bloqueado_ate - agora
        if len(self._consultas) >= self.limite:
            espera = max(espera, self._consultas[0] + self.janela - agora)
        return max(0, math.ceil(espera))

    def restantes(self) -> int:
        """int: quantas consultas ainda cabem na janela atual."""
        agora = self.relogio()
        self._expirar(agora)
        if self._bloqueado_ate > agora:
            return 0
        return max(0, self.limite - len(self._consultas))

    def registrar(self):
        """Marca que uma consulta foi feita agora."""
        self._consultas.append(self.relogio())

    def bloquear(self, segundos: int):
        """Força uma espera (usado quando a API responde 429).

        Args:
            segundos (int): tempo de bloqueio.
        """
        self._bloqueado_ate = max(self._bloqueado_ate, self.relogio() + segundos)


# ── Consulta HTTP ────────────────────────────────────────────────────────────

@dataclass
class Resposta:
    """Resultado de uma consulta bem-sucedida.

    Attributes:
        empresa (Empresa): dados formatados.
        restantes (int | None): consultas que ainda cabem no minuto atual (cabeçalho da API).
    """

    empresa: Empresa
    restantes: int | None


def _mensagem_erro(corpo: bytes, padrao: str) -> str:
    try:
        dados = json.loads(corpo.decode("utf-8"))
        return dados.get("detalhes") or dados.get("titulo") or padrao
    except (ValueError, AttributeError):
        return padrao


def consultar_cnpj(cnpj: str, timeout: int = TIMEOUT_PADRAO, abrir=urllib.request.urlopen) -> Resposta:
    """Consulta um CNPJ na API pública.

    Args:
        cnpj (str): CNPJ com ou sem pontuação.
        timeout (int): tempo máximo da requisição, em segundos.
        abrir (Callable): função de abertura de URL (injetável para testes).

    Returns:
        Resposta: empresa formatada e consultas restantes no minuto.

    Raises:
        CnpjInvalido: CNPJ mal formado (validado antes de gastar uma consulta) ou 400 da API.
        CnpjNaoEncontrado: 404 da API.
        LimiteExcedido: 429 da API.
        ErroCnpj: falha de rede ou resposta inesperada.
    """
    digitos = limpar_cnpj(cnpj)
    if not cnpj_valido(digitos):
        raise CnpjInvalido("CNPJ inválido: confira os 14 dígitos.")

    requisicao = urllib.request.Request(URL_BASE + digitos, headers=_HEADERS)
    try:
        with abrir(requisicao, timeout=timeout) as resp:
            corpo = resp.read()
            restantes = resp.headers.get("x-ratelimit-remaining")
    except urllib.error.HTTPError as e:
        corpo = e.read() if hasattr(e, "read") else b""
        if e.code == 400:
            raise CnpjInvalido(_mensagem_erro(corpo, "CNPJ inválido.")) from None
        if e.code == 404:
            raise CnpjNaoEncontrado(_mensagem_erro(corpo, "CNPJ não encontrado na base.")) from None
        if e.code == 429:
            reset = (e.headers or {}).get("x-ratelimit-reset") or (e.headers or {}).get("retry-after")
            raise LimiteExcedido(int(reset) if str(reset or "").isdigit() else 60) from None
        raise ErroCnpj(f"A API respondeu HTTP {e.code}: {_mensagem_erro(corpo, e.reason)}") from None
    except urllib.error.URLError as e:
        raise ErroCnpj(f"Falha de conexão: {e.reason}") from None
    except TimeoutError:
        raise ErroCnpj("A API demorou demais para responder.") from None

    try:
        dados = json.loads(corpo.decode("utf-8"))
    except ValueError:
        raise ErroCnpj("A API devolveu uma resposta que não é JSON.") from None
    return Resposta(
        empresa=montar_empresa(dados),
        restantes=int(restantes) if str(restantes or "").isdigit() else None,
    )
