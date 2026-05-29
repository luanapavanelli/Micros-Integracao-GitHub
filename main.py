from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
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

# URL do seu microsserviço de Ingestão (Ajustado para HTTPS)
INGESTAO_SERVICE_URL = "https://ingestaomod2.azurewebsites.net" 

# DTO para a nova rota de importação
class ImportarRepoDTO(BaseModel):
    owner: str
    repo: str
    projeto_id: int
    branch: Optional[str] = "main"
    token: Optional[str] = None


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

async def processar_e_enviar_arquivos(owner: str, repo: str, branch: str, projeto_id: int, token: Optional[str]):
    """
    Função em background que baixa os arquivos do GitHub e encaminha 
    para o microsserviço de Ingestão.
    """
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    # 1. Busca a árvore completa de arquivos de forma recursiva
    tree_url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(tree_url, headers=headers)
            if response.status_code != 200:
                print(f"Erro ao buscar árvore do GitHub: {response.text}")
                return

            tree_data = response.json()
            # Formatos permitidos pela regra de negócio do Domínio de Ingestão
            formatos_permitidos = ['pdf', 'txt', 'docx', 'csv']

            for item in tree_data.get("tree", []):
                # Validar se o item é um arquivo (blob) e não uma pasta (tree)
                if item.get("type") == "blob":
                    path = item.get("path")
                    nome_arquivo = path.split('/')[-1]
                    extensao = nome_arquivo.split('.')[-1].lower() if '.' in nome_arquivo else ''

                    if extensao in formatos_permitidos:
                        # 2. Baixa o conteúdo bruto (raw) do arquivo do GitHub
                        raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
                        
                        # CORREÇÃO APLICADA AQUI: Passando os headers para baixar arquivos de repositórios privados
                        raw_response = await client.get(raw_url, headers=headers)

                        if raw_response.status_code == 200:
                            # 3. Encaminha o arquivo fisicamente para o Microsserviço de Ingestão
                            files = {
                                'file': (nome_arquivo, raw_response.content, f'application/{extensao}')
                            }
                            
                            ingestao_url = f"{INGESTAO_SERVICE_URL}/api/postarquivos/projeto/{projeto_id}"
                            
                            # Aumentando o timeout para não estourar em arquivos maiores
                            upload_response = await client.post(ingestao_url, files=files, timeout=60.0)
                            
                            if upload_response.status_code == 200:
                                print(f"Arquivo {nome_arquivo} importado e enviado com sucesso.")
                            else:
                                print(f"Falha ao enviar {nome_arquivo} para Ingestão: {upload_response.text}")
                        else:
                            print(f"Erro ao baixar arquivo do GitHub: {raw_url} (Status: {raw_response.status_code})")
        
        except Exception as e:
            print(f"Erro crítico durante o processamento do GitHub: {str(e)}")


@app.post("/api/github/importar")
async def importar_repositorio(payload: ImportarRepoDTO, background_tasks: BackgroundTasks):
    """
    Endpoint principal. Ele recebe as informações do repositório, valida a existência
    e dispara uma tarefa em segundo plano para não travar a requisição do usuário.
    """
    headers = {"Accept": "application/vnd.github.v3+json"}
    if payload.token:
        headers["Authorization"] = f"token {payload.token}"

    # Validação inicial rápida: O repositório existe?
    repo_url = f"{GITHUB_API_URL}/repos/{payload.owner}/{payload.repo}"
    
    async with httpx.AsyncClient() as client:
        response = await client.get(repo_url, headers=headers)
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="Repositório não encontrado no GitHub.")
        elif response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="Erro ao validar repositório no GitHub.")

    # Dispara o download e envio dos arquivos em Background
    background_tasks.add_task(
        processar_e_enviar_arquivos,
        payload.owner,
        payload.repo,
        payload.branch,
        payload.projeto_id,
        payload.token
    )

    return {
        "status": "processando",
        "mensagem": f"A importação do repositório {payload.owner}/{payload.repo} foi iniciada em segundo plano."
    }
