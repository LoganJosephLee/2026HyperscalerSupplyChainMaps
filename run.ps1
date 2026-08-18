<#
    Windows PowerShell equivalent of the Makefile.

    Windows has no `make`, and PowerShell 5.1 (the version that ships with
    Windows) does not accept `&&` as a statement separator, so the bash
    instructions in the README do not transfer. Use this instead:

        .\run.ps1 setup
        .\run.ps1 fetch
        .\run.ps1 sections
        .\run.ps1 refresh

    If PowerShell refuses to run the script at all ("running scripts is
    disabled on this system"), allow local scripts for this user once:

        Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
#>

param(
    [Parameter(Position = 0)]
    [ValidateSet('help', 'setup', 'refresh', 'fetch', 'sections', 'extract', 'verify',
                 'build', 'review', 'serve', 'test', 'neo4j-load', 'clean')]
    [string]$Task = 'help',

    # Extra arguments are passed through, e.g. .\run.ps1 fetch MSFT
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = 'Stop'
$Extractions = 'data/extractions.json'

function Invoke-Hscm {
    param([string[]]$HscmArgs)
    Write-Host "> uv run hscm $($HscmArgs -join ' ')" -ForegroundColor DarkGray
    & uv run hscm @HscmArgs
    if ($LASTEXITCODE -ne 0) { throw "hscm $($HscmArgs[0]) exited with $LASTEXITCODE" }
}

function Assert-Contact {
    if (-not $env:HSCM_EDGAR_CONTACT) {
        Write-Warning @"
HSCM_EDGAR_CONTACT is not set. SEC's fair-access policy wants a real contact
address in the User-Agent header. Set it before fetching:

    `$env:HSCM_EDGAR_CONTACT = "you@example.com"
"@
    }
}

switch ($Task) {
    'help' {
        Write-Host @"
Usage: .\run.ps1 <task>

  setup       install dependencies (uv creates its own Python if you have none)
  fetch       cache the latest 10-K for each seed company
  sections    report what the section splitter found in the cached filings
  extract     run the configured extractor over the cached filings
  verify      string-match every claimed sentence back into its filing
  build       verify, resolve, and export the graph for the site
  refresh     the whole pipeline: fetch -> extract -> verify -> build
  review      build the entity resolution queue for a human to work through
  serve       serve the site at http://localhost:8000
  test        run the test suite
  neo4j-load  load the graph into Neo4j (add --dry-run to just print the Cypher)
  clean       delete the cache and generated data

Before fetching:
    `$env:HSCM_EDGAR_CONTACT = "you@example.com"

To use the real extractor once you have an API key:
    `$env:ANTHROPIC_API_KEY = "sk-ant-..."
    `$env:HSCM_EXTRACTOR = "anthropic"
    uv sync --extra anthropic
"@
    }

    'setup' {
        & uv sync --extra dev
        if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }
        Write-Host "`nReady. Next: `$env:HSCM_EDGAR_CONTACT = 'you@example.com'; .\run.ps1 fetch" -ForegroundColor Green
    }

    'fetch'    { Assert-Contact; Invoke-Hscm (@('fetch') + $Rest) }
    'sections' { Invoke-Hscm (@('sections') + $Rest) }
    'extract'  { Invoke-Hscm (@('extract', '--out', $Extractions) + $Rest) }
    'verify'   { Invoke-Hscm @('verify', $Extractions, '--out', 'data/verification-report.json') }
    'build'    { Invoke-Hscm @('build', '--extractions', $Extractions) }

    'refresh' {
        Assert-Contact
        Invoke-Hscm @('fetch')
        Invoke-Hscm @('extract', '--out', $Extractions)
        Invoke-Hscm @('verify', $Extractions, '--out', 'data/verification-report.json')
        Invoke-Hscm @('build', '--extractions', $Extractions)
        Write-Host "`nDataset rebuilt. Every page carries the newest filing date in the data." -ForegroundColor Green
    }

    'review' {
        Invoke-Hscm @('review', 'build', '--extractions', $Extractions)
        Write-Host "`nEdit data\review\entity_review_queue.csv, then: .\run.ps1 review-apply" -ForegroundColor Yellow
        Write-Host "(or: uv run hscm review apply)"
    }

    'serve' {
        Write-Host "http://localhost:8000 - Ctrl-C to stop"
        Push-Location site
        try { & uv run python -m http.server 8000 } finally { Pop-Location }
    }

    'test' {
        $env:PYTHONWARNDEFAULTENCODING = '1'
        & uv run pytest -q
    }

    'neo4j-load' { Invoke-Hscm (@('neo4j-load', '--extractions', $Extractions) + $Rest) }

    'clean' {
        foreach ($path in 'data/cache', $Extractions, 'data/verification-report.json') {
            if (Test-Path $path) { Remove-Item -Recurse -Force $path; Write-Host "removed $path" }
        }
    }
}
