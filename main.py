import asyncio
import re
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

app = FastAPI()

# Configuração do CORS para seu Dashboard.jsx
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CHROME_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'

@app.get("/")
def read_root():
    return {"message": "API Online - Use /consultar/SUAPLACA"}

@app.get("/consultar/{placa}")
async def rota_consultar(placa: str):
    resultado = await consultar_placa(placa)
    if resultado.get("status") == "erro":
        # Retorna o erro específico para o frontend
        raise HTTPException(status_code=500, detail=resultado.get("mensagem"))
    return resultado

async def consultar_placa(placa: str):
    placa_limpa = placa.upper().replace("-", "").strip()
    site_alvo = f"https://www.placafipe.com/placa/{placa_limpa}"
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox", 
                "--disable-dev-shm-usage", 
                "--disable-blink-features=AutomationControlled",
                "--disable-gpu"
            ]
        )
        
        context = await browser.new_context(user_agent=CHROME_USER_AGENT)
        page = await context.new_page()
        
        # --- BLOQUEIO DE RECURSOS PARA VELOCIDADE ---
        # Bloqueia CSS, Imagens e Fontes para carregar apenas o texto
        await page.route("**/*.{png,jpg,jpeg,gif,svg,css,woff,woff2,otf}", lambda route: route.abort())

        try:
            # Vai direto para a URL da placa para evitar cliques extras
            # Usamos 'commit' para ser o mais rápido possível
            await page.goto(site_alvo, wait_until="commit", timeout=30000)
            
            # Esperamos o texto principal que contém os detalhes
            # O seletor 'body' é garantido, vamos buscar o conteúdo dentro dele
            await page.wait_for_selector("body", timeout=30000)
            
            # Captura o texto da página para extração via Regex (mais rápido que seletores complexos)
            texto_pagina = await page.inner_text("body")
            
            # Se não encontrar a placa no texto, o site provavelmente retornou erro
            if "não encontrada" in texto_pagina.lower():
                return {"status": "erro", "mensagem": "Placa não encontrada na base de dados."}

            # Extração dos dados usando padrões de texto do site
            def extrair_valor(campo):
                match = re.search(rf"{campo}:?\s*([^\n\r]+)", texto_pagina, re.IGNORECASE)
                return match.group(1).strip() if match else "Não informado"

            detalhes = {
                "Marca": extrair_valor("Marca"),
                "Modelo": extrair_valor("Modelo"),
                "Ano": extrair_valor("Ano"),
                "Ano Modelo": extrair_valor("Ano Modelo"),
                "Cor": extrair_valor("Cor"),
                "Combustível": extrair_valor("Combustível"),
                "Municipio": extrair_valor("Município"),
                "UF": extrair_valor("UF")
            }

            # Tenta extrair valores FIPE se houver tabela
            valores_fipe = []
            try:
                # Busca por linhas que contenham "R$"
                linhas = texto_pagina.split("\n")
                for linha in linhas:
                    if "R$" in linha and ("202" in linha or "201" in linha):
                        valores_fipe.append({"info": linha.strip()})
            except:
                pass

            return {
                "placa": placa_limpa,
                "detalhes": detalhes,
                "valores_fipe": valores_fipe[:3], # Limita aos 3 primeiros para velocidade
                "status": "sucesso"
            }

        except PlaywrightTimeoutError:
            return {"status": "erro", "mensagem": "O site demorou muito para responder."}
        except Exception as e:
            return {"status": "erro", "mensagem": str(e)}
        finally:
            await browser.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
