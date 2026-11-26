"""Aba "Rede de empresas": consulta um CNPJ e segue automaticamente os sócios que são empresas.

Só segue sócio pessoa jurídica (CNPJ). Sócio pessoa física aparece na árvore com o
documento mascarado e NÃO é pesquisado automaticamente: o usuário pode, um por vez,
levar o sócio para a aba de CPF (campos preenchidos, busca só com clique em Buscar).
"""

from __future__ import annotations

import csv
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from tkinter import filedialog, messagebox, ttk

from cnpj_ws import Empresa, cnpj_valido, formatar_cnpj, limpar_cnpj, meio_cpf
from caminhos import pasta_resultados
from ui.servico_cnpj import ServicoCnpj
from ui.tema import Tema, icone_dica
from ui.widgets import Tabela, adicionar_cartao

DONO = "rede"

COLUNAS = (
    ("cnpj", "CNPJ / documento", 170, False),
    ("relacao", "Relação", 190, True),
    ("situacao", "Situação", 90, False),
    ("local", "Cidade / UF", 170, True),
    ("status", "Status", 120, False),
)

DICA_REDE = (
    "Consulta o CNPJ informado e, para cada sócio que é EMPRESA, consulta também essa empresa "
    "— e assim por diante até a profundidade escolhida.\n\n"
    "Profundidade 1 = só a empresa e seus sócios. 2 = também as empresas-sócias dela. "
    "Cada empresa gasta 1 consulta (limite de 3/min): o \"máximo de empresas\" evita redes enormes.\n\n"
    "Sócios pessoa física são só listados (documento mascarado); não são pesquisados."
)


@dataclass
class No:
    """Empresa na árvore da rede.

    Attributes:
        iid (str): linha na árvore.
        digitos (str): CNPJ.
        nivel (int): 0 = empresa inicial.
    """

    iid: str
    digitos: str
    nivel: int


