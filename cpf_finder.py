import re
import sys
import json
import itertools
import argparse
import asyncio
from datetime import datetime

import aiohttp
from playwright.async_api import async_playwright, Page, Browser


PORTAL_BASE   = "https://portaldatransparencia.gov.br"
SEARCH_URL    = "https://busca.portaldatransparencia.gov.br/busca/pessoa-fisica"
STATIC_TOKEN  = "wtwLC1ItJOLhvc2n6rhY"

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

_BLOCK_TYPES = {"image", "media", "font"}

_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => false, configurable: true});
Object.defineProperty(navigator, 'plugins', {get: () => [
    {name:'Chrome PDF Plugin', filename:'internal-pdf-viewer', description:'Portable Document Format'},
    {name:'Chrome PDF Viewer', filename:'mhjfbmdgcfjbbpaeojofohoefgiehjai', description:''},
    {name:'Native Client', filename:'internal-nacl-plugin', description:''}
]});
Object.defineProperty(navigator, 'languages', {get: () => ['pt-BR','pt','en-US','en']});
Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
window.chrome = {runtime: {}, app: {isInstalled: false}};
"""

_debug_saved = asyncio.Event()


# ── CPF helpers ───────────────────────────────────────────────────────────────

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


def generate_candidates(mask: str) -> list[str]:
    clean = re.sub(r"[^0-9Xx]", "", mask).upper()
    if len(clean) != 11:
        print(f"[ERRO] Máscara inválida: '{mask}'. Use formato como XXX.452.217-XX")
        sys.exit(1)

    prefix = list(clean[:9])
    x_pos = [i for i, c in enumerate(prefix) if c == "X"]

    out = []
    for combo in itertools.product(range(10), repeat=len(x_pos)):
        if x_pos and x_pos[0] == 0 and all(x_pos[k] == k for k in range(len(x_pos))) and combo == (0,) * len(x_pos):
            continue
        base = prefix.copy()
        for pos, digit in zip(x_pos, combo):
            base[pos] = str(digit)
        cpf = _build_cpf("".join(base))
        if cpf:
            out.append(cpf)
    return out


# ── JSON parser (estrutura real da API) ───────────────────────────────────────

def _parse_json(data: dict) -> tuple[bool, str]:
    total = data.get("totalRegistros", 0)
    records = data.get("registros", [])
    name = ""
    if records:
        raw = records[0].get("nome", "")
        name = raw.title() if raw else ""
    return (total > 0 or bool(records), name)


# ── Modo 1: API direta (sem browser) ─────────────────────────────────────────

async def _probe_api(session: aiohttp.ClientSession) -> bool:
    """Verifica se a API aceita chamadas sem reCAPTCHA."""
    try:
        async with session.get(
            SEARCH_URL,
            params={"termo": "14345221722", "pagina": 1, "tamanhoPagina": 1, "t": STATIC_TOKEN},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 200:
                ct = resp.headers.get("Content-Type", "")
                if "json" in ct:
                    data = await resp.json()
                    return "registros" in data or "totalRegistros" in data
    except Exception:
        pass
    return False


async def _query_api(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    idx: int,
    cpf: str,
    total: int,
    results: list,
    lock: asyncio.Lock,
) -> None:
    async with sem:
        formatted = fmt(cpf)
        found, name = False, ""
        try:
            async with session.get(
                SEARCH_URL,
                params={
                    "termo": cpf,
                    "letraInicial": "",
                    "pagina": 1,
                    "tamanhoPagina": 10,
                    "t": STATIC_TOKEN,
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    ct = resp.headers.get("Content-Type", "")
                    if "json" in ct:
                        found, name = _parse_json(await resp.json())
        except Exception:
            pass

        async with lock:
            if found:
                results.append({"cpf": formatted, "nome": name})
                print(f"[{idx:04d}/{total}] {formatted} ... ENCONTRADO — {name or 'nome não disponível'}")
            else:
                print(f"[{idx:04d}/{total}] {formatted} ... não encontrado")


async def run_api(candidates: list[str], workers: int) -> list[dict]:
    total = len(candidates)
    results: list[dict] = []
    lock = asyncio.Lock()
    sem = asyncio.Semaphore(workers)

    conn = aiohttp.TCPConnector(limit=workers * 2, ssl=False)
    async with aiohttp.ClientSession(headers=_API_HEADERS, connector=conn) as session:
        tasks = [
            _query_api(session, sem, idx, cpf, total, results, lock)
            for idx, cpf in enumerate(candidates, 1)
        ]
        await asyncio.gather(*tasks)

    return results


# ── Modo 2: Playwright (fallback com reCAPTCHA) ───────────────────────────────

async def _query_playwright(browser: Browser, cpf: str, sem: asyncio.Semaphore, debug: bool) -> tuple[bool, str]:
    async with sem:
        captured: dict = {}
        ready = asyncio.Event()

        context = await browser.new_context(
            user_agent=_API_HEADERS["User-Agent"],
            viewport={"width": 1280, "height": 800},
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            extra_http_headers={"Accept-Language": "pt-BR,pt;q=0.9"},
        )
        await context.add_init_script(_STEALTH_JS)
        page: Page = await context.new_page()

        async def block_route(route):
            if route.request.resource_type in _BLOCK_TYPES:
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", block_route)

        # Intercepta a chamada AJAX para busca.portaldatransparencia.gov.br
        async def on_response(response):
            if "busca.portaldatransparencia.gov.br" in response.url and response.status == 200:
                try:
                    data = await response.json()
                    captured["json"] = data
                    ready.set()
                except Exception:
                    pass

        page.on("response", on_response)

        await page.goto(
            f"{PORTAL_BASE}/pessoa-fisica/busca/lista?termo={cpf}&pagina=1&tamanhoPagina=10",
            wait_until="domcontentloaded",
        )

        try:
            btn = await page.wait_for_selector(
                'button[aria-label="Enviar dados do formulário de busca"]',
                timeout=10_000,
            )
            await btn.click()
        except Exception:
            pass

        try:
            await asyncio.wait_for(ready.wait(), timeout=20.0)
        except asyncio.TimeoutError:
            pass

        if debug and not _debug_saved.is_set():
            _debug_saved.set()
            html = await page.content()
            with open("debug.html", "w", encoding="utf-8") as f:
                f.write(html)
            if "json" in captured:
                with open("debug.json", "w", encoding="utf-8") as f:
                    json.dump(captured["json"], f, ensure_ascii=False, indent=2)
            print(f"[DEBUG] Salvo em debug.html" + (" + debug.json" if "json" in captured else ""))

        await page.close()
        await context.close()

        if "json" in captured:
            return _parse_json(captured["json"])

        return (False, "")


async def run_playwright(candidates: list[str], workers: int, debug: bool) -> list[dict]:
    total = len(candidates)
    results: list[dict] = []
    lock = asyncio.Lock()
    sem = asyncio.Semaphore(workers)

    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True, channel="chrome")
        except Exception:
            browser = await p.chromium.launch(headless=True)

        async def process(idx: int, cpf: str):
            formatted = fmt(cpf)
            found, name = await _query_playwright(browser, cpf, sem, debug)
            async with lock:
                if found:
                    results.append({"cpf": formatted, "nome": name})
                    print(f"[{idx:04d}/{total}] {formatted} ... ENCONTRADO — {name or 'nome não disponível'}")
                else:
                    print(f"[{idx:04d}/{total}] {formatted} ... não encontrado")

        await asyncio.gather(*[process(idx, cpf) for idx, cpf in enumerate(candidates, 1)])
        await browser.close()

    return results


# ── Output ────────────────────────────────────────────────────────────────────

def _save(mask: str, results: list[dict], total: int) -> None:
    results.sort(key=lambda r: r["cpf"])
    output_file = mask + ".txt"
    print("-" * 50)
    print(f"Total encontrado: {len(results)}/{total}")
    with open(output_file, "w", encoding="utf-8") as f:
        f.write("zcpf-finder — busca por máscara\n")
        f.write(f"Máscara : {mask}\n")
        f.write(f"Data    : {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n")
        f.write(f"Resultado: {len(results)} encontrado(s) de {total} candidato(s)\n")
        f.write("=" * 50 + "\n\n")
        for r in results:
            line = r["cpf"] + (f" — {r['nome']}" if r["nome"] else "")
            f.write(line + "\n")
        if not results:
            f.write("Nenhum CPF encontrado.\n")
    print(f"Resultados salvos em: {output_file}")


# ── Entrypoint ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="cpf_finder",
        description="Busca CPFs no Portal da Transparência usando uma máscara.",
    )
    parser.add_argument("mask", help="Máscara do CPF. Ex: XXX.452.217-XX")
    parser.add_argument("--workers", type=int, default=20, metavar="N",
                        help="Concorrência (padrão: 20 no modo API, 3 no modo Playwright)")
    parser.add_argument("--debug", action="store_true",
                        help="Salva debug.html / debug.json da primeira consulta")
    parser.add_argument("--force-playwright", action="store_true",
                        help="Força uso do Playwright mesmo se API direta funcionar")
    args = parser.parse_args()

    if args.workers < 1:
        print("[ERRO] --workers deve ser >= 1")
        sys.exit(1)

    candidates = generate_candidates(args.mask)
    total = len(candidates)

    async def _main():
        if args.force_playwright:
            use_api = False
        else:
            print("Verificando API direta...", end=" ", flush=True)
            conn = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(headers=_API_HEADERS, connector=conn) as s:
                use_api = await _probe_api(s)
            print("disponível ✓" if use_api else "indisponível (usando Playwright)")

        workers = args.workers if use_api else min(args.workers, 5)
        mode = "API direta (sem browser)" if use_api else "Playwright (com reCAPTCHA)"

        print(f"Modo      : {mode}")
        print(f"Máscara   : {args.mask}")
        print(f"Candidatos: {total}")
        print(f"Workers   : {workers}")
        print("-" * 50)

        if use_api:
            return await run_api(candidates, workers)
        else:
            return await run_playwright(candidates, workers, debug=args.debug)

    results = asyncio.run(_main())
    _save(args.mask, results, total)


if __name__ == "__main__":
    main()
