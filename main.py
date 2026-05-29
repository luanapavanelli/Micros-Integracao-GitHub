from fastapi import FastAPI, HTTPException
import httpx

app = FastAPI(title="Microsserviço de Integração GitHub")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Base URL oficial da API do GitHub
GITHUB_API_URL = "https://api.github.com"


@app.get("/")
def home():
    return {"mensagem": "Serviço de Integração do GitHub operante!"}


@app.get("/repositorios/{usuario}")
async def listar_repositorios(usuario: str):
    """ Busca repositórios públicos de um usuário """
    async with httpx.AsyncClient() as client:
        resposta = await client.get(f"{GITHUB_API_URL}/users/{usuario}/repos")

        if resposta.status_code != 200:
            raise HTTPException(status_code=resposta.status_code, detail="Erro ao buscar repositórios")

        dados_limpos = [
            {"nome": repo["name"], "url": repo["html_url"], "visibilidade": repo["visibility"]}
            for repo in resposta.json()
        ]
        return dados_limpos


@app.get("/repositorios/{usuario}/{repositorio}/branches")
async def listar_branches(usuario: str, repositorio: str):
    """ Busca todas as branches de um repositório específico """
    async with httpx.AsyncClient() as client:
        resposta = await client.get(f"{GITHUB_API_URL}/repos/{usuario}/{repositorio}/branches")

        if resposta.status_code != 200:
            raise HTTPException(status_code=resposta.status_code, detail="Erro ao buscar branches")

        branches_github = resposta.json()

        # Filtramos para devolver apenas o nome da branch
        dados_limpos = [{"nome": branch["name"]} for branch in branches_github]
        return dados_limpos


@app.get("/repositorios/{usuario}/{repositorio}/prs")
async def listar_pull_requests(usuario: str, repositorio: str):
    """ Busca os Pull Requests (Abertos e Fechados) de um repositório """
    async with httpx.AsyncClient() as client:
        # O parâmetro ?state=all garante que vem tanto os PRs abertos quanto os fechados
        resposta = await client.get(f"{GITHUB_API_URL}/repos/{usuario}/{repositorio}/pulls?state=all")

        if resposta.status_code != 200:
            raise HTTPException(status_code=resposta.status_code, detail="Erro ao buscar Pull Requests")

        prs_github = resposta.json()

        # Limpamos os dados do GitHub para o que importa para a tela
        dados_limpos = [
            {
                "titulo": pr["title"],
                "estado": pr["state"],
                "url": pr["html_url"],
                "autor": pr["user"]["login"]
            }
            for pr in prs_github
        ]
        return dados_limpos
