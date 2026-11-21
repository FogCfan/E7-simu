<#
.SYNOPSIS
    Installe la chaine de compilation et de simulation E7 Twin dans tools/ (sans rien installer au niveau systeme).

.DESCRIPTION
    Idempotent : chaque etape est sautee si l'outil est deja en place. Versions et SHA-256 epingles.
      - Arm GNU Toolchain 14.3.rel1 (arm-none-eabi, zip Windows)
      - CMSIS-Toolbox 2.12.0 (cbuild, csolution, cpackget)
      - Renode 1.17.0 portable Windows (autonome : runtime .NET 8.0.10 embarque, pas de .NET 10 requis)
      - Pack root CMSIS dans tools/packs + packs du template Alif :
        AlifSemiconductor::Ensemble@2.2.1, ARM::CMSIS@6.1.0, ARM::CMSIS-Compiler@2.1.0
      - Clone de alif_vscode-template (commit epingle) dans tools/alif_vscode-template

    Agent 02 (Toolchain & Build). Ne flashe jamais la carte.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install_tools.ps1
    powershell -ExecutionPolicy Bypass -File scripts\install_tools.ps1 -SkipRenode -SkipTemplate
#>
[CmdletBinding()]
param(
    [string]$ToolsDir = '',
    [switch]$SkipGcc,
    [switch]$SkipCmsisToolbox,
    [switch]$SkipRenode,
    [switch]$SkipPacks,
    [switch]$SkipTemplate,
    # Accord EXPLICITE de l'humain aux licences embarquees des packs (ARM : Apache-2.0 ;
    # AlifSemiconductor::Ensemble : licence logicielle Alif, proprietaire). Sans ce switch,
    # le script extrait les textes de licence et s'arrete (code 2) sans installer les packs.
    [switch]$AcceptPackLicenses
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# --------------------------------------------------------------------------------------------
# Versions epinglees (a modifier ici et nulle part ailleurs ; toolchain.py lit les dossiers)
# --------------------------------------------------------------------------------------------
$Gcc = @{
    Version = '14.3.rel1'
    Dir     = 'arm-gnu-toolchain-14.3.rel1'
    Url     = 'https://developer.arm.com/-/media/Files/downloads/gnu/14.3.rel1/binrel/arm-gnu-toolchain-14.3.rel1-mingw-w64-x86_64-arm-none-eabi.zip'
    Sha256  = '864c0c8815857d68a1bbba2e5e2782255bb922845c71c97636004a3d74f60986'  # .sha256asc publie par Arm
    Check   = 'bin\arm-none-eabi-gcc.exe'
}
$Ctb = @{
    Version = '2.12.0'
    Dir     = 'cmsis-toolbox-2.12.0'
    Url     = 'https://github.com/Open-CMSIS-Pack/cmsis-toolbox/releases/download/2.12.0/cmsis-toolbox-windows-amd64.zip'
    Sha256  = '07093299c702243a092966c28290af16cfc4d92ee15c288218cd052946fdb42c'  # cmsis-toolbox-checksums.txt
    Check   = 'bin\cbuild.exe'
}
$Renode = @{
    Version = '1.17.0'
    Dir     = 'renode-1.17.0'
    Url     = 'https://github.com/renode/renode/releases/download/v1.17.0/renode-1.17.0.windows-portable.zip'
    Sha256  = '18bcf145422039e8702c3e5f9864c7787561bfd5deb7a3ee6e168ecf19e2b9db'  # calcule au 1er telechargement (pas de checksum publie)
    Check   = 'renode.exe'
}
$PackIndex = 'https://www.keil.com/pack/index.pidx'
$Packs = @(
    'ARM::CMSIS@6.1.0',
    'ARM::CMSIS-Compiler@2.1.0',
    'AlifSemiconductor::Ensemble@2.2.1'
)
$Template = @{
    Url    = 'https://github.com/alifsemi/alif_vscode-template'
    Commit = 'ebbf1513edaeffad912407543fe09da3c7ec9700'
    Dir    = 'alif_vscode-template'
}

# --------------------------------------------------------------------------------------------
function Write-Step([string]$msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok([string]$msg)   { Write-Host "    OK  $msg" -ForegroundColor Green }

$Tar  = Join-Path $env:SystemRoot 'System32\tar.exe'    # bsdtar : extrait les zip bien plus vite qu'Expand-Archive
$Curl = Join-Path $env:SystemRoot 'System32\curl.exe'

function Get-Archive([hashtable]$t) {
    $dl = Join-Path $ToolsDir '_dl'
    New-Item -ItemType Directory -Force $dl | Out-Null
    $file = Join-Path $dl ([IO.Path]::GetFileName(([Uri]$t.Url).AbsolutePath))
    $ok = $false
    if (Test-Path $file) {
        $ok = ((Get-FileHash -Algorithm SHA256 $file).Hash -eq $t.Sha256.ToUpper())
        if (-not $ok) { Write-Host "    archive presente mais SHA-256 different : re-telechargement"; Remove-Item -Force $file }
    }
    if (-not $ok) {
        Write-Host "    telechargement $($t.Url)"
        & $Curl -L --fail --retry 3 -sS -o $file $t.Url
        if ($LASTEXITCODE -ne 0) { throw "Echec du telechargement : $($t.Url)" }
        $h = (Get-FileHash -Algorithm SHA256 $file).Hash
        if ($h -ne $t.Sha256.ToUpper()) { throw "SHA-256 inattendu pour $file : $h (attendu $($t.Sha256))" }
    }
    return $file
}

function Install-Zip([string]$label, [hashtable]$t) {
    Write-Step "$label $($t.Version)"
    $dest = Join-Path $ToolsDir $t.Dir
    if (Test-Path (Join-Path $dest $t.Check)) { Write-Ok "deja installe : $dest"; return }
    $zip = Get-Archive $t
    $tmp = Join-Path $ToolsDir ('_x_' + $t.Dir)
    if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
    New-Item -ItemType Directory -Force $tmp | Out-Null
    & $Tar -xf $zip -C $tmp
    if ($LASTEXITCODE -ne 0) { throw "Echec de l'extraction de $zip" }
    # Si l'archive contient un seul dossier racine, on le remonte d'un niveau.
    $children = @(Get-ChildItem -Force $tmp)
    $src = $tmp
    if ($children.Count -eq 1 -and $children[0].PSIsContainer) { $src = $children[0].FullName }
    if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
    Move-Item $src $dest
    if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
    if (-not (Test-Path (Join-Path $dest $t.Check))) { throw "Installation incomplete : $(Join-Path $dest $t.Check) absent" }
    Write-Ok "installe : $dest"
}

# --------------------------------------------------------------------------------------------
# PowerShell 5.1 : $PSScriptRoot n'est pas disponible dans les valeurs par defaut de param().
if (-not $ToolsDir) { $ToolsDir = Join-Path (Split-Path -Parent $PSScriptRoot) 'tools' }
New-Item -ItemType Directory -Force $ToolsDir | Out-Null
$ToolsDir = (Resolve-Path $ToolsDir).Path
Write-Host "Dossier outils : $ToolsDir"

if (-not $SkipGcc)          { Install-Zip 'Arm GNU Toolchain' $Gcc }
if (-not $SkipCmsisToolbox) { Install-Zip 'CMSIS-Toolbox' $Ctb }
if (-not $SkipRenode)       { Install-Zip 'Renode (portable, .NET 8 embarque)' $Renode }

# Environnement pour cpackget / cbuild (equivalent de e7twin.toolchain.env())
$GccBin = Join-Path (Join-Path $ToolsDir $Gcc.Dir) 'bin'
$CtbBin = Join-Path (Join-Path $ToolsDir $Ctb.Dir) 'bin'
$PackRoot = Join-Path $ToolsDir 'packs'
$env:CMSIS_PACK_ROOT = $PackRoot
$env:CMSIS_COMPILER_ROOT = Join-Path (Join-Path $ToolsDir $Ctb.Dir) 'etc'
$env:GCC_TOOLCHAIN_14_3_1 = $GccBin
$env:PATH = "$CtbBin;$GccBin;$env:PATH"

if (-not $SkipTemplate) {
    Write-Step "alif_vscode-template @ $($Template.Commit.Substring(0,8))"
    $tdir = Join-Path $ToolsDir $Template.Dir
    if (-not (Test-Path (Join-Path $tdir '.git'))) {
        & git clone -q $Template.Url $tdir
        if ($LASTEXITCODE -ne 0) { throw "git clone a echoue" }
    }
    $head = (& git -C $tdir rev-parse HEAD).Trim()
    if ($head -ne $Template.Commit) {
        & git -C $tdir fetch -q origin
        & git -C $tdir -c advice.detachedHead=false checkout -q $Template.Commit
        if ($LASTEXITCODE -ne 0) { throw "checkout $($Template.Commit) impossible" }
    }
    Write-Ok "template au commit epingle : $tdir"
}

$PendingLicenses = @()
if (-not $SkipPacks) {
    Write-Step "Packs CMSIS dans $PackRoot"
    $cpackget = Join-Path $CtbBin 'cpackget.exe'
    if (-not (Test-Path $cpackget)) { throw "cpackget introuvable ($cpackget) : installer CMSIS-Toolbox d'abord" }
    if (-not (Test-Path (Join-Path $PackRoot '.Web\index.pidx'))) {
        & $cpackget init $PackIndex -R $PackRoot
        if ($LASTEXITCODE -ne 0) { throw "cpackget init a echoue" }
        Write-Ok "pack root initialise"
    } else { Write-Ok "pack root deja initialise" }
    foreach ($p in $Packs) {
        $m = [regex]::Match($p, '^(?<v>[^:]+)::(?<n>[^@]+)@(?<ver>.+)$')
        $vendor = $m.Groups['v'].Value; $name = $m.Groups['n'].Value; $ver = $m.Groups['ver'].Value
        $pdir = Join-Path $PackRoot (Join-Path $vendor (Join-Path $name $ver))
        if (Test-Path $pdir) { Write-Ok "$p deja present"; continue }
        if ($AcceptPackLicenses) {
            Write-Host "    cpackget add --agree-embedded-license $p"
            & $cpackget add $p --agree-embedded-license -R $PackRoot
        } else {
            # -x : extrait la licence dans .Download et n'installe rien (pas d'invite bloquante).
            # Le nom du fichier varie selon le pack (.LICENSE, .License.txt...) : on le lit dans la sortie.
            $out = (& $cpackget add $p --extract-embedded-license -R $PackRoot 2>&1 | Out-String)
            $mm = [regex]::Match($out, 'Extracting embedded license to (?<f>[^\r\n]+)')
            if ($mm.Success) { $lic = $mm.Groups['f'].Value.Trim() } else { $lic = "(licence non extraite ; voir : cpackget add -x $p)" }
            $PendingLicenses += "$p  ->  $lic"
            continue
        }
        # cpackget renvoie 0 meme quand la licence est refusee : on verifie le dossier.
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $pdir)) { throw "cpackget add $p a echoue (pack absent de $pdir)" }
        Write-Ok "$p installe"
    }
}

if ($PendingLicenses.Count -gt 0) {
    Write-Host ""
    Write-Host "BLOQUE : packs non installes, leur licence embarquee doit etre acceptee par un humain." -ForegroundColor Yellow
    foreach ($l in $PendingLicenses) { Write-Host "    $l" -ForegroundColor Yellow }
    Write-Host "Apres lecture, relancer :  powershell -ExecutionPolicy Bypass -File scripts\install_tools.ps1 -AcceptPackLicenses" -ForegroundColor Yellow
    exit 2
}

Write-Step 'Termine. Verifier avec : python -c "from e7twin import toolchain; print(toolchain.doctor())"'
