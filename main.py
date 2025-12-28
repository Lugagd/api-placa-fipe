import asyncio
import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright, Browser

# Configuração de Logs
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("scraper")

# Variável global para reutilizar o navegador
browser: Browser = None
playwright_instance = None

# --- OTIMIZAÇÃO 1: Lifespan (Ciclo de Vida) ---
# Abre o navegador UMA vez quando a API inicia, e não a cada request.
@asynccontextmanager
async def lifespan(app: FastAPI):
    global browser, playwright_instance
    logger.info("Iniciando Playwright e Browser...")
    
    playwright_instance = await async_playwright().start()
    
    # Args para evitar detecção e rodar liso em container (Sandbox desativado)
    browser = await playwright_instance.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-blink-features=AutomationControlled" # Ajuda no Stealth
        ]
    )
    yield
    # Limpeza ao desligar
    logger.info("Fechando Browser...")
    if browser:
        await browser.close()
    if playwright_instance:
        await playwright_instance.stop()

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_URL = "https://placafipe.com/placa"

# Rota de health check para o Cloud Run não matar o container
@app.get("/")
def read_root():
    return {"status": "online", "browser_ready": browser is not None}

@app.get("/consultar/{placa}")
async def rota_consultar(placa: str):
    # Retry logic simples: Tenta 2 vezes caso falhe na primeira (comum em scraping)
    for tentativa in range(2):
        try:
            return await consultar_placa(placa)
        except Exception as e:
            logger.error(f"Erro na tentativa {tentativa + 1}: {e}")
            if tentativa == 1: # Se falhou na última tentativa
                 raise HTTPException(status_code=500, detail="Erro interno ao processar a placa. Tente novamente.")
            await asyncio.sleep(0.5)

async def consultar_placa(placa: str):
    if not browser:
        raise HTTPException(status_code=500, detail="Browser não inicializado.")

    placa_limpa = placa.upper().replace("-", "").strip()
    url_alvo = f"{BASE_URL}/{placa_limpa}"

    # Cria um contexto isolado (rápido e leve)
    context = await browser.new_context(
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
        viewport={'width': 1280, 'height': 720},
        device_scale_factor=1,
    )
    
    # Scripts para "enganar" detecção de bot simples
    await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

    page = await context.new_page()

    try:
        # --- OTIMIZAÇÃO 2: Bloqueio Agressivo ---
        # Bloqueia Imagens, Fontes E CSS (Stylesheets). 
        # A maioria dos dados está no HTML cru, não precisamos de estilo.
        await page.route("**/*.{png,jpg,jpeg,gif,svg,woff,woff2,otf,css,stylesheet}", lambda route: route.abort())
        
        # Bloqueia Google Ads e Analytics para não travar o carregamento
        await page.route("**/*googlesyndication*", lambda route: route.abort())
        await page.route("**/*google-analytics*", lambda route: route.abort())

        # --- OTIMIZAÇÃO 3: Wait Strategy ---
        # "domcontentloaded" é MUITO mais rápido que "networkidle". 
        response = await page.goto(url_alvo, wait_until="domcontentloaded", timeout=15000)

        if response.status == 404:
             return {"status": "erro", "mensagem": "Placa não encontrada no servidor."}

        # Verifica se caiu em página de erro ou se a tabela existe
        # Reduzimos o timeout para falhar rápido e tentar retry se necessário
        try:
            # Espera a tabela OU o texto de erro
            await page.wait_for_selector("table.fipeTablePriceDetail, text='Placa não encontrada'", timeout=5000)
        except:
             return {"status": "erro", "mensagem": "Timeout: Elemento não carregou a tempo."}

        if await page.query_selector("text='Placa não encontrada'"):
             return {"status": "erro", "mensagem": "Placa não encontrada."}

        # 1. Extração de Detalhes
        detalhes = {}
        # Usamos evaluate para rodar JS direto no browser (mais rápido que múltiplos calls de Python->Browser)
        detalhes = await page.evaluate("""() => {
            const dados = {};
            document.querySelectorAll('table.fipeTablePriceDetail tr').forEach(tr => {
                const cols = tr.querySelectorAll('td');
                if(cols.length === 2) {
                    const chave = cols[0].innerText.replace(':', '').trim();
                    const valor = cols[1].innerText.trim();
                    dados[chave] = valor;
                }
            });
            return dados;
        }""")

        # 2. Extração FIPE (Lógica unificada Desktop/Mobile)
        valores_fipe = await page.evaluate("""() => {
            const rows = document.querySelectorAll('table.fipe-desktop tr, table.fipe-mobile tr');
            const lista = [];
            rows.forEach(tr => {
                const cols = tr.querySelectorAll('td');
                if(cols.length >= 3) {
                    lista.push({
                        codigo: cols[0].innerText.trim(),
                        modelo: cols[1].innerText.trim(),
                        valor: cols[2].innerText.trim()
                    });
                }
            });
            return lista;
        }""")

        # 3. Histórico IPVA
        historico_ipva = await page.evaluate("""() => {
            // Procura a tabela que contém o texto 'Ano IPVA'
            const tables = Array.from(document.querySelectorAll('table'));
            const targetTable = tables.find(t => t.innerText.includes('Ano IPVA'));
            const lista = [];
            if (targetTable) {
                targetTable.querySelectorAll('tr').forEach(tr => {
                    const cols = tr.querySelectorAll('td');
                    if(cols.length >= 3) {
                        const ano = cols[0].innerText.trim();
                        if (!isNaN(ano) && ano.length === 4) {
                            lista.push({
                                ano: ano,
                                valor_venal: cols[1].innerText.trim(),
                                valor_ipva: cols[2].innerText.trim()
                            });
                        }
                    }
                });
            }
            return lista;
        }""")

        return {
            "placa": placa_limpa,
            "veiculo": detalhes,
            "fipe": valores_fipe,
            "historico_ipva": historico_ipva,
            "status": "sucesso"
        }

    finally:
        # Fecha apenas a página/contexto, MANTENDO o browser aberto
        await context.close()

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    # Workers = 1 é recomendado quando se usa uma variável global async como o browser
    uvicorn.run(app, host="0.0.0.0", port=port)
