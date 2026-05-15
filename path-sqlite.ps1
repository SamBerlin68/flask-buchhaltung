# sqlite-path.ps1

$pathToAdd = "D:\sqlite\sqlite-tools\"

# Hole aktuellen Benutzer-PATH
$currentPath = [Environment]::GetEnvironmentVariable("Path", "User")

# Prüfe, ob der Pfad bereits enthalten ist
if ($currentPath -split ";" -contains $pathToAdd) {
    Write-Host "✅ Pfad ist bereits in der Umgebungsvariablen enthalten."
}
else {
    # Hänge den Pfad an
    $newPath = "$currentPath;$pathToAdd"
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    Write-Host "✅ Pfad erfolgreich hinzugefügt. Bitte starte dein Terminal neu."
}
