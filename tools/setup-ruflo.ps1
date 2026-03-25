$ErrorActionPreference = "Stop"

Write-Host "Checking Node.js toolchain..."

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Error "Node.js is not installed or not on PATH. Install Node.js first, then rerun this script."
}

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Error "npm is not installed or not on PATH. Install Node.js/npm first, then rerun this script."
}

Write-Host "Initializing Ruflo/claude-flow for this repo..."
npx claude-flow@alpha init --verify --pair --force

Write-Host ""
Write-Host "Ruflo project initialization finished."
Write-Host "If you want MCP integration, add the server to your global AI client config:"
Write-Host "npx claude-flow@alpha mcp start"
