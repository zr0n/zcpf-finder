"""Atendente único da API do CNPJ.ws para todas as abas.

Junta fila, cache e limite de consultas num lugar só: as abas pedem
``servico.consultar(cnpj, callback, dono)`` e recebem o resultado na thread da
interface. Assim a aba "Consulta CNPJ", a rede de empresas e o lote nunca
estouram juntos o limite de 3 consultas por minuto.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from collections.abc import Callable

from cnpj_ws import Empresa, ErroCnpj, LimiteExcedido, LimitadorConsultas, consultar_cnpj
from fila_cnpj import FilaCnpj, Retorno, Tarefa

INTERVALO_MS = 250


class ServicoCnpj:
    """Executa uma consulta por vez, respeitando o limite, e devolve o resultado na thread da UI."""

    def __init__(self, raiz: tk.Misc, consultar: Callable[[str], object] = consultar_cnpj):
        """Cria o serviço e inicia o laço de agendamento.

        Args:
            raiz (tk.Misc): qualquer widget, usado para ``after``.
            consultar (Callable[[str], Resposta]): função de consulta (injetável para testes).
        """
        self.raiz = raiz
        self._consultar = consultar
        self.limitador = LimitadorConsultas()
        self.fila = FilaCnpj()
        self.cache: dict[str, Empresa] = {}
        self.em_andamento: Tarefa | None = None
        self._respostas: queue.Queue = queue.Queue()
        self._ouvintes: list[Callable[[], None]] = []
        self._laco()

    # ── API para as abas ───────────────────────────────────────────────────
    def consultar(self, digitos: str, retorno: Retorno, dono: str, prioridade: bool = False):
        """Pede a consulta de um CNPJ. Se já estiver no cache, responde na hora (sem gastar limite).

        Args:
            digitos (str): CNPJ só com dígitos (já validado).
            retorno (Retorno): ``callback(digitos, empresa, erro)`` chamado na thread da UI.
            dono (str): quem pediu (usado em :meth:`cancelar`).
            prioridade (bool): True fura a fila (consulta manual).
        """
        if digitos in self.cache:
            self.raiz.after_idle(retorno, digitos, self.cache[digitos], None)
            return
        if self.em_andamento and self.em_andamento.digitos == digitos:
            self.em_andamento.retornos.append((dono, retorno))
            return
        self.fila.adicionar(digitos, retorno, dono, prioridade)
        self._avisar()

    def cancelar(self, dono: str) -> int:
        """Tira da fila os pedidos de um dono (a consulta já em andamento termina normalmente).

        Args:
            dono (str): identificador usado em :meth:`consultar`.

        Returns:
            int: quantos CNPJs saíram da fila.
        """
        removidos = self.fila.cancelar(dono)
        if self.em_andamento:
            self.em_andamento.retornos = [(d, r) for d, r in self.em_andamento.retornos if d != dono]
        self._avisar()
        return removidos

    def ao_mudar(self, ouvinte: Callable[[], None]):
        """Registra uma função chamada quando a fila ou o limite mudam (para atualizar contadores).

        Args:
            ouvinte (Callable[[], None]): função sem argumentos.
        """
        self._ouvintes.append(ouvinte)

    def resumo(self) -> str:
        """Texto curto do estado da fila, para rodapés.

        Returns:
            str: ex. ``"Fila: 5 CNPJs · próxima em 32s"``.
        """
        pendentes = len(self.fila)
        espera = self.limitador.espera()
        partes = []
        if self.em_andamento:
            partes.append("consultando…")
        if pendentes:
            partes.append(f"{pendentes} na fila")
            if espera:
                partes.append(f"próxima em {espera}s")
        return "Fila: " + " · ".join(partes) if partes else ""

    # ── Laço interno ───────────────────────────────────────────────────────
    def _avisar(self):
        for ouvinte in self._ouvintes:
            ouvinte()

    def _laco(self):
        self._entregar_respostas()
        if self.em_andamento is None and len(self.fila) and self.limitador.espera() == 0:
            self.em_andamento = self.fila.proxima()
            self.limitador.registrar()
            threading.Thread(target=self._executar, args=(self.em_andamento.digitos,), daemon=True).start()
            self._avisar()
        self.raiz.after(INTERVALO_MS, self._laco)

    def _executar(self, digitos: str):
        """Roda em thread separada; só conversa com a UI pela fila de respostas."""
        try:
            self._respostas.put((self._consultar(digitos).empresa, None))
        except (ErroCnpj, LimiteExcedido) as e:
            self._respostas.put((None, e))
        except Exception as e:  # rede/SO inesperado: não pode travar a fila
            self._respostas.put((None, ErroCnpj(f"Erro inesperado: {e}")))

    def _entregar_respostas(self):
        try:
            empresa, erro = self._respostas.get_nowait()
        except queue.Empty:
            return
        tarefa, self.em_andamento = self.em_andamento, None
        if isinstance(erro, LimiteExcedido):
            # A API recusou por limite: espera o tempo pedido e tenta de novo, sem perder o pedido.
            self.limitador.bloquear(erro.segundos)
            if tarefa.retornos:
                self.fila.devolver(tarefa)
            self._avisar()
            return
        if empresa is not None:
            self.cache[tarefa.digitos] = empresa
        for _dono, retorno in tarefa.retornos:
            retorno(tarefa.digitos, empresa, erro)
        self._avisar()
