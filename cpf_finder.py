#!/usr/bin/env python3
"""zcpf-finder — busca por máscara no Portal da Transparência (modo Playwright)."""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from playwright.async_api import (
    Browser,
    BrowserContext,
    Error as PWError,
    Page,
    async_playwright,
)

# ── Configuração ──────────────────────────────────────────────────────────────

PORTAL_BASE = "https://portaldatransparencia.gov.br"
LISTA_URL = f"{PORTAL_BASE}/pessoa-fisica/busca/lista"
BUSCA_HOST = "busca.portaldatransparencia.gov.br"

DEFAULT_WORKERS = 3
DEFAULT_TIMEOUT = 25.0
DEFAULT_PASSES = 2
CONTEXT_TTL = 25  # recicla o contexto a cada N consultas

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

BLOCKED_RESOURCE_TYPES = frozenset({"image", "media", "font"})

STEALTH_JS = """
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

log = logging.getLogger("zcpf")


class Outcome(Enum):
    FOUND = "encontrado"
    NOT_FOUND = "não encontrado"
    ERROR = "erro"


# ── Estruturas ────────────────────────────────────────────────────────────────

@dataclass
class Settings:
    workers: int
    timeout: float
    passes: int
    debug: bool
    outfile: Path


@dataclass
class ResultStore:
    outfile: Path
    total: int
    results: dict[str, dict] = field(default_factory=dict)
    errors: set[str] = field(default_factory=set)
    _debug_saved: bool = field(default=False)

    def add(self, cpf: str, name: str) -> bool:
        """Registra um achado. Retorna True se for novo."""
        if cpf in self.results:
            return False
        self.results[cpf] = {"cpf": cpf, "nome": name}
        self.errors.discard(cpf)
        self.flush()
        return True

    def mark_error(self, cpf: str) -> None:
        self.errors.add(cpf)

    def clear_error(self, cpf: str) -> None:
        self.errors.discard(cpf)

    async def save_debug(self, page: Page, captured: dict) -> None:
        if self._debug_saved:
            return
        self._debug_saved = True
        try:
            Path("debug.html").write_text(await page.content(), encoding="utf-8")
            extra = ""
            if "json" in captured:
                Path("debug.json").write_text(
                    json.dumps(captured["json"], ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                extra = " + debug.json"
            log.info("[DEBUG] Salvo em debug.html%s", extra)
        except Exception as exc:
            log.debug("falha ao salvar debug: %s", exc)

    def flush(self) -> None:
        rows = sorted(self.results.values(), key=lambda r: r["cpf"])
        with open(self.outfile, "w", encoding="utf-8") as f:
            f.write("zcpf-finder — busca por máscara\n")
            f.write(f"Data    : {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n")
            f.write(f"Resultado: {len(rows)} encontrado(s)\n")
            f.write("=" * 50 + "\n\n")
            for r in rows:
                f.write(r["cpf"] + (f" — {r['nome']}" if r["nome"] else "") + "\n")


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
        raise ValueError(f"Máscara inválida: '{mask}'. Use formato como XXX.452.217-XX")

    prefix = list(clean[:9])
    x_pos = [i for i, c in enumerate(prefix) if c == "X"]

    out = []
    for combo in itertools.product(range(10), repeat=len(x_pos)):
        if x_pos and combo == (0,) * len(x_pos):
            continue
        base = prefix.copy()
        for pos, digit in zip(x_pos, combo):
            base[pos] = str(digit)
        cpf = _build_cpf("".join(base))
        if cpf:
            out.append(cpf)
    return out


def parse_payload(data: dict) -> tuple[bool, str]:
    total = data.get("totalRegistros", 0)
    records = data.get("registros") or []
    raw = records[0].get("nome", "") if records else ""
    return (total > 0 or bool(records), raw.title() if raw else "")


# ── Sessão Playwright (uma página exclusiva por worker) ───────────────────────

class SearchSession:
    def __init__(self, browser: Browser, session_id: int, cfg: Settings):
        self.browser = browser
        self.session_id = session_id
        self.cfg = cfg
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self._uses = 0

    async def _new_page(self) -> None:
        self.context = await self.browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 800},
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            extra_http_headers={"Accept-Language": "pt-BR,pt;q=0.9"},
        )
        await self.context.add_init_script(STEALTH_JS)
        self.page = await self.context.new_page()

        async def block_route(route):
            if route.request.resource_type in BLOCKED_RESOURCE_TYPES:
                await route.abort()
            else:
                await route.continue_()

        await self.page.route("**/*", block_route)
        self._uses = 0

    async def _recycle(self) -> None:
        if self.context:
            try:
                await self.context.close()
            except PWError:
                pass
        self.context = None
        self.page = None

    async def close(self) -> None:
        await self._recycle()

    async def search(self, cpf: str) -> tuple[Outcome, dict | None]:
        """Uma consulta. Retorna (outcome, json|None). Nunca levanta."""
        if self.page is None:
            try:
                await self._new_page()
            except PWError as exc:
                log.debug("[sess %d] falha ao criar página: %s", self.session_id, exc)
                return Outcome.ERROR, None

        self._uses += 1
        page = self.page
        captured: dict = {}
        ready = asyncio.Event()

        async def on_response(response):
            if BUSCA_HOST in response.url and response.status == 200:
                try:
                    captured["json"] = await response.json()
                    ready.set()
                except Exception:
                    pass

        page.on("response", on_response)
        try:
            await page.goto(
                f"{LISTA_URL}?termo={cpf}&pagina=1&tamanhoPagina=10",
                wait_until="domcontentloaded",
                timeout=self.cfg.timeout * 1000,
            )

            # A busca geralmente dispara sozinha (termo já na URL); o clique
            # cobre o caso em que o formulário exige submit explícito.
            try:
                btn = await page.wait_for_selector(
                    'button[aria-label="Enviar dados do formulário de busca"]',
                    timeout=8_000,
                )
                await btn.click()
            except Exception:
                pass

            try:
                await asyncio.wait_for(ready.wait(), timeout=self.cfg.timeout)
            except asyncio.TimeoutError:
                log.debug("[sess %d] timeout aguardando JSON para %s", self.session_id, cpf)
                if self.cfg.debug:
                    await ResultStoreDebugHook(self.cfg, captured, page)
                return Outcome.ERROR, None

            return Outcome.FOUND, captured["json"]  # FOUND = resposta recebida

        except PWError as exc:
            log.debug("[sess %d] erro de navegação: %s", self.session_id, exc)
            await self._recycle()  # página em estado ruim — recria na próxima
            return Outcome.ERROR, None
        finally:
            try:
                page.remove_listener("response", on_response)
            except PWError:
                pass
            if self._uses >= CONTEXT_TTL:
                await self._recycle()


async def ResultStoreDebugHook(cfg: Settings, captured: dict, page: Page) -> None:
    """Salva debug na primeira falha de captura, se --debug estiver ativo."""
    if not cfg.debug:
        return
    try:
        Path("debug.html").write_text(await page.content(), encoding="utf-8")
        extra = ""
        if "json" in captured:
            Path("debug.json").write_text(
                json.dumps(captured["json"], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            extra = " + debug.json"
        log.info("[DEBUG] Salvo em debug.html%s", extra)
    except Exception:
        pass


# ── Workers (fila + sessão própria) ───────────────────────────────────────────

async def worker_loop(sess: SearchSession, queue: asyncio.Queue,
                      store: ResultStore, cfg: Settings) -> None:
    while True:
        item = await queue.get()
        try:
            if item is None:
                return
            idx, cpf = item
            formatted = fmt(cpf)

            outcome, data = await sess.search(cpf)

            if outcome is Outcome.ERROR or data is None:
                store.mark_error(cpf)
                log.info("[%04d] %s ... ERRO (será reconsultado)", idx, formatted)
            else:
                found, name = parse_payload(data)
                if found and store.add(formatted, name):
                    log.info("[%04d] %s ... ENCONTRADO — %s",
                             idx, formatted, name or "nome não disponível")
                else:
                    store.clear_error(cpf)
                    log.debug("[%04d] %s ... não encontrado", idx, formatted)
        except Exception as exc:
            # Nunca deixa o worker morrer silenciosamente.
            if item is not None:
                idx, cpf = item
                store.mark_error(cpf)
                log.info("[%04d] %s ... ERRO inesperado: %s", idx, fmt(cpf), exc)
        finally:
            queue.task_done()


async def run(candidates: list[str], cfg: Settings) -> ResultStore:
    store = ResultStore(cfg.outfile, len(candidates))
    pending = list(candidates)

    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True, channel="chrome")
        except Exception:
            browser = await p.chromium.launch(headless=True)
        try:
            for pass_no in range(1, cfg.passes + 1):
                if not pending:
                    break
                if pass_no > 1:
                    print(f"\n[passada {pass_no}] Reconsultando {len(pending)} "
                          f"candidato(s) com erro...")
                    await asyncio.sleep(5)

                queue: asyncio.Queue = asyncio.Queue()
                for i, c in enumerate(pending, 1):
                    queue.put_nowait((i, c))
                for _ in range(cfg.workers):
                    queue.put_nowait(None)  # sentinels de encerramento

                workers = [
                    asyncio.create_task(
                        worker_loop(SearchSession(browser, i, cfg), queue, store, cfg)
                    )
                    for i in range(cfg.workers)
                ]
                await asyncio.gather(*workers)
                pending = sorted(store.errors)
        finally:
            await browser.close()
    return store


# ── Resumo ────────────────────────────────────────────────────────────────────

def summarize(store: ResultStore) -> None:
    print("-" * 50)
    print(f"Total encontrado : {len(store.results)}/{store.total}")
    print(f"Erros pendentes  : {len(store.errors)}")
    if store.errors:
        print("[!] Alguns candidatos NÃO foram verificados — rode novamente com --passes 3.")
    print(f"Resultados salvos em: {store.outfile}")


# ── Entrypoint ────────────────────────────────────────────────────────────────

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="cpf_finder",
        description="Busca CPFs no Portal da Transparência usando uma máscara.",
    )
    parser.add_argument("mask", help='Máscara do CPF. Ex: "XXX.768.156-XX"')
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, metavar="N")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, metavar="S")
    parser.add_argument("--passes", type=int, default=DEFAULT_PASSES, metavar="N")
    parser.add_argument("--output", "-o", default=None)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


async def amain(args: argparse.Namespace) -> ResultStore:
    candidates = generate_candidates(args.mask)
    cfg = Settings(
        workers=max(1, args.workers),
        timeout=args.timeout,
        passes=max(1, args.passes),
        debug=args.debug,
        outfile=Path(args.output or f"{args.mask}.txt"),
    )
    print("Modo      : Playwright (página por worker)")
    print(f"Máscara   : {args.mask}")
    print(f"Candidatos: {len(candidates)}")
    print(f"Workers   : {cfg.workers}")
    print(f"Passadas  : {cfg.passes}")
    print("-" * 50)
    return await run(candidates, cfg)


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )
    try:
        store = asyncio.run(amain(args))
    except KeyboardInterrupt:
        print("\n[!] Interrompido — resultados parciais já foram salvos.")
        sys.exit(130)
    except ValueError as exc:
        print(f"[ERRO] {exc}")
        sys.exit(1)
    summarize(store)


if __name__ == "__main__":
    main()