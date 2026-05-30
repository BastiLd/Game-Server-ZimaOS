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
│   ├── index.html          # Frontend-Markup (single-page)
│   ├── style.css           # Styles (ausgelagert, v1.1.0)
│   └── app.js              # Frontend-Logik (ausgelagert, v1.1.0)
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
| POST    | `/api/servers/{id}/plugins/upload` | Eigene `.jar` hochladen (multipart) |
| GET     | `/api/servers/{id}/backups`       | Backups auflisten              |
| POST    | `/api/servers/{id}/backups`       | Backup von `/data` erstellen (tar.gz) |
| GET     | `/api/servers/{id}/backups/{name}` | Backup herunterladen          |
| DELETE  | `/api/servers/{id}/backups/{name}` | Backup loeschen               |
| POST    | `/api/servers/{id}/backups/{name}/restore` | Backup einspielen (Server-Stopp+Start) |
| GET     | `/api/auth/check`                 | Prueft, ob/welcher Token noetig ist |
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
| `CRAFTCONTROL_TOKEN`      | _(leer)_                         | Optionaler Zugriffs-Token. Wenn gesetzt, ist ein Login (Bearer-Token) noetig |
| `CRAFTCONTROL_CORS_ORIGINS` | _(leer)_                       | Kommagetrennte erlaubte Cross-Origins (Default: keine, Same-Origin) |
| `CRAFTCONTROL_BACKUP_ROOT` | `/var/lib/craftcontrol/backups` | Ablageort fuer Backups + Optimizer-Status (persistentes Volume) |
| `CRAFTCONTROL_MAX_UPLOAD_MB` | `100`                         | Maximale Groesse beim `.jar`-Upload          |
| `CRAFTCONTROL_STATS_INTERVAL` | `5`                          | Intervall (Sek.) des Hintergrund-Stats-Sammlers |

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
Es ist daher nicht fuer den ungeschuetzten Betrieb in einem oeffentlichen Netz
gedacht. Seit v1.1.0 kann ein optionaler Zugriffs-Token gesetzt werden
(`CRAFTCONTROL_TOKEN`); dann verlangt das Panel einen Login. Fuer den Betrieb
im Internet wird zusaetzlich ein Reverse-Proxy mit TLS empfohlen, ansonsten
LAN/VPN.

## Bekannte Einschraenkungen

- Crafatar braucht UUIDs, daher liefert v1.0.4+ die Avatare ueber mc-heads.net.
- playit.gg-Tunnel benoetigt beim ersten Start eine Verknuepfung ueber
  https://playit.gg/claim oder einen `SECRET_KEY`.
- Performance-Verlauf ist client-seitig (im Browser-State) - bei Reload des
  Tabs startet die History neu.

## Aenderungen

Versionsuebersicht in [`CHANGELOG.md`](./CHANGELOG.md). Highlights v1.1.0:
optionaler Login-Token, echte Backups (erstellen/herunterladen/loeschen/
einspielen) und echter `.jar`-Upload, XSS-Haertung der Konsole, ausgelagerte
`style.css`/`app.js`, nicht-blockierende Stats und reaktivierter Theme-Switcher.
