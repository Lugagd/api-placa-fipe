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

class BrowserManager:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.lock = asyncio.Lock() # Evita que duas requisições tentem criar o browser ao mesmo tempo

    async def get_browser(self):
        async with self.lock:
            if not self.browser or not self.browser.is_connected():
                if self.playwright:
                    try: await self.playwright.stop()
                    except: pass
                
                self.playwright = await async_playwright().start()
                self.browser = await self.playwright.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox", 
                        "--disable-dev-shm-usage", 
                        "--disable-gpu",
                        "--disable-setuid-sandbox"
                    ]
                )
            return self.browser

    async def close(self):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

browser_manager = BrowserManager()

@app.on_event("startup")
async def startup_event():
    # Pré-aquece o browser no início
    try:
        await browser_manager.get_browser()
    except Exception as e:
        print(f"Erro ao iniciar browser no startup: {e}")

@app.get("/consultar/{placa}")
async def rota_consultar(placa: str):
    placa_limpa = placa.upper().replace("-", "").strip()
    if not placa_limpa:
        raise HTTPException(status_code=400, detail="Placa inválida")

    # Tenta a consulta
    resultado = await consultar_placa(placa_limpa)
    
    if resultado.get("status") == "erro":
        msg = resultado.get("mensagem", "")
        if "não encontrada" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=500, detail=msg)
    
    return resultado

async def consultar_placa(placa: str):
    url_alvo = f"{BASE_URL}/{placa}"
    
    try:
        browser = await browser_manager.get_browser()
        # Contexto isolado para não misturar cookies/cache de buscas diferentes
        context = await browser.new_context(user_agent=CHROME_USER_AGENT)
        page = await context.new_page()

        # Bloqueio de tudo que não é essencial para o texto
        await page.route("**/*.{png,jpg,jpeg,gif,svg,css,woff,woff2,otf}", lambda route: route.abort())

        # Vai para a página - domcontentloaded é o gatilho mais rápido
        response = await page.goto(url_alvo, wait_until="domcontentloaded", timeout=12000)
        
        if response.status == 404:
            await context.close()
            return {"status": "erro", "mensagem": "Placa não encontrada."}

        # Aguarda o elemento chave ou mensagem de erro
        try:
            await page.wait_for_selector("table.fipeTablePriceDetail", timeout=6000)
        except:
            # Se não achou a tabela, checa se tem texto de erro na tela
            corpo = await page.content()
            if "não encontrada" in corpo.lower():
                await context.close()
                return {"status": "erro", "mensagem": "Placa não encontrada."}
            await context.close()
            return {"status": "erro", "mensagem": "Timeout ou Bloqueio pelo site."}

        # EXTRAÇÃO ULTRA-FAST VIA JS
        dados = await page.evaluate("""() => {
            const getTabela = (sel) => {
                const res = {};
                document.querySelectorAll(sel + " tr").forEach(r => {
                    const c = r.querySelectorAll("td");
                    if(c.length >= 2) res[c[0].innerText.replace(':','').trim()] = c[1].innerText.trim();
                });
                return res;
            };

            const getFipe = () => {
                return Array.from(document.querySelectorAll("table.fipe-desktop tr, table.fipe-mobile tr"))
                    .map(r => {
                        const c = r.querySelectorAll("td");
                        return c.length >= 3 ? {codigo: c[0].innerText.trim(), modelo: c[1].innerText.trim(), valor: c[2].innerText.trim()} : null;
                    }).filter(x => x);
            };

            return { veiculo: getTabela("table.fipeTablePriceDetail"), fipe: getFipe() };
        }""")

        await context.close()
        return {
            "placa": placa,
            "veiculo": dados["veiculo"],
            "fipe": dados["fipe"],
            "status": "sucesso"
        }

    except Exception as e:
        return {"status": "erro", "mensagem": f"Falha interna: {str(e)}"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
