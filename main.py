import asyncio
import os
import random
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
        self.lock = asyncio.Lock()

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
    try:
        await browser_manager.get_browser()
    except Exception as e:
        print(f"Erro ao iniciar browser no startup: {e}")

@app.get("/consultar/{placa}")
async def rota_consultar(placa: str):
    placa_limpa = placa.upper().replace("-", "").strip()
    if not placa_limpa:
        raise HTTPException(status_code=400, detail="Placa inválida")

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
        # 1. Introduz um delay aleatório 
        await asyncio.sleep(random.uniform(1.0, 3.0))

        browser = await browser_manager.get_browser()
        context = await browser.new_context(user_agent=CHROME_USER_AGENT)
        page = await context.new_page()
        await page.route("**/*.{png,jpg,jpeg,gif,svg,css,woff,woff2,otf}", lambda route: route.abort())

        # 2. Navegação com timeout estendido
        response = await page.goto(url_alvo, wait_until="domcontentloaded", timeout=20000)
        
        if response.status == 404:
            await context.close()
            return {"status": "erro", "mensagem": "Placa não encontrada."}

        try:
            await page.wait_for_selector("table.fipeTablePriceDetail", timeout=8000)
        except:
            corpo = await page.content()
            await context.close()
            if "não encontrada" in corpo.lower():
                return {"status": "erro", "mensagem": "Placa não encontrada."}
            return {"status": "erro", "mensagem": "O site demorou a responder ou bloqueou a conexão (6.3s timeout)."}

        # 3. Extração via JS incluindo o Histórico de IPVA
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

            const getIPVA = () => {
                const rows = Array.from(document.querySelectorAll("table tr")).filter(r => r.innerText.includes("Ano IPVA") === false);
                const ipvaData = [];
                // Tenta localizar a tabela que contém dados de IPVA por contexto
                const tables = Array.from(document.querySelectorAll("table"));
                const targetTable = tables.find(t => t.innerText.includes("Ano IPVA") && t.innerText.includes("Valor Venal"));
                
                if (targetTable) {
                    targetTable.querySelectorAll("tr").forEach(r => {
                        const c = r.querySelectorAll("td");
                        if (c.length >= 3 && !isNaN(parseInt(c[0].innerText))) {
                            ipvaData.push({
                                ano: c[0].innerText.trim(),
                                valor_venal: c[1].innerText.trim(),
                                valor_ipva: c[2].innerText.trim()
                            });
                        }
                    });
                }
                return ipvaData;
            };

            return { 
                veiculo: getTabela("table.fipeTablePriceDetail"), 
                fipe: getFipe(),
                historico_ipva: getIPVA()
            };
        }""")

        await context.close()
        return {
            "placa": placa,
            "veiculo": dados["veiculo"],
            "fipe": dados["fipe"],
            "historico_ipva": dados["historico_ipva"],
            "status": "sucesso"
        }

    except Exception as e:
        return {"status": "erro", "mensagem": f"Falha na extração: {str(e)}"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
