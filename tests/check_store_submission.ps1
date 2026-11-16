# Exercise publishing against in-memory Partner Center and AzCopy substitutes.
$ErrorActionPreference = 'Stop'
$publisher = Join-Path $PSScriptRoot '../packaging/publish-store.ps1'
$packageName = 'Cuvyro-1.6.3.0-x64.msix'
$appUri = 'https://manage.devcenter.microsoft.com/v1.0/my/applications/9MX46V9WW83Q'
$draftUri = "$appUri/submissions/123"
$events = [Collections.Generic.List[string]]::new()

function Assert-Check($condition, $message) {
    if (-not $condition) { throw $message }
}

function New-ExistingPackage($architecture, $version, $name) {
    [pscustomobject]@{ architecture = $architecture; version = $version; fileName = $name; fileStatus = 'Uploaded' }
}

function Invoke-RestMethod {
    param($Uri, $Method = 'Get', $Headers, $Body, $ContentType, $TimeoutSec, $MaximumRedirection)

    if ($Uri -like 'https://login.microsoftonline.com/*/oauth2/token' -and $Method -eq 'Post') {
        $events.Add('token')
        return @{ access_token = 'test-token' }
    }
    Assert-Check ($Headers.Authorization -eq 'Bearer test-token') 'Request missing the test authorization header.'
    if ($Uri -eq $appUri -and $Method -eq 'Get') {
        $events.Add('app')
        return @{
            packageIdentityName = 'CrisprLabs.Cuvyro'
            publisherName = 'CN=3E3BFB6E-6C9B-46C6-BFE3-23DEFC167689'
            lastPublishedApplicationSubmission = @{ id = '100' }
            pendingApplicationSubmission = if ($scenario -in @('existing draft', 'pending version')) { @{ id = '101' } }
        }
    }
    switch ("$Method $Uri") {
        "Get $appUri/submissions/100" {
            $events.Add('live')
            $price = if ($scenario -eq 'unsupported price') { 'Base' } else { 'Free' }
            return @{
                pricing = @{ priceId = $price }
                applicationPackages = @(New-ExistingPackage 'X64' '1.6.2.7' 'previous.msix')
            }
        }
        "Get $appUri/submissions/101" {
            $events.Add('pending')
            if ($scenario -eq 'pending version') {
                return @{ status = 'CommitStarted'; applicationPackages = @(@{ fileName = $packageName; fileStatus = 'PendingUpload' }) }
            }
            return @{ status = 'PendingCommit' }
        }
        "Post $appUri/submissions" {
            $events.Add('create')
            if ($scenario -eq 'draft race') { throw '409: Another submission is active.' }
            return [pscustomobject]@{
                id = '123'
                status = 'PendingCommit'
                fileUploadUrl = 'https://example.blob.core.windows.net/ingestion/draft?sig=test%2Btoken'
                pricing = @{ priceId = 'Free'; marketSpecificPricings = @{ IE = 'Free' } }
                listings = [pscustomobject]@{
                    'en-us' = [pscustomobject]@{
                        baseListing = [pscustomobject]@{
                            description = 'Existing app description.'
                            images = @([pscustomobject]@{
                                fileName = 'https://store.example.test/listing.png'
                                fileStatus = 'PendingUpload'
                            })
                        }
                    }
                }
                targetPublishMode = 'Immediate'
                applicationPackages = @(
                    (New-ExistingPackage 'X64' '1.6.2.0' 'previous.msix')
                    (New-ExistingPackage 'ARM64' '1.6.2.0' 'arm.msix')
                )
            }
        }
        "Put $draftUri" {
            $events.Add('update new draft')
            $payload = $Body | ConvertFrom-Json
            Assert-Check (-not $payload.fileUploadUrl) 'SAS URL must not be sent in the update body.'
            Assert-Check ($payload.pricing.priceId -eq 'Free' -and $payload.pricing.marketSpecificPricings.IE -eq 'Free') 'Pricing changed.'
            Assert-Check ($payload.targetPublishMode -eq 'Immediate') 'Publish mode changed.'
            Assert-Check ($payload.listings.'en-us'.baseListing.description -eq 'Existing app description.') 'Listing changed.'
            Assert-Check ($payload.applicationPackages[0].fileStatus -eq 'PendingDelete') 'Previous x64 package was not replaced.'
            Assert-Check ($payload.applicationPackages[1].fileStatus -eq 'Uploaded') 'ARM64 package was changed.'
            $script:imageEntry = $payload.listings.'en-us'.baseListing.images[0].fileName
            Assert-Check ($script:imageEntry -match '^[0-9a-f-]+\.png$') 'The existing image is not referenced in the upload ZIP.'
            return @{ id = '123'; status = 'PendingCommit'; applicationPackages = $payload.applicationPackages }
        }
        "Post $draftUri/commit" {
            $events.Add('commit new draft')
            return @{ status = 'CommitStarted' }
        }
        { $_ -in @("Get $draftUri/status", "Get $appUri/submissions/101/status") } {
            $events.Add('poll commit')
            if ($scenario -eq 'rejected commit') { return @{ status = 'PreProcessingFailed' } }
            return @{ status = 'PreProcessing' }
        }
        default { throw "Unexpected Store request: $Method." }
    }
}

