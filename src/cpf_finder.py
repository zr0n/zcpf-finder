from __future__ import annotations

import argparse
import asyncio
import itertools
import re
import sys
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher

import aiohttp

from caminhos import pasta_resultados


# ── Constantes ───────────────────────────────────────────────────────────────

PORTAL_BASE  = "https://portaldatransparencia.gov.br"
SEARCH_URL   = "https://busca.portaldatransparencia.gov.br/busca/pessoa-fisica"
STATIC_TOKEN = "wtwLC1ItJOLhvc2n6rhY"

_API_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
    "Referer": f"{PORTAL_BASE}/pessoa-fisica/busca/lista",
    "Origin": PORTAL_BASE,
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}


# ── CPF helpers ──────────────────────────────────────────────────────────────

def _calc_digit(partial: str, weight: int) -> int:
    soma = sum(int(d) * w for d, w in zip(partial, range(weight, 1, -1)))
    resto = (soma * 10) % 11
    return 0 if resto >= 10 else resto


def _build_cpf(base9: str) -> str | None:
    if len(set(base9)) == 1:
        return None
    d1 = _calc_digit(base9, 10)
    d2 = _calc_digit(base9 + str(d1), 11)
    return base9 + str(d1) + str(d2)


def fmt(cpf: str) -> str:
    return f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"


def _expand_mask(clean: str) -> list[str]:
    prefix = list(clean[:9])
    x_pos  = [i for i, c in enumerate(prefix) if c == "X"]
    out: list[str] = []
    for combo in itertools.product(range(10), repeat=len(x_pos)):
        base = prefix.copy()
        for pos, digit in zip(x_pos, combo):
            base[pos] = str(digit)
        cpf = _build_cpf("".join(base))
        if cpf:
            out.append(cpf)
    return out


def generate_candidates(raw: str) -> list[str]:
    """
    Regras (na ordem):
      1) 11 dígitos, sem X         → CPF completo
      2) Tem X em qualquer lugar   → máscara explícita (respeita posição)
      3) 6 dígitos, sem X          → MEIO do CPF: XXX.<dig>-XX
      4) Outros comprimentos < 11  → prefixo (completa com X à direita)
    """
    clean = re.sub(r"[^0-9Xx]", "", raw).upper()

    if not clean:
        raise ValueError("Entrada vazia.")
    if len(clean) > 11:
        raise ValueError(f"Muitos caracteres ({len(clean)}/11). Informe no máximo 11.")

    # 1) CPF completo
    if len(clean) == 11 and "X" not in clean:
        expected = _build_cpf(clean[:9])
        if expected is None:
            print(f"[aviso] '{fmt(clean)}' tem todos os dígitos iguais — inválido, "
                  f"consultando mesmo assim.")
        elif expected != clean:
            print(f"[aviso] '{fmt(clean)}' tem DV inválido "
                  f"(calculado seria '{fmt(expected)}'). Usando o valor digitado.")
        return [clean]

    # 2) Máscara explícita (tem X em algum lugar)
    if "X" in clean:
        if len(clean) < 11:
            clean = clean.ljust(11, "X")
        return _expand_mask(clean)

    # 3) 6 dígitos sem X → MEIO do CPF
    if len(clean) == 6:
        mid = "XXX" + clean + "XX"   # 3 + 6 + 2 = 11
        print(f"   → Interpretando '{raw}' como meio do CPF: {mid}")
        return _expand_mask(mid)

    # 4) Outros comprimentos sem X → prefixo
    print(f"[aviso] '{raw}' interpretado como PREFIXO do CPF: "
          f"{clean.ljust(11, 'X')}")
    clean = clean.ljust(11, "X")
    return _expand_mask(clean)


# ── Normalização de nomes ───────────────────────────────────────────────────

