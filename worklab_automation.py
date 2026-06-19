#!/usr/bin/env python3
"""
Automacao WorkLab - Importacao CDA.
Executa sem uso de LLM. Toda a logica condicional eh implementada em Python.

Em cada execucao processa DOIS periodos em sequencia:
  1) Dia anterior (D-1)
  2) Dia atual    (D)
Para cada periodo: importa e analisa resultado.
"""
import json
import os
import sys
import time
import traceback
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# Garantir que browsers do Playwright sejam encontrados
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/home/ubuntu/.cache/ms-playwright")

ENV_PATH = "/home/ubuntu/shared/.env"
load_dotenv(ENV_PATH)

URL = os.getenv("WORKLAB_URL", "https://www.worklabweb.com.br/")
LAB_ID = os.getenv("WORKLAB_LAB_ID", "")
USER = os.getenv("WORKLAB_USER", "")
PASSWORD = os.getenv("WORKLAB_PASSWORD", "")
WORKLAB_HEADLESS = os.getenv("WORKLAB_HEADLESS", "true").strip().lower() in {"1", "true", "yes", "y"}

# Telegram
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_BOT_TOKEN = ""
try:
    _secrets_path = "/home/ubuntu/.config/abacusai_auth_secrets.json"
    if os.path.exists(_secrets_path):
        with open(_secrets_path, "r") as _f:
            _secrets = json.load(_f)
        # Tentar ambas as capitalizacoes
        tg_block = _secrets.get("Telegram") or _secrets.get("telegram") or {}
        TELEGRAM_BOT_TOKEN = tg_block.get("secrets", {}).get("BOT_TOKEN", _secrets.get("bot_token", {})).get("value", "")
        if not TELEGRAM_BOT_TOKEN:
            TELEGRAM_BOT_TOKEN = tg_block.get("secrets", {}).get("bot_token", {}).get("value", "")
except Exception:
    pass

# Tempos de espera (segundos)
WAIT_ENTRAR = 4
WAIT_IMPORTAR = 4
WAIT_PESQUISAR = 3
WAIT_CONFERIR = 2

# URL base apos login (preenchida em runtime)
BASE_URL = ""

# Fuso America/Sao_Paulo (UTC-3)
SP_TZ = timezone(timedelta(hours=-3))