function Invoke-WebRequest {
    param($Uri, $OutFile, $TimeoutSec)
    Assert-Check ($Uri.AbsoluteUri -eq 'https://store.example.test/listing.png') 'Unexpected listing image source.'
    $events.Add('copy existing listing image')
    Set-Content -LiteralPath $OutFile -Value 'test image'
}

function azcopy {
    $events.Add('upload')
    Assert-Check ($args[0] -eq 'copy' -and $args[2] -eq 'https://example.blob.core.windows.net/ingestion/draft?sig=test%2Btoken') 'Unexpected Azure upload destination.'
    $archive = [IO.Compression.ZipFile]::OpenRead($args[1])
    try {
        Assert-Check ($archive.Entries.Count -eq 2 -and $archive.GetEntry($packageName) -and $archive.GetEntry($script:imageEntry)) 'Wrong contents in the upload ZIP.'
    } finally {
        $archive.Dispose()
    }
    $global:LASTEXITCODE = if ($scenario -eq 'upload failed') { 1 } else { 0 }
}

function Start-Sleep { param($Seconds) }

function Invoke-Scenario($scenario, $sequence, $failure = '', [switch]$CheckAccess) {
    $events.Clear()
    $actualFailure = ''
    try {
        & $publisher -CheckAccess:$CheckAccess
    } catch {
        $actualFailure = $_.Exception.Message
    }
    if ($failure) {
        Assert-Check ($actualFailure.Contains($failure)) "$scenario failed unexpectedly: $actualFailure"
    } else {
        Assert-Check (-not $actualFailure) "$scenario failed: $actualFailure"
    }
    Assert-Check (($events -join ',') -eq $sequence) "$scenario performed unexpected steps: $events"
    Write-Host "Passed: $scenario"
}

$original = Get-Location
$directory = New-Item -ItemType Directory -Path (Join-Path ([IO.Path]::GetTempPath()) "cuvyro-store-test-$([guid]::NewGuid())")
$envNames = @('STORE_TENANT_ID', 'STORE_CLIENT_ID', 'STORE_CLIENT_SECRET', 'STORE_VERSION', 'STORE_SHA256')
$previousEnv = @{}
foreach ($name in $envNames) { $previousEnv[$name] = [Environment]::GetEnvironmentVariable($name) }
try {
    Set-Location -LiteralPath $directory.FullName
    $null = New-Item -ItemType Directory -Path dist
    Set-Content -LiteralPath "dist/$packageName" -Value 'test MSIX'
    $env:STORE_TENANT_ID = '00000000-0000-0000-0000-000000000001'
    $env:STORE_CLIENT_ID = 'test-client'
    $env:STORE_CLIENT_SECRET = 'test-secret'
    $env:STORE_VERSION = '1.6.3.0'
    $env:STORE_SHA256 = (Get-FileHash -LiteralPath "dist/$packageName").Hash
    $uploadSteps = 'token,app,live,create,copy existing listing image,update new draft,upload'
    $commitSteps = "$uploadSteps,commit new draft,poll commit"

    Invoke-Scenario 'read access' 'token,app,live' -CheckAccess
    Invoke-Scenario 'existing draft' 'token,app,live,pending' 'already has submission 101'
    Invoke-Scenario 'draft race' 'token,app,live,create' '409: Another submission'
    Invoke-Scenario 'unsupported price' 'token,app,live' 'supported base price'
    Invoke-Scenario 'pending version' 'token,app,live,pending,poll commit'
    Invoke-Scenario 'upload failed' $uploadSteps 'Package upload failed'
    Invoke-Scenario 'rejected commit' $commitSteps 'PreProcessingFailed'
    Invoke-Scenario 'accepted commit' $commitSteps
    $env:STORE_SHA256 = '0' * 64
    Invoke-Scenario 'package hash mismatch' '' 'does not match the SHA-256'
} finally {
    Set-Location -LiteralPath $original.Path
    Remove-Item -LiteralPath $directory.FullName -Recurse -Force
    foreach ($name in $envNames) { [Environment]::SetEnvironmentVariable($name, $previousEnv[$name]) }
}
