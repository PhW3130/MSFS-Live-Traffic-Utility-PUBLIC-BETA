# MSFS Live Traffic Utility

**Real-world ADS-B traffic on a live map, with optional AI traffic injection into Microsoft Flight Simulator.**

MSFS Live Traffic Utility is a Windows desktop companion for Microsoft Flight Simulator 2020 and 2024. It shows publicly available real-world aircraft traffic on an interactive map and can optionally use SimConnect to inject compatible AI aircraft into the simulator.

The live map starts on its own and remains usable when MSFS is closed. This is an external utility, not an aircraft, scenery, or livery package.

## Features

- Live map and aircraft table with search and selection by callsign, ICAO hex, type, or registration.
- Aircraft trails, filters, altitude coloring, and emergency highlighting for applicable ADS-B emergency codes.
- Route and aircraft details when data is available from the relevant sources.
- Optional follow/TCAS view using the player's MSFS position. Enable **TCAS (MSFS)** in the toolbar; this centers the live traffic query on the current player position within an 80 km radius. It requires MSFS and a working SimConnect connection, but no AI aircraft models.
- Optional SimConnect AI traffic injection with compatible models installed locally; FSLTL models are recommended and are not included. This can be toggled independently of the TCAS view.
- Optional advanced proxy setup for relay/failover features and sampled shared aircraft history. The proxy is not required for standalone map use.
- Timestamped diagnostic logs to help investigate issues.

## Requirements

- Windows 10 or 11, 64-bit, for the packaged application.
- Internet connection for live ADS-B and map data. Coverage and availability vary by region and provider.
- For the player-position TCAS/follow view: MSFS must be running with a working SimConnect connection. AI aircraft models are not required.
- For AI traffic injection: Microsoft Flight Simulator 2020 or 2024, a working SimConnect connection, and compatible AI aircraft models installed locally.
- Python 3.10 or newer is needed only when running from source instead of using the packaged application.

## Installation

1. Download and extract the Windows archive to a folder you can write to.
2. Run `MSFS Live Traffic Utility.exe` from the extracted application folder.
3. The live map starts automatically; MSFS does not need to be running to use map mode.
4. For player-centered traffic, start MSFS and enable **TCAS (MSFS)** in the toolbar; the app queries an 80 km area around the current player position. This works without installed AI models. If the app starts before MSFS, it retries the SimConnect connection automatically. For simulator AI traffic, install compatible models and enable **MSFS AI traffic**; pausing AI injection does not stop player-position updates for TCAS.

## Data and limitations

The application can use public ADS-B sources such as adsb.lol and adsb.fi, with additional sources or proxy options depending on configuration. Coverage, aircraft details, update frequency, quotas, and availability are controlled by the data providers and are not guaranteed. Route information may be unavailable for some aircraft.

Follow each data provider's terms, attribution requirements, and usage limits. Some providers restrict use to personal or non-commercial purposes. This application is for flight simulation and informational use only; it is not an air-navigation, dispatch, or air-traffic-control tool.

## License and third-party notices

The project's original code is licensed under the GNU Affero General Public License v3.0 only (AGPL-3.0-only); see the included `LICENSE`. If you modify the program and let users interact with your modified version remotely over a network, AGPLv3 section 13 requires you to offer those users the corresponding source code.

Third-party components, assets, models, map tiles, and data remain under their respective licenses and terms; the project license does not replace those terms. See `THIRD_PARTY_NOTICES.txt` and `docs/PUBLISHING-CHECKLIST.md`. FSLTL aircraft models are not included.

## Support and feedback

