"""Fila de consultas de CNPJ compartilhada entre as abas (sem dependência de interface).

Por que uma fila? A API pública aceita só 3 consultas por minuto. A rede de empresas
e o lote podem pedir dezenas de CNPJs; a fila guarda os pedidos e entrega um por vez,
quando o limite permite. Pedidos repetidos do mesmo CNPJ viram uma consulta só.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

# callback(digitos, empresa | None, erro | None)
Retorno = Callable[[str, object, Exception | None], None]


@dataclass
class Tarefa:
    """Um CNPJ a consultar e quem está esperando a resposta.

    Attributes:
        digitos (str): CNPJ só com dígitos.
        retornos (list[tuple[str, Retorno]]): pares (dono, callback) interessados no resultado.
    """

    digitos: str
    retornos: list[tuple[str, Retorno]] = field(default_factory=list)

    def donos(self) -> set[str]:
        """set[str]: abas/fluxos que pediram este CNPJ."""
        return {dono for dono, _ in self.retornos}


class FilaCnpj:
    """Fila FIFO com prioridade opcional, junção de repetidos e cancelamento por dono."""

    def __init__(self):
        """Cria a fila vazia."""
        self._fila: deque[Tarefa] = deque()

    def __len__(self) -> int:
        return len(self._fila)

    def _buscar(self, digitos: str) -> Tarefa | None:
        return next((t for t in self._fila if t.digitos == digitos), None)

    def adicionar(self, digitos: str, retorno: Retorno, dono: str, prioridade: bool = False) -> bool:
        """Enfileira um pedido.

        Args:
            digitos (str): CNPJ só com dígitos.
            retorno (Retorno): chamado com o resultado.
            dono (str): identificador de quem pediu (para cancelar depois).
            prioridade (bool): True coloca na frente (consulta manual do usuário).

        Returns:
            bool: True se criou uma tarefa nova; False se juntou a uma já existente.
        """
        existente = self._buscar(digitos)
        if existente:
            existente.retornos.append((dono, retorno))
            if prioridade and self._fila[0] is not existente:
                self._fila.remove(existente)
                self._fila.appendleft(existente)
            return False
        tarefa = Tarefa(digitos, [(dono, retorno)])
        if prioridade:
            self._fila.appendleft(tarefa)
        else:
            self._fila.append(tarefa)
        return True

    def proxima(self) -> Tarefa | None:
        """Retira e devolve a próxima tarefa (ou None se vazia)."""
        return self._fila.popleft() if self._fila else None

    def devolver(self, tarefa: Tarefa):
        """Recoloca uma tarefa na frente (ex.: a API respondeu 429 e ela precisa esperar).

        Args:
            tarefa (Tarefa): tarefa retirada com :meth:`proxima`.
        """
        self._fila.appendleft(tarefa)

    def cancelar(self, dono: str) -> int:
        """Remove os pedidos de um dono; tarefas sem mais ninguém esperando saem da fila.

        Args:
            dono (str): identificador usado em :meth:`adicionar`.

        Returns:
            int: quantas tarefas saíram da fila.
        """
        antes = len(self._fila)
        for tarefa in list(self._fila):
            tarefa.retornos = [(d, r) for d, r in tarefa.retornos if d != dono]
            if not tarefa.retornos:
                self._fila.remove(tarefa)
        return antes - len(self._fila)

    def pendentes(self, dono: str | None = None) -> int:
        """Quantas tarefas aguardam (de um dono específico, ou no total).

        Args:
            dono (str | None): filtra por dono; None conta todas.

        Returns:
            int: número de tarefas na fila.
        """
        if dono is None:
            return len(self._fila)
        return sum(1 for t in self._fila if dono in t.donos())
