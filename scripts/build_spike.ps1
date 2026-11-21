<#
.SYNOPSIS
    Phase 0 : compile le projet "hello" de alif_vscode-template pour M55_HP et M55_HE (DevKit-E7)
    et depose les ELF + .map dans build/spike/ pour le spike Renode.

.DESCRIPTION
    Pre-requis : scripts\install_tools.ps1 -AcceptPackLicenses (outils + packs dans tools/).
    Environnement : celui de e7twin.toolchain.env() (aucune variable codee ici en double).
    Les sorties de cbuild vont dans build/alif_template (option -O) pour ne pas polluer le clone.
    Ne flashe rien. Agent 02 (Toolchain & Build).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build_spike.ps1
#>
[CmdletBinding()]
param(
    [string]$BuildType = 'debug',
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'

$Repo     = Split-Path -Parent $PSScriptRoot
$Template = Join-Path $Repo 'tools\alif_vscode-template'
$Spike    = Join-Path $Repo 'build\spike'

# 1. Environnement depuis e7twin.toolchain (source unique)
Push-Location $Repo
try { $json = & $Python -m e7twin.toolchain --env-json } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { throw "Impossible d'interroger e7twin.toolchain avec '$Python'" }
$info = ($json -join "`n") | ConvertFrom-Json
$info.env.PSObject.Properties | ForEach-Object { Set-Item -Path "Env:$($_.Name)" -Value $_.Value }
if (@($info.missing).Count -gt 0) {
    throw ("Outils/packs manquants : " + (@($info.missing) -join ', ') + "`n-> scripts\install_tools.ps1 -AcceptPackLicenses")
}

New-Item -ItemType Directory -Force $Spike | Out-Null
$readelf = Join-Path (Split-Path -Parent (Get-Command arm-none-eabi-gcc).Source) 'arm-none-eabi-readelf.exe'

foreach ($core in @('HP', 'HE')) {
    $target = "DevKit-E7-$core"
    $ctx = "hello.$BuildType+$target"
    # Un dossier de sortie par coeur : cbuild/CMake reutilise tmp/ (cache CMake lie a un contexte).
    $OutBase = Join-Path $Repo ("build" + [char]92 + "alif_template" + [char]92 + $target)
    if (Test-Path $OutBase) { Remove-Item -Recurse -Force $OutBase }
    Write-Host "==> cbuild $ctx" -ForegroundColor Cyan
    Push-Location $Template
    try {
        & cbuild alif.csolution.yml --context $ctx --frozen-packs -O $OutBase
        if ($LASTEXITCODE -ne 0) { throw "cbuild $ctx a echoue" }
    } finally { Pop-Location }

    $elf = Get-ChildItem -Recurse -File -Path $OutBase -Filter 'hello.elf' | Select-Object -First 1
    if (-not $elf) { throw "hello.elf introuvable sous $OutBase pour $target" }
    # cdefault.yml : -Wl,-Map=linker.map (relatif au dossier de build CMake, sous tmp/)
    $map = Get-ChildItem -Recurse -File -Path $OutBase -Filter 'linker.map' | Select-Object -First 1

    $lc = $core.ToLower()
    Copy-Item -Force $elf.FullName (Join-Path $Spike "hello_$lc.elf")
    if ($map) { Copy-Item -Force $map.FullName (Join-Path $Spike "hello_$lc.map") }
    else { Write-Warning "fichier .map introuvable pour $target" }
    & $readelf -h -l -A (Join-Path $Spike "hello_$lc.elf") | Out-File -Encoding utf8 (Join-Path $Spike "hello_$lc.readelf.txt")
    & (Join-Path (Split-Path -Parent $readelf) 'arm-none-eabi-size.exe') -A (Join-Path $Spike "hello_$lc.elf") | Out-File -Encoding utf8 (Join-Path $Spike "hello_$lc.size.txt")
    Write-Host "    OK  $(Join-Path $Spike "hello_$lc.elf")  (source : $($elf.FullName))" -ForegroundColor Green
}

# Le clone du template est en lecture seule pour nous. cbuild reecrit RTE/_debug_*/RTE_Components.h
# (fins de ligne LF -> CRLF via autocrlf) : on ne signale que les vraies differences de contenu.
$dirty = & git -C $Template diff --ignore-space-at-eol --name-only 2>$null
if ($dirty) { Write-Warning "cbuild a modifie le contenu de fichiers du template :`n$($dirty -join "`n")" }
