"""Design system da interface: paletas claro/escuro, fontes e estilos ttk.

Mesmo tema do projeto Analizador-De-Video-Youtube. As abas não conhecem cores:
pedem um estilo pelo nome (ex.: "Destaque.TButton") e, para widgets tk clássicos
(Text, Menu), registram um callback em :meth:`Tema.ao_mudar`.
"""

from __future__ import annotations

import sys
import tkinter as tk
from collections.abc import Callable
from tkinter import font as tkfont, ttk

PALETAS = {
    "claro": {
        "fundo": "#EEF1F6", "painel": "#FFFFFF", "texto": "#1F2937", "suave": "#6B7280",
        "destaque": "#2563EB", "destaque_ativo": "#1D4ED8", "borda": "#D1D5DB",
        "campo": "#FFFFFF", "selecao": "#DBEAFE", "perigo": "#DC2626", "perigo_ativo": "#B91C1C",
        "listra": "#F8FAFC", "sucesso": "#16A34A", "alerta": "#D97706",
    },
    "escuro": {
        "fundo": "#0F172A", "painel": "#1E293B", "texto": "#F1F5F9", "suave": "#94A3B8",
        "destaque": "#3B82F6", "destaque_ativo": "#2563EB", "borda": "#334155",
        "campo": "#0F172A", "selecao": "#1E3A8A", "perigo": "#EF4444", "perigo_ativo": "#DC2626",
        "listra": "#162032", "sucesso": "#4ADE80", "alerta": "#FBBF24",
    },
}


