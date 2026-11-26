"""Aba "Consulta CNPJ": dados cadastrais de empresa pela API pública do CNPJ.ws."""

from __future__ import annotations

import json
import tkinter as tk
import webbrowser
from collections.abc import Callable
from tkinter import filedialog, messagebox, ttk

from cnpj_ws import (
    LIMITE_POR_MINUTO,
    CnpjInvalido,
    CnpjNaoEncontrado,
    Empresa,
    cnpj_valido,
    formatar_cnpj,
    limpar_cnpj,
    meio_cpf,
)
from jusbrasil import url_busca
from caminhos import pasta_resultados
from ui.servico_cnpj import ServicoCnpj
from ui.tema import Tema, icone_dica
from ui.widgets import Tabela, adicionar_cartao

DONO = "consulta"

DICA_API = (
    "Os dados vêm da API pública do CNPJ.ws (base da Receita Federal).\n\n"
    f"Limite: {LIMITE_POR_MINUTO} consultas por minuto. Quando o limite acaba, o botão mostra "
    "quanto tempo falta.\n\n"
    "CNPJs já consultados nesta sessão ficam guardados e não gastam consulta de novo."
)

COLUNAS_SOCIOS = (
    ("nome", "Nome", 280, True),
    ("qualificacao", "Qualificação", 200, True),
    ("tipo", "Tipo", 120, False),
    ("documento", "CPF/CNPJ", 130, False),
    ("entrada", "Entrada", 100, False),
    ("faixa_etaria", "Faixa etária", 120, False),
)
COLUNAS_ATIVIDADES = (
    ("tipo", "Tipo", 100, False),
    ("codigo", "CNAE", 110, False),
    ("descricao", "Descrição", 600, True),
)
COLUNAS_INSCRICOES = (
    ("inscricao", "Inscrição estadual", 180, False),
    ("uf", "UF", 60, False),
    ("ativa", "Ativa", 80, False),
    ("atualizado", "Atualizado em", 130, True),
)


def resumo_texto(emp: Empresa) -> list[tuple[str, list[tuple[str, str]]]]:
    """Organiza os dados gerais em seções (título → pares rótulo/valor).

    Usado tanto para desenhar a aba "Dados gerais" quanto para o "Copiar resumo",
    garantindo que os dois mostrem exatamente a mesma coisa.

    Args:
        emp (Empresa): empresa consultada.

    Returns:
        list[tuple[str, list[tuple[str, str]]]]: seções na ordem de exibição.
    """
    return [
        ("Identificação", [
            ("CNPJ", f"{emp.cnpj}  ({emp.tipo})"),
            ("Razão social", emp.razao_social),
            ("Nome fantasia", emp.nome_fantasia),
            ("Natureza jurídica", emp.natureza_juridica),
            ("Porte", emp.porte),
            ("Capital social", emp.capital_social),
            ("Início das atividades", emp.abertura),
        ]),
        ("Situação cadastral", [
            ("Situação", f"{emp.situacao} desde {emp.data_situacao}"),
            ("Motivo", emp.motivo_situacao),
            ("Simples Nacional", emp.simples),
            ("MEI", emp.mei),
        ]),
        ("Endereço e contato", [
            ("Endereço", emp.endereco),
            ("Cidade / UF", emp.cidade_uf),
            ("CEP", emp.cep),
            ("Telefones", "  ·  ".join(emp.telefones) or "—"),
            ("E-mail", emp.email),
        ]),
        ("Atividade principal", [
            ("CNAE", emp.atividade_principal),
        ]),
        ("Base", [
            ("Atualizado em", emp.atualizado_em),
        ]),
    ]


