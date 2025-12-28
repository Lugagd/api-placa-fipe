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



@app.get("/")

def read_root():

    return {"message": "Rodando no Cloud Run"}



@app.get("/consultar/{placa}")

async def rota_consultar(placa: str):

    resultado = await consultar_placa(placa)

    if resultado.get("status") == "erro":

        if "não encontrada" in resultado.get("mensagem").lower():

             raise HTTPException(status_code=404, detail=resultado.get("mensagem"))

        raise HTTPException(status_code=500, detail=resultado.get("mensagem"))

    return resultado



async def consultar_placa(placa: str):

    placa_limpa = placa.upper().replace("-", "").strip()

    url_alvo = f"{BASE_URL}/{placa_limpa}"

    

    async with async_playwright() as p:

        browser = await p.chromium.launch(

            headless=True, 

            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]

        )

        

        context = await browser.new_context(

            user_agent=CHROME_USER_AGENT,

            viewport={'width': 1280, 'height': 720}

        )

        page = await context.new_page()

        



        await page.route("**/*.{png,jpg,jpeg,gif,svg,woff,woff2,otf}", lambda route: route.abort())



        try:

            response = await page.goto(url_alvo, wait_until="networkidle", timeout=30000)

            

            if response.status == 404:

                 return {"status": "erro", "mensagem": "Placa não encontrada no servidor."}



            try:

                await page.wait_for_selector("table.fipeTablePriceDetail", timeout=8000)

            except:

                if await page.query_selector("text='Placa não encontrada'"):

                    return {"status": "erro", "mensagem": "Placa não encontrada."}

                return {"status": "erro", "mensagem": "Timeout: O site demorou a responder ou bloqueou a conexão."}



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

            return {"status": "erro", "mensagem": f"Falha na extração: {str(e)}"}

        finally:

            await browser.close()



if __name__ == "__main__":

    import uvicorn

    port = int(os.environ.get("PORT", 8000))

    uvicorn.run(app, host="0.0.0.0", port=port)