class AbaRede(ttk.Frame):
    """Monta a árvore empresa → empresas-sócias, consultando pela fila compartilhada."""

    def __init__(self, pai: tk.Widget, tema: Tema, servico: ServicoCnpj, abrir_detalhe: Callable[[str], None],
                 buscar_cpf: Callable[[str, str], None]):
        """Monta a aba.

        Args:
            pai (tk.Widget): notebook principal.
            tema (Tema): tema ativo.
            servico (ServicoCnpj): fila/cache/limite compartilhados.
            abrir_detalhe (Callable[[str], None]): mostra um CNPJ na aba "Consulta CNPJ".
            buscar_cpf (Callable[[str, str], None]): preenche a aba "Busca de CPF" com (meio, nome).
        """
        super().__init__(pai, padding=(0, 12, 0, 0))
        self.tema, self.servico, self.abrir_detalhe = tema, servico, abrir_detalhe
        self.buscar_cpf = buscar_cpf
        self._nos: dict[str, No] = {}          # iid → nó
        self._visitados: set[str] = set()      # CNPJs já colocados na árvore
        self._consultas = 0
        self._max_prof = 2
        self._max_empresas = 15
        self.ativo = False

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self._montar_busca()
        self._montar_arvore()
        self._montar_rodape()
        self._tique()

    # ── Construção ─────────────────────────────────────────────────────────
    def _montar_busca(self):
        cartao = ttk.Frame(self, style="Painel.TFrame", padding=18)
        cartao.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        cartao.columnconfigure(0, weight=1)

        titulo = ttk.Frame(cartao, style="Painel.TFrame")
        titulo.grid(row=0, column=0, sticky="w")
        ttk.Label(titulo, text="CNPJ inicial", style="Secao.TLabel").pack(side=tk.LEFT)
        icone_dica(titulo, DICA_REDE, self.tema).pack(side=tk.LEFT, padx=(6, 0))

        linha = ttk.Frame(cartao, style="Painel.TFrame")
        linha.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        linha.columnconfigure(0, weight=1)
        self.entry = ttk.Entry(linha)
        self.entry.grid(row=0, column=0, sticky="ew", padx=(0, 14))
        self.entry.bind("<Return>", lambda _e: self._on_montar())

        ttk.Label(linha, text="Profundidade", style="Painel.TLabel").grid(row=0, column=1, padx=(0, 6))
        self.spin_prof = ttk.Spinbox(linha, from_=1, to=4, width=4)
        self.spin_prof.set(2)
        self.spin_prof.grid(row=0, column=2, padx=(0, 14))
        ttk.Label(linha, text="Máx. empresas", style="Painel.TLabel").grid(row=0, column=3, padx=(0, 6))
        self.spin_max = ttk.Spinbox(linha, from_=1, to=60, width=4)
        self.spin_max.set(15)
        self.spin_max.grid(row=0, column=4, padx=(0, 14))

        self.btn_montar = ttk.Button(linha, text="Montar rede", command=self._on_montar, style="Destaque.TButton")
        self.btn_montar.grid(row=0, column=5)
        self.lbl_info = ttk.Label(cartao, style="Dica.TLabel",
                                  text="Segue só sócios que são empresas. Duplo clique: empresa → detalhe; "
                                       "pessoa física → leva para a Busca de CPF.")
        self.lbl_info.grid(row=2, column=0, sticky="w", pady=(6, 0))

    def _montar_arvore(self):
        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=1, column=0, sticky="nsew")
        self.tabela = adicionar_cartao(self.notebook, "Rede", lambda p: Tabela(
            p, self.tema, COLUNAS, self._status, coluna_copia="cnpj", arvore=("Empresa / sócio", 340),
            acoes_extras=self._acoes_da_linha,
        ))
        self.tabela.tree.bind("<Double-1>", lambda _e: self._abrir_selecionado())

    def _acoes_da_linha(self, iid: str) -> list:
        """Menu de contexto: empresa → abrir detalhe; sócio pessoa física → buscar CPF na 1ª aba."""
        if meio_cpf(self.tabela.tree.set(iid, "cnpj")):
            return [("Buscar CPF deste sócio (aba Busca de CPF)", self._buscar_cpf_selecionado)]
        return [("Abrir detalhe da empresa", self._abrir_selecionado)]

    def _buscar_cpf_selecionado(self):
        sel = self.tabela.tree.selection()
        if not sel:
            return
        meio = meio_cpf(self.tabela.tree.set(sel[0], "cnpj"))
        if meio:
            self.buscar_cpf(meio, self.tabela.tree.item(sel[0], "text"))

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
        """Coloca o cursor no campo de CNPJ."""
        self.entry.focus_set()

    def _status(self, texto: str):
        self.lbl_status.configure(text=texto)

    def _set_ativo(self, ativo: bool):
        self.ativo = ativo
        estado = "disabled" if ativo else "normal"
        for w in (self.entry, self.spin_prof, self.spin_max, self.btn_montar, self.btn_limpar):
            w.configure(state=estado)
        if ativo:
            self.btn_cancelar.grid(row=1, column=1, sticky="e", pady=(6, 0))
            self.btn_csv.configure(state="disabled")
        else:
            self.btn_cancelar.grid_remove()
            self.btn_csv.configure(state="normal" if self._nos else "disabled")

    def _tique(self):
        if self.ativo:
            pendentes = self.servico.fila.pendentes(DONO)
            fila = self.servico.resumo()
            self._status(f"{self._consultas - pendentes - self._em_voo()} de {self._consultas} empresa(s) "
                         f"consultada(s)  ·  {fila or 'consultando…'}")
            self.progress.configure(maximum=max(1, self._consultas),
                                    value=self._consultas - pendentes - self._em_voo())
        self.after(1000, self._tique)

    def _em_voo(self) -> int:
        andamento = self.servico.em_andamento
        return 1 if andamento and DONO in andamento.donos() else 0

    # ── Fluxo ──────────────────────────────────────────────────────────────
    def _on_montar(self):
        if self.ativo:
            return
        digitos = limpar_cnpj(self.entry.get())
        if not cnpj_valido(digitos):
            messagebox.showerror("CNPJ inválido", "Confira os 14 dígitos do CNPJ.")
            return
        try:
            self._max_prof = max(1, min(4, int(self.spin_prof.get())))
            self._max_empresas = max(1, min(60, int(self.spin_max.get())))
        except ValueError:
            messagebox.showerror("Valor inválido", "Profundidade e máximo de empresas devem ser números.")
            return
        self.entry.delete(0, "end")
        self.entry.insert(0, formatar_cnpj(digitos))
        self._limpar_arvore()
        self._set_ativo(True)
        self._enfileirar(digitos, pai="", nivel=0, relacao="Empresa consultada")

    def _enfileirar(self, digitos: str, pai: str, nivel: int, relacao: str, nome: str = ""):
        """Cria a linha "na fila" e pede a consulta ao serviço."""
        self._visitados.add(digitos)
        self._consultas += 1
        iid = self.tabela.inserir([formatar_cnpj(digitos), relacao, "", "", "Na fila"],
                                  tags=("suave",), pai=pai, texto=nome or formatar_cnpj(digitos))
        self._nos[iid] = No(iid, digitos, nivel)
        self.servico.consultar(digitos, lambda d, emp, erro, i=iid: self._receber(i, emp, erro), dono=DONO)

    def _receber(self, iid: str, emp: Empresa | None, erro: Exception | None):
        if iid not in self._nos:  # árvore foi limpa enquanto a resposta vinha
            return
        no = self._nos[iid]
        if emp is None:
            self.tabela.atualizar(iid, tags=("erro",), status=f"Erro: {erro}")
        else:
            self.tabela.atualizar(iid, texto=emp.razao_social, situacao=emp.situacao, local=emp.cidade_uf,
                                  status="OK", tags=() if emp.ativa else ("erro",))
            self._expandir(no, emp)
        self._verificar_fim()

    def _expandir(self, no: No, emp: Empresa):
        """Lista os sócios da empresa; enfileira os que são empresas, se couber na profundidade/limite."""
        for socio in emp.socios:
            relacao = socio["qualificacao"]
            if not socio["cnpj"]:
                self.tabela.inserir([socio["documento"], relacao, "", "", "Pessoa física"],
                                    tags=("suave",), pai=no.iid, texto=socio["nome"])
                continue
            if socio["cnpj"] in self._visitados:
                self.tabela.inserir([formatar_cnpj(socio["cnpj"]), relacao, "", "", "Já listada"],
                                    tags=("suave",), pai=no.iid, texto=socio["nome"])
                continue
            if no.nivel + 1 > self._max_prof:
                motivo = "Fora da profundidade"
            elif self._consultas >= self._max_empresas:
                motivo = "Limite de empresas"
            else:
                self._enfileirar(socio["cnpj"], pai=no.iid, nivel=no.nivel + 1, relacao=relacao,
                                 nome=socio["nome"])
                continue
            self.tabela.inserir([formatar_cnpj(socio["cnpj"]), relacao, "", "", motivo],
                                tags=("suave",), pai=no.iid, texto=socio["nome"])

    def _verificar_fim(self):
        if self.servico.fila.pendentes(DONO) or self._em_voo():
            return
        self._set_ativo(False)
        self.progress.configure(value=self.progress["maximum"])
        self._status(f"Rede concluída: {self._consultas} empresa(s) consultada(s).")

    def cancelar(self):
        """Tira da fila as empresas que ainda não foram consultadas."""
        if not self.ativo:
            return
        removidos = self.servico.cancelar(DONO)
        for no in self._nos.values():
            if self.tabela.tree.set(no.iid, "status") == "Na fila":
                self.tabela.atualizar(no.iid, status="Cancelado")
        self._set_ativo(False)
        self._status(f"Cancelado: {removidos} empresa(s) não consultada(s).")

    def _limpar_arvore(self):
        self.tabela.limpar()
        self._nos.clear()
        self._visitados.clear()
        self._consultas = 0
        self.progress.configure(value=0, maximum=1)

    def _on_limpar(self):
        if self.ativo:
            return
        self._limpar_arvore()
        self.entry.delete(0, "end")
        self.btn_csv.configure(state="disabled")
        self._status("Pronto.")

    def _abrir_selecionado(self):
        sel = self.tabela.tree.selection()
        if not sel:
            return
        documento = self.tabela.tree.set(sel[0], "cnpj")
        if meio_cpf(documento):  # sócio pessoa física: leva para a busca de CPF (preenchida, sem iniciar)
            self._buscar_cpf_selecionado()
            return
        digitos = limpar_cnpj(documento)
        if len(digitos) == 14 and digitos in self.servico.cache:
            self.abrir_detalhe(digitos)
        elif len(digitos) == 14:
            self._status("Essa empresa ainda não foi consultada (está na fila ou fora do limite).")
        else:
            self._status("Sem documento utilizável nesta linha.")

    def _on_exportar(self):
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")],
                                            initialdir=pasta_resultados(), initialfile="rede_empresas.csv")
        if not path:
            return
        tree = self.tabela.tree

        def linhas(pai: str, nivel: int):
            for iid in tree.get_children(pai):
                yield [nivel, tree.item(iid, "text"), *[tree.set(iid, c[0]) for c in COLUNAS]]
                yield from linhas(iid, nivel + 1)

        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["nivel", "nome", *[c[0] for c in COLUNAS]])
            w.writerows(linhas("", 0))
        self._status(f"CSV salvo em {path}")
