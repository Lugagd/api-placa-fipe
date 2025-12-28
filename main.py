import asyncio
import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright

app = FastAPI()

# Configuração de CORS para o seu Dashboard
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CHROME_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
BASE_URL = "https://placafipe.com/placa"

@app.get("/")
def read_root():
    return {"message": "API de Placas Online - Estabilizada"}

@app.get("/consultar/{placa}")
async def rota_consultar(placa: str):
    resultado = await consultar_placa(placa)
    if resultado.get("status") == "erro":
        # Se for erro de placa não encontrada, retorna 404
        if "não encontrada" in resultado.get("mensagem").lower():
             raise HTTPException(status_code=404, detail=resultado.get("mensagem"))
        # Outros erros retornam 500
        raise HTTPException(status_code=500, detail=resultado.get("mensagem"))
    return resultado

async def consultar_placa(placa: str):
    placa_limpa = placa.upper().replace("-", "").strip()
    url_alvo = f"{BASE_URL}/{placa_limpa}"
    
    async with async_playwright() as p:
        # Abrimos o browser aqui para garantir que cada requisição tenha seu processo limpo
        # Isso evita o erro 'Uncaught signal: 5' que você viu nos logs
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
        )
        
        context = await browser.new_context(
            user_agent=CHROME_USER_AGENT,
            viewport={'width': 1280, 'height': 720}
        )
        page = await context.new_page()

        # BLOQUEIO DE RECURSOS: Acelera muito o carregamento sem parecer bot
        async def block_resources(route):
            url = route.request.url.lower()
            # Bloqueia anúncios e rastreadores que pesam a página
            bad_domains = ["google", "ads", "analytics", "facebook", "doubleclick"]
            if any(domain in url for domain in bad_domains) or route.request.resource_type in ["image", "font", "media"]:
                return await route.abort()
            return await route.continue_()

        await page.route("**/*", block_resources)

        try:
            # wait_until="domcontentloaded" é o equilíbrio perfeito entre velocidade e dados prontos
            await page.goto(url_alvo, wait_until="domcontentloaded", timeout=25000)

            # Verifica se a placa existe usando um seletor rápido
            if await page.query_selector("text='Placa não encontrada'"):
                return {"status": "erro", "mensagem": "Placa não encontrada."}

            # Aguarda o elemento vital da tabela
            try:
                await page.wait_for_selector("table.fipeTablePriceDetail", timeout=8000)
            except:
                return {"status": "erro", "mensagem": "Timeout ao localizar dados do veículo."}

            # EXTRAÇÃO OTIMIZADA: Executamos um único script JS dentro do navegador
            # Isso é MUITO mais rápido do que múltiplos 'await page.locator'
            dados = await page.evaluate("""() => {
                const getTableData = (selector) => {
                    const rows = document.querySelectorAll(`${selector} tr`);
                    return Array.from(rows).map(r => {
                        const cols = r.querySelectorAll('td');
                        return Array.from(cols).map(c => c.innerText.trim());
                    });
                };

                // Detalhes Técnicos
                const tecnicos = {};
                getTableData('table.fipeTablePriceDetail').forEach(row => {
                    if(row.length === 2) tecnicos[row[0].replace(':','').trim()] = row[1];
                });

                // Tabela Fipe
                const fipeRaw = getTableData('table.fipe-desktop').length > 0 
                    ? getTableData('table.fipe-desktop') 
                    : getTableData('table.fipe-mobile');
                
                const fipe = fipeRaw.filter(r => r.length >= 3).map(r => ({
                    codigo: r[0], modelo: r[1], valor: r[2]
                }));

                // Histórico IPVA (SP)
                const ipvaRows = Array.from(document.querySelectorAll("table:has-text('Ano IPVA') tr"));
                const ipva = ipvaRows.slice(1).map(r => {
                    const c = r.querySelectorAll('td');
                    if(c.length >= 3) {
                        return { ano: c[0].innerText.trim(), valor_venal: c[1].innerText.trim(), valor_ipva: c[2].innerText.trim() };
                    }
                    return null;
                }).filter(x => x && !isNaN(x.ano));

                return { veiculo: tecnicos, fipe, historico_ipva: ipva };
            }""")

            return {
                "placa": placa_limpa,
                "veiculo": dados['veiculo'],
                "fipe": dados['fipe'],
                "historico_ipva": dados['historico_ipva'],
                "status": "sucesso"
            }

        except Exception as e:
            return {"status": "erro", "mensagem": f"Falha na extração: {str(e)}"}
        finally:
            # Crucial: fecha o browser para liberar os 2GB de RAM para a próxima chamada
            await browser.close()

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
