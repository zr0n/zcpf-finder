"""Aba "Busca de CPF": máscara → candidatos → Portal da Transparência (+ busca profunda no Jusbrasil)."""

from __future__ import annotations

import asyncio
import csv
import queue
import re
import threading
import time
import tkinter as tk
import webbrowser
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

import aiohttp

from cpf_finder import _API_HEADERS, fmt, generate_candidates, names_match, query_term
from jusbrasil import scrape_jusbrasil_by_cpf
from caminhos import pasta_resultados
from ui.tema import Tema, icone_dica
from ui.widgets import AreaLog, Tabela, adicionar_cartao, formatar_numero

INTERVALO_FILA_MS = 100
# Acima disso a busca leva muito tempo e gera muita requisição: pedimos confirmação.
LIMITE_CONFIRMACAO = 100_000

COLUNAS = (
    ("cpf", "CPF", 140, False),
    ("nome", "Nome", 300, True),
    ("cpf_parcial", "CPF (portal)", 150, False),
    ("skPessoa", "skPessoa", 110, False),
    ("relacao", "Relação GF", 240, True),
)
COLUNAS_JUS = (
    ("nome", "Nome", 240, True),
    ("cpf_mascarado", "CPF (jusbrasil)", 140, False),
    ("idade", "Idade", 80, False),
    ("estados", "Estados", 90, False),
    ("processos", "Processos", 90, False),
    ("tribunais", "Tribunais", 200, True),
    ("anos", "Anos", 160, True),
    ("url", "URL", 220, True),
)

DICA_WORKERS = (
    "Quantos CPFs são consultados no Portal ao mesmo tempo (1 a 50).\n\n"
    "Mais requisições = busca mais rápida, porém o Portal pode limitar ou bloquear "
    "temporariamente o acesso. Se aparecerem muitos erros ou nenhum resultado, reduza o valor."
)
DICA_MATCH_FROUXO = (
    "Define o quanto o nome informado precisa bater com o nome do Portal.\n\n"
    "Desmarcado (estrito): todas as palavras do nome digitado precisam aparecer no nome do Portal "
    "(ignorando acentos e variações como Z/S, PH/F, Y/I).\n\n"
    "Marcado (frouxo): aceita também 2 palavras em comum ou nomes muito parecidos. "
    "Encontra mais pessoas, mas gera falsos positivos."
)
DICA_SSL = (
    "Desliga a verificação do certificado de segurança (HTTPS) do Portal.\n\n"
    "Use só se a busca falhar com erro de certificado, comum em redes corporativas com proxy."
)


def descrever_entrada(raw: str) -> tuple[str, int] | None:
    """Prevê, sem gerar a lista, como a entrada será interpretada.

    Espelha as regras de ``cpf_finder.generate_candidates`` só para exibir uma
    prévia barata a cada tecla (gerar a lista de verdade pode custar bilhões de itens).

    Args:
        raw (str): texto digitado pelo usuário.

    Returns:
        tuple[str, int] | None: (máscara ``XXX.XXX.XXX-XX``, estimativa de candidatos),
        ou None se a entrada estiver vazia ou inválida.
    """
    limpo = re.sub(r"[^0-9Xx]", "", raw).upper()
    if not limpo or len(limpo) > 11:
        return None
    if len(limpo) == 11 and "X" not in limpo:
        mascara = limpo
    elif "X" not in limpo and len(limpo) == 6:
        mascara = "XXX" + limpo + "XX"
    else:
        mascara = limpo.ljust(11, "X")
    # Os 2 últimos são dígitos verificadores calculados; só os X dos 9 primeiros multiplicam.
    total = 10 ** mascara[:9].count("X")
    return f"{mascara[:3]}.{mascara[3:6]}.{mascara[6:9]}-{mascara[9:]}", total