class Tema:
    """Guarda a paleta ativa, as fontes e reaplica os estilos ttk na troca de tema."""

    def __init__(self, root: tk.Tk, nome: str = "claro"):
        """Configura fontes e o tema base "clam".

        Args:
            root (tk.Tk): janela raiz.
            nome (str): paleta inicial ("claro" ou "escuro").
        """
        self.root = root
        self.nome = nome
        self.estilo = ttk.Style(root)
        self.estilo.theme_use("clam")  # "clam" aceita cores personalizadas em todos os sistemas
        self._callbacks: list[Callable[[dict], None]] = []

        familia = "Segoe UI" if sys.platform == "win32" else tkfont.nametofont("TkDefaultFont").actual("family")
        for nome_fonte in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            tkfont.nametofont(nome_fonte).configure(family=familia, size=10)
        self.fonte_titulo = tkfont.Font(family=familia, size=17, weight="bold")
        self.fonte_secao = tkfont.Font(family=familia, size=11, weight="bold")
        self.fonte_negrito = tkfont.Font(family=familia, size=10, weight="bold")
        self.fonte_destaque = tkfont.Font(family=familia, size=14, weight="bold")
        self.fonte_mono = tkfont.Font(family="Consolas" if sys.platform == "win32" else "DejaVu Sans Mono", size=9)

    @property
    def cores(self) -> dict:
        """dict: paleta ativa."""
        return PALETAS[self.nome]

    def ao_mudar(self, callback: Callable[[dict], None]):
        """Registra um callback chamado com a paleta sempre que o tema for aplicado.

        Args:
            callback (Callable[[dict], None]): recebe o dict de cores.
        """
        self._callbacks.append(callback)

    def alternar(self):
        """Troca entre claro e escuro e reaplica tudo."""
        self.nome = "escuro" if self.nome == "claro" else "claro"
        self.aplicar()

    def aplicar(self):
        """Reconfigura todos os estilos ttk e chama os callbacks registrados."""
        c = self.cores
        e = self.estilo
        self.root.configure(bg=c["fundo"])

        e.configure(".", background=c["fundo"], foreground=c["texto"], bordercolor=c["borda"],
                    lightcolor=c["borda"], darkcolor=c["borda"], troughcolor=c["fundo"],
                    fieldbackground=c["campo"], selectbackground=c["selecao"], selectforeground=c["texto"])
        e.configure("TFrame", background=c["fundo"])
        e.configure("Painel.TFrame", background=c["painel"])
        e.configure("TLabel", background=c["fundo"], foreground=c["texto"])
        e.configure("Painel.TLabel", background=c["painel"], foreground=c["texto"])
        e.configure("Titulo.TLabel", background=c["fundo"], font=self.fonte_titulo)
        e.configure("Subtitulo.TLabel", background=c["fundo"], foreground=c["suave"])
        e.configure("Secao.TLabel", background=c["painel"], font=self.fonte_secao)
        e.configure("Dica.TLabel", background=c["painel"], foreground=c["suave"])
        e.configure("Info.TLabel", background=c["painel"], foreground=c["destaque"], font=self.fonte_secao)
        for nome, cor in (("Previa", "destaque"), ("PreviaAlerta", "alerta"), ("PreviaErro", "perigo"),
                          ("PreviaOk", "sucesso")):
            e.configure(f"{nome}.TLabel", background=c["painel"], foreground=c[cor], font=self.fonte_negrito)
        e.configure("TSeparator", background=c["borda"])
        e.configure("Destaque.TLabel", background=c["painel"], foreground=c["texto"], font=self.fonte_destaque)
        # Selos coloridos (ex.: situação cadastral "Ativa"/"Baixada").
        for nome, cor in (("SeloOk", "sucesso"), ("SeloAlerta", "alerta"), ("SeloErro", "perigo")):
            e.configure(f"{nome}.TLabel", background=c[cor], foreground="white", font=self.fonte_negrito,
                        padding=(10, 3))

        e.configure("TButton", padding=(12, 6), background=c["painel"], foreground=c["texto"])
        e.map("TButton", background=[("disabled", c["fundo"]), ("active", c["selecao"])],
              foreground=[("disabled", c["suave"])])
        e.configure("Destaque.TButton", padding=(26, 8), background=c["destaque"], foreground="white",
                    bordercolor=c["destaque"], font=self.fonte_negrito)
        e.map("Destaque.TButton", background=[("disabled", c["borda"]), ("active", c["destaque_ativo"])],
              foreground=[("disabled", c["suave"])])
        e.configure("Perigo.TButton", background=c["perigo"], foreground="white", bordercolor=c["perigo"])
        e.map("Perigo.TButton", background=[("active", c["perigo_ativo"])])

        for estilo in ("TEntry", "TSpinbox"):
            e.configure(estilo, fieldbackground=c["campo"], foreground=c["texto"], insertcolor=c["texto"],
                        padding=5, arrowcolor=c["suave"], background=c["painel"])
            e.map(estilo, bordercolor=[("focus", c["destaque"])],
                  fieldbackground=[("disabled", c["fundo"])], foreground=[("disabled", c["suave"])])
        for estilo in ("Painel.TCheckbutton", "Painel.TRadiobutton"):
            e.configure(estilo, background=c["painel"], foreground=c["texto"],
                        indicatorbackground=c["campo"], indicatorforeground=c["destaque"])
            e.map(estilo, background=[("active", c["painel"])],
                  indicatorbackground=[("selected", c["campo"])], foreground=[("disabled", c["suave"])])

        # Abas principais (nível 1) e sub-abas de resultado (nível 2, dentro do cartão branco).
        e.configure("TNotebook", background=c["fundo"], borderwidth=0, tabmargins=(0, 0, 0, 0))
        e.configure("TNotebook.Tab", padding=(16, 8), background=c["fundo"], foreground=c["suave"],
                    font=self.fonte_secao)
        # O "clam" mapeia um padding menor para a aba selecionada (ela ficava mais baixa que as outras):
        # repetir o mesmo padding no estado "selected" mantém todas do mesmo tamanho.
        e.map("TNotebook.Tab", background=[("selected", c["painel"])], foreground=[("selected", c["texto"])],
              expand=[("selected", (0, 0, 0, 0))], padding=[("selected", (16, 8))])
        e.configure("Principal.TNotebook", background=c["fundo"], borderwidth=0)
        e.configure("Principal.TNotebook.Tab", padding=(22, 10), font=self.fonte_secao)
        e.map("Principal.TNotebook.Tab", background=[("selected", c["painel"]), ("!selected", c["fundo"])],
              foreground=[("selected", c["destaque"]), ("!selected", c["suave"])],
              expand=[("selected", (0, 0, 0, 0))], padding=[("selected", (22, 10))])

        e.configure("Treeview", background=c["campo"], fieldbackground=c["campo"], foreground=c["texto"],
                    rowheight=28, borderwidth=0)
        e.map("Treeview", background=[("selected", c["selecao"])], foreground=[("selected", c["texto"])])
        e.configure("Treeview.Heading", background=c["painel"], foreground=c["suave"], font=self.fonte_secao,
                    padding=(2, 6), relief="flat")  # padding pequeno: alinha o título ao texto das células
        e.map("Treeview.Heading", background=[("active", c["selecao"])])

        e.configure("Horizontal.TProgressbar", background=c["destaque"], troughcolor=c["borda"],
                    bordercolor=c["borda"], thickness=10)
        # No "clam", background é o "polegar" da barra: cor de borda para contrastar nos dois temas.
        e.configure("Vertical.TScrollbar", background=c["borda"], troughcolor=c["painel"], bordercolor=c["painel"],
                    lightcolor=c["borda"], darkcolor=c["borda"], arrowcolor=c["suave"], gripcount=0)
        e.map("Vertical.TScrollbar", background=[("active", c["suave"])])

        for callback in self._callbacks:
            callback(c)

    def estilizar_texto(self, texto: tk.Text):
        """Aplica (e mantém atualizadas) as cores do tema em um ``tk.Text``.

        Args:
            texto (tk.Text): widget clássico, que não segue o ttk.Style.
        """
        def aplicar(c: dict):
            texto.configure(bg=c["campo"], fg=c["texto"], insertbackground=c["texto"],
                            highlightbackground=c["borda"], highlightcolor=c["destaque"],
                            selectbackground=c["selecao"], selectforeground=c["texto"])
            texto.tag_configure("ok", foreground=c["sucesso"])
            texto.tag_configure("aviso", foreground=c["alerta"])
            texto.tag_configure("erro", foreground=c["perigo"])
            texto.tag_configure("info", foreground=c["suave"])
        self.ao_mudar(aplicar)

    def menu(self, pai: tk.Widget) -> tk.Menu:
        """Cria um menu de contexto já com as cores da paleta atual.

        Args:
            pai (tk.Widget): widget dono do menu.

        Returns:
            tk.Menu: menu sem "tearoff".
        """
        c = self.cores
        return tk.Menu(pai, tearoff=0, bg=c["painel"], fg=c["texto"],
                       activebackground=c["selecao"], activeforeground=c["texto"], bd=0)


