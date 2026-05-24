# CraftControl

Ein selbst gehostetes Game-Server-Panel fuer Minecraft, vorgesehen fuer ZimaOS.
Das Panel ist eine Web-Oberflaeche (HTML/CSS/JS) plus FastAPI-Backend, das
ueber den Docker-Socket Container vom Image `itzg/minecraft-server` startet,
stoppt und ueberwacht.

## Projektstruktur

```
.
├── backend/
│   ├── main.py             # FastAPI-Anwendung
│   └── requirements.txt
├── web/
│   └── index.html          # Frontend (single-page)
├── Dockerfile
├── docker-compose.yml
└── README.md
```

## Schnellstart

```bash
docker compose up -d --build
```

Anschliessend ist das Panel unter <http://HOST:8080> erreichbar.

## Wichtige Endpunkte

| Methode | Pfad                              | Zweck                          |
| ------- | --------------------------------- | ------------------------------ |
| GET     | `/api/servers`                    | Liste aller Server             |
| POST    | `/api/servers`                    | Neuen Server anlegen + starten |
| POST    | `/api/servers/{id}/start`         | Server starten                 |
| POST    | `/api/servers/{id}/stop`          | Server stoppen                 |
| POST    | `/api/servers/{id}/restart`       | Server neustarten              |
| DELETE  | `/api/servers/{id}`               | Container loeschen (Volume per `?purge=true|false`) |
| GET     | `/api/servers/{id}/logs?tail=200` | Letzte Konsolen-Zeilen         |
| POST    | `/api/servers/{id}/command`       | RCON-Befehl senden             |
| GET     | `/api/servers/{id}/plugins/search?query=&type=auto` | Modrinth-Suche (auto/mod/plugin) |
| POST    | `/api/servers/{id}/plugins/install` | Plugin/Mod von Modrinth installieren |
| GET     | `/api/servers/{id}/plugins/installed` | Installierte .jar-Dateien     |
| DELETE  | `/api/servers/{id}/plugins/installed/{filename}` | Erweiterung entfernen |
| GET     | `/api/servers/{id}/players`       | Online-Spieler (RCON `list`)   |
| POST    | `/api/servers/{id}/players/op`    | OP vergeben                    |
| POST    | `/api/servers/{id}/players/deop`  | OP entziehen                   |
| POST    | `/api/servers/{id}/players/kick`  | Spieler kicken                 |
| POST    | `/api/servers/{id}/players/ban`   | Spieler bannen                 |
| GET     | `/api/servers/{id}/files`         | Liste editierbarer Dateien     |
| GET     | `/api/servers/{id}/files/{name}`  | Datei lesen (Web-FTP)          |
| PUT     | `/api/servers/{id}/files/{name}`  | Datei schreiben (Web-FTP)      |
| PUT     | `/api/servers/{id}/optimizer`     | RAM-Optimierer aktivieren      |
| GET     | `/api/servers/{id}/tunnel`        | playit.gg Tunnel-Status        |
| POST    | `/api/servers/{id}/tunnel/start`  | playit.gg Tunnel starten       |
| POST    | `/api/servers/{id}/tunnel/stop`   | playit.gg Tunnel stoppen       |
| GET     | `/api/stats`                      | Aggregierte Stats              |
| GET     | `/api/minecraft/versions`         | Release-Versionen (Mojang-Manifest, 12 h gecached, mit Fallback) |

## Konfiguration ueber Environment-Variablen

| Variable                  | Default                          | Bedeutung                                    |
| ------------------------- | -------------------------------- | -------------------------------------------- |
| `CRAFTCONTROL_IMAGE`      | `itzg/minecraft-server:latest`   | Image, das fuer neue Server verwendet wird   |
| `CRAFTCONTROL_PORT_START` | `25565`                          | Untere Grenze des dynamischen Port-Bereichs  |
| `CRAFTCONTROL_PORT_END`   | `25600`                          | Obere Grenze                                 |
| `CRAFTCONTROL_WEB_DIR`    | `/app/web`                       | Pfad zum statischen Frontend                 |

## Wie erkennt das Panel "seine" Container?

Jeder vom Panel erzeugte Container traegt das Label
`craftcontrol.managed=true` plus weitere Labels fuer Name, Software, Version
und RAM-Allokation. Ein Neustart des Panels findet seine Server somit
zuverlaessig wieder.

## Volumes

Pro Server wird ein Docker-Volume `craftcontrol-<name>-data` angelegt, das
unter `/data` in den Minecraft-Container gemountet wird. Welt, Plugins und
`server.properties` ueberleben damit Container-Updates.

## Sicherheitshinweise

Das Panel hat ueber den Docker-Socket faktisch Root-Rechte auf dem Host.
Es ist daher nicht fuer den Betrieb in einem oeffentlichen Netz gedacht.
Setze entweder einen Reverse-Proxy mit Authentifizierung davor oder
betreibe es ausschliesslich im LAN/VPN.

## Bekannte Einschraenkungen
## Bekannte Einschraenkungen

- Backup-Tabellen sind weiterhin client-seitig simuliert und werden noch nicht
  vom Backend persistiert.
- Die "eigene .jar hochladen"-Dropzone ist weiterhin nur visuell - reale
  Uploads bitte ueber den Modrinth-Katalog installieren.
- Crafatar braucht UUIDs, daher liefert v1.0.4+ die Avatare ueber mc-heads.net.
- playit.gg-Tunnel benoetigt beim ersten Start eine Verknuepfung ueber
  https://playit.gg/claim oder einen `SECRET_KEY`.
- Performance-Verlauf ist client-seitig (im Browser-State) - bei Reload des
  Tabs startet die History neu.
- Der Theme-Switcher ist im UI deaktiviert; die CSS-Variablen sind aber
  vorbereitet, sodass spaeter ueber `data-theme` gewechselt werden kann.

## Aenderungen

Versionsuebersicht in [`CHANGELOG.md`](./CHANGELOG.md). Fuer v1.0.6 dazu:
Terminal-Inhalt ist jetzt markierbar/kopierbar, mit zwei neuen
Header-Buttons (`[Kopieren]`, `[Log herunterladen]`).
