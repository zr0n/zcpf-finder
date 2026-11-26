"""Componentes reutilizáveis entre as abas (tabela ordenável, área de log)."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Sequence
from datetime import datetime
from tkinter import ttk

from ui.tema import Tema

# (chave, rótulo, largura, estica?)
Coluna = tuple[str, str, int, bool]


def formatar_numero(n: int) -> str:
    """Formata inteiro com separador de milhar brasileiro.

    Args:
        n (int): número a formatar.

    Returns:
        str: ex. ``1.000.000``.
    """
    return f"{n:,}".replace(",", ".")


def adicionar_cartao(notebook: ttk.Notebook, titulo: str, criar: Callable[[tk.Widget], tk.Widget]):
    """Cria uma aba com cartão (fundo de painel + padding) e o widget dentro dela.

    Args:
        notebook (ttk.Notebook): notebook de destino.
        titulo (str): texto da aba.
        criar (Callable[[tk.Widget], tk.Widget]): fábrica que recebe o cartão como pai.

    Returns:
        tk.Widget: o widget criado pela fábrica.
    """
    cartao = ttk.Frame(notebook, style="Painel.TFrame", padding=12)
    cartao.columnconfigure(0, weight=1)
    cartao.rowconfigure(0, weight=1)
    widget = criar(cartao)
    widget.grid(row=0, column=0, sticky="nsew")
    notebook.add(cartao, text=titulo)
    return widget


class Tabela(ttk.Frame):
    """Treeview com rolagem, linhas listradas, ordenação por coluna e menu "copiar".

    Attributes:
        tree (ttk.Treeview): a tabela em si (exposta para binds específicos).
    """

    def __init__(self, pai: tk.Widget, tema: Tema, colunas: Sequence[Coluna],
                 ao_copiar: Callable[[str], None] | None = None,
                 coluna_copia: str | None = None,
                 acoes_extras: Sequence[tuple[str, Callable[[], None]]] = (),
                 arvore: tuple[str, int] | None = None):
        """Monta a tabela.

        Args:
            pai (tk.Widget): container.
            tema (Tema): tema ativo (cores das listras e do menu).
            colunas (Sequence[Coluna]): definição das colunas.
            ao_copiar (Callable[[str], None] | None): chamado com uma mensagem após copiar
                (para a aba mostrar no status).
            coluna_copia (str | None): coluna copiada no duplo clique/Ctrl+C; None = linha inteira.
            acoes_extras (Sequence[tuple[str, Callable]] | Callable[[str], Sequence]): itens extras no topo do
                menu de contexto, ou função que recebe o id da linha clicada e devolve esses itens.
            arvore (tuple[str, int] | None): (rótulo, largura) da coluna hierárquica; None = tabela plana.
        """
        super().__init__(pai, style="Painel.TFrame")
        self.tema = tema
        self.colunas = colunas
        self.ao_copiar = ao_copiar or (lambda _m: None)
        self.coluna_copia = coluna_copia
        self.acoes_extras = acoes_extras
        self.arvore = arvore is not None
        self._reverso: dict[str, bool] = {}

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(self, columns=[c[0] for c in colunas],
                                 show="tree headings" if arvore else "headings", selectmode="extended")
        if arvore:
            self.tree.heading("#0", text=arvore[0], anchor="w")
            self.tree.column("#0", width=arvore[1], minwidth=120, stretch=True)
        for chave, rotulo, largura, estica in colunas:
            self.tree.heading(chave, text=rotulo, anchor="w", command=lambda k=chave: self.ordenar(k))
            self.tree.column(chave, width=largura, minwidth=60, anchor="w", stretch=estica)
        rolagem = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=rolagem.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        rolagem.grid(row=0, column=1, sticky="ns")

        self.tree.bind("<Double-1>", lambda _e: self.copiar(self.coluna_copia))
        self.tree.bind("<Control-c>", lambda _e: self.copiar(self.coluna_copia))
        self.tree.bind("<Button-3>", self._menu)
        tema.ao_mudar(self._cores)

    def _cores(self, c: dict):
        # Tags de uso comum entre as abas: "suave" (pendente/secundário), "erro", "ok", "destaque".
        self.tree.tag_configure("listra", background=c["listra"])
        self.tree.tag_configure("suave", foreground=c["suave"])
        self.tree.tag_configure("erro", foreground=c["perigo"])
        self.tree.tag_configure("ok", foreground=c["sucesso"])
        self.tree.tag_configure("destaque", foreground=c["destaque"])

    def inserir(self, valores: Sequence, tags: Sequence[str] = (), pai: str = "", texto: str = "") -> str:
        """Adiciona uma linha no fim (alternando a listra no modo plano).

        Args:
            valores (Sequence): valores na ordem das colunas.
            tags (Sequence[str]): tags extras (ex.: para cor de texto).
            pai (str): id da linha pai (só no modo árvore).
            texto (str): texto da coluna hierárquica (só no modo árvore).

        Returns:
            str: id da linha criada (para :meth:`atualizar`).
        """
        listra = ("listra",) if not self.arvore and len(self.tree.get_children()) % 2 else ()
        return self.tree.insert(pai, "end", text=texto, values=list(valores), tags=(*listra, *tags), open=True)

    def atualizar(self, iid: str, tags: Sequence[str] | None = None, texto: str | None = None, **valores):
        """Altera células de uma linha existente.

        Args:
            iid (str): id devolvido por :meth:`inserir`.
            tags (Sequence[str] | None): novas tags extras (a listra é preservada).
            texto (str | None): novo texto da coluna hierárquica.
            **valores: coluna=valor a alterar.
        """
        for coluna, valor in valores.items():
            self.tree.set(iid, coluna, valor)
        if texto is not None:
            self.tree.item(iid, text=texto)
        if tags is not None:
            listra = ("listra",) if "listra" in self.tree.item(iid, "tags") else ()
            self.tree.item(iid, tags=(*listra, *tags))

    def limpar(self):
        """Remove todas as linhas."""
        self.tree.delete(*self.tree.get_children())

    def __len__(self) -> int:
        return len(self.tree.get_children())

    def selecionados(self, coluna: str) -> list[str]:
        """Valores de uma coluna nas linhas selecionadas.

        Args:
            coluna (str): chave da coluna.

        Returns:
            list[str]: valores, na ordem da seleção.
        """
        return [self.tree.set(i, coluna) for i in self.tree.selection()]

    def ordenar(self, coluna: str):
        """Ordena pela coluna; cliques seguidos alternam crescente/decrescente.

        Args:
            coluna (str): chave da coluna clicada.
        """
        reverso = self._reverso.get(coluna, False)
        itens = sorted(self.tree.get_children(), key=lambda i: str(self.tree.set(i, coluna)).casefold(),
                       reverse=reverso)
        for pos, item in enumerate(itens):
            self.tree.move(item, "", pos)
            if self.arvore:  # na árvore não há listras (a indentação já separa visualmente)
                continue
            outras = [t for t in self.tree.item(item, "tags") if t != "listra"]
            self.tree.item(item, tags=(*(("listra",) if pos % 2 else ()), *outras))
        self._reverso[coluna] = not reverso

    def copiar(self, coluna: str | None = None):
        """Copia a coluna indicada (ou a linha inteira, separada por TAB) das linhas selecionadas.

        Args:
            coluna (str | None): chave da coluna; None copia a linha toda.
        """
        if coluna:
            linhas = self.selecionados(coluna)
        else:
            linhas = ["\t".join(str(v) for v in self.tree.item(i, "values")) for i in self.tree.selection()]
        if not linhas:
            return
        self.clipboard_clear()
        self.clipboard_append("\n".join(linhas))
        self.ao_copiar(f"{len(linhas)} linha(s) copiada(s).")

    def _menu(self, evento):
        linha = self.tree.identify_row(evento.y)
        if not linha:
            return
        if linha not in self.tree.selection():
            self.tree.selection_set(linha)
        menu = self.tema.menu(self)
        # acoes_extras pode ser fixa ou uma função(iid) → lista, para variar conforme a linha clicada.
        acoes = self.acoes_extras(linha) if callable(self.acoes_extras) else self.acoes_extras
        for rotulo, acao in acoes:
            menu.add_command(label=rotulo, command=acao)
        if acoes:
            menu.add_separator()
        if self.coluna_copia:
            rotulo = dict((c[0], c[1]) for c in self.colunas)[self.coluna_copia]
            menu.add_command(label=f"Copiar {rotulo}", command=lambda: self.copiar(self.coluna_copia))
        menu.add_command(label="Copiar linha", command=lambda: self.copiar(None))
        menu.tk_popup(evento.x_root, evento.y_root)


class AreaLog(ttk.Frame):
    """Texto somente leitura com horário e cores por tipo de mensagem (ok/aviso/erro/info)."""

    def __init__(self, pai: tk.Widget, tema: Tema):
        """Monta a área de log.

        Args:
            pai (tk.Widget): container.
            tema (Tema): tema ativo.
        """
        super().__init__(pai, style="Painel.TFrame")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.texto = tk.Text(self, state="disabled", wrap=tk.WORD, relief=tk.FLAT, font=tema.fonte_mono,
                             highlightthickness=1, padx=8, pady=6)
        rolagem = ttk.Scrollbar(self, orient="vertical", command=self.texto.yview)
        self.texto.configure(yscrollcommand=rolagem.set)
        self.texto.grid(row=0, column=0, sticky="nsew")
        rolagem.grid(row=0, column=1, sticky="ns")
        tema.estilizar_texto(self.texto)

    def escrever(self, msg: str, tag: str | None = None):
        """Adiciona uma linha com horário.

        Args:
            msg (str): mensagem.
            tag (str | None): "ok", "aviso", "erro" ou "info".
        """
        self.texto.configure(state="normal")
        self.texto.insert("end", f"{datetime.now():%H:%M:%S}  ", "info")
        self.texto.insert("end", msg + "\n", tag or ())
        self.texto.see("end")
        self.texto.configure(state="disabled")

    def limpar(self):
        """Apaga todo o conteúdo."""
        self.texto.configure(state="normal")
        self.texto.delete("1.0", "end")
        self.texto.configure(state="disabled")
