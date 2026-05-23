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
| GET     | `/api/stats`                      | Aggregierte Stats              |

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

- Backup-Tabellen sind weiterhin client-seitig simuliert und werden noch nicht
  vom Backend persistiert.
- Spielerzahlen werden derzeit nicht ausgelesen (Anzeige immer `0 / 20`).
- Die "eigene .jar hochladen"-Dropzone ist weiterhin nur visuell - reale
  Uploads bitte ueber den Modrinth-Katalog installieren.
- Der Theme-Switcher ist im UI deaktiviert; die CSS-Variablen sind aber
  vorbereitet, sodass spaeter ueber `data-theme` gewechselt werden kann.
