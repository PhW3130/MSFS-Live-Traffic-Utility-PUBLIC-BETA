# Veröffentlichung: Status und offene Punkte

## Im Repository behoben bzw. automatisiert geprüft

- PR-CI führt Python-, PHP-, C++- und GUI-Wiring-Tests aus.
- Der Windows-Distributionsbuild verwendet `requirements.txt`; SimConnect ist beim Release-Build nicht mehr ein stillschweigend optionaler Build-Schritt.
- OpenStreetMap, CARTO und OpenTopoMap haben sichtbare, providerbezogene Karten-Attribution. Nicht dokumentierte Google-Tile-Endpunkte sind aus dem eingebauten Kartenmenü entfernt.
- Die Leaflet-BSD-2-Clause-Lizenz und `THIRD_PARTY_NOTICES.txt` werden mit der Anwendung ausgeliefert.
- Der Repository-Clone-Befehl und die GitHub-Release-Adresse in der README sind korrigiert.
- Die CI-Pakete verwenden weiterhin ein ausdrücklich nicht geheimes Token-Platzhalterpaar und sind nicht unverändert produktiv deploybar.

## Vor einer Veröffentlichung noch erforderlich

1. **Flugplan-/Plan-Ebene:** Es ist noch kein Flugplan-Provider und keine separate Plan-Kartenebene integriert. Im Repository gibt es keinen verifizierten, kostenlosen und zur gewünschten Nutzung berechtigenden Schedule-Feed für 10–20 Airlines. Die vorhandenen ADS-B-Quellen liefern Live-/Historienverkehr, keine veröffentlichten vollständigen Airline-Flugpläne. Ohne bestätigte Quelle darf die Anwendung keine geplanten Routen als echte Flüge vortäuschen. Vor der Implementierung müssen pro Quelle API-Dokumentation, Nutzungsrechte, Limits und gegebenenfalls lokale Zugangsdaten feststehen.
2. **MSFS-Parkplatzfüllung:** Der Füller ist bewusst best-effort: bis zur ersten fehlgeschlagenen SimConnect-Park-Anforderung oder zum konfigurierten Limit (Standard 50). SimConnect liefert keine verlässliche Anzahl freier Stände. Flughafen-ICAO-Erkennung und tatsächliches Verhalten müssen mit einer laufenden MSFS-Installation, SimConnect und den installierten FSLTL-Modellen getestet werden.
3. **Drittanbieter-Redistribution:** Vor öffentlicher Weitergabe der EXE/des vollständigen Quellpakets die Weitergaberechte der gebündelten `SimConnect.dll` und der `modelmap.txt` dokumentieren. Die Leaflet-Lizenz ist enthalten; sie klärt die Rechte an diesen anderen Dateien nicht.
4. **Produktionsdeployment:** Für die eigene PHP-Statusseite `token.txt` lokal erzeugen, `build_all.bat` auf dem Release-Stand ausführen und nur die frisch erzeugten Dateien deployen. Der von GitHub Actions nach `main` committete `rea/`-Ordner enthält absichtlich einen öffentlichen Token-Platzhalter und ist nicht produktiv einsetzbar. Vor dem Deployment `TOKEN-SETUP.txt` befolgen und ein privates Token setzen. OpenSky-OAuth-Zugangsdaten gehören ausschließlich in eine lokale `credentials.json` auf den Collector-PC; OpenSky nur im dokumentierten Forschungs-/nicht-kommerziellen Rahmen verwenden.
5. **Build-Automation:** Pull Requests werden nur validiert. Ein Push auf `main` baut die Distribution und überträgt die Dateien aus `rea/` in größenbegrenzten Teil-Commits auf einen temporären Branch; erst nach vollständigem Upload wird `main` aktualisiert. Es wird kein Actions-Artefakt hochgeladen. Dafür muss `GITHUB_TOKEN` den Schreibzugriff `contents: write` haben; Branchschutz-Regeln müssen direkte Pushes durch `github-actions[bot]` zulassen. Ein versionierter Tag `v*` löst zusätzlich den GitHub-Release-Build aus. Vor öffentlicher Weitergabe die Punkte 1–3 und den realen MSFS-Test abschließen.

## Entscheidung zu Flugplanquellen

Geplant ist eine nach Airline getrennte Adapterstruktur mit klarer `Plan`-Kennzeichnung. Es werden keine Bezahl-Feeds, kein blindes Flugnummern-Bruteforce und kein Scraping ohne nachgewiesene Erlaubnis verwendet. Schlüssel für gegebenenfalls erlaubte kostenlose APIs dürfen nur lokal auf dem Collector liegen, niemals in Client, EXE, PHP-Ausgabe, Commit oder CI-Build-Ausgabe.
