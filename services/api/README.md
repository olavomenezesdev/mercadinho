# Camada de integração da API

Todas as telas falam com o servidor Flask (`app.py`) por meio de `client.js`.

## O que o `client.js` faz
- Envia o token no cabeçalho `Authorization: Bearer` em toda chamada para `/api/*`, exceto o login.
- Guarda o token em `localStorage.mercadinho_token` e os dados do usuário (`nome`, `login`, `perfil` e `permissoes`) em `localStorage.mercadinho_usuario`.
- Encerra a sessão e volta para o login quando a API responde 401.
- Mostra uma mensagem amigável quando a API responde 403 (tela não liberada).

## Padrão de integração
- Cada tela importa `apiClient` de `client.js` e chama as rotas diretamente, como `apiClient.fetch('/api/produtos')`.
- As regras de negócio ficam no servidor: FEFO, cálculo de estoque, estoque mínimo, avisos de validade e permissões.
- As rotas disponíveis estão documentadas em `docs/2-manter/api.md`.
