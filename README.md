# zCpf-finder

Busca CPFs no Portal da Transparência usando uma máscara. Você passa os dígitos que conhece e marca o restante com `X` — o programa gera todas as combinações válidas, calcula os dígitos verificadores de cada uma e consulta o portal automaticamente.

## Como funciona

O CPF tem 11 dígitos: os 9 primeiros são o número base e os 2 últimos são verificadores matemáticos. A máscara usa `X` nos dígitos desconhecidos dos 9 primeiros. Os dois últimos (`-XX`) são sempre calculados pelo programa, não precisam estar na máscara.

Exemplo: `XXX.332.217-XX` → testa `001.332.217` até `999.332.217`, calcula os verificadores de cada candidato e consulta o Portal da Transparência.

Por padrão tenta usar a API direta do portal (bem mais rápido). Se não estiver disponível, cai automaticamente pro Playwright com Chrome.

## Instalação

```bash
pip install -r requirements.txt
playwright install chromium
```

## Uso

```bash
python cpf_finder.py "XXX.452.217-XX"
```

| Flag | Descrição |
|------|-----------|
| `--workers N` | Requisições paralelas (padrão: 20 na API, 5 no Playwright) |
| `--force-playwright` | Força uso do Playwright mesmo que a API esteja disponível |
| `--debug` | Salva `debug.html` e `debug.json` da primeira consulta |

```bash
# Exemplo com 10 workers
python cpf_finder.py "XXX.332.217-XX" --workers 10
```

O resultado é salvo num `.txt` com o mesmo nome da máscara. Ex: `XXX.332.217-XX.txt`.

## Saída

```
Verificando API direta... disponível ✓
Modo      : API direta (sem browser)
Máscara   : XXX.332.217-XX
Candidatos: 998
Workers   : 20
--------------------------------------------------
[0001/998] 001.332.217-09 ... não encontrado
[0002/998] 002.332.217-06 ... não encontrado
...
[0143/998] 141.332.217-22 ... ENCONTRADO — Luiz Fernando Ziron
...
--------------------------------------------------
Total encontrado: 1/998
Resultados salvos em: XXX.332.217-XX.txt
```

## Notas

- Os dados consultados são públicos, disponíveis no [Portal da Transparência](https://portaldatransparencia.gov.br).
- Precisa do Chrome instalado pra funcionar no modo Playwright.
- Quanto mais `X` na máscara, mais candidatos — `XXX` nos primeiros três dígitos gera ~998 combinações.

## Desenvolvido por

[Ziron](https://github.com/zr0n)

## Licença

MIT
