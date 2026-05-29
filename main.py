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

        dados_limpos = [{"nome": branch["name"]} for branch in branches_github]
        return dados_limpos


@app.get("/repositorios/{usuario}/{repositorio}/prs")
async def listar_pull_requests(usuario: str, repositorio: str):
    """ Busca os Pull Requests (Abertos e Fechados) de um repositório """
    async with httpx.AsyncClient() as client:
        resposta = await client.get(f"{GITHUB_API_URL}/repos/{usuario}/{repositorio}/pulls?state=all")

        if resposta.status_code != 200:
            raise HTTPException(status_code=resposta.status_code, detail="Erro ao buscar Pull Requests")

        prs_github = resposta.json()

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

    tree_url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
    
    # Criamos o client com timeouts maiores porque a importação pode demorar em repositórios grandes
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            # 1. Busca a árvore completa de arquivos de forma recursiva
            response = await client.get(tree_url, headers=headers)
            if response.status_code != 200:
                print(f"Erro ao buscar árvore do GitHub: {response.text}")
                return

            tree_data = response.json()
        except Exception as e:
            print(f"Erro crítico ao acessar árvore do GitHub: {str(e)}")
            return

        formatos_permitidos = [
            # Documentos
            'pdf', 'txt', 'docx', 'doc', 'odt', 'rtf', 'md',
            # Dados
            'csv', 'json', 'xlsx', 'xls', 'yaml', 'yml', 'xml',
            # Código-Fonte
            'py', 'js', 'ts', 'html', 'css', 'java', 'cpp', 'c', 'h', 
            'cs', 'go', 'rs', 'php', 'rb', 'sh'
        ]

        # Percorremos cada item da árvore do repositório
        for item in tree_data.get("tree", []):
            if item.get("type") == "blob":
                path = item.get("path")
                nome_arquivo = path.split('/')[-1]
                extensao = nome_arquivo.split('.')[-1].lower() if '.' in nome_arquivo else ''

                if extensao in formatos_permitidos:
                    # Agora o TRY/EXCEPT está DENTRO DO LOOP para CADA ARQUIVO INDIVIDUAL. 
                    # Se 1 falhar, ele apenas loga o erro e pula pro próximo!
                    try:
                        raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
                        
                        raw_response = await client.get(raw_url, headers=headers)

                        if raw_response.status_code == 200:
                            # 3. Encaminha o arquivo fisicamente para o Microsserviço de Ingestão
                            # Simplificamos o envio para que a biblioteca detecte o Content-Type automaticamente
                            files = {
                                'file': (nome_arquivo, raw_response.content)
                            }
                            
                            ingestao_url = f"{INGESTAO_SERVICE_URL}/api/postarquivos/projeto/{projeto_id}"
                            upload_response = await client.post(ingestao_url, files=files)
                            
                            if upload_response.status_code == 200:
                                print(f"Arquivo {nome_arquivo} importado e enviado com sucesso.")
                            else:
                                print(f"Falha ao enviar {nome_arquivo} para Ingestão: {upload_response.text}")
                        else:
                            print(f"Erro ao baixar arquivo do GitHub: {raw_url} (Status: {raw_response.status_code})")
                    
                    except Exception as file_error:
                        print(f"Erro isolado ao processar arquivo {nome_arquivo}: {str(file_error)}")
                        # O loop for continuará rodando para o próximo arquivo da lista


@app.post("/api/github/importar")
async def importar_repositorio(payload: ImportarRepoDTO, background_tasks: BackgroundTasks):
    """
    Endpoint principal. Ele recebe as informações do repositório, valida a existência
    e dispara uma tarefa em segundo plano para não travar a requisição do usuário.
    """
    headers = {"Accept": "application/vnd.github.v3+json"}
    if payload.token:
        headers["Authorization"] = f"token {payload.token}"

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
