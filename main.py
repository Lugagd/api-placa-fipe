import asyncio
import os
import random
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
# Importação da camuflagem (Necessário: pip install playwright-stealth)
from playwright_stealth import stealth_async 

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_URL = "https://placafipe.com/placa"

# Lista de User-Agents para rotacionar e parecer usuários diferentes
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0'
]

@app.get("/")
def read_root():
    return {"message": "API de Placas Online - Blindada"}

@app.get("/consultar/{placa}")
async def rota_consultar(placa: str):
    # Tentaremos até 3 vezes antes de desistir
    max_retries = 3
    for tentativa in range(max_retries):
        try:
            resultado = await consultar_placa(placa)
            
            if resultado.get("status") == "erro":
                # Se for erro de "não encontrado", não adianta tentar de novo
                if "não encontrada" in resultado.get("mensagem").lower():
                    raise HTTPException(status_code=404, detail=resultado.get("mensagem"))
                # Se for outro erro, lançamos exceção para cair no 'except' e tentar de novo
                raise Exception(resultado.get("mensagem"))
            
            return resultado

        except HTTPException as he:
            raise he # Erros 404 reais repassa direto
        except Exception as e:
            print(f"Tentativa {tentativa + 1} falhou: {e}")
            if tentativa == max_retries - 1:
                # Se falhou na última tentativa, retorna erro 500 genérico
                raise HTTPException(status_code=500, detail=f"Erro após {max_retries} tentativas. O site alvo pode estar instável ou bloqueando.")
            
            # Espera um pouco antes de tentar de novo (Backoff)
            await asyncio.sleep(2)

async def consultar_placa(placa: str):
    placa_limpa = placa.upper().replace("-", "").strip()
    url_alvo = f"{BASE_URL}/{placa_limpa}"
    
    async with async_playwright() as p:
        # Argumentos vitais para evitar detecção e reduzir uso de memória
        browser_args = [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-blink-features=AutomationControlled", # CRUCIAL: Esconde que é automação
            "--disable-infobars",
            "--window-size=1920,1080"
        ]

        browser = await p.chromium.launch(
            headless=True, 
            args=browser_args
        )
        
        try:
            # Contexto novo com User-Agent rotativo
            context = await browser.new_context(
                user_agent=random.choice(USER_AGENTS),
                viewport={'width': 1920, 'height': 1080},
                locale="pt-BR",
                timezone_id="America/Sao_Paulo"
            )

            # Scripts para mascarar webdriver
            await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            page = await context.new_page()
            
            # Aplica a camuflagem pesada do playwright-stealth
            await stealth_async(page)

            # Bloqueio de recursos inúteis
            await page.route("**/*.{png,jpg,jpeg,gif,svg,woff,woff2,otf,css}", lambda route: route.abort())

            # Aumentei o timeout para 20s para conexões lentas
            response = await page.goto(url_alvo, wait_until="domcontentloaded", timeout=20000)
            
            if response.status == 404:
                 return {"status": "erro", "mensagem": "Placa não encontrada no servidor."}

            # Verificação de bloqueio (Cloudflare ou similar)
            title = await page.title()
            if "Attention Required" in title or "Just a moment" in title:
                raise Exception("Bloqueio de WAF detectado.")

            # Espera inteligente
            try:
                # Espera por um dos dois: ou a tabela ou o aviso de erro
                await page.wait_for_selector("table.fipeTablePriceDetail, .alert-danger", timeout=10000)
            except PlaywrightTimeout:
                 # Se der timeout aqui, provavelmente a página não carregou o conteúdo principal
                 return {"status": "erro", "mensagem": "Timeout ao buscar elementos na página."}

            # Verifica se existe aviso de "não encontrada" na tela
            content = await page.content()
            if "Placa não encontrada" in content or "Veículo não encontrado" in content:
                return {"status": "erro", "mensagem": "Placa não encontrada."}

            # --- EXTRAÇÃO DE DADOS ---
            
            # 1. Detalhes
            detalhes = {}
            rows = await page.locator("table.fipeTablePriceDetail tr").all()
            if not rows:
                 # Se a tabela não existe mas não deu erro antes, algo estranho aconteceu
                 raise Exception("Tabela de detalhes vazia.")

            for row in rows:
                cols = await row.locator("td").all()
                if len(cols) == 2:
                    chave = (await cols[0].inner_text()).replace(":", "").strip()
                    valor = (await cols[1].inner_text()).strip()
                    detalhes[chave] = valor

            # 2. FIPE
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

            # 3. IPVA
            historico_ipva = []
            # Seletor mais específico para evitar pegar tabelas erradas
            ipva_rows = await page.locator("h3:has-text('IPVA') + div table tr").all()
            if not ipva_rows:
                 # Tenta fallback genérico se a estrutura mudou
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
            # Captura erro para o loop de retry
            return {"status": "erro", "mensagem": f"{str(e)}"}
        finally:
            # Fecha contexto e página, mas browser é fechado pelo context manager 'async with'
            if 'context' in locals(): await context.close()

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
