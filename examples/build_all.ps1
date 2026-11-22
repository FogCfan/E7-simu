<#
.SYNOPSIS
    Compile les firmwares de reference E7 Twin (examples/) avec cbuild et depose
    ELF, .map et .su dans build/examples/<nom>.{elf,map,su}.

.DESCRIPTION
    Pre-requis : scripts\install_tools.ps1 -AcceptPackLicenses (outils + packs dans tools/).
    Environnement : celui de e7twin.toolchain.env() (meme source que scripts\build_spike.ps1).
    Un dossier de sortie cbuild par projet (le cache CMake est lie a un contexte, cf. docs/BUILD.md piege 10).
    -fstack-usage est active dans chaque *.cproject.yml : les .su de l'application sont fusionnes en <nom>.su.
    Ne flashe rien. Agent 07 (Firmwares & Tests).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File examples\build_all.ps1
    powershell -ExecutionPolicy Bypass -File examples\build_all.ps1 -Only too_big
#>
[CmdletBinding()]
param(
    [string[]]$Only = @(),
    [string]$BuildType = 'debug',
    [string]$Python = 'py',
    [string]$PythonVersion = '-3.14'
)
$ErrorActionPreference = 'Stop'

$Repo = Split-Path -Parent $PSScriptRoot
$Ex   = Join-Path $Repo 'examples'
$Dest = Join-Path $Repo 'build\examples'

# nom du projet -> coeur ; un projet = un contexte <nom>.<type>+DevKit-E7-<coeur>
$Projects = [ordered]@{
    hello_hp       = 'HP'
    hello_he       = 'HE'
    helium_dot     = 'HP'
    too_big        = 'HE'
    stack_overflow = 'HP'
    dual_core_mhu_hp = 'HP'
    dual_core_mhu_he = 'HE'
    load_loop_hp     = 'HP'
    load_loop_he     = 'HE'
    display_hp       = 'HP'
}
if ($Only.Count -gt 0) {
    foreach ($o in $Only) { if (-not $Projects.Contains($o)) { throw "projet inconnu : $o (attendus : $($Projects.Keys -join ', '))" } }
}

# 1. Environnement depuis e7twin.toolchain (source unique)
Push-Location $Repo
$pyArgs = @(); if ($PythonVersion) { $pyArgs += $PythonVersion }; $pyArgs += @('-m', 'e7twin.toolchain', '--env-json')
try { $json = & $Python @pyArgs } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { throw "Impossible d'interroger e7twin.toolchain avec '$Python $PythonVersion'" }
$info = ($json -join "`n") | ConvertFrom-Json
$info.env.PSObject.Properties | ForEach-Object { Set-Item -Path "Env:$($_.Name)" -Value $_.Value }
if (@($info.missing).Count -gt 0) {
    throw ("Outils/packs manquants : " + (@($info.missing) -join ', ') + "`n-> scripts\install_tools.ps1 -AcceptPackLicenses")
}

New-Item -ItemType Directory -Force $Dest | Out-Null
$failed = @()

foreach ($name in $Projects.Keys) {
    if ($Only.Count -gt 0 -and $Only -notcontains $name) { continue }
    $target = "DevKit-E7-$($Projects[$name])"
    $ctx = "$name.$BuildType+$target"
    $OutBase = Join-Path $Repo ("build" + [char]92 + "examples_cbuild" + [char]92 + $name)
    if (Test-Path $OutBase) { Remove-Item -Recurse -Force $OutBase }
    Write-Host "==> cbuild $ctx" -ForegroundColor Cyan
    Push-Location $Ex
    try {
        & cbuild alif.csolution.yml --context $ctx -O $OutBase
        $rc = $LASTEXITCODE
    } finally { Pop-Location }
    if ($rc -ne 0) { Write-Warning "cbuild $ctx a echoue (code $rc)"; $failed += $name; continue }

    $elf = Get-ChildItem -Recurse -File -Path $OutBase -Filter "$name.elf" | Select-Object -First 1
    if (-not $elf) { Write-Warning "$name.elf introuvable sous $OutBase"; $failed += $name; continue }
    Copy-Item -Force $elf.FullName (Join-Path $Dest "$name.elf")

    # cdefault.yml : -Wl,-Map=linker.map (relatif au dossier de build CMake, sous tmp/)
    $map = Get-ChildItem -Recurse -File -Path $OutBase -Filter 'linker.map' | Select-Object -First 1
    if ($map) { Copy-Item -Force $map.FullName (Join-Path $Dest "$name.map") } else { Write-Warning "$name : .map introuvable" }

    # .su : uniquement ceux de l'application (pas ceux du SDK/pack, hors du dossier du projet)
    $su = Get-ChildItem -Recurse -File -Path $OutBase -Filter '*.su'
    $suOut = Join-Path $Dest "$name.su"
    if ($su) {
        Get-Content -Raw ($su | ForEach-Object { $_.FullName }) | Set-Content -Encoding ascii $suOut
    } else {
        Write-Warning "$name : aucun .su (flag -fstack-usage non pris en compte ?)"
        if (Test-Path $suOut) { Remove-Item $suOut }
    }
    Write-Host "    OK  $(Join-Path $Dest "$name.elf")  ($(@($su).Count) .su)" -ForegroundColor Green
}

if ($failed.Count -gt 0) { Write-Error ("projets en echec : " + ($failed -join ', ')); exit 1 }
Write-Host "Tous les projets compiles dans $Dest" -ForegroundColor Green