class Dica:
    """Balão de ajuda que aparece ao passar o mouse (ou clicar) sobre um widget."""

    ATRASO_MS = 350

    def __init__(self, widget: tk.Widget, texto: str, tema: Tema):
        """Liga o balão aos eventos do widget.

        Args:
            widget (tk.Widget): elemento que dispara a dica (o ícone "i").
            texto (str): conteúdo do balão.
            tema (Tema): fornece a paleta ativa no momento de abrir o balão.
        """
        self.widget, self.texto, self.tema = widget, texto, tema
        self._janela: tk.Toplevel | None = None
        self._agendado: str | None = None
        widget.bind("<Enter>", self._agendar, add="+")
        widget.bind("<Leave>", self._esconder, add="+")
        widget.bind("<Button-1>", self._alternar, add="+")

    def _agendar(self, _e=None):
        self._cancelar_agendamento()
        self._agendado = self.widget.after(self.ATRASO_MS, self._mostrar)

    def _cancelar_agendamento(self):
        if self._agendado:
            self.widget.after_cancel(self._agendado)
            self._agendado = None

    def _alternar(self, _e=None):
        if self._janela:
            self._esconder()
        else:
            self._mostrar()

    def _mostrar(self):
        self._cancelar_agendamento()
        if self._janela:
            return
        c = self.tema.cores
        self._janela = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)  # sem barra de título: vira um balão
        borda = tk.Frame(tw, bg=c["borda"], padx=1, pady=1)
        borda.pack()
        tk.Label(borda, text=self.texto, justify=tk.LEFT, wraplength=340, bg=c["painel"], fg=c["texto"],
                 font="TkDefaultFont", padx=12, pady=10).pack()
        tw.update_idletasks()
        # Abaixo do ícone; se não couber à direita da tela, desloca para a esquerda.
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        x = min(x, self.widget.winfo_screenwidth() - tw.winfo_reqwidth() - 8)
        tw.wm_geometry(f"+{max(0, x)}+{y}")

    def _esconder(self, _e=None):
        self._cancelar_agendamento()
        if self._janela:
            self._janela.destroy()
            self._janela = None


def icone_dica(pai: tk.Widget, texto: str, tema: Tema) -> ttk.Label:
    """Cria o ícone "ⓘ" com balão de ajuda (passar o mouse ou clicar).

    Args:
        pai (tk.Widget): container onde o ícone será colocado.
        texto (str): explicação exibida no balão.
        tema (Tema): tema ativo.

    Returns:
        ttk.Label: o ícone, ainda não posicionado (o chamador faz pack/grid).
    """
    icone = ttk.Label(pai, text="ⓘ", style="Info.TLabel", cursor="question_arrow")
    Dica(icone, texto, tema)
    return icone
