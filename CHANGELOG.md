# Changelog

Alle nennenswerten Aenderungen an CraftControl. Format lose angelehnt an
[Keep a Changelog](https://keepachangelog.com/de/1.1.0/), Versionierung
nach [SemVer](https://semver.org/lang/de/).

## [1.0.6] - 2026-05-23

### Added
- Terminal-Output ist jetzt markierbar/kopierbar (`user-select: text` ueberschreibt das globale `none`).
- Neuer Header-Button **`[Kopieren]`**: kopiert die Auswahl oder das ganze Terminal in die Zwischenablage (mit `execCommand`-Fallback).
- Neuer Header-Button **`[Log herunterladen]`**: lädt den Terminal-Inhalt als
  `craftcontrol-<server>-terminal-<YYYY-MM-DD_HH-mm-ss>.log` rein clientseitig herunter.

### Changed
- `renderConsoleLogs` ueberspringt einen Repaint, solange der Nutzer Text im Terminal markiert hat — die Auswahl bleibt erhalten.
- Optik des Terminals (Farben, Schrift, Header, Scrollbar, Layout) bewusst unveraendert.
- FastAPI-`version` auf `1.0.6` angehoben.

## [1.0.5]
- Hotfix-Bundle: playit-Image, Hybrid-Mods (Mohist/Arclight), CPU-Skalierung,
  neuer Performance-Verlauf-Sub-Tab.

## [1.0.4]
- Spieler-API + RCON-Aktionen, Web-FTP, RAM-Optimierer, playit.gg-Tunnel,
  Overload-Warnung.

## [1.0.3]
- Server-Loeschen + Verbindungs-Info-Block.

## [1.0.2]
- Modrinth-Integration fuer Plugin/Mod-Suche, -Installation und -Verwaltung.

## [1.0.1]
- Fix: DELETE `/api/servers/{id}` darf bei Status 204 keinen Body senden.

## [1.0.0]
- Erstes Release: FastAPI-Backend + Frontend, Docker-Setup fuer ZimaOS.