class AbaCpf(ttk.Frame):
    """Busca de CPF por máscara no Portal da Transparência."""

    def __init__(self, pai: tk.Widget, tema: Tema):
        """Monta a aba e começa a consumir a fila de eventos da busca.

        Args:
            pai (tk.Widget): notebook principal.
            tema (Tema): tema ativo.
        """
        super().__init__(pai, padding=(0, 12, 0, 0))
        self.tema = tema
        self.queue: queue.Queue = queue.Queue()
        self.confirmados: list[dict] = []
        self.divergentes: list[dict] = []
        self.searching = False
        self._cancelar = threading.Event()
        self._inicio = 0.0

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self._montar_busca()
        self._montar_resultados()
        self._montar_rodape()
        self._atualizar_previa()
        self._poll_queue()

    # ── Construção ─────────────────────────────────────────────────────────
    def _montar_busca(self):
        cartao = ttk.Frame(self, style="Painel.TFrame", padding=18)
        cartao.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        cartao.columnconfigure(0, weight=3)
        cartao.columnconfigure(1, weight=2)

        ttk.Label(cartao, text="CPF, meio ou máscara", style="Secao.TLabel").grid(row=0, column=0, sticky="w")
        self.var_cpf = tk.StringVar()
        self.var_cpf.trace_add("write", lambda *_: self._atualizar_previa())
        self.entry_cpf = ttk.Entry(cartao, textvariable=self.var_cpf)
        self.entry_cpf.grid(row=1, column=0, sticky="ew", pady=(6, 0), padx=(0, 14))
        self.lbl_previa = ttk.Label(cartao, style="Previa.TLabel")
        self.lbl_previa.grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Label(cartao, style="Dica.TLabel",
                  text="Ex.: 12345678909  ·  456.789 (meio → XXX.456.789-XX)  ·  XXX.456.789.XX",
                  ).grid(row=3, column=0, sticky="w", pady=(2, 0))

        ttk.Label(cartao, text="Nome (opcional)", style="Secao.TLabel").grid(row=0, column=1, sticky="w")
        self.entry_nome = ttk.Entry(cartao)
        self.entry_nome.grid(row=1, column=1, sticky="ew", pady=(6, 0))
        ttk.Label(cartao, style="Dica.TLabel", wraplength=360, justify=tk.LEFT,
                  text="Com nome, os registros são separados em confirmados e divergentes. "
                       "Com 1 único confirmado, é oferecida a busca profunda no Jusbrasil.",
                  ).grid(row=2, column=1, rowspan=2, sticky="nw", pady=(6, 0))

        ttk.Separator(cartao).grid(row=4, column=0, columnspan=2, sticky="ew", pady=14)
        linha = ttk.Frame(cartao, style="Painel.TFrame")
        linha.grid(row=5, column=0, columnspan=2, sticky="ew")
        linha.columnconfigure(3, weight=1)

        opt_workers = ttk.Frame(linha, style="Painel.TFrame")
        opt_workers.grid(row=0, column=0, sticky="w", padx=(0, 20))
        ttk.Label(opt_workers, text="Requisições simultâneas", style="Painel.TLabel").pack(side=tk.LEFT)
        icone_dica(opt_workers, DICA_WORKERS, self.tema).pack(side=tk.LEFT, padx=(4, 8))
        self.spin_workers = ttk.Spinbox(opt_workers, from_=1, to=50, width=5)
        self.spin_workers.set(20)
        self.spin_workers.pack(side=tk.LEFT)

        self.var_loose = tk.BooleanVar(value=False)
        self.chk_loose = self._opcao(linha, 1, "Match frouxo de nome", self.var_loose, DICA_MATCH_FROUXO)
        self.var_insecure = tk.BooleanVar(value=False)
        self.chk_insecure = self._opcao(linha, 2, "Ignorar SSL", self.var_insecure, DICA_SSL)

        self.btn_limpar = ttk.Button(linha, text="Limpar", command=self._on_clear)
        self.btn_limpar.grid(row=0, column=4, sticky="e", padx=(0, 8))
        self.btn_buscar = ttk.Button(linha, text="Buscar", command=self._on_search, style="Destaque.TButton")
        self.btn_buscar.grid(row=0, column=5, sticky="e")

        for entry in (self.entry_cpf, self.entry_nome):
            entry.bind("<Return>", lambda _e: self._on_search())
        self._controles_busca = [self.entry_cpf, self.entry_nome, self.spin_workers,
                                 self.chk_loose, self.chk_insecure, self.btn_limpar]

    def _opcao(self, pai, coluna: int, texto: str, var: tk.BooleanVar, dica: str) -> ttk.Checkbutton:
        caixa = ttk.Frame(pai, style="Painel.TFrame")
        caixa.grid(row=0, column=coluna, sticky="w", padx=(0, 20))
        chk = ttk.Checkbutton(caixa, text=texto, variable=var, style="Painel.TCheckbutton")
        chk.pack(side=tk.LEFT)
        icone_dica(caixa, dica, self.tema).pack(side=tk.LEFT, padx=(4, 0))
        return chk

    def _montar_resultados(self):
        area = ttk.Frame(self)
        area.grid(row=1, column=0, sticky="nsew")
        area.columnconfigure(0, weight=1)
        area.rowconfigure(0, weight=1)

        self.notebook = ttk.Notebook(area)
        self.notebook.grid(row=0, column=0, sticky="nsew")
        self.tab_conf = self._aba("Confirmados",
                                  lambda p: Tabela(p, self.tema, COLUNAS, self._status, coluna_copia="cpf"))
        self.tab_div = self._aba("Divergentes",
                                 lambda p: Tabela(p, self.tema, COLUNAS, self._status, coluna_copia="cpf"))
        self.tab_jus = self._aba("Jusbrasil", lambda p: Tabela(
            p, self.tema, COLUNAS_JUS, self._status, coluna_copia="url",
            acoes_extras=[("Abrir perfil no navegador", self._abrir_perfil_jusbrasil)],
        ))
        self.tab_jus.tree.bind("<Double-1>", lambda _e: self._abrir_perfil_jusbrasil())
        self.log = self._aba("Log", lambda p: AreaLog(p, self.tema))
        self._atualizar_abas()

        barra = ttk.Frame(area, padding=(0, 8, 0, 0))
        barra.grid(row=1, column=0, sticky="ew")
        ttk.Label(barra, style="Subtitulo.TLabel",
                  text="Duplo clique copia  ·  botão direito: mais opções").pack(side=tk.LEFT)
        self.btn_txt = ttk.Button(barra, text="Exportar TXT (completo)", command=self._on_export_txt,
                                  state="disabled")
        self.btn_txt.pack(side=tk.RIGHT)
        self.btn_csv = ttk.Button(barra, text="Exportar CSV (confirmados)", command=self._on_export_csv,
                                  state="disabled")
        self.btn_csv.pack(side=tk.RIGHT, padx=(0, 8))

    def _aba(self, titulo: str, criar):
        return adicionar_cartao(self.notebook, titulo, criar)

    def _montar_rodape(self):
        rodape = ttk.Frame(self, padding=(0, 10, 0, 0))
        rodape.grid(row=2, column=0, sticky="ew")
        rodape.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(rodape, orient=tk.HORIZONTAL, mode="determinate", maximum=1)
        self.progress.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.lbl_status = ttk.Label(rodape, text="Pronto.", style="Subtitulo.TLabel")
        self.lbl_status.grid(row=1, column=0, sticky="w", pady=(6, 0))
        # O botão Cancelar só aparece (grid) enquanto há uma busca em andamento.
        self.btn_cancelar = ttk.Button(rodape, text="Cancelar", command=self.cancelar, style="Perigo.TButton")

    # ── Helpers de UI ──────────────────────────────────────────────────────
    def _status(self, texto: str):
        if not self.searching:
            self.lbl_status.configure(text=texto)

    def _atualizar_previa(self):
        """Mostra abaixo do campo como a entrada será lida e quantos CPFs serão testados."""
        texto = self.var_cpf.get().strip()
        if not texto:
            self.lbl_previa.configure(text="Digite um CPF, o meio dele ou uma máscara com X.", style="Dica.TLabel")
            return
        previa = descrever_entrada(texto)
        if previa is None:
            self.lbl_previa.configure(text="Entrada inválida: use dígitos e X, no máximo 11.",
                                      style="PreviaErro.TLabel")
            return
        mascara, total = previa
        plural = "candidato" if total == 1 else "candidatos"
        estilo = "PreviaAlerta.TLabel" if total > LIMITE_CONFIRMACAO else "Previa.TLabel"
        self.lbl_previa.configure(text=f"{mascara}   ·   até {formatar_numero(total)} {plural}", style=estilo)

    def _atualizar_abas(self):
        self.notebook.tab(0, text=f"Confirmados  ({len(self.confirmados)})")
        self.notebook.tab(1, text=f"Divergentes  ({len(self.divergentes)})")

    def _set_buscando(self, ativo: bool):
        """Trava/destrava os controles e mostra/esconde o botão Cancelar."""
        self.searching = ativo
        estado = "disabled" if ativo else "normal"
        for w in self._controles_busca:
            w.configure(state=estado)
        self.btn_buscar.configure(state=estado, text="Buscando…" if ativo else "Buscar")
        if ativo:
            self.btn_csv.configure(state="disabled")
            self.btn_txt.configure(state="disabled")
            self.btn_cancelar.configure(state="normal", text="Cancelar")
            self.btn_cancelar.grid(row=1, column=1, sticky="e", pady=(6, 0))
        else:
            self.btn_cancelar.grid_remove()
            self.btn_csv.configure(state="normal" if self.confirmados else "disabled")
            self.btn_txt.configure(state="normal" if (self.confirmados or self.divergentes) else "disabled")

    def _limpar_resultados(self):
        for tabela in (self.tab_conf, self.tab_div, self.tab_jus):
            tabela.limpar()
        self.confirmados.clear()
        self.divergentes.clear()
        self._atualizar_abas()
        self.log.limpar()
        self.progress.configure(value=0, maximum=1)
        self.btn_csv.configure(state="disabled")
        self.btn_txt.configure(state="disabled")

    def _on_clear(self):
        if self.searching:
            return
        self.var_cpf.set("")
        self.entry_nome.delete(0, "end")
        self._limpar_resultados()
        self.lbl_status.configure(text="Pronto.")
        self.entry_cpf.focus_set()

    def focar(self):
        """Coloca o cursor no campo principal (chamado ao entrar na aba)."""
        self.entry_cpf.focus_set()

    def preencher(self, meio: str, nome: str) -> bool:
        """Preenche a busca com o meio do CPF e o nome (vindo de um sócio). NÃO inicia a busca.

        A busca continua sendo uma ação explícita do usuário (botão Buscar / Enter).

        Args:
            meio (str): 6 dígitos visíveis do CPF mascarado.
            nome (str): nome do sócio, usado para separar confirmados e divergentes.

        Returns:
            bool: False se já houver uma busca em andamento (nada é alterado).
        """
        if self.searching:
            self.lbl_status.configure(text="Há uma busca em andamento; aguarde ou cancele antes.")
            return False
        self.var_cpf.set(f"{meio[:3]}.{meio[3:]}")
        self.entry_nome.delete(0, "end")
        self.entry_nome.insert(0, nome)
        self.lbl_status.configure(text="Campos preenchidos a partir do sócio. Confira e clique em Buscar.")
        self.btn_buscar.focus_set()
        return True

    # ── Busca ──────────────────────────────────────────────────────────────
    def _on_search(self):
        if self.searching:
            return
        entrada = self.var_cpf.get().strip()
        if not entrada:
            messagebox.showerror("Erro", "Informe um CPF ou máscara.")
            self.entry_cpf.focus_set()
            return

        previa = descrever_entrada(entrada)
        if previa and previa[1] > LIMITE_CONFIRMACAO and not messagebox.askyesno(
            "Busca grande",
            f"A máscara {previa[0]} gera até {formatar_numero(previa[1])} candidatos.\n"
            "Isso pode levar muito tempo e consumir muita memória.\n\nDeseja continuar?",
        ):
            return

        try:
            candidates = generate_candidates(entrada)
        except ValueError as e:
            messagebox.showerror("Erro", str(e))
            return
        try:
            workers = max(1, min(50, int(self.spin_workers.get())))
        except ValueError:
            messagebox.showerror("Erro", "Requisições simultâneas deve ser um número entre 1 e 50.")
            return

        self._limpar_resultados()
        self._cancelar.clear()
        self._inicio = time.monotonic()
        self._set_buscando(True)
        self.progress.configure(maximum=max(1, len(candidates)), value=0)
        self.lbl_status.configure(text=f"0 / {formatar_numero(len(candidates))}")
        self.log.escrever(f"Iniciando: {entrada} → {formatar_numero(len(candidates))} candidato(s)")

        nome = self.entry_nome.get().strip() or None
        threading.Thread(
            target=self._run_search,
            args=(candidates, nome, workers, self.var_loose.get(), self.var_insecure.get()),
            daemon=True,
        ).start()

    def cancelar(self):
        """Pede o cancelamento da busca em andamento (botão Cancelar ou Esc)."""
        if not self.searching or self._cancelar.is_set():
            return
        self._cancelar.set()
        self.btn_cancelar.configure(state="disabled", text="Cancelando…")
        self.log.escrever("Cancelamento solicitado; aguardando requisições em andamento…", "aviso")

    def _run_search(self, candidates, nome, workers, loose, insecure):
        def registro(cpf_fmt: str, r: dict) -> dict:
            return {"cpf": cpf_fmt, "nome": r["nome"], "cpf_parcial": r["cpf_parcial"],
                    "skPessoa": r["skPessoa"], "relacao": r["relacao"]}

        async def _run():
            sem = asyncio.Semaphore(workers)
            conn = aiohttp.TCPConnector(limit=workers * 2, ssl=not insecure)
            async with aiohttp.ClientSession(headers=_API_HEADERS, connector=conn) as session:
                async def process(cpf: str):
                    # Cancelado: não dispara novas consultas, só drena as tarefas restantes.
                    if self._cancelar.is_set():
                        return
                    formatted = fmt(cpf)
                    try:
                        _tot, recs = await query_term(session, cpf, 1, 10, sem)
                    except Exception:
                        recs = []

                    if recs:
                        if nome:
                            matches = [r for r in recs if names_match(nome, r["nome"], loose=loose)]
                            if matches:
                                self.queue.put(("found", registro(formatted, matches[0])))
                            else:
                                for r in recs:
                                    self.queue.put(("divergent", registro(formatted, r)))
                        else:
                            self.queue.put(("found", registro(formatted, recs[0])))

                    self.queue.put(("progress",))

                await asyncio.gather(*(process(c) for c in candidates))

        try:
            asyncio.run(_run())
            self.queue.put(("cancelled",) if self._cancelar.is_set() else ("done",))
        except Exception as e:
            self.queue.put(("error", str(e)))

    # ── Jusbrasil ──────────────────────────────────────────────────────────
    def _oferecer_busca_profunda(self):
        """Se houver exatamente 1 confirmado, mostra e pergunta se quer buscar no Jusbrasil."""
        if len(self.confirmados) != 1:
            return
        r = self.confirmados[0]
        msg = (f"CPF: {r['cpf']}\nNome: {r['nome']}\nCPF (portal): {r['cpf_parcial']}\n"
               f"skPessoa: {r['skPessoa']}\nRelação GF: {r['relacao'] or '—'}\n\n"
               "Deseja fazer busca profunda no Jusbrasil?")
        if messagebox.askyesno("Resultado único — busca profunda", msg):
            self.log.escrever(f"→ Busca profunda: Jusbrasil para {r['cpf']}…", "info")
            self.lbl_status.configure(text="Consultando Jusbrasil…")
            self.notebook.select(2)
            threading.Thread(target=self._run_jusbrasil, args=(r["cpf"],), daemon=True).start()

    def _run_jusbrasil(self, cpf: str):
        try:
            result = asyncio.run(scrape_jusbrasil_by_cpf(cpf))
        except Exception as e:
            result = {"profiles": [], "error": str(e), "url": ""}
        self.queue.put(("jusbrasil_done", result))

    def _mostrar_jusbrasil(self, result: dict):
        self.tab_jus.limpar()
        self.notebook.select(2)
        if result.get("error"):
            self.log.escrever(f"⚠ Jusbrasil: {result['error']}", "aviso")
            self.lbl_status.configure(text=f"Jusbrasil: {result['error']}")
            return
        profiles = result.get("profiles", [])
        if not profiles:
            self.log.escrever("→ Jusbrasil: nenhum perfil público encontrado para este CPF.", "info")
            self.lbl_status.configure(text="Jusbrasil: sem resultados")
            return
        for p in profiles:
            self.tab_jus.inserir([p[c[0]] for c in COLUNAS_JUS])
        self.log.escrever(f"✓ Jusbrasil: {len(profiles)} perfil(is) encontrado(s).", "ok")
        self.lbl_status.configure(text=f"Jusbrasil: {len(profiles)} perfil(is) — duplo clique abre no navegador")

    def _abrir_perfil_jusbrasil(self):
        urls = self.tab_jus.selecionados("url")
        if urls and urls[0]:
            webbrowser.open(urls[0])

    # ── Fila de eventos (thread de busca → UI) ────────────────────────────
    def _poll_queue(self):
        progresso = 0
        try:
            while True:
                msg = self.queue.get_nowait()
                kind = msg[0]

                if kind == "found":
                    entry = msg[1]
                    self.confirmados.append(entry)
                    self.tab_conf.inserir([entry[c[0]] for c in COLUNAS])
                    self._atualizar_abas()
                    self.log.escrever(f"✓ {entry['cpf']} — {entry['nome']}", "ok")

                elif kind == "divergent":
                    entry = msg[1]
                    self.divergentes.append(entry)
                    self.tab_div.inserir([entry[c[0]] for c in COLUNAS])
                    self._atualizar_abas()
                    self.log.escrever(f"⚠ {entry['cpf']} — {entry['nome']} (nome diverge)", "aviso")

                elif kind == "progress":
                    progresso += 1

                elif kind in ("done", "cancelled"):
                    self._aplicar_progresso(progresso)
                    progresso = 0
                    self._set_buscando(False)
                    resumo = (f"{len(self.confirmados)} confirmado(s), {len(self.divergentes)} divergente(s) "
                              f"em {self._decorrido()}")
                    if kind == "done":
                        self.lbl_status.configure(text=f"Concluído: {resumo}.")
                        self.log.escrever(f"Fim: {resumo}", "ok")
                        self.after(200, self._oferecer_busca_profunda)
                    else:
                        self.lbl_status.configure(text=f"Cancelado: {resumo}.")
                        self.log.escrever(f"Cancelado: {resumo}", "aviso")
                    if self.confirmados:
                        self.notebook.select(0)
                    elif self.divergentes:
                        self.notebook.select(1)

                elif kind == "jusbrasil_done":
                    self._mostrar_jusbrasil(msg[1])

                elif kind == "error":
                    self._set_buscando(False)
                    self.lbl_status.configure(text="Erro na busca.")
                    self.log.escrever(f"Erro: {msg[1]}", "erro")
                    messagebox.showerror("Erro", msg[1])

        except queue.Empty:
            pass
        # Progresso agregado por ciclo: evita redesenhar a barra milhares de vezes por segundo.
        self._aplicar_progresso(progresso)
        self.after(INTERVALO_FILA_MS, self._poll_queue)

    def _aplicar_progresso(self, incremento: int):
        if not incremento:
            return
        self.progress["value"] += incremento
        atual, total = int(self.progress["value"]), int(self.progress["maximum"])
        pct = atual * 100 // total if total else 0
        self.lbl_status.configure(
            text=f"{formatar_numero(atual)} / {formatar_numero(total)}  ({pct}%)  ·  {self._decorrido()}"
        )

    def _decorrido(self) -> str:
        seg = int(time.monotonic() - self._inicio)
        return f"{seg // 60}min {seg % 60:02d}s" if seg >= 60 else f"{seg}s"

    # ── Exportação ─────────────────────────────────────────────────────────
    def _on_export_csv(self):
        if not self.confirmados:
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")],
                                            initialdir=pasta_resultados(), initialfile="confirmados.csv")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[c[0] for c in COLUNAS])
            writer.writeheader()
            writer.writerows(self.confirmados)
        messagebox.showinfo("Exportado", f"CSV salvo em:\n{path}")

    def _on_export_txt(self):
        if not self.confirmados and not self.divergentes:
            return
        path = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("TXT", "*.txt")],
                                            initialdir=pasta_resultados(), initialfile="resultado.txt")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write("cpf-finder — Portal da Transparência\n")
            f.write(f"Data: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n")
            f.write("=" * 55 + "\n\n")
            for titulo, registros in (("✅ CONFIRMADOS", self.confirmados), ("⚠️ DIVERGENTES", self.divergentes)):
                if not registros:
                    continue
                f.write(f"### {titulo} ({len(registros)})\n\n")
                for r in registros:
                    f.write(f"CPF: {r['cpf']}\n")
                    f.write(f"  Nome        : {r['nome']}\n")
                    f.write(f"  CPF (portal): {r['cpf_parcial']}\n")
                    f.write(f"  skPessoa    : {r['skPessoa']}\n")
                    if r["relacao"]:
                        f.write(f"  Relação GF  : {r['relacao']}\n")
                    f.write("\n")
        messagebox.showinfo("Exportado", f"TXT salvo em:\n{path}")