def _normalize(s: str) -> str:
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def _br_phonetic_normalize(s: str) -> str:
    s = _normalize(s)
    if not s:
        return ""
    s = s.replace("ph", "f").replace("th", "t")
    s = s.replace("ss", "s").replace("sc", "s").replace("xc", "s")
    s = s.replace("cq", "k").replace("qu", "k").replace("ck", "k")
    s = s.replace("ch", "x")
    s = s.replace("z", "s").replace("y", "i").replace("w", "v")
    s = s.replace("c", "k").replace("q", "k")
    s = re.sub(r"(.)\1+", r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


def names_match(a: str, b: str, threshold: float = 0.85, loose: bool = False) -> bool:
    na = _br_phonetic_normalize(a)
    nb = _br_phonetic_normalize(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    tokens_a = [t for t in na.split() if len(t) >= 2]
    tokens_b = [t for t in nb.split() if len(t) >= 2]
    if not tokens_a or not tokens_b:
        return False
    sa, sb = set(tokens_a), set(tokens_b)
    if not loose:
        return sa.issubset(sb)
    if sa.issubset(sb) or sb.issubset(sa):
        return True
    if len(sa & sb) >= 2:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= threshold


# ── Prompts interativos ──────────────────────────────────────────────────────

def ask_mask() -> str:
    while True:
        try:
            raw = input(
                "\nCPF ou máscara\n"
                "  CPF completo:  12345678900 ou 123.456.789-00\n"
                "  Meio do CPF:   123456  ou 123.456   → XXX.123.456-XX\n"
                "  Máscara:       XXX.123.456.XX\n"
                "Digite: "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelado.")
            sys.exit(1)
        clean = re.sub(r"[^0-9Xx]", "", raw)
        if 1 <= len(clean) <= 11:
            return raw
        print(f"Inválido ({len(clean)} caracteres). Use de 1 a 11 dígitos.")


def ask_name() -> str | None:
    try:
        return input("Nome completo (opcional, Enter para pular): ").strip() or None
    except (EOFError, KeyboardInterrupt):
        return None


def ask_bool(prompt: str, default: bool = False) -> bool:
    suffix = "[S/n]" if default else "[s/N]"
    try:
        ans = input(f"{prompt} {suffix}: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return default
    return default if not ans else ans in ("s", "sim", "y", "yes")


def interactive_main() -> argparse.Namespace:
    print("=" * 55)
    print("  cpf-finder — Portal da Transparência")
    print("=" * 55)
    mask  = ask_mask()
    name  = ask_name()
    loose = ask_bool("Aceitar match frouxo de nome (gera falsos positivos)?", default=False)
    return argparse.Namespace(
        mask=mask, name=name, workers=20, insecure=False, loose=loose,
    )


# ── Parser do Portal ────────────────────────────────────────────────────────

def _parse_json(data: dict) -> tuple[int, list[dict]]:
    total = int(data.get("totalRegistros", 0) or 0)
    records = []
    for r in (data.get("registros") or []):
        records.append({
            "skPessoa":    r.get("skPessoa", ""),
            "nome":        (r.get("nome", "") or "").title(),
            "cpf_parcial": r.get("cpfNis", ""),
            "relacao":     r.get("descricaoRelacoesGovernoFederal", ""),
        })
    return total, records


async def query_term(
    session: aiohttp.ClientSession,
    term: str,
    page: int,
    page_size: int,
    sem: asyncio.Semaphore,
) -> tuple[int, list[dict]]:
    async with sem:
        params = {
            "termo": term, "letraInicial": "",
            "pagina": page, "tamanhoPagina": page_size,
            "t": STATIC_TOKEN,
        }
        for attempt in range(3):
            try:
                async with session.get(
                    SEARCH_URL, params=params,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status == 200:
                        try:
                            return _parse_json(await resp.json(content_type=None))
                        except Exception:
                            pass
                    elif resp.status in (429, 503):
                        await asyncio.sleep(2 ** attempt)
                        continue
            except (asyncio.TimeoutError, aiohttp.ClientError):
                await asyncio.sleep(1 + attempt)
                continue
        return (0, [])


# ── Loop principal ─────────────────────────────────────────────────────────

async def run_by_cpf(
    candidates: list[str],
    workers: int,
    expected_name: str | None,
    insecure: bool,
    loose: bool,
) -> dict:
    total = len(candidates)
    confirmados: list[dict] = []
    divergentes: list[dict] = []
    lock = asyncio.Lock()
    sem  = asyncio.Semaphore(workers)

    conn = aiohttp.TCPConnector(limit=workers * 2, ssl=not insecure)
    async with aiohttp.ClientSession(headers=_API_HEADERS, connector=conn) as session:
        async def process(idx: int, cpf: str):
            formatted = fmt(cpf)
            _tot, recs = await query_term(session, cpf, 1, 10, sem)

            if not recs:
                print(f"[{idx:04d}/{total}] {formatted} ... não encontrado")
                return

            if expected_name:
                matches = [
                    r for r in recs
                    if names_match(expected_name, r["nome"], loose=loose)
                ]
                if matches:
                    best = matches[0]
                    entry = {
                        "cpf":         formatted,
                        "nome":        best["nome"],
                        "cpf_parcial": best["cpf_parcial"],
                        "skPessoa":    best["skPessoa"],
                        "relacao":     best["relacao"],
                    }
                    async with lock:
                        confirmados.append(entry)
                        print(f"[{idx:04d}/{total}] {formatted} ... ✓ ENCONTRADO — {best['nome']}")
                    return

                nomes = ", ".join(sorted(set(r["nome"] for r in recs[:3])))
                async with lock:
                    for r in recs:
                        divergentes.append({
                            "cpf":         formatted,
                            "nome":        r["nome"],
                            "cpf_parcial": r["cpf_parcial"],
                            "skPessoa":    r["skPessoa"],
                            "relacao":     r["relacao"],
                        })
                    print(f"[{idx:04d}/{total}] {formatted} ... ⚠ existe, nome diferente ({nomes})")
                return

            best = recs[0]
            entry = {
                "cpf":         formatted,
                "nome":        best["nome"],
                "cpf_parcial": best["cpf_parcial"],
                "skPessoa":    best["skPessoa"],
                "relacao":     best["relacao"],
            }
            async with lock:
                confirmados.append(entry)
                print(f"[{idx:04d}/{total}] {formatted} ... ✓ ENCONTRADO — {best['nome']}")

        await asyncio.gather(*(process(i, c) for i, c in enumerate(candidates, 1)))
    return {"confirmados": confirmados, "divergentes": divergentes}


async def _run(args: argparse.Namespace, candidates: list[str]) -> dict:
    conn = aiohttp.TCPConnector(ssl=not args.insecure)
    async with aiohttp.ClientSession(headers=_API_HEADERS, connector=conn) as session:
        if args.name:
            print(f"\n→ Contexto: amostra do que existe no Portal para '{args.name}'")
            sem = asyncio.Semaphore(3)
            total_name, first = await query_term(session, args.name, 1, 10, sem)
            print(f"   → {total_name} registro(s) no Portal")
            if first:
                print("   → Primeiros 5:")
                for r in first[:5]:
                    print(f"      - {r['nome']}  ({r['cpf_parcial']})  skPessoa={r['skPessoa']}")

        modo = "frouxo" if args.loose else "estrito"
        print(f"\n→ Testando {len(candidates)} CPF(s) candidato(s) "
              f"(match de nome: {modo})")
        return await run_by_cpf(
            candidates, args.workers, args.name, args.insecure, args.loose,
        )


# ── Output ──────────────────────────────────────────────────────────────────

def _save(mask: str, resultado: dict, total: int,
          name: str | None, loose: bool) -> None:
    confirmados = resultado["confirmados"]
    divergentes = resultado["divergentes"]

    safe = re.sub(r"[^\w.\-]", "_", mask) or "resultado"
    output_file = pasta_resultados() / f"{safe}.txt"

    print("-" * 55)
    print(f"Confirmados        : {len(confirmados)}")
    print(f"Nome divergente    : {len(divergentes)}")
    print(f"Total com registro : {len(confirmados) + len(divergentes)}")

    def _write_entry(f, r, icon=""):
        f.write(f"{icon} CPF: {r['cpf']}\n")
        f.write(f"    Nome        : {r['nome']}\n")
        f.write(f"    CPF (portal): {r['cpf_parcial']}\n")
        f.write(f"    skPessoa    : {r['skPessoa']}\n")
        if r.get("relacao"):
            f.write(f"    Relação GF  : {r['relacao']}\n")
        f.write("\n")

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("cpf-finder — Portal da Transparência\n")
        f.write(f"Entrada       : {mask}\n")
        if name:
            f.write(f"Nome buscado  : {name}\n")
            f.write(f"Match de nome : {'frouxo' if loose else 'estrito'}\n")
        f.write(f"Data          : {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n")
        f.write(f"Total         : {len(confirmados)} confirmado(s) + "
                f"{len(divergentes)} divergente(s) de {total} candidato(s)\n")
        f.write("=" * 55 + "\n\n")

        if confirmados:
            f.write(f"### ✅ CONFIRMADOS ({len(confirmados)}) — nome bate\n\n")
            for r in confirmados:
                _write_entry(f, r)
            f.write("\n")

        if divergentes:
            f.write(f"### ⚠️ ENCONTRADOS COM NOME DIFERENTE ({len(divergentes)})\n")
            f.write("(o CPF existe no Portal, mas pertence a outra pessoa)\n\n")
            for r in divergentes:
                _write_entry(f, r)

        if not confirmados and not divergentes:
            f.write("Nenhum CPF encontrado no Portal.\n")

    print(f"Resultados salvos em: {output_file}")


# ── Entrypoint ──────────────────────────────────────────────────────────────

def _parse_cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="cpf_finder",
        description="Busca CPFs no Portal da Transparência.",
    )
    parser.add_argument("mask", nargs="?",
                        help="CPF completo (14776815664), meio do CPF (123456), "
                             "ou máscara (XXX.123.456.XX)")
    parser.add_argument("--name",     default=None)
    parser.add_argument("--workers",  type=int, default=20)
    parser.add_argument("--insecure", action="store_true")
    parser.add_argument("--loose",    action="store_true",
                        help="Match frouxo de nome (pode gerar falsos positivos)")
    return parser.parse_args()


def main() -> None:
    if len(sys.argv) == 1:
        args = interactive_main()
    else:
        args = _parse_cli()
        if args.mask is None:
            args.mask = ask_mask()

    if args.workers < 1:
        print("[ERRO] --workers deve ser >= 1")
        sys.exit(1)

    try:
        candidates = generate_candidates(args.mask)
    except ValueError as e:
        print(f"[ERRO] {e}")
        sys.exit(1)

    total = len(candidates)
    print("\n" + "-" * 55)
    print(f"Entrada   : {args.mask}")
    print(f"Candidatos: {total}")
    if args.name:
        print(f"Nome      : {args.name}")
        print(f"Match     : {'frouxo' if args.loose else 'estrito'}")
    print(f"Workers   : {args.workers}")
    print("-" * 55)

    resultado = asyncio.run(_run(args, candidates))
    _save(args.mask, resultado, total, args.name, args.loose)


if __name__ == "__main__":
    main()