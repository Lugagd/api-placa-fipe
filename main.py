import asyncio
import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright
# Instale: pip install fake-useragent
from fake_useragent import UserAgent

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_URL = "https://placafipe.com/placa"

@app.get("/consultar/{placa}")
async def rota_consultar(placa: str):
    # Retry logic simples: tenta 2 vezes antes de falhar
    for tentativa in range(2):
        try:
            resultado = await consultar_placa(placa)
            if resultado.get("status") == "erro":
                if "não encontrada" in resultado.get("mensagem").lower():
                    raise HTTPException(status_code=404, detail=resultado.get("mensagem"))
                # Se for outro erro e for a última tentativa
                if tentativa == 1:
                    raise HTTPException(status_code=500, detail=resultado.get("mensagem"))
                continue # Tenta de novo
            return resultado
        except Exception as e:
            if tentativa == 1:
                raise HTTPException(status_code=500, detail=str(e))
            await asyncio.sleep(1) # Espera 1s antes de tentar de novo

async def consultar_placa(placa: str):
    placa_limpa = placa.upper().replace("-", "").strip()
    url_alvo = f"{BASE_URL}/{placa_limpa}"
    ua = UserAgent()
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, 
            args=[
                "--no-sandbox", 
                "--disable-dev-shm-usage", 
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled", # Esconde que é automação
                "--disable-extensions",
                "--mute-audio"
            ]
        )
        
        context = await browser.new_context(
            user_agent=ua.random, # User agent aleatório
            viewport={'width': 1366, 'height': 768},
            locale='pt-BR',
            timezone_id='America/Sao_Paulo'
        )

        # Script poderoso para esconder o WebDriver
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.navigator.chrome = { runtime: {} };
        """)
        
        page = await context.new_page()
        
        # Bloqueio agressivo de recursos inúteis para acelerar e economizar banda
        await page.route("**/*.{png,jpg,jpeg,gif,svg,woff,woff2,otf,css}", lambda route: route.abort())
        # Opcional: Bloquear Google Ads/Analytics se souber os domínios

        try:
            # Usar domcontentloaded é mais rápido e menos propenso a timeout que networkidle
            response = await page.goto(url_alvo, wait_until="domcontentloaded", timeout=25000)
            
            if response.status == 404:
                 return {"status": "erro", "mensagem": "Placa não encontrada no servidor."}

            # Espera inteligente
            try:
                await page.wait_for_selector("table.fipeTablePriceDetail", timeout=10000)
            except:
                content = await page.content()
                if "Placa não encontrada" in content:
                    return {"status": "erro", "mensagem": "Placa não encontrada."}
                if "Access Denied" in content or "403 Forbidden" in content:
                     return {"status": "erro", "mensagem": "Bloqueio de IP detectado."}
                return {"status": "erro", "mensagem": "Timeout ao buscar tabela."}

            # --- EXTRAÇÃO (SEU CÓDIGO ORIGINAL ABAIXO) ---
            detalhes = {}
            rows = await page.locator("table.fipeTablePriceDetail tr").all()
            for row in rows:
                cols = await row.locator("td").all()
                if len(cols) == 2:
                    chave = (await cols[0].inner_text()).replace(":", "").strip()
                    valor = (await cols[1].inner_text()).strip()
                    detalhes[chave] = valor

            valores_fipe = []
            fipe_rows = await page.locator("table.fipe-desktop tr").all()
            if not fipe_rows:
                fipe_rows = await page.locator("table.fipe-mobile tr").all()
            
            for row in fipe_rows:
                cols = await row.locator("td").all()
                if len(cols) >= 3:
                    valores_fipe.append({
                        "codigo": (await cols[0].inner_text()).strip(),
                        "modelo": (await cols[1].inner_text()).strip(),
                        "valor": (await cols[2].inner_text()).strip()
                    })

            historico_ipva = []
            ipva_rows = await page.locator("table:has-text('Ano IPVA') tr").all()
            
            for row in ipva_rows:
                cols = await row.locator("td").all()
                if len(cols) >= 3:
                    ano = (await cols[0].inner_text()).strip()
                    if ano.isdigit():
                        historico_ipva.append({
                            "ano": ano,
                            "valor_venal": (await cols[1].inner_text()).strip(),
                            "valor_ipva": (await cols[2].inner_text()).strip()
                        })

            return {
                "placa": placa_limpa,
                "veiculo": detalhes,
                "fipe": valores_fipe,
                "historico_ipva": historico_ipva,
                "status": "sucesso"
            }

        except Exception as e:
            return {"status": "erro", "mensagem": f"Erro interno: {str(e)}"}
        finally:
            await browser.close()
