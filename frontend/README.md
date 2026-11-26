# MarBet Frontend

PWA estática em HTML, CSS e JavaScript modular.

## Configuração da API

Defina a URL do backend em `app-config.js` antes do deployment:

```js
window.MARBET_CONFIG = {
  apiBaseUrl: "https://sua-api.onrender.com",
  tokenStorageKey: "marbet_access_token",
  refreshStorageKey: "marbet_refresh_token"
};
```

Não coloque tokens ou credenciais de fornecedores em `app-config.js`.

## Execução local

```powershell
cd C:\Marbet\frontend
python -m http.server 5173
```

Abra `http://127.0.0.1:5173`.

O frontend não gera dados fictícios. Quando os endpoints não estão disponíveis ou não existem dados, apresenta estados vazios e erros reais da API.
