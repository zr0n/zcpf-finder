"""Aba "Lote de CNPJs": cola (ou importa) uma lista e consulta todos pela fila compartilhada."""

from __future__ import annotations

import csv
import math
import tkinter as tk
from collections.abc import Callable
from tkinter import filedialog, messagebox, ttk

from cnpj_ws import LIMITE_POR_MINUTO, Empresa, extrair_cnpjs, formatar_cnpj, limpar_cnpj
from caminhos import pasta_resultados
from ui.servico_cnpj import ServicoCnpj
from ui.tema import Tema, icone_dica
from ui.widgets import Tabela, adicionar_cartao

DONO = "lote"

COLUNAS = (
    ("cnpj", "CNPJ", 165, False),
    ("razao", "Razão social", 280, True),
    ("situacao", "Situação", 90, False),
    ("local", "Cidade / UF", 170, True),
    ("capital", "Capital social", 140, False),
    ("socios", "Sócios", 70, False),
    ("status", "Status", 120, False),
)

DICA_LOTE = (
    "Cole CNPJs em qualquer formato: um por linha, separados por vírgula, ou o conteúdo de uma "
    "planilha/CSV. Também dá para importar um arquivo .txt ou .csv.\n\n"
    "Números com 12–13 dígitos ganham zero à esquerda (o Excel costuma apagar). Repetidos são ignorados.\n\n"
    f"Limite da API: {LIMITE_POR_MINUTO} por minuto — 30 CNPJs levam ~10 minutos. "
    "CNPJs já consultados nesta sessão saem na hora."
)


