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

playwright_manager = None
browser_instance = None

async def get_browser():
    """Mantém uma instância única do navegador aberta para economizar tempo de boot."""
    global playwright_manager, browser_instance
    if browser_instance is None:
        playwright_manager = await async_playwright().start()
        browser_instance = await playwright_manager.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox", 
                "--disable-dev-shm-usage", 
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled" 
            ]
        )
    return browser_instance

@app.on_event("shutdown")
async def shutdown_event():
    """Fecha o navegador quando a API desliga."""
    if browser_instance:
        await browser_instance.close()
    if playwright_manager:
        await playwright_manager.stop()

@app.get("/")
def read_root():
    return {"message": "API Otimizada - Cloud Run"}

@app.get("/consultar/{placa}")
async def rota_consultar(placa: str):
    resultado = await consultar_placa(placa)
    if resultado.get("status") == "erro":
        status_code = 404 if "não encontrada" in resultado.get("mensagem").lower() else 500
        raise HTTPException(status_code=status_code, detail=resultado.get("mensagem"))
    return resultado

async def consultar_placa(placa: str):
    placa_limpa = placa.upper().replace("-", "").strip()
    url_alvo = f"{BASE_URL}/{placa_limpa}"
    
    browser = await get_browser()
    
    context = await browser.new_context(
        user_agent=CHROME_USER_AGENT,
        viewport={'width': 1280, 'height': 720}
    )
    page = await context.new_page()

    await page.route("**/*", lambda route: 
        route.abort() if route.request.resource_type in ["image", "media", "font", "stylesheet"] 
        else route.continue_()
    )

    try:
        response = await page.goto(url_alvo, wait_until="domcontentloaded", timeout=15000)
        
        if response and response.status == 404:
            return {"status": "erro", "mensagem": "Placa não encontrada no servidor."}

        try:
            await page.wait_for_selector("table.fipeTablePriceDetail", timeout=6000)
        except:
            if await page.query_selector("text='Placa não encontrada'"):
                return {"status": "erro", "mensagem": "Placa não encontrada."}
            return {"status": "erro", "mensagem": "Timeout ou bloqueio pelo site."}

        dados = await page.evaluate("""() => {
            const extrairTabela = (selector) => {
                const rows = document.querySelectorAll(`${selector} tr`);
                let obj = {};
                rows.forEach(row => {
                    const cols = row.querySelectorAll('td');
                    if (cols.length >= 2) {
                        const chave = cols[0].innerText.replace(':', '').trim();
                        obj[chave] = cols[1].innerText.trim();
                    }
                });
                return obj;
            };

            const extrairFipe = () => {
                const rows = document.querySelectorAll('table.fipe-desktop tr, table.fipe-mobile tr');
                return Array.from(rows).slice(1).map(row => {
                    const cols = row.querySelectorAll('td');
                    return cols.length >= 3 ? {
                        codigo: cols[0].innerText.trim(),
                        modelo: cols[1].innerText.trim(),
                        valor: cols[2].innerText.trim()
                    } : null;
                }).filter(x => x);
            };

            return {
                veiculo: extrairTabela('table.fipeTablePriceDetail'),
                fipe: extrairFipe()
            };
        }""")

        return {
            "placa": placa_limpa,
            "veiculo": dados["veiculo"],
            "fipe": dados["fipe"],
            "status": "sucesso"
        }

    except Exception as e:
        return {"status": "erro", "mensagem": f"Erro: {str(e)}"}
    finally:
        await context.close()

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
