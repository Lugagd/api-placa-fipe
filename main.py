import asyncio
import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CHROME_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
BASE_URL = "https://placafipe.com/placa"

# Variável global para manter o browser aberto entre requisições (Warm Start)
playwright_instance = None
browser_instance = None

async def get_browser():
    global playwright_instance, browser_instance
    if not browser_instance:
        playwright_instance = await async_playwright().start()
        browser_instance = await playwright_instance.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox", 
                "--disable-dev-shm-usage", 
                "--disable-gpu",
                "--disable-setuid-sandbox",
                "--no-first-run",
                "--no-zygote",
                "--single-process" # Melhora performance em containers pequenos
            ]
        )
    return browser_instance

@app.get("/consultar/{placa}")
async def rota_consultar(placa: str):
    browser = await get_browser()
    # Criamos um novo contexto por aba para isolar cookies/cache
    context = await browser.new_context(user_agent=CHROME_USER_AGENT)
    page = await context.new_page()

    # BLOQUEIO AGRESSIVO DE ANÚNCIOS E SCRIPTS LENTOS
    async def block_agressive(route):
        url = route.request.url.lower()
        bad_words = ["google-analytics", "doubleclick", "facebook", "fontawesome", "adsbygoogle", "adservice"]
        if any(word in url for word in bad_words) or url.endswith((".png", ".jpg", ".gif", ".woff", ".woff2")):
            return await route.abort()
        return await route.continue_()

    await page.route("**/*", block_agressive)

    try:
        placa_limpa = placa.upper().replace("-", "").strip()
        # Navegação 'commit' é a mais rápida possível
        await page.goto(f"{BASE_URL}/{placa_limpa}", wait_until="commit", timeout=15000)

        # Espera curta por um elemento que prova que o conteúdo carregou
        try:
            await page.wait_for_selector("table.fipeTablePriceDetail", timeout=7000)
        except:
            if await page.query_selector("text='Placa não encontrada'"):
                return {"status": "erro", "mensagem": "Placa não encontrada."}
            return {"status": "erro", "mensagem": "Erro de carregamento rápido."}

        # EXTRAÇÃO DE DADOS (Otimizada com evaluate para rodar direto no JS do browser)
        dados = await page.evaluate("""() => {
            const extrairTabela = (selector) => {
                const rows = document.querySelectorAll(`${selector} tr`);
                return Array.from(rows).map(row => {
                    const cols = row.querySelectorAll('td');
                    return Array.from(cols).map(c => c.innerText.trim());
                });
            };

            const detalhesRaw = extrairTabela('table.fipeTablePriceDetail');
            const detalhes = {};
            detalhesRaw.forEach(r => { if(r.length === 2) detalhes[r[0].replace(':','')] = r[1]; });

            const fipeRaw = extrairTabela('table.fipe-desktop') || extrairTabela('table.fipe-mobile');
            const fipe = fipeRaw.filter(r => r.length >= 3).map(r => ({ codigo: r[0], modelo: r[1], valor: r[2] }));

            return { veiculo: detalhes, fipe: fipe };
        }""")

        return {
            "placa": placa_limpa,
            "veiculo": dados['veiculo'],
            "fipe": dados['fipe'],
            "status": "sucesso"
        }

    except Exception as e:
        return {"status": "erro", "mensagem": str(e)}
    finally:
        await context.close() # Fecha apenas a aba, mantém o browser vivo

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
