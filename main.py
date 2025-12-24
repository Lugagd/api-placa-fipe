import asyncio
from fastapi import FastAPI, HTTPException
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

app = FastAPI(title="API Consulta Placa FIPE")
CHROME_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
SITE_URL = "https://www.tabelafipebrasil.com/placa"

@app.get("/")
def read_root():
    return {"message": "API Online - Use /consultar/SUAPLACA"}

@app.get("/consultar/{placa}")
async def consultar_placa(placa: str):
    placa = placa.upper().replace("-", "") 
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--no-first-run",
                "--no-zygote",
                "--single-process" 
            ]
        )
        
        context = await browser.new_context(
            user_agent=CHROME_USER_AGENT,
            viewport={"width": 1280, "height": 800}
        )
        
        page = await context.new_page()
        
        try:
            await page.goto(SITE_URL, timeout=60000, wait_until="domcontentloaded")
          
            input_selector = 'input[type="text"]'
            await page.wait_for_selector(input_selector, state='visible', timeout=1000)
            await page.fill(input_selector, placa)
      
            button_selector = 'button:has-text("Pesquisa")'
            await page.click(button_selector)

            tabela_selector = 'table.fipeTablePriceDetail'
            await page.wait_for_selector(tabela_selector, timeout=1000)
            
            dados_extraidos = {}
            rows = await page.locator(f'{tabela_selector} tr').all()
            for row in rows:
                cols = await row.locator('td').all()
                if len(cols) == 2:
                    label = (await cols[0].inner_text()).replace(':', '').strip()
                    valor = (await cols[1].inner_text()).strip()
                    if label:
                        dados_extraidos[label] = valor

            fipe_lista = []
            fipe_rows = await page.locator('table.fipe-desktop tr').all()
            
            for i in range(1, len(fipe_rows)):
                cols = await fipe_rows[i].locator('td').all()
                if len(cols) >= 3:
                    fipe_lista.append({
                        "codigo_fipe": (await cols[0].inner_text()).strip(),
                        "modelo": (await cols[1].inner_text()).strip(),
                        "valor": (await cols[2].inner_text()).strip()
                    })

            return {
                "placa": placa,
                "detalhes": dados_extraidos,
                "valores_fipe": fipe_lista,
                "status": "sucesso"
            }

        except PlaywrightTimeoutError:
            raise HTTPException(status_code=504, detail="Tempo esgotado ao consultar o site. Tente novamente.")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")
        finally:
            await browser.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
