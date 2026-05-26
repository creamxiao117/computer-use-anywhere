param(
    [string]$Python = "python",
    [switch]$OneFile,
    [switch]$Console,
    [switch]$InstallMissing,
    [switch]$Zip
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$Entry = Join-Path $ProjectRoot "run.py"
$DistDir = Join-Path $ProjectRoot "dist"
$BuildDir = Join-Path $ProjectRoot "build"
$SpecDir = Join-Path $ProjectRoot "build"
$Name = "ComputerUseAnywhere"

if (-not (Test-Path $Entry)) {
    throw "未找到入口文件：$Entry"
}

Push-Location $ProjectRoot
try {
    & $Python -m pip show pyinstaller *> $null
    if ($LASTEXITCODE -ne 0) {
        if (-not $InstallMissing) {
            throw "PyInstaller is not installed. Re-run with -InstallMissing or run: $Python -m pip install pyinstaller"
        }
        & $Python -m pip install pyinstaller
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to install PyInstaller, exit code: $LASTEXITCODE"
        }
    }

    $modeArgs = @("--onedir")
    if ($OneFile) {
        $modeArgs = @("--onefile")
    }

    $windowArgs = @("--windowed")
    if ($Console) {
        $windowArgs = @("--console")
    }

    $args = @(
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name", $Name,
        "--paths", (Join-Path $ProjectRoot "src"),
        "--exclude-module", "matplotlib",
        "--exclude-module", "scipy",
        "--exclude-module", "pandas",
        "--distpath", $DistDir,
        "--workpath", $BuildDir,
        "--specpath", $SpecDir
    ) + $modeArgs + $windowArgs + @($Entry)

    & $Python @args
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller 打包失败，退出码：$LASTEXITCODE"
    }

    if ($OneFile) {
        $OutputPath = Join-Path $DistDir "$Name.exe"
    } else {
        $OutputPath = Join-Path $DistDir "$Name\$Name.exe"
    }
    Write-Host "Build finished: $OutputPath"

    if ($Zip) {
        if ($OneFile) {
            $ZipSource = $OutputPath
        } else {
            $ZipSource = Join-Path $DistDir $Name
        }
        $ZipPath = Join-Path $DistDir "$Name-portable.zip"
        if (Test-Path $ZipPath) {
            Remove-Item -LiteralPath $ZipPath -Force
        }
        Compress-Archive -LiteralPath $ZipSource -DestinationPath $ZipPath -Force
        Write-Host "Portable zip: $ZipPath"
    }
} finally {
    Pop-Location
}
