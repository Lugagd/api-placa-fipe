import asyncio
import re
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

app = FastAPI()

# Configuração do CORS para seu Dashboard
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CHROME_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
SITE_BASE_URL = "https://www.placafipe.com/placa"

@app.get("/")
def read_root():
    return {"message": "API Online - Use /consultar/SUAPLACA"}

@app.get("/consultar/{placa}")
async def rota_consultar(placa: str):
    resultado = await consultar_placa(placa)
    if resultado.get("status") == "erro":
        raise HTTPException(status_code=500, detail=resultado.get("mensagem"))
    return resultado

async def consultar_placa(placa: str):
    placa_limpa = placa.upper().replace("-", "").strip()
    url_final = f"{SITE_BASE_URL}/{placa_limpa}"
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
        )
        context = await browser.new_context(user_agent=CHROME_USER_AGENT)
        page = await context.new_page()
        
        # Bloqueio de mídia para acelerar o carregamento
        await page.route("**/*.{png,jpg,jpeg,gif,svg,css,woff,2,otf}", lambda route: route.abort())

        try:
            # Navegação rápida
            await page.goto(url_final, wait_until="domcontentloaded", timeout=30000)
            
            # 1. Extração de Detalhes Técnicos (Usando a tabela limpa)
            detalhes = {}
            rows = await page.locator("table.fipe-uma-coluna tr").all()
            for row in rows:
                text = await row.inner_text()
                if ":" in text:
                    chave, valor = text.split(":", 1)
                    detalhes[chave.strip()] = valor.strip()

            # 2. Extração de Valores FIPE (Tabela de modelos encontrados)
            valores_fipe = []
            fipe_rows = await page.locator("table:has-text('Código FIPE') tr").all()
            for row in fipe_rows[1:]: # Pula cabeçalho
                cols = await row.locator("td").all()
                if len(cols) >= 3:
                    valores_fipe.append({
                        "codigo": await cols[0].inner_text(),
                        "modelo": await cols[1].inner_text(),
                        "valor": await cols[2].inner_text()
                    })

            # 3. Extração de IPVA e Valor Venal (Foco em SP / Último Ano)
            ipva_info = None
            try:
                # Localiza especificamente a tabela de histórico de IPVA
                ipva_table_rows = await page.locator("table:has-text('Ano IPVA') tr").all()
                if len(ipva_table_rows) > 1:
                    # Pega a primeira linha de dados (ano mais recente)
                    cols = await ipva_table_rows[1].locator("td").all()
                    if len(cols) >= 3:
                        ipva_info = {
                            "ano": await cols[0].inner_text(),
                            "valor_venal": await cols[1].inner_text(),
                            "valor_ipva": await cols[2].inner_text(),
                            "estado": "São Paulo (SP)"
                        }
            except:
                pass # Caso não exista tabela de IPVA

            return {
                "placa": placa_limpa,
                "veiculo": {
                    "marca": detalhes.get("Marca"),
                    "modelo": detalhes.get("Modelo"),
                    "ano": detalhes.get("Ano"),
                    "ano_modelo": detalhes.get("Ano Modelo"),
                    "cor": detalhes.get("Cor"),
                    "municipio": detalhes.get("Município"),
                    "uf": detalhes.get("UF"),
                    "chassi": detalhes.get("Chassi")
                },
                "fipe": valores_fipe,
                "ipva_sp": ipva_info,
                "status": "sucesso"
            }

        except Exception as e:
            return {"status": "erro", "mensagem": f"Erro na extração: {str(e)}"}
        finally:
            await browser.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