class AbaLote(ttk.Frame):
    """Consulta em lote com tabela de resultados e exportação CSV."""

    def __init__(self, pai: tk.Widget, tema: Tema, servico: ServicoCnpj, abrir_detalhe: Callable[[str], None]):
        """Monta a aba.

        Args:
            pai (tk.Widget): notebook principal.
            tema (Tema): tema ativo.
            servico (ServicoCnpj): fila/cache/limite compartilhados.
            abrir_detalhe (Callable[[str], None]): mostra um CNPJ na aba "Consulta CNPJ".
        """
        super().__init__(pai, padding=(0, 12, 0, 0))
        self.tema, self.servico, self.abrir_detalhe = tema, servico, abrir_detalhe
        self._linhas: dict[str, str] = {}       # cnpj → iid
        self._resultados: dict[str, Empresa] = {}
        self._erros = 0
        self.ativo = False

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self._montar_entrada()
        self._montar_tabela()
        self._montar_rodape()
        self._atualizar_contagem()
        self._tique()

    # ── Construção ─────────────────────────────────────────────────────────
    def _montar_entrada(self):
        cartao = ttk.Frame(self, style="Painel.TFrame", padding=18)
        cartao.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        cartao.columnconfigure(0, weight=1)

        titulo = ttk.Frame(cartao, style="Painel.TFrame")
        titulo.grid(row=0, column=0, sticky="w")
        ttk.Label(titulo, text="Lista de CNPJs", style="Secao.TLabel").pack(side=tk.LEFT)
        icone_dica(titulo, DICA_LOTE, self.tema).pack(side=tk.LEFT, padx=(6, 0))

        moldura = ttk.Frame(cartao, style="Painel.TFrame")
        moldura.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        moldura.columnconfigure(0, weight=1)
        self.texto = tk.Text(moldura, height=4, wrap=tk.WORD, relief=tk.FLAT, highlightthickness=1,
                             padx=8, pady=6, font="TkDefaultFont", undo=True)
        rolagem = ttk.Scrollbar(moldura, orient="vertical", command=self.texto.yview)
        self.texto.configure(yscrollcommand=rolagem.set)
        self.texto.grid(row=0, column=0, sticky="ew")
        rolagem.grid(row=0, column=1, sticky="ns")
        self.texto.bind("<<Modified>>", self._ao_editar)
        self.tema.estilizar_texto(self.texto)

        linha = ttk.Frame(cartao, style="Painel.TFrame")
        linha.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        linha.columnconfigure(0, weight=1)
        self.lbl_contagem = ttk.Label(linha, style="Dica.TLabel")
        self.lbl_contagem.grid(row=0, column=0, sticky="w")
        self.btn_importar = ttk.Button(linha, text="Importar arquivo…", command=self._on_importar)
        self.btn_importar.grid(row=0, column=1, padx=(0, 8))
        self.btn_consultar = ttk.Button(linha, text="Consultar lote", command=self._on_consultar,
                                        style="Destaque.TButton")
        self.btn_consultar.grid(row=0, column=2)

    def _montar_tabela(self):
        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=1, column=0, sticky="nsew")
        self.tabela = adicionar_cartao(self.notebook, "Resultados", lambda p: Tabela(
            p, self.tema, COLUNAS, self._status, coluna_copia="cnpj",
            acoes_extras=[("Abrir detalhe da empresa", self._abrir_selecionado)],
        ))
        self.tabela.tree.bind("<Double-1>", lambda _e: self._abrir_selecionado())

    def _montar_rodape(self):
        rodape = ttk.Frame(self, padding=(0, 10, 0, 0))
        rodape.grid(row=2, column=0, sticky="ew")
        rodape.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(rodape, mode="determinate", maximum=1)
        self.progress.grid(row=0, column=0, columnspan=4, sticky="ew")
        self.lbl_status = ttk.Label(rodape, text="Pronto.", style="Subtitulo.TLabel")
        self.lbl_status.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.btn_csv = ttk.Button(rodape, text="Exportar CSV", command=self._on_exportar, state="disabled")
        self.btn_csv.grid(row=1, column=2, sticky="e", pady=(6, 0), padx=(8, 0))
        self.btn_limpar = ttk.Button(rodape, text="Limpar", command=self._on_limpar)
        self.btn_limpar.grid(row=1, column=3, sticky="e", pady=(6, 0), padx=(8, 0))
        self.btn_cancelar = ttk.Button(rodape, text="Cancelar", command=self.cancelar, style="Perigo.TButton")

    # ── Estado ─────────────────────────────────────────────────────────────
    def focar(self):
        """Coloca o cursor na caixa de texto."""
        self.texto.focus_set()

    def _status(self, texto: str):
        self.lbl_status.configure(text=texto)

    def _ao_editar(self, _e=None):
        self.texto.edit_modified(False)  # rearma o evento <<Modified>>
        self._atualizar_contagem()

    def _atualizar_contagem(self):
        validos, invalidos = extrair_cnpjs(self.texto.get("1.0", "end"))
        if not validos and not invalidos:
            self.lbl_contagem.configure(text="Cole os CNPJs acima (um por linha, vírgula ou CSV).",
                                        style="Dica.TLabel")
            return
        novos = [c for c in validos if c not in self.servico.cache]
        minutos = math.ceil(len(novos) / LIMITE_POR_MINUTO)
        texto = f"{len(validos)} CNPJ(s) válido(s)"
        if invalidos:
            texto += f"  ·  {len(invalidos)} inválido(s): {', '.join(invalidos[:3])}{'…' if len(invalidos) > 3 else ''}"
        texto += f"  ·  tempo estimado: ~{minutos} min" if novos else "  ·  todos já no cache"
        self.lbl_contagem.configure(text=texto, style="PreviaAlerta.TLabel" if invalidos else "PreviaOk.TLabel")

    def _set_ativo(self, ativo: bool):
        self.ativo = ativo
        estado = "disabled" if ativo else "normal"
        for w in (self.btn_importar, self.btn_consultar, self.btn_limpar):
            w.configure(state=estado)
        self.texto.configure(state=estado)
        if ativo:
            self.btn_cancelar.grid(row=1, column=1, sticky="e", pady=(6, 0))
            self.btn_csv.configure(state="disabled")
        else:
            self.btn_cancelar.grid_remove()
            self.btn_csv.configure(state="normal" if self._linhas else "disabled")

    def _concluidos(self) -> int:
        return len(self._resultados) + self._erros

    def _tique(self):
        if self.ativo:
            total = len(self._linhas)
            fila = self.servico.resumo()
            self._status(f"{self._concluidos()} de {total} consultado(s)  ·  {fila or 'consultando…'}")
            self.progress.configure(maximum=max(1, total), value=self._concluidos())
        self.after(1000, self._tique)

    # ── Fluxo ──────────────────────────────────────────────────────────────
    def _on_importar(self):
        path = filedialog.askopenfilename(initialdir=pasta_resultados(),
                                          filetypes=[("Texto ou CSV", "*.txt *.csv"), ("Todos", "*.*")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8-sig", errors="replace") as f:
                conteudo = f.read()
        except OSError as e:
            messagebox.showerror("Erro ao abrir", str(e))
            return
        self.texto.delete("1.0", "end")
        self.texto.insert("1.0", conteudo)
        self._atualizar_contagem()

    def _on_consultar(self):
        if self.ativo:
            return
        validos, invalidos = extrair_cnpjs(self.texto.get("1.0", "end"))
        if not validos:
            messagebox.showerror("Nenhum CNPJ válido", "Não encontrei nenhum CNPJ válido no texto.")
            return
        if invalidos and not messagebox.askyesno(
            "CNPJs inválidos",
            f"{len(invalidos)} item(ns) parecem CNPJ mas têm dígito verificador errado e serão ignorados:\n"
            f"{', '.join(invalidos[:10])}{'…' if len(invalidos) > 10 else ''}\n\nContinuar com os "
            f"{len(validos)} válidos?",
        ):
            return
        self._limpar_tabela()
        self._set_ativo(True)
        for digitos in validos:
            iid = self.tabela.inserir([formatar_cnpj(digitos), "", "", "", "", "", "Na fila"], tags=("suave",))
            self._linhas[digitos] = iid
            self.servico.consultar(digitos, self._receber, dono=DONO)

    def _receber(self, digitos: str, emp: Empresa | None, erro: Exception | None):
        iid = self._linhas.get(digitos)
        if iid is None or digitos in self._resultados:
            return
        if emp is None:
            self._erros += 1
            self.tabela.atualizar(iid, tags=("erro",), status=f"Erro: {erro}")
        else:
            self._resultados[digitos] = emp
            self.tabela.atualizar(iid, razao=emp.razao_social, situacao=emp.situacao, local=emp.cidade_uf,
                                  capital=emp.capital_social, socios=len(emp.socios), status="OK",
                                  tags=() if emp.ativa else ("erro",))
        if self._concluidos() >= len(self._linhas):
            self._set_ativo(False)
            self.progress.configure(maximum=max(1, len(self._linhas)), value=self._concluidos())
            self._status(f"Lote concluído: {len(self._resultados)} OK, {self._erros} com erro.")

    def cancelar(self):
        """Tira da fila os CNPJs do lote que ainda não foram consultados."""
        if not self.ativo:
            return
        removidos = self.servico.cancelar(DONO)
        for digitos, iid in self._linhas.items():
            if digitos not in self._resultados and self.tabela.tree.set(iid, "status") == "Na fila":
                self.tabela.atualizar(iid, status="Cancelado")
        self._set_ativo(False)
        self._status(f"Cancelado: {removidos} CNPJ(s) não consultado(s).")

    def _limpar_tabela(self):
        self.tabela.limpar()
        self._linhas.clear()
        self._resultados.clear()
        self._erros = 0
        self.progress.configure(value=0, maximum=1)

    def _on_limpar(self):
        if self.ativo:
            return
        self._limpar_tabela()
        self.texto.delete("1.0", "end")
        self.btn_csv.configure(state="disabled")
        self._atualizar_contagem()
        self._status("Pronto.")

    def _abrir_selecionado(self):
        cnpjs = self.tabela.selecionados("cnpj")
        if not cnpjs:
            return
        digitos = limpar_cnpj(cnpjs[0])
        if digitos in self.servico.cache:
            self.abrir_detalhe(digitos)
        else:
            self._status("Esse CNPJ ainda não foi consultado.")

    def _on_exportar(self):
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")],
                                            initialdir=pasta_resultados(), initialfile="lote_cnpj.csv")
        if not path:
            return
        campos = ["cnpj", "razao_social", "nome_fantasia", "situacao", "abertura", "porte", "natureza_juridica",
                  "capital_social", "atividade_principal", "endereco", "cidade_uf", "cep", "telefones", "email",
                  "qtd_socios", "status"]
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(campos)
            for digitos, iid in self._linhas.items():
                emp = self._resultados.get(digitos)
                status = self.tabela.tree.set(iid, "status")
                if emp is None:
                    w.writerow([formatar_cnpj(digitos)] + [""] * (len(campos) - 2) + [status])
                    continue
                w.writerow([emp.cnpj, emp.razao_social, emp.nome_fantasia, emp.situacao, emp.abertura, emp.porte,
                            emp.natureza_juridica, emp.capital_social, emp.atividade_principal, emp.endereco,
                            emp.cidade_uf, emp.cep, " | ".join(emp.telefones), emp.email, len(emp.socios), status])
        self._status(f"CSV salvo em {path}")
