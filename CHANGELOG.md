# Changelog

Alle nennenswerten Aenderungen an CraftControl. Format lose angelehnt an
[Keep a Changelog](https://keepachangelog.com/de/1.1.0/), Versionierung
nach [SemVer](https://semver.org/lang/de/).

## [1.0.7] - 2026-05-24

### Fixed
- **playit.gg Tunnel**: Image strikt auf `playitcloud/playit:latest` umgestellt
  (offizielles Docker-Image). `playitgg/playit` und `ghcr.io/playit-cloud/playit-cli`
  sind komplett raus, kein Fallback. Image-Pull-Fehler liefern jetzt `ok:false`
  + verständliche Message statt 500 ohne Details.
- **Modrinth-Plugin-Filter**: Kein `project_type:plugin` mehr - Plugins werden
  über Loader-Kategorien (`paper/spigot/bukkit/purpur/sponge/folia`) gefiltert,
  Mods über `fabric/forge/neoforge/quilt`. Hybrid-Software (Mohist/Arclight/Magma)
  feuert beide Suchen und mergt deduped.
- **Optionale Config-Dateien**: Fehlende `bukkit.yml`/`spigot.yml`/`paper-global.yml`
  zeigen keine roten Fehler mehr. Editor öffnet leer mit Hinweisbanner
  *"Datei existiert noch nicht. Beim Speichern wird sie erstellt."*

### Added
- **Tunnel-Wizard**: 4-Schritte-Statusleiste *Image gezogen → Agent gestartet →
  Token/Auth nötig → Tunnel aktiv*. Claim-Link wird aus den playit-Logs extrahiert,
  Logauszug ist aufklappbar.
- **`GET /api/minecraft/versions`**: Mojang piston-meta-Manifest
  (`version_manifest_v2.json`) mit 12 h Cache + statischem Fallback. ~85
  Release-Versionen von 1.21.10 zurueck bis 1.0.
- **Plugin-Karten**: Klassifizierungs-Badge `PLUGIN/MOD/HYBRID/UNKNOWN`, Loader-
  Pills (paper, spigot, fabric, ...), Anzeige des Zielordners
  (`/data/plugins` vs `/data/mods`), Warnung bei Client-only-Mods
  (`server_side: unsupported`).
- **Hybrid-Install**: Bei Hybrid-Erweiterungen fragt das UI "plugins" vs "mods"
  ab und sendet das Ziel über `target` an `/api/servers/{id}/plugins/install`.
- Neuer Filter-Wert *Alle kompatiblen* im Plugin-Store-Dropdown.

### Changed
- `SOFTWARE_PROFILE` erweitert: Purpur/Magma sind jetzt benannte Profile,
  Mohist/Arclight nutzen Hybrid-Loader-Liste.
- Tunnel-Container-Naming: `craftcontrol-playit-<server_id>`,
  Config-Volume `craftcontrol-playit-<server_id>:/etc/playit` (überlebt Neustarts).
- `network_mode` per `CRAFTCONTROL_PLAYIT_NETWORK` ENV (`host` oder Legacy
  `container:<name>`).
- FastAPI-`version` auf `1.0.7` angehoben.

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
