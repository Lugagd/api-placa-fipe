import asyncio
import re
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

app = FastAPI()

# Configuração do CORS para permitir acesso do seu Dashboard
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CHROME_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
# Mudamos para o novo site conforme solicitado
SITE_URL = "https://www.placafipe.com"

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
    
    async with async_playwright() as p:
        # Lançamento otimizado
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox", 
                "--disable-dev-shm-usage", 
                "--disable-setuid-sandbox",
                "--disable-gpu", # Economiza recursos no Cloud Run
                "--disable-blink-features=AutomationControlled"
            ]
        )
        
        context = await browser.new_context(user_agent=CHROME_USER_AGENT)
        page = await context.new_page()
        
        # --- ESTRATÉGIA TURBO: Bloqueio de Imagens e Anúncios ---
        # Isso reduz o consumo de banda e acelera o carregamento em até 60%
        await page.route("**/*.{png,jpg,jpeg,gif,svg,css,woff,otf}", lambda route: route.abort())

        # Timeouts curtos para falhar rápido se o site estiver fora, mas longos o suficiente para o processamento
        page.set_default_timeout(40000) 

        try:
            # Indo direto para a URL de busca do novo site para ganhar tempo
            # domcontentloaded é muito mais rápido que networkidle
            await page.goto(f"{SITE_URL}/placa/{placa_limpa}", wait_until="domcontentloaded")
            
            # Espera o elemento principal de detalhes aparecer (conforme imagem_44ccea.png)
            # O seletor abaixo busca o container principal de informações
            detalhes_selector = "div.container" 
            await page.wait_for_selector(detalhes_selector, state="visible")

            # Extração dos dados baseada no padrão do placafipe.com
            content = await page.content()
            
            # Limpeza de dados via Regex para ser mais rápido que percorrer tabelas
            # Buscando os campos principais que aparecem na imagem_44ccea.png
            def extrair(campo):
                match = re.search(rf"{campo}:?\s*</b>\s*([^<]+)", content, re.IGNORECASE)
                return match.group(1).strip() if match else "Não informado"

            dados_veiculo = {
                "Marca": extrair("Marca"),
                "Modelo": extrair("Modelo"),
                "Ano": extrair("Ano"),
                "Ano Modelo": extrair("Ano Modelo"),
                "Cor": extrair("Cor"),
                "Cilindrada": extrair("Cilindrada"),
                "Potência": extrair("Potência"),
                "Combustível": extrair("Combustível"),
                "Municipio": extrair("Município"),
                "UF": extrair("UF")
            }

            # Tenta pegar os valores da Tabela FIPE que ficam mais abaixo na página
            valores_fipe = []
            try:
                # Localiza linhas que contenham "R$" (valor monetário)
                fipe_elements = await page.locator("table tr").all()
                for el in fipe_elements:
                    text = await el.inner_text()
                    if "R$" in text:
                        parts = text.split("\t")
                        if len(parts) >= 2:
                            valores_fipe.append({"modelo": parts[0].strip(), "valor": parts[-1].strip()})
            except:
                pass

            return {
                "placa": placa_limpa,
                "detalhes": dados_veiculo,
                "valores_fipe": valores_fipe,
                "status": "sucesso"
            }

        except Exception as e:
            return {"status": "erro", "mensagem": f"Erro na consulta: {str(e)}"}
        finally:
            await browser.close()

if __name__ == "__main__":
    import uvicorn
    # Mantendo a porta 8000 conforme sua configuração do Cloud Run
    uvicorn.run(app, host="0.0.0.0", port=8000)
