"""Ponto de entrada da interface gráfica: ``python app_tk.py``.

Janela = cabeçalho (título + botão de tema) + abas principais:
  - "Busca de CPF"   → ui/aba_pessoa.py (CPF/máscara + nome; fonte Portal ou Jusbrasil)
  - "Busca de CNPJ"  → sub-abas:
        · "Consulta"          → ui/aba_cnpj.py (API pública do CNPJ.ws)
        · "Rede de empresas"  → ui/aba_rede.py (CNPJ → empresas-sócias, recursivo)
        · "Lote de CNPJs"     → ui/aba_lote.py (lista colada/importada)

As sub-abas de CNPJ dividem um ServicoCnpj (fila + cache + limite de 3/min).
A janela abre no tema escuro; o botão no cabeçalho alterna para o claro.
Tema/estilos em ui/tema.py; componentes reutilizáveis em ui/widgets.py.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk

from ui.aba_cnpj import AbaCnpj
from ui.aba_lote import AbaLote
from ui.aba_pessoa import AbaPessoa
from ui.aba_rede import AbaRede
from ui.servico_cnpj import ServicoCnpj
from ui.tema import Tema

NOME_APP = "CPF Finder"
TEMA_INICIAL = "escuro"  # a janela sempre abre no modo escuro (o botão alterna para o claro)


class App:
    """Janela principal: monta o cabeçalho e as abas, e cuida da troca de tema."""

    def __init__(self, root: tk.Tk):
        """Monta a janela.

        Args:
            root (tk.Tk): janela raiz do Tkinter.
        """
        self.root = root
        root.title(f"{NOME_APP} — Portal da Transparência e CNPJ")
        self._definir_icone(root)
        root.geometry("1180x860")
        root.minsize(960, 700)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        self.tema = Tema(root, TEMA_INICIAL)
        self._montar_cabecalho()

        corpo = ttk.Frame(root, padding=(20, 0, 20, 14))
        corpo.grid(row=1, column=0, sticky="nsew")
        corpo.columnconfigure(0, weight=1)
        corpo.rowconfigure(0, weight=1)
        self.abas = ttk.Notebook(corpo, style="Principal.TNotebook")
        self.abas.grid(row=0, column=0, sticky="nsew")

        # ── Aba 1: Busca de CPF ──
        self.aba_pessoa = AbaPessoa(self.abas, self.tema)

        # ── Aba 2: Busca de CNPJ (contém um sub-notebook com 3 sub-abas) ──
        # Um único serviço de CNPJ: as 3 sub-abas dividem fila, cache e o limite de 3/min.
        self.servico_cnpj = ServicoCnpj(root)
        self.cnpj_container = ttk.Frame(self.abas, padding=(0, 10, 0, 0))
        self.cnpj_container.columnconfigure(0, weight=1)
        self.cnpj_container.rowconfigure(0, weight=1)
        self.sub_cnpj = ttk.Notebook(self.cnpj_container)
        self.sub_cnpj.grid(row=0, column=0, sticky="nsew")

        self.aba_cnpj = AbaCnpj(self.sub_cnpj, self.tema, self.servico_cnpj, self.buscar_cpf_socio)
        self.aba_rede = AbaRede(self.sub_cnpj, self.tema, self.servico_cnpj, self.abrir_cnpj, self.buscar_cpf_socio)
        self.aba_lote = AbaLote(self.sub_cnpj, self.tema, self.servico_cnpj, self.abrir_cnpj)
        self.subabas_cnpj = ((self.aba_cnpj, "Consulta"), (self.aba_rede, "Rede de empresas"),
                             (self.aba_lote, "Lote de CNPJs"))
        for aba, titulo in self.subabas_cnpj:
            self.sub_cnpj.add(aba, text=titulo)

        self.abas.add(self.aba_pessoa, text="Busca de CPF")
        self.abas.add(self.cnpj_container, text="Busca de CNPJ")

        # Ao trocar de aba (topo ou sub), foca a folha ativa.
        self.abas.bind("<<NotebookTabChanged>>", self._ao_trocar_aba)
        self.sub_cnpj.bind("<<NotebookTabChanged>>", self._ao_trocar_aba)

        root.bind("<Escape>", self._cancelar_aba_atual)
        # Ctrl+1/2: abas do topo. Ctrl+3/4/5: sub-abas de CNPJ (abre o CNPJ e seleciona a sub).
        root.bind("<Control-Key-1>", lambda _e: self.abas.select(self.aba_pessoa))
        root.bind("<Control-Key-2>", lambda _e: self.abas.select(self.cnpj_container))
        for i, (aba, _t) in enumerate(self.subabas_cnpj):
            root.bind(f"<Control-Key-{i + 3}>", lambda _e, a=aba: self._ir_para_cnpj(a))

        self.tema.ao_mudar(lambda _c: self.botao_tema.configure(
            text="Modo escuro" if self.tema.nome == "claro" else "Modo claro"))
        self.tema.aplicar()
        self.aba_pessoa.focar()

    def _definir_icone(self, root: tk.Tk):
        """Aplica o ícone da janela, se os arquivos existirem (falha silenciosa é aceitável aqui)."""
        pasta = Path(__file__).resolve().parent / "assets"
        try:
            ico = pasta / "icone.ico"
            if ico.exists():  # Windows usa o .ico na barra de título e na barra de tarefas
                root.iconbitmap(default=str(ico))
        except tk.TclError:
            pass  # iconbitmap(default=...) não é suportado em alguns Linux/Mac
        try:
            png = pasta / "icone-64.png"
            if png.exists():  # PhotoImage funciona em todos os sistemas (mantém referência viva)
                self._icone_img = tk.PhotoImage(file=str(png))
                root.iconphoto(True, self._icone_img)
        except tk.TclError:
            pass

    def _montar_cabecalho(self):
        cab = ttk.Frame(self.root, padding=(20, 16, 20, 10))
        cab.grid(row=0, column=0, sticky="ew")
        cab.columnconfigure(0, weight=1)
        ttk.Label(cab, text=NOME_APP, style="Titulo.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(cab, style="Subtitulo.TLabel",
                  text="Busca de CPF (Portal da Transparência ou Jusbrasil) e consulta de dados cadastrais de CNPJ.",
                  ).grid(row=1, column=0, sticky="w")
        self.botao_tema = ttk.Button(cab, command=self.tema.alternar)
        self.botao_tema.grid(row=0, column=1, rowspan=2, sticky="e")

    def _folha_ativa(self):
        """Resolve a aba realmente visível: a do topo ou, se for a de CNPJ, a sub-aba ativa."""
        topo = self.root.nametowidget(self.abas.select())
        if topo is self.cnpj_container:
            return self.root.nametowidget(self.sub_cnpj.select())
        return topo

    def _ir_para_cnpj(self, aba: tk.Widget):
        """Seleciona a aba de CNPJ no topo e a sub-aba indicada."""
        self.abas.select(self.cnpj_container)
        self.sub_cnpj.select(aba)

    def _ao_trocar_aba(self, _evento=None):
        focar = getattr(self._folha_ativa(), "focar", None)
        if focar:
            focar()

    def _cancelar_aba_atual(self, _evento=None):
        cancelar = getattr(self._folha_ativa(), "cancelar", None)
        if cancelar:
            cancelar()

    def abrir_cnpj(self, digitos: str):
        """Leva o usuário à sub-aba "Consulta" mostrando a empresa (do cache, sem gastar consulta).

        Args:
            digitos (str): CNPJ só com dígitos.
        """
        self._ir_para_cnpj(self.aba_cnpj)
        self.aba_cnpj.mostrar(digitos)

    def buscar_cpf_socio(self, meio: str, nome: str):
        """Leva o usuário à aba "Busca de CPF" com meio do CPF e nome do sócio já preenchidos.

        Args:
            meio (str): 6 dígitos visíveis do CPF mascarado.
            nome (str): nome do sócio.
        """
        self.abas.select(self.aba_pessoa)
        self.aba_pessoa.preencher(meio, nome)


# Compatibilidade: código/scripts antigos importavam CpfFinderApp deste módulo.
CpfFinderApp = App


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