def log(msg):
    ts = datetime.now(SP_TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def date_str_for(offset_days: int) -> str:
    """Retorna dd/mm/yyyy para hoje + offset_days (ex.: -1 = ontem, 0 = hoje)."""
    d = datetime.now(SP_TZ).date() + timedelta(days=offset_days)
    return d.strftime("%d/%m/%Y")


def safe_fill(page, selectors, value):
    """Tenta preencher o primeiro seletor que existir."""
    for s in selectors:
        try:
            loc = page.locator(s).first
            if loc.count() > 0:
                loc.fill(value)
                return s
        except Exception:
            continue
    raise RuntimeError(f"Nenhum seletor encontrado para preencher valor: {selectors}")


def safe_click(page, selectors):
    for s in selectors:
        try:
            loc = page.locator(s).first
            if loc.count() > 0:
                loc.click()
                return s
        except Exception:
            continue
    raise RuntimeError(f"Nenhum seletor clicavel encontrado: {selectors}")


def login(page):
    log("Acessando WorkLab...")
    page.goto(URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_load_state("networkidle", timeout=30000)

    # Marcar checkbox "Novo Login"
    try:
        chk = page.get_by_label("Novo Login")
        if chk.count() == 0:
            chk = page.locator("input[type=checkbox]").first
        if not chk.is_checked():
            chk.check()
        log("Checkbox 'Novo Login' marcado.")
    except Exception as e:
        log(f"AVISO: nao foi possivel marcar 'Novo Login' explicitamente: {e}")

    # Preencher Lab, Usuario, Senha (tenta seletores comuns)
    safe_fill(page, [
        "input[name=new_login_cliente]", "input[name=lab i]", "input[id*=lab i]",
        "input[placeholder*=Lab i]", "input[name*=Lab]",
    ], LAB_ID)

    safe_fill(page, [
        "input[name=new_login_username]", "input[name*=usuario i]", "input[id*=usuario i]",
        "input[placeholder*=Usu i]", "input[name=user]", "input[name=username]",
    ], USER)

    safe_fill(page, [
        "input[name=new_login_password]", "input[type=password]", "input[name*=senha i]", "input[id*=senha i]",
    ], PASSWORD)

    safe_click(page, [
        "button:has-text('ENTRAR')", "input[type=submit][value*=ENTRAR i]",
        "button:has-text('Entrar')", "input[value*=Entrar i]",
    ])
    log(f"Login submetido. Aguardando {WAIT_ENTRAR}s...")
    time.sleep(WAIT_ENTRAR)
    page.wait_for_load_state("networkidle", timeout=30000)

    # Capturar BASE_URL apos login (ex: https://app2.worklabweb.com.br/)
    global BASE_URL
    from urllib.parse import urlparse
    parsed = urlparse(page.url)
    BASE_URL = f"{parsed.scheme}://{parsed.netloc}"
    log(f"BASE_URL capturada: {BASE_URL}")


def set_react_date(page, input_idx, date_str):
    """Define valor em input React (datepicker) via nativeInputValueSetter."""
    result = page.evaluate(f"""
        () => {{
            const inputs = document.querySelectorAll('input');
            const inp = inputs[{input_idx}];
            if (!inp) return 'not found';
            const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value').set;
            nativeInputValueSetter.call(inp, '{date_str}');
            inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
            inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
            return inp.value;
        }}
    """)
    return result


def wait_for_import_complete(page, max_wait_s=120):
    """Aguarda o progresso da importacao terminar (desaparece do DOM)."""
    for _ in range(max_wait_s // 5):
        time.sleep(5)
        progress = page.evaluate("""
            () => {
                const body = document.body.innerText || '';
                const idx = body.toLowerCase().indexOf('progresso');
                if (idx >= 0) return body.substring(idx, idx+30);
                return '';
            }
        """)
        log(f"  Progresso: {progress.strip() or 'concluido'}")
        if not progress.strip():
            break


def hover_and_click(page, parent_text, child_text):
    """Hover em menu e clica subitem (fallback - pode nao funcionar em headless)."""
    parent = page.get_by_text(parent_text, exact=False).first
    parent.hover()
    page.wait_for_timeout(500)
    child = page.get_by_text(child_text, exact=False).first
    child.click()


def click_first_visible(page, selectors, timeout_ms=2500):
    """Clica no primeiro elemento visivel entre varios seletores."""
    for s in selectors:
        try:
            loc = page.locator(s)
            count = loc.count()
            for i in range(count):
                item = loc.nth(i)
                if item.is_visible():
                    item.click(timeout=timeout_ms)
                    return s
        except Exception:
            continue
    return None


def verify_conferido_sim(page):
    """Valida se o campo Conferido ficou com valor/texto equivalente a 'Sim'."""
    try:
        res = page.evaluate("""
            () => {
                const norm = (v) => (v || '').toString().normalize('NFD')
                    .replace(/[\u0300-\u036f]/g, '').toLowerCase().trim();

                // 1) SELECT nativo
                const selects = Array.from(document.querySelectorAll('select'));
                for (const sel of selects) {
                    const ref = norm(`${sel.id || ''} ${sel.name || ''} ${sel.getAttribute('aria-label') || ''}`);
                    if (!ref.includes('conferido')) continue;
                    const opt = sel.options && sel.selectedIndex >= 0 ? sel.options[sel.selectedIndex] : null;
                    const selectedTxt = norm(opt ? opt.textContent : '');
                    const selectedVal = norm(sel.value);
                    if (selectedTxt.includes('sim') || selectedVal === 'sim' || selectedVal === 's' || selectedVal === '1') {
                        return true;
                    }
                }

                // 2) MUI Select - procurar label "Conferido" e verificar valor no select proximo
                const allLabels = Array.from(document.querySelectorAll('label, span, legend, p'));
                for (const lbl of allLabels) {
                    if (norm(lbl.textContent) !== 'conferido') continue;
                    let container = lbl.parentElement;
                    for (let i = 0; i < 5 && container; i++) {
                        // MUI Select mostra o valor selecionado dentro de um div com classe MuiSelect-select
                        const selectDisplay = container.querySelector(
                            '[class*="MuiSelect-select"], [role="button"][aria-haspopup="listbox"]'
                        );
                        if (selectDisplay && norm(selectDisplay.textContent).includes('sim')) {
                            return true;
                        }
                        container = container.parentElement;
                    }
                }

                // 3) Combobox/input custom
                const controls = Array.from(document.querySelectorAll('[role="combobox"], input, div, button, span'));
                for (const el of controls) {
                    const ref = norm(`${el.id || ''} ${el.getAttribute('name') || ''} ${el.getAttribute('aria-label') || ''} ${el.getAttribute('placeholder') || ''}`);
                    const txt = norm(el.textContent || el.value || '');
                    if (ref.includes('conferido') && txt.includes('sim')) {
                        return true;
                    }
                }

                // 4) Fallback por bloco que contenha label "conferido" + "sim" (excluindo texto explicativo)
                const formLabels = Array.from(document.querySelectorAll('label, legend'))
                    .filter(el => norm(el.textContent).includes('conferido'));
                for (const lb of formLabels) {
                    const container = lb.closest('.MuiFormControl-root, .sc-iCmkLe') || lb.parentElement;
                    if (!container) continue;
                    const txt = norm(container.textContent || '');
                    if (txt.includes('conferido') && txt.includes('sim')) return true;
                }

                return false;
            }
        """)
        return bool(res)
    except Exception:
        return False


def set_conferido_sim(page):
    """Define o campo Conferido como 'Sim' de forma direta via JS e clique MUI."""
    log("  Ajustando campo 'Conferido' para 'Sim'...")

    # ── Passo 1: Detectar se o campo Conferido existe na pagina ──
    field_info = page.evaluate("""
        () => {
            const norm = (v) => (v || '').toString().normalize('NFD')
                .replace(/[\\u0300-\\u036f]/g, '').toLowerCase().trim();

            // A) Select nativo
            const selects = Array.from(document.querySelectorAll('select'));
            for (const sel of selects) {
                const ref = norm(`${sel.id || ''} ${sel.name || ''} ${sel.getAttribute('aria-label') || ''}`);
                if (ref.includes('conferido')) return { type: 'native-select', id: sel.id, name: sel.name };
            }

            // B) MUI Select - procurar por label/span "Conferido" proximo a um MuiSelect
            const allLabels = Array.from(document.querySelectorAll('label, span, div, p'));
            for (const lbl of allLabels) {
                const txt = norm(lbl.textContent || '');
                if (txt === 'conferido' || txt === 'conferido:') {
                    // Procurar MUI Select no container pai
                    const container = lbl.closest('.sc-iCmkLe, .MuiFormControl-root, div') || lbl.parentElement;
                    if (!container) continue;
                    const muiSelect = container.querySelector('[class*="MuiSelect"], [role="combobox"], [role="button"][aria-haspopup="listbox"]');
                    if (muiSelect) return { type: 'mui-select', found: true };
                    // Procurar qualquer select-like no mesmo container
                    const anySelect = container.querySelector('select, [class*="Select"], [class*="select"]');
                    if (anySelect) return { type: 'generic-select', tag: anySelect.tagName };
                }
            }

            // C) Input com id/name conferido
            const input = document.querySelector('#conferido, [name="conferido"], [name*="conferido"]');
            if (input) return { type: 'input', tag: input.tagName, id: input.id };

            // D) Qualquer elemento com texto "Conferido" que tenha um dropdown/select proximo
            const body = document.body.innerHTML.toLowerCase();
            if (body.includes('conferido') && (body.includes('muiselect') || body.includes('mui-select'))) {
                return { type: 'mui-in-page' };
            }

            return { type: 'not-found' };
        }
    """)
    log(f"  Campo Conferido detectado: {field_info}")

    if field_info.get('type') == 'not-found':
        log("  Campo 'Conferido' nao encontrado na pagina. Pode nao estar disponivel nesta sessao.")
        log("  Prosseguindo sem alterar Conferido (interceptor de rede sera usado como fallback).")
        return

    # ── Passo 2: Tentar via JS direto (select nativo) ──
    if field_info.get('type') == 'native-select':
        js_res = page.evaluate("""
            () => {
                const norm = (v) => (v || '').toString().normalize('NFD')
                    .replace(/[\\u0300-\\u036f]/g, '').toLowerCase().trim();
                const selects = Array.from(document.querySelectorAll('select'));
                for (const sel of selects) {
                    const ref = norm(`${sel.id || ''} ${sel.name || ''} ${sel.getAttribute('aria-label') || ''}`);
                    if (ref.includes('conferido')) {
                        const opts = Array.from(sel.options || []);
                        const opt = opts.find(o => norm(o.textContent) === 'sim' || norm(o.value) === 'sim' || norm(o.value) === 's' || norm(o.value) === '1');
                        if (opt) {
                            sel.value = opt.value;
                            sel.dispatchEvent(new Event('input', { bubbles: true }));
                            sel.dispatchEvent(new Event('change', { bubbles: true }));
                            return `native-ok:${opt.value}`;
                        }
                    }
                }
                return 'native-fail';
            }
        """)
        log(f"  Resultado select nativo: {js_res}")
        page.wait_for_timeout(500)
        if verify_conferido_sim(page):
            log("  Conferido = Sim confirmado (select nativo).")
            return

    # ── Passo 3: Tentar via clique MUI Select ──
    # MUI Select: clicar no elemento que contem "Conferido" label para abrir o dropdown
    log("  Tentando selecionar 'Sim' via clique MUI Select...")
    try:
        # Estrategia A: Clicar no MUI Select proximo ao label "Conferido"
        # Procurar o container que tem label "Conferido" e clicar no select dentro dele
        clicked = page.evaluate("""
            () => {
                const norm = (v) => (v || '').toString().normalize('NFD')
                    .replace(/[\\u0300-\\u036f]/g, '').toLowerCase().trim();
                // Procurar label/span com texto "Conferido"
                const labels = Array.from(document.querySelectorAll('label, span, div, p, legend'));
                for (const lbl of labels) {
                    if (norm(lbl.textContent) !== 'conferido') continue;
                    // Subir ate o container e procurar o elemento clicavel do MUI Select
                    let container = lbl.parentElement;
                    for (let i = 0; i < 5 && container; i++) {
                        const selectEl = container.querySelector(
                            '[class*="MuiSelect-select"], [role="button"][aria-haspopup="listbox"], ' +
                            '[class*="MuiInputBase-root"], [class*="MuiSelect-root"], ' +
                            'div[tabindex="0"][role="button"]'
                        );
                        if (selectEl) {
                            selectEl.click();
                            return 'clicked-mui-select';
                        }
                        container = container.parentElement;
                    }
                }
                return 'no-mui-select-found';
            }
        """)
        log(f"  Clique MUI: {clicked}")

        if clicked == 'clicked-mui-select':
            page.wait_for_timeout(800)
            # Agora o menu popup deve estar aberto - clicar em "Sim"
            try:
                sim_option = page.locator(
                    "li:has-text('Sim'), [role='option']:has-text('Sim'), "
                    ".MuiMenuItem-root:has-text('Sim'), [data-value='Sim'], [data-value='sim'], [data-value='true']"
                ).first
                sim_option.click(timeout=3000)
                log("  Opcao 'Sim' clicada no menu MUI.")
                page.wait_for_timeout(500)
            except Exception as e:
                log(f"  Falha ao clicar opcao 'Sim' no menu: {e}")
                # Tentar fechar o menu com Escape
                page.keyboard.press("Escape")
        else:
            # Estrategia B: Tentar clicar diretamente em qualquer MUI Select visivel
            # que esteja mostrando "Não" (valor padrao)
            try:
                nao_el = page.locator("text=Não").first
                if nao_el.is_visible(timeout=1000):
                    # Verificar se esta dentro de um MUI Select (nao no texto explicativo)
                    parent_cls = page.evaluate("""
                        () => {
                            const els = Array.from(document.querySelectorAll('*'));
                            for (const el of els) {
                                if (el.textContent?.trim() === 'Não' && el.closest('[class*="MuiSelect"]')) {
                                    return el.closest('[class*="MuiSelect"]').className;
                                }
                            }
                            return null;
                        }
                    """)
                    if parent_cls:
                        nao_el.click(timeout=2000)
                        page.wait_for_timeout(800)
                        page.locator("li:has-text('Sim'), [role='option']:has-text('Sim')").first.click(timeout=3000)
                        log("  Opcao 'Sim' selecionada via clique em 'Nao' -> 'Sim'.")
            except Exception:
                pass

    except Exception as e:
        log(f"  Erro ao tentar MUI Select: {e}")

    # ── Passo 4: Tentar via input#conferido como fallback ──
    try:
        inp = page.query_selector('#conferido, [name="conferido"]')
        if inp:
            inp.fill('Sim')
            inp.dispatch_event('input')
            inp.dispatch_event('change')
            page.keyboard.press('Enter')
            log("  Valor 'Sim' forcado via input#conferido.")
            page.wait_for_timeout(500)
    except Exception:
        pass

    # ── Verificacao final ──
    if verify_conferido_sim(page):
        log("  Conferido = Sim confirmado com sucesso!")
    else:
        log("  AVISO: Nao foi possivel confirmar 'Conferido=Sim'. Interceptor de rede sera usado como fallback.")

def _intercept_conferido(route):
    """Intercepta requisicoes POST para a API de integracao e forca conferido=true."""
    request = route.request
    if request.method == "POST" and "integracoes" in request.url:
        try:
            body = request.post_data
            if body and "conferido" in body:
                data = json.loads(body)
                if "filters" in data and "conferido" in data["filters"]:
                    old_val = data["filters"]["conferido"]
                    data["filters"]["conferido"] = True
                    new_body = json.dumps(data)
                    log(f"  [INTERCEPT] conferido: {old_val} -> True em {request.url}")
                    route.continue_(post_data=new_body)
                    return
        except Exception as e:
            log(f"  [INTERCEPT] erro ao modificar body: {e}")
    route.continue_()


def conferencia_geral(page, date_str):
    """
    Fallback/verificacao pos-importacao:
    Rotina -> Conferencia Geral -> pesquisa data -> confere todos pendentes.
    Retorna numero de pacientes conferidos (0 = todos ja estavam conferidos).
    Protecoes: timeout curto por operacao, max 50 iteracoes, deteccao de sem-progresso.
    """
    global BASE_URL
    log(f"[Conferencia Geral] Verificando pendencias para data {date_str}...")

    def _wait_page(ms=1500):
        """Aguarda pagina com timeout curto e seguro."""
        try:
            page.wait_for_load_state("networkidle", timeout=6000)
        except Exception:
            pass
        page.wait_for_timeout(ms)

    # ── 1) Navegar para Conferencia Geral via menu Rotina ──
    try:
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=20000)
        _wait_page(1500)
        hover_and_click(page, "Rotina", "Conferência Geral")
        _wait_page(2000)
        log("[Conferencia Geral] Navegado: Rotina -> Conferencia Geral")
    except Exception as e:
        log(f"[Conferencia Geral] ERRO ao navegar: {e}. Pulando conferencia.")
        return 0

    # ── 2) Preencher datas ──
    try:
        r0 = set_react_date(page, 0, date_str)
        r1 = set_react_date(page, 1, date_str)
        log(f"[Conferencia Geral] Datas definidas: {r0} | {r1}")
        page.wait_for_timeout(1000)
    except Exception as e:
        log(f"[Conferencia Geral] ERRO ao preencher datas: {e}")
        return 0

    # ── 3) Clicar Pesquisar ──
    try:
        safe_click(page, [
            "button:has-text('PESQUISAR')",
            "button:has-text('Pesquisar')",
            "input[value*=PESQUISAR i]",
            "input[value*=Pesquisar i]",
        ])
        log("[Conferencia Geral] Pesquisar clicado.")
        _wait_page(2000)
    except Exception as e:
        log(f"[Conferencia Geral] ERRO ao pesquisar: {e}")
        return 0

    # ── Helper: contar linhas de paciente na tabela ──
    def contar_pacientes():
        try:
            rows = page.locator("table tr").all()
            # Descontar header (primeira linha)
            return max(0, len(rows) - 1)
        except Exception:
            return 0

    # ── Helper: detectar lista vazia ──
    def lista_vazia():
        txt = (page.evaluate("() => document.body.innerText || ''") or "").lower()
        if any(k in txt for k in ["nenhum", "sem resultado", "0 resultado",
                                   "nenhum paciente", "nenhum registro"]):
            return True
        return contar_pacientes() == 0

    # ── 4) Lista vazia => tudo ja conferido ──
    if lista_vazia():
        log("[Conferencia Geral] Lista vazia - todos ja conferidos na importacao!")
        return 0

    qtd_inicial = contar_pacientes()
    log(f"[Conferencia Geral] {qtd_inicial} paciente(s) pendente(s) encontrado(s).")

    # ── 5) Loop: duplo clique -> checkbox -> Conferir ──
    total_conferidos = 0
    checkbox_marcado = False
    qtd_anterior = qtd_inicial

    for iteracao in range(50):  # max 50 pacientes por seguranca
        if lista_vazia():
            log(f"[Conferencia Geral] Lista esvaziada! Total conferidos: {total_conferidos}")
            break

        # Deteccao de sem-progresso: se o numero de linhas nao mudou apos Conferir, parar
        qtd_atual = contar_pacientes()
        if iteracao > 0 and qtd_atual >= qtd_anterior:
            log(f"[Conferencia Geral] Sem progresso (linhas: {qtd_anterior} -> {qtd_atual}). Encerrando.")
            break
        qtd_anterior = qtd_atual

        # Duplo clique no primeiro paciente (segunda linha da tabela = apos header)
        try:
            todas = page.locator("table tr").all()
            if len(todas) < 2:
                log("[Conferencia Geral] Tabela sem linhas de paciente.")
                break
            linha = todas[1]  # primeira linha de dados (indice 1 = apos header)
            linha.dblclick(timeout=5000)
            log(f"[Conferencia Geral] Duplo clique paciente #{iteracao + 1}")
            _wait_page(1500)
        except Exception as e:
            log(f"[Conferencia Geral] ERRO duplo clique: {e}")
            break

        # Marcar checkbox "Ir para o proximo paciente" (somente primeira vez)
        if not checkbox_marcado:
            try:
                chk = None
                for loc in page.locator("input[type=checkbox]").all():
                    try:
                        parent_txt = (loc.evaluate(
                            "el => (el.closest('label') || el.parentElement || el).innerText || ''"
                        ) or "").lower()
                        if "pr" in parent_txt and "ximo" in parent_txt:
                            chk = loc
                            break
                    except Exception:
                        continue

                if not chk:
                    try:
                        label = page.get_by_text("próximo paciente", exact=False).first
                        if label.count() > 0:
                            chk = label.locator("..").locator("input[type=checkbox]").first
                    except Exception:
                        pass

                if chk and chk.count() > 0:
                    if not chk.is_checked():
                        chk.check()
                        log("[Conferencia Geral] Checkbox 'proximo paciente' marcado.")
                    else:
                        log("[Conferencia Geral] Checkbox ja estava marcado.")
                    checkbox_marcado = True
                else:
                    log("[Conferencia Geral] Checkbox nao encontrado - prosseguindo.")
                    checkbox_marcado = True
            except Exception as e:
                log(f"[Conferencia Geral] Aviso checkbox: {e}")
                checkbox_marcado = True

        # Clicar Conferir
        try:
            safe_click(page, [
                "button:has-text('CONFERIR')",
                "button:has-text('Conferir')",
                "input[value*=CONFERIR i]",
                "input[value*=Conferir i]",
            ])
            total_conferidos += 1
            log(f"[Conferencia Geral] Conferir #{total_conferidos} clicado.")
            _wait_page(1000)
        except Exception as e:
            log(f"[Conferencia Geral] ERRO ao clicar Conferir: {e}")
            break

    log(f"[Conferencia Geral] Finalizado. Conferidos como fallback: {total_conferidos}")
    return total_conferidos


def import_cda(page, date_str):
    global BASE_URL
    log(f"Navegando: Integracoes -> Retorno WebService CDA (data {date_str})")
    # Navegar diretamente para a URL da pagina CDA
    cda_url = BASE_URL.rstrip('/') + '/wsRetornoIntegracaoAPI.php?destino=CDA'
    page.goto(cda_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_load_state("networkidle", timeout=30000)
    page.wait_for_timeout(2000)

    # Preencher Data Inicio e Data Fim via React nativeInputValueSetter
    r0 = set_react_date(page, 0, date_str)
    r1 = set_react_date(page, 1, date_str)
    log(f"  Datas definidas: inicio={r0}, fim={r1}")
    page.wait_for_timeout(1000)

    # Setar campo Conferido para "Sim" na UI antes de importar
    set_conferido_sim(page)

    # Registrar interceptacao como fallback para forcar conferido=true na API
    page.route("**/integracoes/**", _intercept_conferido)
    log("  Route interceptor registrado para forcar conferido=true")

    safe_click(page, [
        "button:has-text('IMPORTAR')", "input[value*=IMPORTAR i]",
        "button:has-text('Importar')",
    ])
    log(f"IMPORTAR clicado. Aguardando conclusao do progresso...")
    wait_for_import_complete(page, max_wait_s=120)

    # Remover interceptacao apos uso para evitar duplicacao em proximas chamadas
    try:
        page.unroute("**/integracoes/**", _intercept_conferido)
    except Exception:
        pass


def parse_import_result(page):
    """
    Retorna dict: {sem_exames: bool, statuses: [str], pacientes: [str]}
    Procura por 'Sem exames' e analisa linhas com Status sucesso/info.
    Suporta tanto tabelas HTML quanto conteudo React renderizado em body text.
    """
    body_html = page.content().lower()
    body_text = page.evaluate("() => document.body.innerText || ''")
    body_text_low = body_text.lower()

    sem = "sem exames" in body_html or "sem exames" in body_text_low

    statuses = []
    pacientes = []

    # Tentar via tabela HTML primeiro
    rows = page.locator("table tr").all()
    if rows:
        for r in rows:
            try:
                txt = (r.inner_text() or "").strip()
                if not txt:
                    continue
                low = txt.lower()
                status = None
                if "sucesso" in low:
                    status = "sucesso"
                elif "info" in low:
                    status = "info"
                if status:
                    statuses.append(status)
                    cells = r.locator("td").all()
                    nome = ""
                    if cells:
                        try:
                            nome = (cells[0].inner_text() or "").strip()
                        except Exception:
                            nome = ""
                    pacientes.append(nome or txt[:60])
            except Exception:
                continue

    # Se nao encontrou via tabela, parsear body text (React)
    if not statuses:
        import re
        # Linhas do tipo: "0003251\tGABRIEL SARAIVA LEAO\t\tinfo"
        for line in body_text.splitlines():
            line = line.strip()
            if not line:
                continue
            low = line.lower()
            status = None
            if "sucesso" in low:
                status = "sucesso"
            elif "\tinfo" in low or line.endswith("\tinfo") or "\tinfo\t" in low:
                status = "info"
            elif low.endswith("info") and "\t" in line:
                status = "info"
            if status:
                statuses.append(status)
                parts = line.split("\t")
                nome = parts[1].strip() if len(parts) > 1 else line[:60]
                pacientes.append(nome)

    return {"sem_exames": sem, "statuses": statuses, "pacientes": pacientes}


def processar_periodo(page, rotulo: str, data_alvo: str) -> dict:
    """
    Executa importacao + analise para uma data.
    Sempre executa Conferencia Geral apos importacao como verificacao/fallback.
    Retorna dict com import_result, conferencia_fallback e conclusao.
    """
    log(f"--- INICIO PERIODO {rotulo} (data {data_alvo}) ---")
    periodo = {
        "rotulo": rotulo,
        "data_alvo": data_alvo,
        "import_result": None,
        "conferencia_fallback": 0,
        "conclusao": "",
    }
    try:
        import_cda(page, data_alvo)
        res = parse_import_result(page)
        periodo["import_result"] = res
        log(f"[{rotulo}] Resultado importacao: sem_exames={res['sem_exames']} "
            f"pacientes={len(res['pacientes'])} statuses={res['statuses']}")

        if res["sem_exames"]:
            periodo["conclusao"] = "Sem exames - periodo encerrado."
        else:
            # Sempre executa Conferencia Geral como verificacao pos-importacao.
            # Se Conferido=Sim funcionou na importacao, a lista estara vazia e retorna 0.
            # Se ficou pendente, confere todos automaticamente.
            log(f"[{rotulo}] Iniciando Conferencia Geral (verificacao/fallback)...")
            conferidos = conferencia_geral(page, data_alvo)
            periodo["conferencia_fallback"] = conferidos

            if res["statuses"] and all(s == "info" for s in res["statuses"]):
                periodo["conclusao"] = (
                    f"Todos com status 'info'. "
                    f"Conferencia fallback: {conferidos} paciente(s)."
                )
            elif any(s == "sucesso" for s in res["statuses"]):
                periodo["conclusao"] = (
                    f"Status 'sucesso' identificado. "
                    f"Conferencia fallback: {conferidos} paciente(s)."
                )
            else:
                periodo["conclusao"] = (
                    f"Periodo encerrado. "
                    f"Conferencia fallback: {conferidos} paciente(s)."
                )

    except PWTimeout as e:
        periodo["conclusao"] = f"FALHA timeout: {e}"
        log(f"[{rotulo}] ERRO timeout: {e}")
    except Exception as e:
        periodo["conclusao"] = f"FALHA: {e}"
        log(f"[{rotulo}] ERRO: {e}\n{traceback.format_exc()}")
    log(f"--- FIM PERIODO {rotulo}: {periodo['conclusao']} ---")
    return periodo


def imprimir_relatorio_periodo(p: dict):
    log(f"-- {p['rotulo']} (data {p['data_alvo']}) --")
    ir = p.get("import_result") or {}
    log(f"   Importacao - sem_exames: {ir.get('sem_exames')}")
    log(f"   Importacao - total pacientes: {len(ir.get('pacientes', []))}")
    log(f"   Importacao - statuses: {ir.get('statuses', [])}")
    log(f"   Importacao - pacientes: {ir.get('pacientes', [])}")
    conf = p.get("conferencia_fallback", 0)
    if conf > 0:
        log(f"   Conferencia Geral fallback: {conf} paciente(s) conferidos")
    else:
        log("   Conferencia Geral fallback: nenhum pendente (ja conferidos na importacao)")
    log(f"   Conclusao: {p.get('conclusao', '')}")


def _telegram_send_raw(text: str, max_retries: int = 3):
    """Envia mensagem para Telegram com retries. Retorna True se OK."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log("AVISO Telegram: token ou chat_id ausente, pulando envio.")
        return False

    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
    }).encode("utf-8")

    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(api_url, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                if result.get("ok"):
                    log(f"Telegram: mensagem enviada com sucesso (tentativa {attempt}).")
                    return True
                else:
                    log(f"Telegram: resposta inesperada (tentativa {attempt}): {result}")
        except Exception as e:
            log(f"Telegram: falha tentativa {attempt}/{max_retries}: {e}")
            if attempt < max_retries:
                time.sleep(2 * attempt)  # backoff: 2s, 4s
    log("ERRO Telegram: todas as tentativas falharam.")
    return False


def send_telegram(relatorio: dict, inicio, fim, prefixo=""):
    """Envia resumo da execucao via Telegram Bot API. Sempre notifica."""
    data_str = fim.strftime("%d/%m/%Y")
    hora_str = fim.strftime("%H:%M")
    duracao = int((fim - inicio).total_seconds())

    total_pac = sum(
        len((per.get("import_result") or {}).get("pacientes", []))
        for per in relatorio.get("periodos", [])
    )

    header = f"{prefixo} " if prefixo else ""
    lines = [
        f"🤖 {header}WorkLab - Execucao Concluida",
        f"📅 {data_str} {hora_str}",
        f"⚡ Executado pelo Claude",
        "",
    ]

    if total_pac == 0:
        lines.append("ℹ️ Nenhum paciente importado nesta execucao.")
    else:
        for per in relatorio.get("periodos", []):
            ir = per.get("import_result") or {}
            qtd = len(ir.get("pacientes", []))
            statuses = ir.get("statuses", [])
            sem = ir.get("sem_exames", False)

            if qtd == 0 and not sem:
                continue

            lines.append(f"📊 PERIODO {per['rotulo'].upper()}")
            if sem:
                lines.append("- Sem exames encontrados")
        else:
            lines.append(f"✅ {qtd} pacientes importados")
        if statuses:
            contagem = {}
            for s in statuses:
                contagem[s] = contagem.get(s, 0) + 1
            status_str = ", ".join(f"{k}: {v}" for k, v in contagem.items())
            lines.append(f"   Status: {status_str}")
        conf = per.get("conferencia_fallback", 0)
        if conf > 0:
            lines.append(f"   🔄 Conferencia fallback: {conf} paciente(s)")
        lines.append("")

    lines.append(f"⏱️ Duracao: {duracao}s")

    texto = "\n".join(lines)
    _telegram_send_raw(texto)


def send_telegram_error(error_msg: str, prefixo=""):
    """Envia notificacao de ERRO critico via Telegram."""
    header = f"{prefixo} " if prefixo else ""
    now = datetime.now(SP_TZ)
    texto = (
        f"🚨 {header}WorkLab - ERRO NA EXECUCAO\n"
        f"⚡ Executado pelo Claude\n"
        f"📅 {now.strftime('%d/%m/%Y %H:%M')}\n\n"
        f"❌ {error_msg[:500]}"
    )
    _telegram_send_raw(texto)


def update_dashboard(relatorio: dict, inicio, fim):
    """Append execution record to shared JSON for dashboard consumption."""
    dashboard_path = "/home/ubuntu/shared/dashboard_data.json"
    try:
        records = []
        if os.path.exists(dashboard_path):
            with open(dashboard_path, "r", encoding="utf-8") as f:
                records = json.load(f)
        # Keep last 200 records
        if len(records) >= 200:
            records = records[-199:]

        total_pac = 0
        periodos_resumo = []
        tem_falha = False
        telegram_ok = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

        for per in relatorio.get("periodos", []):
            ir = per.get("import_result") or {}
            qtd = len(ir.get("pacientes", []))
            total_pac += qtd
            sem = ir.get("sem_exames", False)
            conclusao = per.get("conclusao", "")
            if "FALHA" in conclusao:
                tem_falha = True
            periodos_resumo.append({
                "rotulo": per["rotulo"],
                "data_alvo": per.get("data_alvo", ""),
                "pacientes": qtd,
                "sem_exames": sem,
                "statuses": ir.get("statuses", []),
                "conclusao": conclusao,
            })

        record = {
            "inicio": inicio.isoformat(),
            "fim": fim.isoformat(),
            "duracao_s": int((fim - inicio).total_seconds()),
            "total_pacientes": total_pac,
            "status": "erro" if tem_falha else "ok",
            "telegram_enviado": telegram_ok,
            "periodos": periodos_resumo,
        }
        records.append(record)
        with open(dashboard_path, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        log(f"Dashboard atualizado: {dashboard_path} ({len(records)} registros)")
    except Exception as e:
        log(f"AVISO: falha ao atualizar dashboard: {e}")


def main():
    inicio = datetime.now(SP_TZ)
    log(f"=== INICIO EXECUCAO WorkLab @ {inicio.strftime('%Y-%m-%d %H:%M:%S')} ===")
    data_ontem = date_str_for(-1)
    data_hoje = date_str_for(0)
    log(f"Datas alvo: D-1={data_ontem} | D={data_hoje}")

    relatorio = {
        "inicio": inicio.isoformat(),
        "data_ontem": data_ontem,
        "data_hoje": data_hoje,
        "periodos": [],
    }

    if not (LAB_ID and USER and PASSWORD):
        log("ERRO: credenciais ausentes em /home/ubuntu/shared/.env")
        sys.exit(2)

    with sync_playwright() as p:
        log(f"Modo navegador: {'headless' if WORKLAB_HEADLESS else 'nao-headless'}")
        browser = p.chromium.launch(headless=WORKLAB_HEADLESS, args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 1366, "height": 900})
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            login(page)

            # 1) Dia anterior (D-1)
            relatorio["periodos"].append(
                processar_periodo(page, "D-1 (dia anterior)", data_ontem)
            )

            # 2) Dia atual (D)
            relatorio["periodos"].append(
                processar_periodo(page, "D (dia atual)", data_hoje)
            )
        except PWTimeout as e:
            log(f"ERRO timeout no fluxo principal: {e}")
            relatorio["periodos"].append({
                "rotulo": "GLOBAL",
                "data_alvo": "-",
                "import_result": None,
                "conclusao": f"FALHA timeout global: {e}",
            })
        except Exception as e:
            log(f"ERRO no fluxo principal: {e}\n{traceback.format_exc()}")
            relatorio["periodos"].append({
                "rotulo": "GLOBAL",
                "data_alvo": "-",
                "import_result": None,
                "conclusao": f"FALHA: {e}",
            })
        finally:
            try:
                context.close()
                browser.close()
            except Exception:
                pass

    fim = datetime.now(SP_TZ)
    log("=== RELATORIO ===")
    log(f"Inicio: {relatorio['inicio']}")
    log(f"Fim:    {fim.isoformat()}")
    log(f"Data D-1: {relatorio['data_ontem']} | Data D: {relatorio['data_hoje']}")
    for p in relatorio["periodos"]:
        imprimir_relatorio_periodo(p)
    log("=== FIM EXECUCAO ===")

    # Salvar relatorio em arquivo para envio por email
    duracao = fim - inicio
    minutos = int(duracao.total_seconds() // 60)
    segundos = int(duracao.total_seconds() % 60)
    report_path = "/home/ubuntu/worklab_report.md"
    try:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(f"# Relatório WorkLab - Execução {fim.strftime('%d/%m/%Y %H:%M')}\n\n")
            f.write(f"**Data/hora da execução:** {fim.strftime('%d/%m/%Y %H:%M:%S')} (Brasília)\n\n")
            f.write(f"**Duração total:** {minutos}min {segundos}s\n\n")
            f.write(f"**Créditos utilizados:** 0 (script determinístico)\n\n")
            f.write("---\n\n")
            for per in relatorio["periodos"]:
                ir = per.get("import_result") or {}
                qtd = len(ir.get("pacientes", []))
                statuses = ir.get("statuses", [])
                sem = ir.get("sem_exames", False)
                f.write(f"## {per['rotulo']}\n\n")
                f.write(f"- **Data processada:** {per['data_alvo']}\n")
                f.write(f"- **Quantidade de pacientes:** {qtd}\n")
                if sem:
                    f.write(f"- **Status:** Sem exames encontrados\n")
                elif statuses:
                    contagem = {}
                    for s in statuses:
                        contagem[s] = contagem.get(s, 0) + 1
                    status_str = ", ".join(f"{k}: {v}" for k, v in contagem.items())
                    f.write(f"- **Status:** {status_str}\n")
                else:
                    f.write(f"- **Status:** Nenhum resultado retornado\n")
                f.write(f"- **Conclusão:** {per.get('conclusao', 'N/A')}\n\n")
            f.write("---\n\n")
            f.write("## Resumo\n\n")
            total_pac = sum(len((p.get("import_result") or {}).get("pacientes", [])) for p in relatorio["periodos"])
            f.write(f"Total de pacientes processados (ambos períodos): **{total_pac}**\n\n")
            f.write(f"Execução concluída com {'sucesso' if all('FALHA' not in (p.get('conclusao','')) for p in relatorio['periodos']) else 'erros — verificar detalhes acima'}.\n")
        log(f"Relatorio salvo em {report_path}")
    except Exception as e:
        log(f"AVISO: falha ao salvar relatorio: {e}")

    # Atualizar dashboard JSON
    update_dashboard(relatorio, inicio, fim)

    # Enviar notificacao Telegram
    prefixo = os.getenv("WORKLAB_PREFIXO", "")
    send_telegram(relatorio, inicio, fim, prefixo=prefixo)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        prefixo = os.getenv("WORKLAB_PREFIXO", "")
        send_telegram_error(str(e), prefixo=prefixo)
        raise
