```bash
    . .venv/bin/activate
    screener-mcp
```

```bash
    {
      "mcpServers": {
        "screener": {
          "command": "npx",
          "args": [
            "-y",
            "mcp-remote",
            "http://localhost:11345/sse",
            "--transport",
            "sse-only",
            "--header",
            "SSO_USERNAME:${SSO_USERNAME}",
            "--header",
            "SSO_PASSWORD:${SSO_PASSWORD}",
            "--header",
            "SCREENER_USERNAME:${SCREENER_USERNAME}",
            "--header",
            "SCREENER_PASSWORD:${SCREENER_PASSWORD}"
          ],
          "env": {
            "SSO_USERNAME": "imdibyajyotisahoo@gmail.com",
            "SSO_PASSWORD": "Dibya@123456788888",
            "SCREENER_USERNAME": "applicationmaintainer@gmail.com",
            "SCREENER_PASSWORD": "Dibya@123456788888"
          }
        }
      }
    }
```