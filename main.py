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

playwright_instance = None
browser_instance = None

@app.on_event("startup")
async def startup_event():
    global playwright_instance, browser_instance
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
            "--single-process" 
        ]
    )

@app.on_event("shutdown")
async def shutdown_event():
    if browser_instance:
        await browser_instance.close()
    if playwright_instance:
        await playwright_instance.stop()

@app.get("/")
def read_root():
    return {"message": "API de Placas Online - Ultra Fast Mode"}

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
    
    context = await browser_instance.new_context(
        user_agent=CHROME_USER_AGENT,
        viewport={'width': 800, 'height': 600} 
    )
    page = await context.new_page()

    await page.route("**/*", lambda route: route.abort() 
        if route.request.resource_type in ["image", "media", "font", "stylesheet", "script", "other"] 
        else route.continue_()
    )

    try:
        response = await page.goto(url_alvo, wait_until="domcontentloaded", timeout=7000)
        
        if response.status == 404:
            return {"status": "erro", "mensagem": "Placa não encontrada no servidor."}

        try:
            await page.wait_for_selector("table.fipeTablePriceDetail", timeout=5000)
        except:
            return {"status": "erro", "mensagem": "Placa não encontrada ou bloqueio de conexão."}

        dados = await page.evaluate("""() => {
            const extrairTabelaSimples = (selector) => {
                const rows = document.querySelectorAll(selector + " tr");
                const obj = {};
                rows.forEach(row => {
                    const cols = row.querySelectorAll("td");
                    if(cols.length >= 2) {
                        const chave = cols[0].innerText.replace(":", "").trim();
                        obj[chave] = cols[1].innerText.trim();
                    }
                });
                return obj;
            };

            const extrairTabelaFipe = () => {
                const rows = document.querySelectorAll("table.fipe-desktop tr, table.fipe-mobile tr");
                const lista = [];
                rows.forEach(row => {
                    const cols = row.querySelectorAll("td");
                    if(cols.length >= 3) {
                        lista.append({
                            codigo: cols[0].innerText.trim(),
                            modelo: cols[1].innerText.trim(),
                            valor: cols[2].innerText.trim()
                        });
                    }
                });
                return lista;
            };

            const extrairIPVA = () => {
                const tables = Array.from(document.querySelectorAll("table"));
                const ipvaTable = tables.find(t => t.innerText.includes("Ano IPVA"));
                if (!ipvaTable) return [];
                
                return Array.from(ipvaTable.querySelectorAll("tr"))
                    .slice(1) // Pula o cabeçalho
                    .map(row => {
                        const cols = row.querySelectorAll("td");
                        if(cols.length >= 3) {
                            return {
                                ano: cols[0].innerText.trim(),
                                valor_venal: cols[1].innerText.trim(),
                                valor_ipva: cols[2].innerText.trim()
                            };
                        }
                        return null;
                    }).filter(i => i && !isNaN(i.ano));
            };

            return {
                veiculo: extrairTabelaSimples("table.fipeTablePriceDetail"),
                fipe: Array.from(document.querySelectorAll("table.fipe-desktop tr, table.fipe-mobile tr"))
                    .map(row => {
                        const cols = row.querySelectorAll("td");
                        return cols.length >= 3 ? {
                            codigo: cols[0].innerText.trim(),
                            modelo: cols[1].innerText.trim(),
                            valor: cols[2].innerText.trim()
                        } : null;
                    }).filter(x => x),
                historico_ipva: extrairIPVA()
            };
        }""")

        return {
            "placa": placa_limpa,
            "veiculo": dados["veiculo"],
            "fipe": dados["fipe"],
            "historico_ipva": dados["historico_ipva"],
            "status": "sucesso"
        }

    except Exception as e:
        return {"status": "erro", "mensagem": f"Falha na extração: {str(e)}"}
    finally:
        await page.close()
        await context.close()

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