For bug reports and feature requests, use the [project's GitHub repository](https://github.com/PhW3130/Live-Traffic/issues). When reporting an issue, include the application log from the `logs` folder where possible, and remove any personal or sensitive information first.

## Disclaimer

This project is not affiliated with or endorsed by Microsoft, Asobo Studio, Flightsim.to, FSLTL, ADS-B Exchange, or the ADS-B data providers mentioned above. Microsoft Flight Simulator and associated trademarks belong to their respective owners.

---

# Deutsche Version

**Echtzeitnahe Anzeige von ADS-B-Flugverkehr auf einer Live-Karte – mit optionaler KI-Verkehrsinjektion in Microsoft Flight Simulator.**

MSFS Live Traffic Utility ist ein Windows-Desktop-Begleitprogramm für Microsoft Flight Simulator 2020 und 2024. Es zeigt öffentlich verfügbare reale Flugzeugpositionen auf einer interaktiven Karte und kann optional kompatible KI-Flugzeuge über SimConnect in den Simulator einspeisen.

Die Live-Karte startet unabhängig und kann auch bei geschlossenem MSFS genutzt werden. Es handelt sich um ein externes Hilfsprogramm, nicht um ein Flugzeug-, Szenerie- oder Lackierungs-Paket.

## Funktionen

- Live-Karte und Flugzeugtabelle mit Suche und Auswahl nach Rufzeichen, ICAO-Hex, Flugzeugtyp oder Registrierung.
- Flugspuren, Filter, höhenabhängige Farben und Hervorhebung entsprechender ADS-B-Notfallcodes.
- Routen- und Flugzeuginformationen, sofern die jeweiligen Datenquellen Angaben liefern.
- Optionaler Follow-/TCAS-Modus mit der eigenen Position aus MSFS. Dazu **TCAS (MSFS)** in der Symbolleiste aktivieren; die Live-Traffic-Abfrage wird dann auf die Spielerposition und einen Radius von 80 km zentriert. Dafür sind keine KI-Modelle erforderlich.
- Optionale KI-Verkehrsinjektion über SimConnect. Dafür müssen kompatible KI-Modelle lokal installiert sein; FSLTL-Modelle werden empfohlen, sind aber nicht enthalten. Sie lässt sich unabhängig von der TCAS-Ansicht pausieren.
- Optionaler, erweiterter Proxy-Betrieb für Relay-/Failover-Funktionen und gemeinsam genutzte, abgetastete Flugspuren. Für den eigenständigen Kartenbetrieb ist kein Proxy erforderlich.
- Zeitgestempelte Diagnoseprotokolle zur Fehlersuche.

## Voraussetzungen

- Windows 10 oder 11, 64-Bit, für das fertige Anwendungspaket.
- Internetverbindung für ADS-B- und Kartendaten. Abdeckung und Verfügbarkeit unterscheiden sich je nach Region und Anbieter.
- Für den TCAS-/Follow-Modus mit Spielerposition: MSFS muss laufen und über eine funktionierende SimConnect-Verbindung erreichbar sein. KI-Flugzeugmodelle sind dafür nicht erforderlich.
- Für die KI-Verkehrsinjektion: Microsoft Flight Simulator 2020 oder 2024, eine funktionierende SimConnect-Verbindung und lokal installierte, kompatible KI-Flugzeugmodelle.
- Python 3.10 oder neuer wird nur benötigt, wenn die Anwendung aus dem Quellcode statt aus dem fertigen Paket gestartet wird.

## Installation

1. Das Windows-Archiv herunterladen und in einen beschreibbaren Ordner entpacken.
2. `MSFS Live Traffic Utility.exe` aus dem entpackten Anwendungsordner starten.
3. Die Live-Karte startet automatisch; für den Kartenmodus muss MSFS nicht laufen.
4. Für den spielerzentrierten Verkehr MSFS starten und **TCAS (MSFS)** in der Symbolleiste aktivieren; die App fragt dann den Bereich innerhalb von 80 km um die aktuelle Spielerposition ab. Dafür sind keine KI-Modelle nötig. Startet die App vor MSFS, wird die SimConnect-Verbindung automatisch erneut versucht. Für KI-Verkehr kompatible Modelle installieren und **MSFS AI-Traffic** aktivieren; das Pausieren der KI-Injektion beendet die Positionsaktualisierung für TCAS nicht.

## Daten und Einschränkungen

Die Anwendung kann öffentliche ADS-B-Quellen wie adsb.lol und adsb.fi verwenden; weitere Quellen und Proxy-Funktionen hängen von der Konfiguration ab. Abdeckung, Flugzeugdetails, Aktualisierungsintervall, Kontingente und Verfügbarkeit werden von den Datenanbietern bestimmt und können nicht garantiert werden. Für manche Flugzeuge sind keine Routendaten verfügbar.

Bitte beachte die Nutzungsbedingungen, Attributionspflichten und Limits jedes Datenanbieters. Manche Anbieter beschränken die Nutzung auf private oder nicht-kommerzielle Zwecke. Die Anwendung dient der Flugsimulation und Information, nicht der Navigation, Flugdisposition oder Flugsicherung.

## Lizenz und Drittanbieterhinweise

Der projekt-eigene Code steht unter der GNU Affero General Public License v3.0 only (AGPL-3.0-only); siehe die mitgelieferte `LICENSE`. Wenn du das Programm änderst und deine geänderte Version Nutzern über ein Netzwerk bereitstellst, verlangt Abschnitt 13 der AGPLv3, diesen Nutzern den entsprechenden Quellcode anzubieten.

Drittanbieter-Komponenten, Dateien, Modelle, Kartenkacheln und Daten bleiben jeweils unter ihren eigenen Lizenzen und Nutzungsbedingungen; die Projektlizenz ersetzt diese Bedingungen nicht. Siehe `THIRD_PARTY_NOTICES.txt` und `docs/PUBLISHING-CHECKLIST.md`. FSLTL-Flugzeugmodelle sind nicht enthalten.

## Support und Rückmeldungen

Fehlerberichte und Funktionswünsche bitte über das [GitHub-Repository des Projekts](https://github.com/PhW3130/Live-Traffic/issues) einreichen. Wenn möglich, füge das Anwendungsprotokoll aus dem Ordner `logs` bei und entferne vorher persönliche oder vertrauliche Angaben.

## Haftungshinweis

Dieses Projekt ist nicht mit Microsoft, Asobo Studio, Flightsim.to, FSLTL, ADS-B Exchange oder den genannten ADS-B-Datenanbietern verbunden und wird von ihnen nicht unterstützt. Microsoft Flight Simulator und die zugehörigen Marken sind Eigentum ihrer jeweiligen Inhaber.
