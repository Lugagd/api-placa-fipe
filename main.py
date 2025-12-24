import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

app = FastAPI()

# Configuração do CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CHROME_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
SITE_URL = "https://www.tabelafipebrasil.com/placa"

@app.get("/")
def read_root():
    return {"message": "API Online - Use /consultar/SUAPLACA"}

# --- ROTA QUE CONECTA O NAVEGADOR À FUNÇÃO ---
@app.get("/consultar/{placa}")
async def rota_consultar(placa: str):
    resultado = await consultar_placa(placa)
    if resultado.get("status") == "erro":
        raise HTTPException(status_code=500, detail=resultado.get("mensagem"))
    return resultado

# --- SUA FUNÇÃO DE CONSULTA (MANTIDA) ---
async def consultar_placa(placa: str):
    placa = placa.upper().replace("-", "").strip() 
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(user_agent=CHROME_USER_AGENT)
        page = await context.new_page()
        page.set_default_timeout(60000) 

        try:
            # wait_until="networkidle" ajuda a esperar o JS carregar
            await page.goto(SITE_URL, wait_until="networkidle", timeout=60000)
            
            input_selector = 'input[type="text"]'
            await page.wait_for_selector(input_selector)
            await page.fill(input_selector, placa)
            
            await page.click('button:has-text("Pesquisa")')
            
            # Espera a tabela estar visível para evitar erros de leitura
            tabela_selector = 'table.fipeTablePriceDetail'
            await page.wait_for_selector(tabela_selector, state="visible", timeout=60000)
            
            dados_extraidos = {}
            rows = await page.locator(f'{tabela_selector} tr').all()
            for row in rows:
                cols = await row.locator('td').all()
                if len(cols) >= 2:
                    label = (await cols[0].inner_text()).replace(':', '').strip()
                    valor = (await cols[1].inner_text()).strip()
                    if label:
                        dados_extraidos[label] = valor

            fipe_lista = []
            try:
                fipe_rows = await page.locator('table.fipe-desktop tr').all()
                for i in range(1, len(fipe_rows)):
                    cols = await fipe_rows[i].locator('td').all()
                    if len(cols) >= 3:
                        fipe_lista.append({
                            "modelo": await cols[1].inner_text(),
                            "valor": await cols[2].inner_text()
                        })
            except:
                pass 

            return {
                "placa": placa,
                "detalhes": dados_extraidos,
                "valores_fipe": fipe_lista,
                "status": "sucesso"
            }

        except Exception as e:
            return {"status": "erro", "mensagem": str(e)}
        finally:
            await browser.close()

if __name__ == "__main__":
    import uvicorn
    # No Cloud Run, a porta deve ser a mesma do painel (8000)
    uvicorn.run(app, host="0.0.0.0", port=8000)
