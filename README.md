# zCpf-finder

Busca CPFs no Portal da Transparência a partir de uma **máscara parcial**. Você informa os dígitos que já conhece (o meio do CPF, o CPF completo, ou uma máscara explícita com `X`) e o programa gera todos os candidatos válidos, calcula os dígitos verificadores e consulta o Portal automaticamente — mostrando **todos** os registros encontrados, separados em **confirmados** e **divergentes**.

## Como funciona

O CPF tem 11 dígitos: os 9 primeiros são o número base e os 2 últimos são verificadores matemáticos. A máscara usa `X` nos dígitos desconhecidos dos 9 primeiros — os dois últimos (`-XX`) são sempre calculados pelo programa.

O portal **mascara o CPF** nos resultados no formato `***.123.456-**`, mantendo visíveis apenas os dígitos do **meio**. É justamente por isso que a busca por máscara faz sentido: se você conhece o trecho do meio, o programa descobre o resto.

Exemplos de entrada aceitos:

| Entrada | Interpretação | Candidatos |
|---------|---------------|-----------:|
| `12345678909` | CPF completo | 1 |
| `123.456.789-09` | CPF completo (pontuação ignorada) | 1 |
| `456789` ou `456.789` | **Meio** do CPF → `XXX.456.789-XX` | 1.000 |
| `XXX.456.789.XX` | Máscara explícita | 1.000 |
| `4XXXXXXXXXX` | Máscara (o `4` no início) | 10 bilhões |

> A regra é simples: se a entrada tem **menos de 11 caracteres sem `X`**, o trecho digitado é tratado como o **meio** do CPF (posições 4–9). Se tiver `X`, a posição do `X` é respeitada literalmente.

## Instalação

```bash
python -m venv venv
source venv/bin/activate          # Linux/macOS
# venv\Scripts\activate           # Windows

pip install aiohttp
```

Não precisa mais de Playwright nem de Chrome. Toda a comunicação é feita via `aiohttp` direto na API pública do Portal.

## Interface gráfica

```bash
python src/app_tk.py
```

Quatro abas (atalhos `Ctrl+1` a `Ctrl+4`; `Esc` cancela a operação da aba atual), tema claro/escuro no canto superior direito:

- **Busca de CPF:** mesma busca por máscara do modo linha de comando, com prévia de quantos candidatos serão testados, botão Cancelar (ou `Esc`), exportação CSV/TXT e busca profunda no Jusbrasil quando há um único confirmado.
- **Consulta CNPJ:** dados cadastrais pela [API pública do CNPJ.ws](https://docs.cnpj.ws/referencia-de-api/api-publica/consultando-cnpj) — resumo (situação, endereço, capital social, Simples/MEI), sócios, atividades (CNAE), inscrições estaduais e o JSON bruto, com opção de copiar o resumo ou salvar o JSON.
  - Limite da API: **3 consultas por minuto**. A tela mostra quantas restam e faz contagem regressiva quando o limite acaba.
  - CNPJs já consultados na sessão ficam em cache e não gastam consulta.
  - O CPF de sócio pessoa física é exibido mascarado (`***.123.456-**`), como a Receita publica; o JSON salvo mantém o que a API devolveu.
  - Botão "Processos no Jusbrasil ↗" abre a busca no navegador (o Jusbrasil bloqueia acesso automatizado via Cloudflare).
- **Rede de empresas:** a partir de um CNPJ, consulta automaticamente os sócios que são **empresas**, e os sócios delas, até a profundidade escolhida (1–4) e um máximo de empresas. Mostra tudo em árvore; sócios pessoa física são apenas listados (mascarados) e **não** são pesquisados automaticamente. Exporta CSV.
- **Sócio pessoa física → Busca de CPF:** na Rede ou na sub-aba "Sócios" da Consulta CNPJ, duplo clique (ou botão direito → "Buscar CPF deste sócio") leva à primeira aba com o meio do CPF e o nome já preenchidos. A busca **só começa quando você clica em Buscar** — um sócio por vez.
- **Lote de CNPJs:** cole uma lista (qualquer formato, inclusive CSV) ou importe `.txt`/`.csv`; mostra válidos/inválidos e tempo estimado antes de começar, consulta todos e exporta CSV completo. Números de 12–13 dígitos ganham o zero à esquerda que o Excel costuma apagar.

As três abas de CNPJ compartilham **uma única fila**, cache e o limite de 3/min: a consulta manual fura a fila, pedidos repetidos viram uma consulta só, e uma resposta 429 recoloca o pedido na fila em vez de perdê-lo. Duplo clique numa empresa da rede ou do lote abre o detalhe na aba "Consulta CNPJ" sem gastar consulta.

Estrutura:

| Arquivo | Responsabilidade |
|---------|------------------|
| `src/app_tk.py` | Janela principal: cabeçalho + abas |
| `src/cpf_finder.py` | Motor da busca de CPF (também roda sozinho pela linha de comando) |
| `src/caminhos.py` | Caminho da pasta `resultados/` |
| `resultados/` | Buscas salvas e exportações (fora do git) |
| `tests/` | Testes (`pytest`) |
| `src/ui/tema.py` | Paletas, fontes, estilos ttk e balões de dica (ⓘ) |
| `src/ui/widgets.py` | Tabela ordenável e área de log reutilizáveis |
| `src/ui/aba_*.py` | Cada aba |
| `src/ui/servico_cnpj.py` | Fila + cache + limite compartilhados pelas abas de CNPJ |
| `src/fila_cnpj.py` | Fila de consultas (sem interface, testável) |
| `src/cnpj_ws.py` | Cliente da API do CNPJ.ws (validação, consulta, limite, extração de CNPJs de texto) |
| `src/jusbrasil.py` | Scraping da página pública do Jusbrasil |

Testes: `pytest` (não acessam a rede).

## Uso

### Modo interativo

```bash
python src/cpf_finder.py
```

Fluxo:
```
CPF ou máscara
  CPF completo:  12345678909 ou 123.456.789-09
  Meio do CPF:   456789  ou 456.789   → XXX.456.789-XX
  Máscara:       XXX.456.789.XX
Digite: 456789
Nome completo (opcional, Enter para pular): Maria Aparecida dos Santos
Aceitar match frouxo de nome (gera falsos positivos)? [s/N]:
```

### Linha de comando

```bash
# CPF completo
python src/cpf_finder.py 12345678909

# Meio do CPF, filtrando por nome
python src/cpf_finder.py 456789 --name "Maria Aparecida dos Santos"

# Máscara explícita, com 30 workers
python src/cpf_finder.py "XXX.456.789.XX" --workers 30

# Aceitar match frouxo de nome (aceita "Maria Aparecida" casar com "Maria Aparecida dos Santos Lima")
python src/cpf_finder.py 456789 --name "Maria Aparecida" --loose
```

### Flags

| Flag | Descrição |
|------|-----------|
| `--name "Nome Completo"` | Filtra resultados por nome (recomendado). Usa match fonético PT-BR. |
| `--loose` | Aceita match frouxo de nome — qualquer token em comum já passa. Útil para explorar candidatos próximos, mas gera falsos positivos. |
| `--workers N` | Requisições paralelas (padrão: 20). Aumente se a rede for rápida; reduza se o Portal começar a retornar 429. |
| `--insecure` | Desabilita verificação SSL. Use apenas se enfrentar erro de certificado. |

## Match de nomes

A comparação de nomes é **fonética PT-BR**: `S↔Z`, `PH↔F`, `TH↔T`, `Y↔I`, `W↔V`, `K↔C`, `Ç→S`, remoção de acentos e colapso de consoantes duplicadas. Isso faz com que:

- `Souza` = `Sousa`
- `Filipe` = `Philippe`
- `Wagner` = `Vagner`
- `Kátia` = `Cátia`
- `Yasmin` = `Jasmin`

### Modo estrito (padrão)

**Todos** os tokens do nome buscado devem existir no nome retornado.

| Nome buscado | Nome no Portal | Resultado |
|--------------|----------------|:---------:|
| `Maria Aparecida` | `Maria Aparecida dos Santos` | ✅ passa |
| `Maria Aparecida` | `João Carlos Pereira Lima` | ❌ descarta |
| `Maria Aparecida dos Santos Lima` | `Maria Aparecida dos Santos` | ❌ descarta (falta "lima") |

### Modo frouxo (`--loose`)

Aceita match parcial — se pelo menos **2 tokens** coincidem, ou se algum token tem ≥4 letras em comum, passa. Útil apenas para **explorar** quais variações existem no Portal; não use para confirmar identidade.

## Saída

O arquivo `.txt` (com o mesmo nome da máscara/entrada) é salvo na pasta `resultados/` — onde também abrem, por padrão, as janelas de exportação da interface (CSV/TXT/JSON). A pasta está no `.gitignore`: são dados pessoais e não devem ir para o repositório. O arquivo separa os resultados em **duas seções**:

```
cpf-finder — Portal da Transparência
Entrada       : 456.789
Nome buscado  : Maria Aparecida dos Santos
Match de nome : estrito
Data          : 25/11/2026 17:36:25
Total         : 1 confirmado(s) + 2 divergente(s) de 1000 candidato(s)
=======================================================

### ✅ CONFIRMADOS (1) — nome bate

✅ CPF: 014.456.789-32
    Nome        : Maria Aparecida dos Santos
    CPF (portal): CPF ***.456.789-**
    skPessoa    : 312987654
    Relação GF  : Beneficiário de Programa Social

### ⚠️ ENCONTRADOS COM NOME DIFERENTE (2)
(o CPF existe no Portal, mas pertence a outra pessoa)

⚠ CPF: 128.456.789-90
    Nome        : João Carlos Pereira Lima
    CPF (portal): CPF ***.456.789-**
    skPessoa    : 298765432
    Relação GF  : Beneficiário de Programa Social, Favorecido de recurso público

⚠ CPF: 301.456.789-15
    Nome        : Ana Paula Ribeiro Costa
    CPF (portal): CPF ***.456.789-**
    skPessoa    : 276543210
    Relação GF  : Servidor Público Federal
```

Nenhum registro é descartado silenciosamente. Se o Portal retornar algo, você vê — mesmo que o nome não bata com o que você buscou.

## Progresso no terminal

```
→ Contexto: amostra do que existe no Portal para 'Maria Aparecida'
   → 10000 registro(s) no Portal
   → Primeiros 5:
      - Maria Aparecida dos Santos  (CPF ***.438.478-**)  skPessoa=219211466
      - Maria Aparecida Lima        (CPF ***.733.609-**)  skPessoa=193536974
      ...

→ Testando 1000 CPF(s) candidato(s) (match de nome: estrito)
[0001/1000] 000.456.789-XX ... não encontrado
[0002/1000] 001.456.789-XX ... não encontrado
...
[0123/1000] 122.456.789-45 ... ✓ ENCONTRADO — Maria Aparecida dos Santos
...
Encontrados : 1
```

## Retry e rate limit

Cada consulta tem **3 tentativas** com **backoff exponencial** (1s, 2s, 4s). Se o Portal devolver `429` (rate limit) ou `503`, o script espera e tenta de novo. Se ainda assim falhar, o CPF é reportado como `não encontrado` e o script segue.

Se o Portal começar a bloquear com frequência, **reduza o `--workers`** para 5 ou 10.

## Sobre o Portal da Transparência

Os dados consultados são públicos e estão disponíveis em [portaldatransparencia.gov.br](https://portaldatransparencia.gov.br). A base inclui apenas pessoas com **vínculo com o governo federal**:

| Aparece no Portal | Não aparece no Portal |
|---|---|
| Servidores públicos federais | Cidadão sem vínculo federal |
| Beneficiários de programas sociais (Bolsa Família, BPC, etc.) | Quem nunca recebeu programa social |
| Pessoas que receberam recursos federais | Profissionais do setor privado sem auxílio federal |

Se o CPF que você procura **não tiver vínculo federal**, ele simplesmente **não existe** nessa base — e nenhum script vai encontrá-lo aqui. Para esses casos, é necessário usar outras fontes (Jusbrasil PRO, Receita Federal, diários oficiais).

## Limitações

- **Não substitui** uma consulta oficial à Receita Federal.
- O `skPessoa` retornado é a **chave primária** do Portal — use-o como identificador único do registro.
- O CPF vem **mascarado** no resultado (`***.123.456-**`); o script consegue reconstruir o meio, mas os 3 primeiros e os 2 últimos só aparecem se você já os tinha na máscara.
- O token anti-bot do Portal (`STATIC_TOKEN`) pode expirar — se todas as consultas falharem de uma vez, abra um `issue`.

## Desenvolvido por

[Ziron](https://github.com/zr0n)

##  Contributors

[LuixCarlos00](https://github.com/LuixCarlos00)

## Licença

MIT