class AbaCnpj(ttk.Frame):
    """Consulta de CNPJ com resumo, sócios, atividades, inscrições estaduais e JSON bruto."""

    def __init__(self, pai: tk.Widget, tema: Tema, servico: ServicoCnpj,
                 buscar_cpf: Callable[[str, str], None] = lambda _m, _n: None):
        """Monta a aba.

        Args:
            pai (tk.Widget): notebook principal.
            tema (Tema): tema ativo.
            servico (ServicoCnpj): fila/cache/limite compartilhados com as outras abas de CNPJ.
            buscar_cpf (Callable[[str, str], None]): preenche a aba "Busca de CPF" com (meio, nome).
        """
        super().__init__(pai, padding=(0, 12, 0, 0))
        self.tema = tema
        self.servico = servico
        self.buscar_cpf = buscar_cpf
        self.empresa: Empresa | None = None
        self.consultando = False
        self._do_cache = False

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self._montar_busca()
        self._montar_resultado()
        self._montar_rodape()
        self._atualizar_previa()
        self._mostrar_vazio()
        self._tique()

    # ── Construção ─────────────────────────────────────────────────────────
    def _montar_busca(self):
        cartao = ttk.Frame(self, style="Painel.TFrame", padding=18)
        cartao.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        cartao.columnconfigure(0, weight=1)

        titulo = ttk.Frame(cartao, style="Painel.TFrame")
        titulo.grid(row=0, column=0, sticky="w")
        ttk.Label(titulo, text="CNPJ", style="Secao.TLabel").pack(side=tk.LEFT)
        icone_dica(titulo, DICA_API, self.tema).pack(side=tk.LEFT, padx=(6, 0))

        linha = ttk.Frame(cartao, style="Painel.TFrame")
        linha.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        linha.columnconfigure(0, weight=1)
        self.var_cnpj = tk.StringVar()
        self.var_cnpj.trace_add("write", lambda *_: self._atualizar_previa())
        self.entry_cnpj = ttk.Entry(linha, textvariable=self.var_cnpj)
        self.entry_cnpj.grid(row=0, column=0, sticky="ew", padx=(0, 14))
        self.entry_cnpj.bind("<Return>", lambda _e: self._on_consultar())
        self.btn_limpar = ttk.Button(linha, text="Limpar", command=self._on_limpar)
        self.btn_limpar.grid(row=0, column=1, padx=(0, 8))
        self.btn_consultar = ttk.Button(linha, text="Consultar", command=self._on_consultar,
                                        style="Destaque.TButton")
        self.btn_consultar.grid(row=0, column=2)

        info = ttk.Frame(cartao, style="Painel.TFrame")
        info.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        info.columnconfigure(0, weight=1)
        self.lbl_previa = ttk.Label(info, style="Previa.TLabel")
        self.lbl_previa.grid(row=0, column=0, sticky="w")
        self.lbl_limite = ttk.Label(info, style="Dica.TLabel")
        self.lbl_limite.grid(row=0, column=1, sticky="e")
        ttk.Label(info, style="Dica.TLabel", text="Ex.: 27.865.757/0001-02 ou 27865757000102",
                  ).grid(row=1, column=0, sticky="w", pady=(2, 0))

    def _montar_resultado(self):
        area = ttk.Frame(self)
        area.grid(row=1, column=0, sticky="nsew")
        area.columnconfigure(0, weight=1)
        area.rowconfigure(1, weight=1)

        # Cabeçalho da empresa: razão social em destaque + selo da situação.
        cab = ttk.Frame(area, style="Painel.TFrame", padding=(18, 14))
        cab.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        cab.columnconfigure(0, weight=1)
        self.lbl_razao = ttk.Label(cab, style="Destaque.TLabel")
        self.lbl_razao.grid(row=0, column=0, sticky="w")
        self.lbl_selo = ttk.Label(cab, style="SeloOk.TLabel")
        self.lbl_selo.grid(row=0, column=1, rowspan=2, sticky="e")
        self.lbl_sub = ttk.Label(cab, style="Dica.TLabel")
        self.lbl_sub.grid(row=1, column=0, sticky="w", pady=(2, 0))

        self.notebook = ttk.Notebook(area)
        self.notebook.grid(row=1, column=0, sticky="nsew")
        self.txt_resumo = adicionar_cartao(self.notebook, "Dados gerais", self._criar_texto_resumo)
        self.tab_socios = adicionar_cartao(self.notebook, "Sócios", lambda p: Tabela(
            p, self.tema, COLUNAS_SOCIOS, self._status, coluna_copia="nome", acoes_extras=self._acoes_socio))
        self.tab_socios.tree.bind("<Double-1>", lambda _e: self._abrir_socio())
        self.tab_atividades = adicionar_cartao(self.notebook, "Atividades", lambda p: Tabela(
            p, self.tema, COLUNAS_ATIVIDADES, self._status, coluna_copia="codigo"))
        self.tab_inscricoes = adicionar_cartao(self.notebook, "Inscrições estaduais", lambda p: Tabela(
            p, self.tema, COLUNAS_INSCRICOES, self._status, coluna_copia="inscricao"))
        self.txt_json = adicionar_cartao(self.notebook, "JSON", self._criar_texto_json)
        self.tema.ao_mudar(self._cores_tabelas)

    def _acoes_socio(self, iid: str) -> list:
        """Menu do sócio: pessoa física → busca de CPF; pessoa jurídica → consultar a empresa."""
        documento = self.tab_socios.tree.set(iid, "documento")
        if meio_cpf(documento):
            return [("Buscar CPF deste sócio (aba Busca de CPF)", self._abrir_socio)]
        if len(limpar_cnpj(documento)) in (8, 14):
            return [("Consultar esta empresa", self._abrir_socio)]
        return []

    def _abrir_socio(self):
        """Duplo clique no sócio: PF → preenche a busca de CPF (sem iniciar); PJ → consulta o CNPJ dele."""
        sel = self.tab_socios.tree.selection()
        if not sel or not self.empresa:
            return
        nome = self.tab_socios.tree.set(sel[0], "nome")
        documento = self.tab_socios.tree.set(sel[0], "documento")
        meio = meio_cpf(documento)
        if meio:
            self.buscar_cpf(meio, nome)
            return
        socio = next((s for s in self.empresa.socios if s["nome"] == nome and s["cnpj"]), None)
        if socio:
            self.mostrar(socio["cnpj"])
        else:
            self._status("Este sócio não tem CPF mascarado nem CNPJ utilizável.")

    def _criar_texto_resumo(self, pai) -> ttk.Frame:
        moldura = ttk.Frame(pai, style="Painel.TFrame")
        moldura.columnconfigure(0, weight=1)
        moldura.rowconfigure(0, weight=1)
        texto = tk.Text(moldura, wrap=tk.WORD, relief=tk.FLAT, highlightthickness=0, padx=6, pady=4,
                        font="TkDefaultFont",  # o padrão do tk.Text é monoespaçado
                        cursor="arrow", state="disabled", tabs=("190",), spacing1=2, spacing3=2)
        rolagem = ttk.Scrollbar(moldura, orient="vertical", command=texto.yview)
        texto.configure(yscrollcommand=rolagem.set)
        texto.grid(row=0, column=0, sticky="nsew")
        rolagem.grid(row=0, column=1, sticky="ns")

        def cores(c: dict):
            texto.configure(bg=c["painel"], fg=c["texto"], selectbackground=c["selecao"],
                            selectforeground=c["texto"], inactiveselectbackground=c["selecao"])
            texto.tag_configure("secao", foreground=c["destaque"], font=self.tema.fonte_secao,
                                spacing1=12, spacing3=4)
            texto.tag_configure("rotulo", foreground=c["suave"])
            texto.tag_configure("valor", foreground=c["texto"])
            texto.tag_configure("vazio", foreground=c["suave"])
            texto.tag_configure("ok", foreground=c["sucesso"], font=self.tema.fonte_negrito)
            texto.tag_configure("erro", foreground=c["perigo"], font=self.tema.fonte_negrito)
        self.tema.ao_mudar(cores)
        moldura.texto = texto
        return moldura

    def _criar_texto_json(self, pai) -> ttk.Frame:
        moldura = ttk.Frame(pai, style="Painel.TFrame")
        moldura.columnconfigure(0, weight=1)
        moldura.rowconfigure(0, weight=1)
        texto = tk.Text(moldura, wrap=tk.NONE, relief=tk.FLAT, font=self.tema.fonte_mono,
                        highlightthickness=1, padx=8, pady=6, state="disabled")
        rol_v = ttk.Scrollbar(moldura, orient="vertical", command=texto.yview)
        rol_h = ttk.Scrollbar(moldura, orient="horizontal", command=texto.xview)
        texto.configure(yscrollcommand=rol_v.set, xscrollcommand=rol_h.set)
        texto.grid(row=0, column=0, sticky="nsew")
        rol_v.grid(row=0, column=1, sticky="ns")
        rol_h.grid(row=1, column=0, sticky="ew")
        self.tema.estilizar_texto(texto)
        moldura.texto = texto
        return moldura

    def _cores_tabelas(self, c: dict):
        self.tab_atividades.tree.tag_configure("principal", foreground=c["destaque"])
        self.tab_inscricoes.tree.tag_configure("inativa", foreground=c["suave"])

    def _montar_rodape(self):
        rodape = ttk.Frame(self, padding=(0, 10, 0, 0))
        rodape.grid(row=2, column=0, sticky="ew")
        rodape.columnconfigure(0, weight=1)
        self.lbl_status = ttk.Label(rodape, text="Pronto.", style="Subtitulo.TLabel")
        self.lbl_status.grid(row=0, column=0, sticky="w")
        self.btn_jusbrasil = ttk.Button(rodape, text="Processos no Jusbrasil ↗", command=self._on_abrir_jusbrasil,
                                        state="disabled")
        self.btn_jusbrasil.grid(row=0, column=1, sticky="e", padx=(0, 8))
        self.btn_copiar = ttk.Button(rodape, text="Copiar resumo", command=self._on_copiar_resumo,
                                     state="disabled")
        self.btn_copiar.grid(row=0, column=2, sticky="e", padx=(0, 8))
        self.btn_json = ttk.Button(rodape, text="Salvar JSON", command=self._on_salvar_json, state="disabled")
        self.btn_json.grid(row=0, column=3, sticky="e")

    # ── Estado da tela ─────────────────────────────────────────────────────
    def focar(self):
        """Coloca o cursor no campo de CNPJ (chamado ao entrar na aba)."""
        self.entry_cnpj.focus_set()

    def _status(self, texto: str):
        self.lbl_status.configure(text=texto)

    def _atualizar_previa(self):
        """Formata e valida o CNPJ enquanto o usuário digita."""
        digitos = limpar_cnpj(self.var_cnpj.get())
        if not digitos:
            self.lbl_previa.configure(text="Digite os 14 dígitos do CNPJ.", style="Dica.TLabel")
        elif len(digitos) < 14:
            self.lbl_previa.configure(text=f"{len(digitos)}/14 dígitos", style="Dica.TLabel")
        elif len(digitos) > 14:
            self.lbl_previa.configure(text="CNPJ tem 14 dígitos — há dígitos a mais.", style="PreviaErro.TLabel")
        elif not cnpj_valido(digitos):
            self.lbl_previa.configure(text=f"{formatar_cnpj(digitos)}  ·  dígito verificador inválido",
                                      style="PreviaErro.TLabel")
        else:
            extra = "  ·  já consultado (sem gastar consulta)" if digitos in self.servico.cache else ""
            self.lbl_previa.configure(text=f"{formatar_cnpj(digitos)}  ·  válido{extra}", style="PreviaOk.TLabel")

    def _tique(self):
        """Atualiza o contador de consultas e a contagem regressiva a cada segundo."""
        limitador = self.servico.limitador
        espera = limitador.espera()
        fila = self.servico.resumo()
        if espera:
            texto = f"Limite atingido — próxima consulta em {espera}s"
        else:
            texto = f"Consultas disponíveis: {limitador.restantes()}/{LIMITE_POR_MINUTO} por minuto"
        self.lbl_limite.configure(text=f"{fila}  ·  {texto}" if fila else texto,
                                  style="PreviaAlerta.TLabel" if espera else "Dica.TLabel")
        if self.consultando and espera:
            self.btn_consultar.configure(text=f"Na fila ({espera}s)")
        elif self.consultando:
            self.btn_consultar.configure(text="Consultando…")
        self.after(1000, self._tique)

    def _set_consultando(self, ativo: bool):
        self.consultando = ativo
        estado = "disabled" if ativo else "normal"
        self.entry_cnpj.configure(state=estado)
        # "Limpar" continua ativo durante a espera: serve para desistir da consulta na fila.
        self.btn_consultar.configure(state=estado, text="Consultando…" if ativo else "Consultar")

    def _mostrar_vazio(self):
        self.lbl_razao.configure(text="Nenhuma empresa consultada")
        self.lbl_sub.configure(text="Informe um CNPJ acima e clique em Consultar.")
        self.lbl_selo.grid_remove()
        self._escrever(self.txt_resumo.texto, [("Os dados da empresa aparecerão aqui.", "vazio")])
        self._escrever(self.txt_json.texto, [])
        for tabela in (self.tab_socios, self.tab_atividades, self.tab_inscricoes):
            tabela.limpar()
        self._atualizar_titulos_abas()
        for botao in (self.btn_jusbrasil, self.btn_copiar, self.btn_json):
            botao.configure(state="disabled")

    def _atualizar_titulos_abas(self):
        self.notebook.tab(1, text=f"Sócios  ({len(self.tab_socios)})")
        self.notebook.tab(2, text=f"Atividades  ({len(self.tab_atividades)})")
        self.notebook.tab(3, text=f"Inscrições estaduais  ({len(self.tab_inscricoes)})")

    @staticmethod
    def _escrever(texto: tk.Text, trechos: list[tuple[str, str | tuple]]):
        texto.configure(state="normal")
        texto.delete("1.0", "end")
        for conteudo, tag in trechos:
            texto.insert("end", conteudo, tag)
        texto.configure(state="disabled")

    def _mostrar_empresa(self, emp: Empresa, do_cache: bool):
        self.empresa = emp
        self.lbl_razao.configure(text=emp.razao_social)
        fantasia = f"{emp.nome_fantasia}  ·  " if emp.nome_fantasia != "—" else ""
        self.lbl_sub.configure(text=f"{fantasia}{emp.cnpj}  ·  {emp.tipo}  ·  {emp.cidade_uf}")
        situacao = emp.situacao.lower()
        estilo = "SeloOk.TLabel" if emp.ativa else (
            "SeloAlerta.TLabel" if situacao in ("suspensa", "inapta") else "SeloErro.TLabel")
        self.lbl_selo.configure(text=emp.situacao.upper(), style=estilo)
        self.lbl_selo.grid()

        trechos: list[tuple[str, str | tuple]] = []
        for i, (secao, campos) in enumerate(resumo_texto(emp)):
            trechos.append((("\n" if i else "") + secao + "\n", "secao"))
            for rotulo, valor in campos:
                tag_valor = "vazio" if valor in ("—", "") else "valor"
                if rotulo == "Situação":
                    tag_valor = "ok" if emp.ativa else "erro"
                trechos.append((f"{rotulo}\t", "rotulo"))
                trechos.append((f"{valor}\n", tag_valor))
        self._escrever(self.txt_resumo.texto, trechos)
        self._escrever(self.txt_json.texto, [(json.dumps(emp.bruto, ensure_ascii=False, indent=2), ())])

        for tabela in (self.tab_socios, self.tab_atividades, self.tab_inscricoes):
            tabela.limpar()
        for s in emp.socios:
            self.tab_socios.inserir([s[c[0]] for c in COLUNAS_SOCIOS])
        for a in emp.atividades:
            self.tab_atividades.inserir([a[c[0]] for c in COLUNAS_ATIVIDADES],
                                        tags=("principal",) if a["tipo"] == "Principal" else ())
        for ie in emp.inscricoes:
            self.tab_inscricoes.inserir([ie[c[0]] for c in COLUNAS_INSCRICOES],
                                        tags=() if ie["ativa"] == "Sim" else ("inativa",))
        self._atualizar_titulos_abas()
        self.notebook.select(0)
        for botao in (self.btn_jusbrasil, self.btn_copiar, self.btn_json):
            botao.configure(state="normal")
        origem = "do cache (não gastou consulta)" if do_cache else "da API"
        self._status(f"Consulta concluída — dados {origem}. Atualizados na base em {emp.atualizado_em}.")

    # ── Ações ──────────────────────────────────────────────────────────────
    def _on_consultar(self):
        if self.consultando:
            return
        digitos = limpar_cnpj(self.var_cnpj.get())
        if not cnpj_valido(digitos):
            messagebox.showerror("CNPJ inválido", "Confira os 14 dígitos do CNPJ (o dígito verificador não bate).")
            self.entry_cnpj.focus_set()
            return
        self.mostrar(digitos)

    def mostrar(self, digitos: str):
        """Consulta (ou pega do cache) e exibe um CNPJ. Usado também pelas abas de rede e lote.

        Args:
            digitos (str): CNPJ só com dígitos, já validado.
        """
        self.var_cnpj.set(formatar_cnpj(digitos))
        self._do_cache = digitos in self.servico.cache
        if not self._do_cache:
            self._set_consultando(True)
            espera = self.servico.limitador.espera()
            self._status(f"Na fila — limite de {LIMITE_POR_MINUTO}/min atingido, sai em {espera}s." if espera
                         else f"Consultando {formatar_cnpj(digitos)}…")
        # Prioridade: a consulta manual fura a fila da rede/lote.
        self.servico.consultar(digitos, self._receber, dono=DONO, prioridade=True)

    def _receber(self, digitos: str, empresa: Empresa | None, erro: Exception | None):
        if limpar_cnpj(self.var_cnpj.get()) != digitos and not self.consultando:
            return  # o usuário já mudou de CNPJ; resposta antiga
        self._set_consultando(False)
        if empresa is not None:
            self._mostrar_empresa(empresa, do_cache=self._do_cache)
            self._atualizar_previa()
            return
        titulo = ("CNPJ não encontrado" if isinstance(erro, CnpjNaoEncontrado)
                  else "CNPJ inválido" if isinstance(erro, CnpjInvalido) else "Erro na consulta")
        self._status(f"{titulo}: {erro}")
        messagebox.showerror(titulo, str(erro))

    def _on_limpar(self):
        if self.consultando:
            self.servico.cancelar(DONO)
            self._set_consultando(False)
        self.var_cnpj.set("")
        self.empresa = None
        self._mostrar_vazio()
        self._status("Pronto.")
        self.entry_cnpj.focus_set()

    def _on_abrir_jusbrasil(self):
        """Abre a busca de processos do CNPJ no navegador (o Jusbrasil bloqueia acesso por script)."""
        if not self.empresa:
            return
        webbrowser.open(url_busca(self.empresa.cnpj))
        self._status("Busca de processos aberta no navegador.")

    def _on_copiar_resumo(self):
        if not self.empresa:
            return
        linhas = [self.empresa.razao_social, ""]
        for secao, campos in resumo_texto(self.empresa):
            linhas.append(f"## {secao}")
            linhas.extend(f"{rotulo}: {valor}" for rotulo, valor in campos)
            linhas.append("")
        if self.empresa.socios:
            linhas.append("## Sócios")
            linhas.extend(f"{s['nome']} — {s['qualificacao']} (desde {s['entrada']})" for s in self.empresa.socios)
        self.clipboard_clear()
        self.clipboard_append("\n".join(linhas).strip())
        self._status("Resumo copiado para a área de transferência.")

    def _on_salvar_json(self):
        if not self.empresa:
            return
        nome = f"cnpj_{limpar_cnpj(self.empresa.cnpj)}.json"
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")],
                                            initialdir=pasta_resultados(), initialfile=nome)
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.empresa.bruto, f, ensure_ascii=False, indent=2)
        self._status(f"JSON salvo em {path}")
