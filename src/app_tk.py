"""Ponto de entrada da interface gráfica: ``python app_tk.py``.

Janela = cabeçalho (título + botão de tema) + abas principais:
  - "Busca de CPF"      → ui/aba_cpf.py  (Portal da Transparência + Jusbrasil)
  - "Consulta CNPJ"     → ui/aba_cnpj.py (API pública do CNPJ.ws)
  - "Rede de empresas"  → ui/aba_rede.py (CNPJ → empresas-sócias, recursivo)
  - "Lote de CNPJs"     → ui/aba_lote.py (lista colada/importada)

As abas de CNPJ dividem um ServicoCnpj (fila + cache + limite de 3/min).
Tema/estilos em ui/tema.py; componentes reutilizáveis em ui/widgets.py.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ui.aba_cnpj import AbaCnpj
from ui.aba_cpf import AbaCpf
from ui.aba_lote import AbaLote
from ui.aba_rede import AbaRede
from ui.servico_cnpj import ServicoCnpj
from ui.tema import Tema

NOME_APP = "CPF Finder"


class App:
    """Janela principal: monta o cabeçalho e as abas, e cuida da troca de tema."""

    def __init__(self, root: tk.Tk):
        """Monta a janela.

        Args:
            root (tk.Tk): janela raiz do Tkinter.
        """
        self.root = root
        root.title(f"{NOME_APP} — Portal da Transparência e CNPJ")
        root.geometry("1180x860")
        root.minsize(960, 700)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        self.tema = Tema(root)
        self._montar_cabecalho()

        corpo = ttk.Frame(root, padding=(20, 0, 20, 14))
        corpo.grid(row=1, column=0, sticky="nsew")
        corpo.columnconfigure(0, weight=1)
        corpo.rowconfigure(0, weight=1)
        self.abas = ttk.Notebook(corpo, style="Principal.TNotebook")
        self.abas.grid(row=0, column=0, sticky="nsew")

        # Um único serviço de CNPJ: as 3 abas de CNPJ dividem fila, cache e o limite de 3/min.
        self.servico_cnpj = ServicoCnpj(root)
        self.aba_cpf = AbaCpf(self.abas, self.tema)
        self.aba_cnpj = AbaCnpj(self.abas, self.tema, self.servico_cnpj, self.buscar_cpf_socio)
        self.aba_rede = AbaRede(self.abas, self.tema, self.servico_cnpj, self.abrir_cnpj, self.buscar_cpf_socio)
        self.aba_lote = AbaLote(self.abas, self.tema, self.servico_cnpj, self.abrir_cnpj)
        for aba, titulo in ((self.aba_cpf, "Busca de CPF"), (self.aba_cnpj, "Consulta CNPJ"),
                            (self.aba_rede, "Rede de empresas"), (self.aba_lote, "Lote de CNPJs")):
            self.abas.add(aba, text=titulo)
        self.abas.bind("<<NotebookTabChanged>>", self._ao_trocar_aba)

        root.bind("<Escape>", self._cancelar_aba_atual)
        for i in range(4):
            root.bind(f"<Control-Key-{i + 1}>", lambda _e, i=i: self.abas.select(i))

        self.tema.ao_mudar(lambda _c: self.botao_tema.configure(
            text="Modo escuro" if self.tema.nome == "claro" else "Modo claro"))
        self.tema.aplicar()
        self.aba_cpf.focar()

    def _montar_cabecalho(self):
        cab = ttk.Frame(self.root, padding=(20, 16, 20, 10))
        cab.grid(row=0, column=0, sticky="ew")
        cab.columnconfigure(0, weight=1)
        ttk.Label(cab, text=NOME_APP, style="Titulo.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(cab, style="Subtitulo.TLabel",
                  text="Busca de CPF por máscara no Portal da Transparência e consulta de dados cadastrais de CNPJ.",
                  ).grid(row=1, column=0, sticky="w")
        self.botao_tema = ttk.Button(cab, command=self.tema.alternar)
        self.botao_tema.grid(row=0, column=1, rowspan=2, sticky="e")

    def _aba_atual(self):
        return self.root.nametowidget(self.abas.select())

    def _ao_trocar_aba(self, _evento=None):
        self._aba_atual().focar()

    def _cancelar_aba_atual(self, _evento=None):
        cancelar = getattr(self._aba_atual(), "cancelar", None)
        if cancelar:
            cancelar()

    def abrir_cnpj(self, digitos: str):
        """Leva o usuário à aba "Consulta CNPJ" mostrando a empresa (do cache, sem gastar consulta).

        Args:
            digitos (str): CNPJ só com dígitos.
        """
        self.abas.select(self.aba_cnpj)
        self.aba_cnpj.mostrar(digitos)

    def buscar_cpf_socio(self, meio: str, nome: str):
        """Leva o usuário à aba "Busca de CPF" com meio do CPF e nome do sócio já preenchidos.

        Args:
            meio (str): 6 dígitos visíveis do CPF mascarado.
            nome (str): nome do sócio.
        """
        self.abas.select(self.aba_cpf)
        self.aba_cpf.preencher(meio, nome)


# Compatibilidade: código/scripts antigos importavam CpfFinderApp deste módulo.
CpfFinderApp = App


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
