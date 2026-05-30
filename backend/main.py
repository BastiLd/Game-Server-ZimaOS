"""
CraftControl backend (FastAPI).

Steuert Minecraft-Server-Container (itzg/minecraft-server) ueber den
Docker-Socket. Vorgesehen fuer den Einsatz in einem Container auf ZimaOS,
mit gemountetem /var/run/docker.sock.

API-Endpunkte:
    GET    /api/servers                    -> Liste aller verwalteten Server
    POST   /api/servers                    -> Neuen Server anlegen + starten
    GET    /api/servers/{id}               -> Detail eines Servers
    POST   /api/servers/{id}/start         -> Server starten
    POST   /api/servers/{id}/stop          -> Server stoppen
    POST   /api/servers/{id}/restart       -> Server neustarten
    DELETE /api/servers/{id}               -> Server loeschen (Container + Volume)
    GET    /api/servers/{id}/logs          -> Letzte Konsolen-Zeilen
    POST   /api/servers/{id}/command       -> RCON Befehl senden
    GET    /api/stats                      -> Aggregierte Stats fuer Dashboard
"""

from __future__ import annotations

import gzip
import hmac
import io
import json
import logging
import os
import re
import shlex
import tarfile
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import docker
import httpx
from docker.errors import APIError, NotFound
from docker.models.containers import Container
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("craftcontrol")

# ---------------------------------------------------------------------------
# Konfiguration ueber ENV
# ---------------------------------------------------------------------------
MANAGED_LABEL = "craftcontrol.managed"
NAME_LABEL = "craftcontrol.name"
SOFTWARE_LABEL = "craftcontrol.software"
VERSION_LABEL = "craftcontrol.version"
RAM_LABEL = "craftcontrol.ram"
OPTIMIZER_LABEL = "craftcontrol.optimizer"           # 'true'/'false'
TUNNEL_FOR_LABEL = "craftcontrol.tunnel_for"         # value = parent container name (auf playit-Sidecar)
TUNNEL_KIND_LABEL = "craftcontrol.tunnel_kind"       # 'playit'

CPU_OVERLOAD_THRESHOLD = float(os.getenv("CRAFTCONTROL_CPU_OVERLOAD", "90"))
IDLE_OPTIMIZER_MINUTES = int(os.getenv("CRAFTCONTROL_IDLE_MINUTES", "30"))
PLAYIT_IMAGE = os.getenv("CRAFTCONTROL_PLAYIT_IMAGE", "playitcloud/playit:latest")

MC_IMAGE = os.getenv("CRAFTCONTROL_IMAGE", "itzg/minecraft-server:latest")
DATA_ROOT = os.getenv("CRAFTCONTROL_DATA_ROOT", "/data/craftcontrol")
PORT_RANGE_START = int(os.getenv("CRAFTCONTROL_PORT_START", "25565"))
PORT_RANGE_END = int(os.getenv("CRAFTCONTROL_PORT_END", "25600"))
WEB_DIR = Path(os.getenv("CRAFTCONTROL_WEB_DIR", "/app/web"))

# v1.1.0: Auth-Token (optional). Wenn gesetzt, verlangt das Backend einen
# 'Authorization: Bearer <token>'-Header auf allen /api/*-Endpunkten.
AUTH_TOKEN = os.getenv("CRAFTCONTROL_TOKEN", "").strip()

# v1.1.0: CORS. Standardmaessig KEIN Cross-Origin (Frontend wird Same-Origin
# ausgeliefert). Bei Bedarf kommagetrennte Origin-Liste setzen.
CORS_ORIGINS = [o.strip() for o in os.getenv("CRAFTCONTROL_CORS_ORIGINS", "").split(",") if o.strip()]

# v1.1.0: Pfade fuer persistente Panel-Daten (Backups + Optimizer-Status).
# State-Datei liegt INNERHALB des Backup-Roots, damit sie auf demselben
# persistenten Volume liegt und Container-Neuerstellungen ueberlebt.
BACKUP_ROOT = Path(os.getenv("CRAFTCONTROL_BACKUP_ROOT", "/var/lib/craftcontrol/backups"))
STATE_DIR = Path(os.getenv("CRAFTCONTROL_STATE_DIR", str(BACKUP_ROOT)))
OPTIMIZER_STATE_FILE = STATE_DIR / ".optimizer-state.json"
MAX_UPLOAD_MB = int(os.getenv("CRAFTCONTROL_MAX_UPLOAD_MB", "100"))

# Mapping von UI-Software-Namen zu itzg/minecraft-server TYPE-Werten
# https://docker-minecraft-server.readthedocs.io/en/latest/types-and-platforms/
SOFTWARE_TYPE_MAP: Dict[str, str] = {
    "Vanilla": "VANILLA",
    "Spigot": "SPIGOT",
    "Paper": "PAPER",
    "Purpur": "PURPUR",
    "Forge": "FORGE",
    "Fabric": "FABRIC",
    "Mohist": "MOHIST",
    "Arclight": "ARCLIGHT",
    "Magma": "MAGMA",
}

# Welche Loader-Kategorien laden wir bei Modrinth fuer Plugins bzw. Mods?
# Wichtig: Modrinth nutzt project_type:mod fuer "Server-Modifikationen"
# (inkl. Bukkit-Plugins). Den Plugin-Kontext liefert die Loader-Kategorie.
PLUGIN_LOADERS: List[str] = ["paper", "spigot", "bukkit", "purpur", "sponge", "folia"]
MOD_LOADERS:    List[str] = ["fabric", "forge", "neoforge", "quilt"]

# Pro Server-Software: in welches Verzeichnis installiert wird, welche
# Loader-Kategorien zu nutzen sind und ob Plugins/Mods/beides erlaubt sind.
#   - "kind": "plugin" | "mod" | "any"
#   - "any" = Hybrid (Mohist/Arclight/Magma/Vanilla-default fuer Suche)
SOFTWARE_PROFILE: Dict[str, Dict[str, Any]] = {
    "Vanilla":  {"dir": "plugins", "loaders": [],                          "kind": "any"},
    "Spigot":   {"dir": "plugins", "loaders": ["spigot", "bukkit"],        "kind": "plugin"},
    "Paper":    {"dir": "plugins", "loaders": ["paper", "spigot", "bukkit"], "kind": "plugin"},
    "Purpur":   {"dir": "plugins", "loaders": ["purpur", "paper", "spigot", "bukkit"], "kind": "plugin"},
    "Mohist":   {"dir": "plugins", "loaders": ["paper", "spigot", "bukkit", "forge"], "kind": "any"},
    "Arclight": {"dir": "plugins", "loaders": ["paper", "spigot", "bukkit", "forge"], "kind": "any"},
    "Magma":    {"dir": "plugins", "loaders": ["paper", "spigot", "bukkit", "forge"], "kind": "any"},
    "Forge":    {"dir": "mods",    "loaders": ["forge", "neoforge"],       "kind": "mod"},
    "Fabric":   {"dir": "mods",    "loaders": ["fabric", "quilt"],         "kind": "mod"},
}

MODRINTH_BASE = "https://api.modrinth.com/v2"
MODRINTH_UA = "CraftControl/1.0 (+https://github.com/BastiLd/Game-Server-ZimaOS)"

# ---------------------------------------------------------------------------
# Docker-Client (lazy, damit der Service auch ohne Docker startet)
# ---------------------------------------------------------------------------
_docker_lock = threading.Lock()
_docker_client: Optional[docker.DockerClient] = None


def docker_client() -> docker.DockerClient:
    global _docker_client
    with _docker_lock:
        if _docker_client is None:
            _docker_client = docker.from_env()
        return _docker_client


# ---------------------------------------------------------------------------
# v1.1.0: Hintergrund-Stats-Cache (nicht-blockierend)
# c.stats(stream=False) dauert ~1-2 s pro Container. Frueher wurde das bei
# jedem /api/servers-Aufruf synchron + seriell gemacht. Jetzt aktualisiert ein
# Hintergrund-Thread den Cache, und die DTOs lesen nur noch daraus.
# ---------------------------------------------------------------------------
_stats_lock = threading.Lock()
_STATS_CACHE: Dict[str, Dict[str, float]] = {}     # container.name -> {"cpu","ram_mb","ts"}
_STATS_REFRESH_SECONDS = int(os.getenv("CRAFTCONTROL_STATS_INTERVAL", "5"))


# ---------------------------------------------------------------------------
# Pydantic-Modelle
# ---------------------------------------------------------------------------
class ServerCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=40)
    software: str = Field(default="Paper")
    version: str = Field(default="1.20.4")
    ram: int = Field(default=4, ge=1, le=32)
    eula: bool = Field(default=True)


class CommandRequest(BaseModel):
    command: str


class PluginInstallRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    version_id: Optional[str] = None  # falls eine bestimmte Version gewuenscht ist
    target: Optional[str] = None      # 'plugins' | 'mods' (Override fuer Hybride)


class PlayerActionRequest(BaseModel):
    player: str = Field(..., min_length=1, max_length=32)
    reason: Optional[str] = None


class FileWriteRequest(BaseModel):
    content: str


class OptimizerRequest(BaseModel):
    enabled: bool


class TunnelStartRequest(BaseModel):
    secret: Optional[str] = None  # playit secret key (optional)


class ServerInfo(BaseModel):
    id: str
    container_id: str
    name: str
    status: str
    software: str
    version: str
    ram_max: int
    ram_used: float
    cpu_used: float
    players_current: int
    players_max: int
    port: Optional[int]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _slug(name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in name.lower())
    return f"craftcontrol-{safe[:32].strip('-_') or 'server'}"


def _is_managed(c: Container) -> bool:
    return c.labels.get(MANAGED_LABEL) == "true"


def _list_managed_containers() -> List[Container]:
    return [c for c in docker_client().containers.list(all=True) if _is_managed(c)]


def _used_ports() -> set[int]:
    used: set[int] = set()
    for c in _list_managed_containers():
        for binds in (c.attrs.get("HostConfig", {}).get("PortBindings") or {}).values():
            for b in binds or []:
                try:
                    used.add(int(b.get("HostPort")))
                except (TypeError, ValueError):
                    pass
    return used


def _next_free_port() -> int:
    used = _used_ports()
    for p in range(PORT_RANGE_START, PORT_RANGE_END + 1):
        if p not in used:
            return p
    raise HTTPException(503, f"Kein freier Port im Bereich {PORT_RANGE_START}-{PORT_RANGE_END}")


def _status_from_container(c: Container) -> str:
    """Mappt Docker-Status auf das UI-Vokabular."""
    s = (c.status or "").lower()
    if s == "running":
        # 'health' state nutzen, wenn vorhanden
        health = (c.attrs.get("State", {}).get("Health") or {}).get("Status")
        if health == "starting":
            return "starting"
        return "running"
    if s in ("created", "restarting"):
        return "starting"
    if s == "removing":
        return "stopping"
    return "offline"


def _measure_stats(c: Container) -> Dict[str, float]:
    """Liest CPU/RAM aus einem Stats-Snapshot. Bei Fehler -> Nullen.

    ACHTUNG: blockiert ~1-2 s (Docker holt zwei Snapshots). Wird daher nur vom
    Hintergrund-Thread aufgerufen, nicht im Request-Pfad.

    v1.0.5: Die CPU-Auslastung wird *normalisiert* auf den gesamten Host
    (0-100 %), passend zum ZimaOS-Systemdashboard. Wenn ein Container alle
    Kerne voll auslastet, ergibt das hier 100 %, nicht 400 %.
    """
    if (c.status or "").lower() != "running":
        return {"cpu": 0.0, "ram_mb": 0.0}
    try:
        stats = c.stats(stream=False)
    except APIError as exc:
        log.warning("Stats fuer %s nicht abrufbar: %s", c.name, exc)
        return {"cpu": 0.0, "ram_mb": 0.0}

    # CPU (Linux-Stil) - normalisiert auf Host (0-100 %).
    cpu_pct = 0.0
    try:
        cpu = stats["cpu_stats"]
        pre = stats["precpu_stats"]
        cpu_delta = cpu["cpu_usage"]["total_usage"] - pre["cpu_usage"]["total_usage"]
        sys_delta = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
        # Anzahl Kerne: erst aus stats, dann fallback auf os.cpu_count()
        online = cpu.get("online_cpus") \
                 or len(cpu["cpu_usage"].get("percpu_usage") or []) \
                 or os.cpu_count() \
                 or 1
        if cpu_delta > 0 and sys_delta > 0:
            # Klassische docker-CLI-Formel ergibt "% pro Kern, summiert".
            # Wir teilen wieder durch die Kernzahl, um den Anteil vom Host
            # abzubilden, und clampen sicherheitshalber zwischen 0 und 100.
            raw = (cpu_delta / sys_delta) * online * 100.0
            cpu_pct = max(0.0, min(100.0, raw / online))
    except (KeyError, TypeError):
        cpu_pct = 0.0

    # RAM
    ram_mb = 0.0
    try:
        mem = stats["memory_stats"]
        usage = mem.get("usage", 0)
        # cache abziehen, damit der Wert "echtem" RAM-Verbrauch entspricht
        cache = (mem.get("stats") or {}).get("cache", 0)
        ram_mb = max(0.0, (usage - cache) / 1024 / 1024)
    except (KeyError, TypeError):
        ram_mb = 0.0

    return {"cpu": round(cpu_pct, 1), "ram_mb": round(ram_mb, 1)}


def _cached_stats(name: str) -> Dict[str, float]:
    """Liest den zuletzt gemessenen Stats-Snapshot aus dem Cache (nie blockierend)."""
    with _stats_lock:
        cached = _STATS_CACHE.get(name)
    if cached:
        return {"cpu": cached.get("cpu", 0.0), "ram_mb": cached.get("ram_mb", 0.0)}
    return {"cpu": 0.0, "ram_mb": 0.0}


def _stats_refresher_loop() -> None:
    """Hintergrund-Thread: misst CPU/RAM aller laufenden Container und cached sie."""
    while True:
        try:
            running = [c for c in _list_managed_containers() if (c.status or "").lower() == "running"]
            seen = set()
            for c in running:
                seen.add(c.name)
                stats = _measure_stats(c)
                stats["ts"] = time.time()
                with _stats_lock:
                    _STATS_CACHE[c.name] = stats
            # Eintraege fuer nicht mehr laufende Container auf 0 setzen / entfernen
            with _stats_lock:
                for stale in [n for n in _STATS_CACHE if n not in seen]:
                    _STATS_CACHE[stale] = {"cpu": 0.0, "ram_mb": 0.0, "ts": time.time()}
        except Exception as exc:  # noqa: BLE001
            log.warning("Stats-Refresher Fehler: %s", exc)
        time.sleep(_STATS_REFRESH_SECONDS)


def _host_port(c: Container) -> Optional[int]:
    """Erste gemappte 25565 host port (TCP)."""
    try:
        binds = (c.attrs.get("NetworkSettings", {}).get("Ports") or {}).get("25565/tcp")
        if binds:
            return int(binds[0]["HostPort"])
    except (KeyError, ValueError, TypeError):
        return None
    return None


def _ram_label_to_int(value: Optional[str], fallback: int = 4) -> int:
    if not value:
        return fallback
    try:
        return int(value)
    except ValueError:
        return fallback


def _container_to_dto(c: Container, with_stats: bool = True) -> Dict[str, Any]:
    name = c.labels.get(NAME_LABEL) or c.name
    software = c.labels.get(SOFTWARE_LABEL) or "Vanilla"
    version = c.labels.get(VERSION_LABEL) or "latest"
    ram_max = _ram_label_to_int(c.labels.get(RAM_LABEL))
    optimizer_on = _optimizer_enabled_for(c)

    status = _status_from_container(c)
    stats = _cached_stats(c.name) if with_stats else {"cpu": 0.0, "ram_mb": 0.0}
    ram_used_gb = round(stats["ram_mb"] / 1024, 2)

    # Spieler-Cache (befuellt durch /players-Endpoint und Idle-Watchdog)
    with _players_lock:
        cached_players = dict(_PLAYERS_CACHE.get(c.name) or {})
    players_current = cached_players.get("count", 0)
    players_max = cached_players.get("max", 20)

    return {
        "id": c.name,                  # stabiler Identifier nach aussen
        "container_id": c.id[:12],
        "name": name,
        "status": status,
        "software": software,
        "version": version,
        "ram_max": ram_max,
        "ram_used": ram_used_gb,
        "ram_pct": round(min(100.0, (ram_used_gb / ram_max * 100.0) if ram_max else 0.0), 1),
        "cpu_used": stats["cpu"],
        "overloaded": stats["cpu"] >= CPU_OVERLOAD_THRESHOLD,
        "players_current": players_current,
        "players_max": players_max,
        "port": _host_port(c),
        "optimizer": optimizer_on,
    }


def _get_container(server_id: str) -> Container:
    try:
        c = docker_client().containers.get(server_id)
    except NotFound:
        raise HTTPException(404, f"Server '{server_id}' nicht gefunden")
    if not _is_managed(c):
        raise HTTPException(403, "Container wird nicht von CraftControl verwaltet")
    return c


# ---------------------------------------------------------------------------
# FastAPI-App
# ---------------------------------------------------------------------------
@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Persistierten Optimizer-Status laden
    _load_optimizer_state()
    # Hintergrund-Threads starten (Idle-Watchdog + Stats-Refresher)
    threading.Thread(target=_idle_watchdog_loop, name="idle-watchdog", daemon=True).start()
    threading.Thread(target=_stats_refresher_loop, name="stats-refresher", daemon=True).start()
    log.info("CraftControl gestartet (Auth: %s, CORS-Origins: %s)",
             "an" if AUTH_TOKEN else "aus", CORS_ORIGINS or "keine")
    yield


app = FastAPI(title="CraftControl Backend", version="1.1.0", lifespan=_lifespan)

# CORS: standardmaessig kein Cross-Origin (Frontend ist Same-Origin).
if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Accept"],
    )


# ---------------------------------------------------------------------------
# v1.1.0: Optionale Token-Authentifizierung
# Nur aktiv, wenn CRAFTCONTROL_TOKEN gesetzt ist. Schuetzt alle /api/*-Pfade
# (ausser Health und Auth-Check). Frontend/Static bleiben oeffentlich, damit
# der Login-Screen ausgeliefert werden kann.
# ---------------------------------------------------------------------------
_AUTH_EXEMPT_PATHS = {"/api/health", "/api/auth/check"}


def _token_from_request(request: Request) -> str:
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return ""


def _token_valid(token: str) -> bool:
    if not AUTH_TOKEN:
        return True
    return bool(token) and hmac.compare_digest(token, AUTH_TOKEN)


@app.middleware("http")
async def _auth_middleware(request: Request, call_next):
    if AUTH_TOKEN and request.url.path.startswith("/api/") and request.url.path not in _AUTH_EXEMPT_PATHS:
        if not _token_valid(_token_from_request(request)):
            return JSONResponse(status_code=401, content={"detail": "Nicht autorisiert"})
    return await call_next(request)


@app.get("/api/auth/check")
def auth_check(request: Request) -> Dict[str, Any]:
    """Sagt dem Frontend, ob ein Token noetig ist und ob der mitgesendete passt."""
    return {
        "auth_required": bool(AUTH_TOKEN),
        "ok": _token_valid(_token_from_request(request)),
    }


# -------- API: Servers ------------------------------------------------------
@app.get("/api/servers")
def list_servers() -> List[Dict[str, Any]]:
    return [_container_to_dto(c) for c in _list_managed_containers()]


@app.get("/api/servers/{server_id}")
def get_server(server_id: str) -> Dict[str, Any]:
    return _container_to_dto(_get_container(server_id))


@app.post("/api/servers", status_code=201)
def create_server(payload: ServerCreate) -> Dict[str, Any]:
    if payload.software not in SOFTWARE_TYPE_MAP:
        raise HTTPException(400, f"Unbekannte Software: {payload.software}")

    container_name = _slug(payload.name)
    try:
        existing = docker_client().containers.get(container_name)
        # Wenn schon vorhanden -> 409
        if _is_managed(existing):
            raise HTTPException(409, f"Server '{payload.name}' existiert bereits")
    except NotFound:
        pass

    port = _next_free_port()
    volume_name = f"{container_name}-data"

    env = {
        "EULA": "TRUE" if payload.eula else "FALSE",
        "TYPE": SOFTWARE_TYPE_MAP[payload.software],
        "VERSION": payload.version,
        "MEMORY": f"{payload.ram}G",
        "TZ": "Europe/Berlin",
    }

    labels = {
        MANAGED_LABEL: "true",
        NAME_LABEL: payload.name,
        SOFTWARE_LABEL: payload.software,
        VERSION_LABEL: payload.version,
        RAM_LABEL: str(payload.ram),
        OPTIMIZER_LABEL: "false",
    }

    log.info("Erstelle Server '%s' auf Port %d (%s %s)",
             payload.name, port, payload.software, payload.version)

    try:
        container = docker_client().containers.run(
            image=MC_IMAGE,
            name=container_name,
            detach=True,
            tty=True,
            stdin_open=True,
            environment=env,
            labels=labels,
            ports={"25565/tcp": port},
            volumes={volume_name: {"bind": "/data", "mode": "rw"}},
            mem_limit=f"{payload.ram}g",
            restart_policy={"Name": "unless-stopped"},
        )
    except APIError as exc:
        log.error("Docker-Fehler: %s", exc)
        raise HTTPException(500, f"Docker konnte den Server nicht erstellen: {exc.explanation or exc}")

    # kurz warten, damit Docker den Status uebernehmen kann
    time.sleep(0.4)
    container.reload()
    return _container_to_dto(container, with_stats=False)


@app.delete("/api/servers/{server_id}")
def delete_server(server_id: str, purge: bool = True) -> Dict[str, Any]:
    """
    Loescht den Container. Standardmaessig (`purge=true`) wird zusaetzlich
    das zugehoerige named-Volume `<container>-data` entfernt, damit auch die
    Welt sauber von der Platte verschwindet.
    Mit `?purge=false` bleibt das Volume erhalten.
    """
    c = _get_container(server_id)
    name = c.name
    volume_name = f"{name}-data"

    try:
        c.remove(force=True, v=True)
    except APIError as exc:
        raise HTTPException(500, f"Loeschen fehlgeschlagen: {exc}")

    volume_removed = False
    if purge:
        try:
            vol = docker_client().volumes.get(volume_name)
            vol.remove(force=True)
            volume_removed = True
        except NotFound:
            pass
        except APIError as exc:
            log.warning("Volume %s konnte nicht entfernt werden: %s", volume_name, exc)

    return {
        "status": "deleted",
        "id": name,
        "purge": purge,
        "volume_removed": volume_removed,
    }


# -------- API: Power --------------------------------------------------------
@app.post("/api/servers/{server_id}/start")
def start_server(server_id: str) -> Dict[str, Any]:
    c = _get_container(server_id)
    try:
        c.start()
    except APIError as exc:
        raise HTTPException(500, f"Start fehlgeschlagen: {exc}")
    c.reload()
    return _container_to_dto(c, with_stats=False)


@app.post("/api/servers/{server_id}/stop")
def stop_server(server_id: str) -> Dict[str, Any]:
    c = _get_container(server_id)
    try:
        c.stop(timeout=30)
    except APIError as exc:
        raise HTTPException(500, f"Stop fehlgeschlagen: {exc}")
    c.reload()
    return _container_to_dto(c, with_stats=False)


@app.post("/api/servers/{server_id}/restart")
def restart_server(server_id: str) -> Dict[str, Any]:
    c = _get_container(server_id)
    try:
        c.restart(timeout=30)
    except APIError as exc:
        raise HTTPException(500, f"Neustart fehlgeschlagen: {exc}")
    c.reload()
    return _container_to_dto(c, with_stats=False)


# -------- API: Logs / Konsole ----------------------------------------------
@app.get("/api/servers/{server_id}/logs")
def get_logs(server_id: str, tail: int = 200) -> Dict[str, Any]:
    c = _get_container(server_id)
    try:
        raw = c.logs(tail=tail, timestamps=False).decode("utf-8", errors="replace")
    except APIError as exc:
        raise HTTPException(500, f"Logs nicht lesbar: {exc}")
    lines = [l for l in raw.splitlines() if l.strip()]
    return {"lines": lines}


@app.post("/api/servers/{server_id}/command")
def send_command(server_id: str, payload: CommandRequest) -> Dict[str, Any]:
    """
    Sendet einen Befehl ueber rcon-cli in den Container. Das itzg-Image bringt
    rcon-cli direkt mit und liest Passwort/Port aus den Server-Properties.
    """
    c = _get_container(server_id)
    if (c.status or "").lower() != "running":
        raise HTTPException(409, "Server laeuft nicht")
    try:
        result = c.exec_run(["rcon-cli", payload.command])
    except APIError as exc:
        raise HTTPException(500, f"Befehl konnte nicht gesendet werden: {exc}")

    output = (result.output or b"").decode("utf-8", errors="replace")
    return {"exit_code": result.exit_code, "output": output}


# -------- API: Aggregat-Stats ----------------------------------------------
@app.get("/api/stats")
def aggregate_stats() -> Dict[str, Any]:
    servers = [_container_to_dto(c) for c in _list_managed_containers()]
    online = [s for s in servers if s["status"] == "running"]
    return {
        "total": len(servers),
        "online": len(online),
        "players_current": sum(s["players_current"] for s in online),
        "players_max": sum(s["players_max"] for s in online),
    }


# -------- Health ------------------------------------------------------------
@app.get("/api/health")
def health() -> Dict[str, Any]:
    try:
        info = docker_client().version()
        return {"status": "ok", "docker": info.get("Version")}
    except Exception as exc:  # noqa: BLE001
        return {"status": "degraded", "error": str(exc)}


# ---------------------------------------------------------------------------
# v1.0.7: Minecraft-Versionen (aus Mojang piston-meta, mit Cache + Fallback)
# ---------------------------------------------------------------------------
MOJANG_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
_MC_VERSIONS_CACHE: Dict[str, Any] = {"ts": 0, "data": None}
_MC_VERSIONS_TTL = 12 * 3600  # 12 h

# Fallback-Liste, falls Mojang gerade nicht erreichbar ist.
_MC_VERSIONS_FALLBACK: List[str] = [
    "1.21.10", "1.21.9", "1.21.8", "1.21.7", "1.21.6", "1.21.5", "1.21.4",
    "1.21.3", "1.21.2", "1.21.1", "1.21",
    "1.20.6", "1.20.5", "1.20.4", "1.20.3", "1.20.2", "1.20.1", "1.20",
    "1.19.4", "1.19.3", "1.19.2", "1.19.1", "1.19",
    "1.18.2", "1.18.1", "1.18",
    "1.17.1", "1.17",
    "1.16.5", "1.16.4", "1.16.3", "1.16.2", "1.16.1", "1.16",
    "1.15.2", "1.15.1", "1.15",
    "1.14.4", "1.14.3", "1.14.2", "1.14.1", "1.14",
    "1.13.2", "1.13.1", "1.13",
    "1.12.2", "1.12.1", "1.12",
    "1.11.2", "1.11.1", "1.11",
    "1.10.2", "1.10",
    "1.9.4", "1.9.2", "1.9",
    "1.8.9", "1.8.8", "1.8.7", "1.8.6", "1.8.5", "1.8.4", "1.8.3", "1.8",
    "1.7.10", "1.7.9", "1.7.8", "1.7.5", "1.7.4", "1.7.2",
    "1.6.4", "1.6.2", "1.6.1",
    "1.5.2", "1.5.1", "1.5",
    "1.4.7", "1.4.6", "1.4.5", "1.4.4", "1.4.2",
    "1.3.2", "1.3.1",
    "1.2.5",
    "1.1",
    "1.0",
]


@app.get("/api/minecraft/versions")
async def minecraft_versions() -> Dict[str, Any]:
    """
    Liefert Release-Versionen von Minecraft. Versucht zuerst das Mojang-
    piston-meta-Manifest (12 h gecached) und faellt sonst auf eine
    statische Liste zurueck.
    """
    now = time.time()
    cache = _MC_VERSIONS_CACHE
    if cache["data"] and (now - cache["ts"]) < _MC_VERSIONS_TTL:
        return cache["data"]

    versions: List[str] = []
    latest: Optional[str] = None
    source = "mojang"

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(MOJANG_MANIFEST, headers={"User-Agent": MODRINTH_UA})
            r.raise_for_status()
            data = r.json()
            latest = (data.get("latest") or {}).get("release")
            for v in data.get("versions") or []:
                if v.get("type") == "release":
                    versions.append(v.get("id"))
    except Exception as exc:  # noqa: BLE001
        log.warning("Mojang-Manifest nicht erreichbar (%s) - Fallback aktiv.", exc)
        source = "fallback"
        versions = list(_MC_VERSIONS_FALLBACK)
        latest = versions[0]

    payload = {
        "source": source,
        "latest_release": latest,
        "versions": versions,
    }
    _MC_VERSIONS_CACHE["ts"] = now
    _MC_VERSIONS_CACHE["data"] = payload
    return payload


# ---------------------------------------------------------------------------
# Modrinth-Integration
# ---------------------------------------------------------------------------
SAFE_FILE_RE = re.compile(r"^[A-Za-z0-9._\-+ ]+\.jar$")


def _profile_for(server_software: str) -> Dict[str, Any]:
    return SOFTWARE_PROFILE.get(server_software, SOFTWARE_PROFILE["Vanilla"])


def _plugin_dir_for(container: Container) -> str:
    """Liefert das Server-Verzeichnis (mods oder plugins), abhaengig von der Software."""
    sw = container.labels.get(SOFTWARE_LABEL) or "Vanilla"
    return f"/data/{_profile_for(sw)['dir']}"


async def _modrinth_get(client: httpx.AsyncClient, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    url = f"{MODRINTH_BASE}{path}"
    try:
        resp = await client.get(url, params=params, headers={"User-Agent": MODRINTH_UA}, timeout=15)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Modrinth nicht erreichbar: {exc}")
    if resp.status_code >= 400:
        raise HTTPException(resp.status_code, f"Modrinth-Fehler: {resp.text[:200]}")
    return resp.json()


def _container_exec(container: Container, cmd: List[str]) -> tuple[int, str]:
    """Fuehrt einen Befehl im Container aus und gibt (exit, output) zurueck."""
    res = container.exec_run(cmd, demux=False)
    return res.exit_code, (res.output or b"").decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# RCON / Spieler-Cache
# ---------------------------------------------------------------------------
# Cache pro Container: {"count": int, "players": [str], "ts": float}.
# Wird vom Idle-Watchdog UND vom /players-Endpoint geschrieben/gelesen.
_players_lock = threading.Lock()
_PLAYERS_CACHE: Dict[str, Dict[str, Any]] = {}
_PLAYERS_CACHE_TTL = 4.0   # Sekunden, bevor erneut RCON gefragt wird

# Idle-Tracker: container.name -> erster Zeitpunkt mit 0 Spielern (None wenn jemand drauf ist)
_IDLE_SINCE: Dict[str, float] = {}
# Idle-Optimierer-Flag: container.name -> war zuletzt schon optimiert
_LAST_OPTIMIZED: Dict[str, float] = {}


def _rcon(container: Container, command: str, timeout: int = 6) -> tuple[int, str]:
    """rcon-cli im Container ausfuehren. Gibt (exit, output) zurueck."""
    try:
        res = container.exec_run(["rcon-cli", command], demux=False)
    except APIError as exc:
        return 1, f"rcon error: {exc}"
    return res.exit_code, (res.output or b"").decode("utf-8", errors="replace")


_PLAYER_LIST_RE = re.compile(r"There are\s+(\d+)\s*(?:of a max(?:imum)? of\s+(\d+))?\s*players? online[:\.]?\s*(.*)", re.IGNORECASE)


def _parse_player_list(output: str) -> Dict[str, Any]:
    """
    Parsed die Antwort von /list. Beispiele:
        'There are 2 of a max of 20 players online: Steve, Alex'
        'There are 0 of a max 20 players online:'
    """
    m = _PLAYER_LIST_RE.search(output or "")
    if not m:
        return {"count": 0, "max": 20, "players": []}
    count = int(m.group(1))
    pmax = int(m.group(2)) if m.group(2) else 20
    rest = (m.group(3) or "").strip().rstrip(".")
    players = [p.strip() for p in rest.split(",") if p.strip()] if rest else []
    return {"count": count, "max": pmax, "players": players}


def _refresh_players_cache(container: Container) -> Dict[str, Any]:
    """Holt die Spielerliste per RCON und cached sie kurz."""
    now = time.time()
    with _players_lock:
        cached = _PLAYERS_CACHE.get(container.name)
        if cached and (now - cached.get("ts", 0)) < _PLAYERS_CACHE_TTL:
            return dict(cached)

    if (container.status or "").lower() != "running":
        info = {"count": 0, "max": 20, "players": [], "ts": now}
        with _players_lock:
            _PLAYERS_CACHE[container.name] = info
        return dict(info)

    code, out = _rcon(container, "list")
    info: Dict[str, Any] = {"count": 0, "max": 20, "players": [], "ts": now}
    if code == 0:
        info.update(_parse_player_list(out))
    info["ts"] = now
    with _players_lock:
        _PLAYERS_CACHE[container.name] = info
    return dict(info)


def _ensure_plugin_dir(container: Container, plugin_dir: str) -> None:
    code, _ = _container_exec(container, ["mkdir", "-p", plugin_dir])
    if code != 0:
        raise HTTPException(500, f"Konnte Verzeichnis {plugin_dir} nicht anlegen")


def _put_file_into_container(container: Container, target_dir: str, filename: str, payload: bytes) -> None:
    """Packt den Bytes-Inhalt als tar und legt ihn im Container ab."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name=filename)
        info.size = len(payload)
        info.mode = 0o644
        info.mtime = int(time.time())
        tar.addfile(info, io.BytesIO(payload))
    buf.seek(0)
    if not container.put_archive(target_dir, buf.getvalue()):
        raise HTTPException(500, "Datei konnte nicht in den Container kopiert werden")


def _list_jars(container: Container, plugin_dir: str) -> List[Dict[str, Any]]:
    code, out = _container_exec(
        container,
        ["sh", "-c", f"ls -1 -la {shlex.quote(plugin_dir)} 2>/dev/null | awk '$1 !~ /^d/ {{print $5\"\\t\"$NF}}' | grep -E '\\.jar$' || true"],
    )
    if code != 0:
        return []
    items: List[Dict[str, Any]] = []
    for line in out.splitlines():
        if "\t" not in line:
            continue
        size_str, name = line.split("\t", 1)
        try:
            size = int(size_str)
        except ValueError:
            size = 0
        if name and name.endswith(".jar"):
            items.append({"id": name, "name": name, "size": size})
    return items


def _safe_plugin_filename(name: str) -> str:
    if not SAFE_FILE_RE.match(name):
        raise HTTPException(400, f"Ungueltiger Dateiname: {name}")
    return name


# -------- API: Modrinth Suche ----------------------------------------------
@app.get("/api/servers/{server_id}/plugins/search")
async def search_plugins(
    server_id: str,
    query: str = "",
    type: str = "auto",     # auto | plugin | mod | all
    limit: int = 25,
) -> Dict[str, Any]:
    """
    Modrinth-Suche fuer den aktuellen Server.

    Modrinth nutzt project_type:mod fuer "Server-Modifikationen" inkl.
    Bukkit-Plugins. Plugins lassen sich daher NICHT ueber den project type
    "plugin" finden, sondern ueber Loader-Kategorien (paper/spigot/bukkit/...).

    Wir bauen daraus echte Filter:
      Plugins: project_type:mod + categories:paper|spigot|bukkit|...
      Mods:    project_type:mod + categories:fabric|forge|neoforge|quilt
    Plus optional Versions- und server_side-Facet.
    """
    container = _get_container(server_id)
    sw = container.labels.get(SOFTWARE_LABEL) or "Vanilla"
    version = container.labels.get(VERSION_LABEL) or ""

    profile = _profile_for(sw)
    type = (type or "auto").lower()

    if type not in ("auto", "plugin", "mod", "all"):
        raise HTTPException(400, "type muss 'auto', 'plugin', 'mod' oder 'all' sein")

    if type == "auto":
        kind = profile["kind"]   # 'plugin' | 'mod' | 'any'
    elif type == "all":
        kind = "any"
    else:
        kind = type

    plugin_loaders_for_software = [l for l in PLUGIN_LOADERS if not profile["loaders"] or l in profile["loaders"] or profile["kind"] in ("any", "plugin")]
    if not plugin_loaders_for_software:
        plugin_loaders_for_software = list(PLUGIN_LOADERS)
    mod_loaders_for_software = [l for l in MOD_LOADERS if not profile["loaders"] or l in profile["loaders"] or profile["kind"] in ("any", "mod")]
    if not mod_loaders_for_software:
        mod_loaders_for_software = list(MOD_LOADERS)

    limit = max(1, min(int(limit) if limit else 25, 50))

    async def _search(loaders: List[str]) -> List[Dict[str, Any]]:
        if not loaders:
            return []
        facets: List[List[str]] = [
            ["project_type:mod"],
            [f"categories:{l}" for l in loaders],
        ]
        if version:
            facets.append([f"versions:{version}"])
        # Nur server-relevante Projekte zeigen: required oder optional.
        # 'unsupported' (= client-only) wird damit ausgeblendet.
        facets.append(["server_side:required", "server_side:optional"])
        params = {
            "query": query or "",
            "limit": limit,
            "facets": _to_facets_json(facets),
            "index": "relevance",
        }
        log.info("Modrinth search facets=%s query=%r", params["facets"], query)
        async with httpx.AsyncClient() as client:
            data = await _modrinth_get(client, "/search", params=params)
        return data.get("hits", []) or []

    plugin_hits: List[Dict[str, Any]] = []
    mod_hits: List[Dict[str, Any]] = []

    if kind == "plugin":
        plugin_hits = await _search(plugin_loaders_for_software)
    elif kind == "mod":
        mod_hits = await _search(mod_loaders_for_software)
    else:  # any/all
        plugin_hits = await _search(plugin_loaders_for_software)
        mod_hits = await _search(mod_loaders_for_software)

    # Deduplizieren nach project_id, plugin-Treffer haben Vorrang fuer Plugin-Server.
    seen: Dict[str, Dict[str, Any]] = {}
    primary, secondary = (plugin_hits, mod_hits) if kind != "mod" else (mod_hits, plugin_hits)
    for hit in [*primary, *secondary]:
        pid = hit.get("project_id") or hit.get("slug")
        if not pid or pid in seen:
            continue
        seen[pid] = hit

    results: List[Dict[str, Any]] = []
    for hit in seen.values():
        cats = hit.get("categories") or []
        classification = _classify_project(cats)
        if classification == "unknown" and kind != "any":
            # Bei strikter Plugin/Mod-Suche unbekannte Kategorien rausfiltern
            continue

        results.append({
            "project_id": hit.get("project_id") or hit.get("slug"),
            "slug": hit.get("slug"),
            "title": hit.get("title"),
            "description": hit.get("description"),
            "downloads": hit.get("downloads"),
            "icon_url": hit.get("icon_url"),
            "categories": cats,
            "project_type": hit.get("project_type"),
            "classification": classification,            # plugin | mod | hybrid | unknown
            "loaders": _loaders_in(cats),                # nur die Loader (paper/forge/...)
            "server_side": hit.get("server_side"),
            "client_side": hit.get("client_side"),
            "target_dir": _target_dir_for(classification, profile),
            "latest_version": hit.get("latest_version"),
            "url": f"https://modrinth.com/{hit.get('project_type', 'mod')}/{hit.get('slug')}",
        })

    # Sortierung: Pro angefragtem kind sinnvoll vorne (Plugin-Server -> Plugins zuerst)
    if kind == "plugin":
        results.sort(key=lambda r: (r["classification"] != "plugin", -(r.get("downloads") or 0)))
    elif kind == "mod":
        results.sort(key=lambda r: (r["classification"] != "mod", -(r.get("downloads") or 0)))
    else:
        results.sort(key=lambda r: -(r.get("downloads") or 0))

    return {
        "query": query,
        "kind": kind,
        "software": sw,
        "version": version,
        "loaders_plugin": plugin_loaders_for_software,
        "loaders_mod": mod_loaders_for_software,
        "total": len(results),
        "results": results[:limit],
    }


def _classify_project(categories: List[str]) -> str:
    cats = {c.lower() for c in (categories or [])}
    is_plugin = any(l in cats for l in PLUGIN_LOADERS)
    is_mod    = any(l in cats for l in MOD_LOADERS)
    if is_plugin and is_mod:
        return "hybrid"
    if is_plugin:
        return "plugin"
    if is_mod:
        return "mod"
    return "unknown"


def _loaders_in(categories: List[str]) -> List[str]:
    cats = {c.lower() for c in (categories or [])}
    known = set(PLUGIN_LOADERS) | set(MOD_LOADERS)
    return sorted([c for c in cats if c in known])


def _target_dir_for(classification: str, profile: Dict[str, Any]) -> Optional[str]:
    if classification == "plugin":
        return "/data/plugins"
    if classification == "mod":
        return "/data/mods"
    if classification == "hybrid":
        # Frontend muss in dem Fall fragen. Wir geben Fallback aus dem Profil.
        return f"/data/{profile['dir']}"
    return None


def _to_facets_json(groups: List[List[str]]) -> str:
    """Konvertiert facet-Gruppen zu Modrinth-JSON: [["a:1"],["b:2","b:3"]]."""
    import json
    quoted = [[s for s in g] for g in groups if g]
    return json.dumps(quoted, separators=(",", ":"))


# -------- API: Modrinth Installieren ---------------------------------------
@app.post("/api/servers/{server_id}/plugins/install")
async def install_plugin(server_id: str, payload: PluginInstallRequest) -> Dict[str, Any]:
    container = _get_container(server_id)
    sw = container.labels.get(SOFTWARE_LABEL) or "Vanilla"
    mc_version = container.labels.get(VERSION_LABEL) or ""
    profile = _profile_for(sw)
    default_plugin_dir = f"/data/{profile['dir']}"

    async with httpx.AsyncClient() as client:
        # 1) Passende Version finden
        if payload.version_id:
            version = await _modrinth_get(client, f"/version/{payload.version_id}")
            if not version:
                raise HTTPException(404, "Version nicht gefunden")
            project_meta: Dict[str, Any] = {}
        else:
            params: Dict[str, Any] = {}
            if profile["loaders"]:
                params["loaders"] = _to_facets_json([profile["loaders"]])
            if mc_version:
                params["game_versions"] = _to_facets_json([[mc_version]])

            versions = await _modrinth_get(client, f"/project/{payload.project_id}/version", params=params)
            if not isinstance(versions, list) or not versions:
                raise HTTPException(404, f"Keine kompatible Version fuer {sw} {mc_version} gefunden")
            # Erste Version ist die neueste
            version = versions[0]
            project_meta = {}

        # Project-Meta fuer Hybrid-Routing (Mohist: plugin -> /plugins, mod -> /mods)
        try:
            project_meta = await _modrinth_get(client, f"/project/{payload.project_id}")
        except HTTPException:
            project_meta = {}

        # Klassifizierung anhand der Modrinth-Kategorien (paper/forge/...)
        cats = (project_meta.get("categories") or []) + (version.get("loaders") or [])
        classification = _classify_project(cats)

        # Zielordner bestimmen:
        #   1) explizites target im Payload (UI-Wahl bei Hybrid)
        #   2) Klassifizierung -> plugin/mod
        #   3) Profil-Default
        if payload.target in ("plugins", "mods"):
            plugin_dir = f"/data/{payload.target}"
        elif classification == "plugin":
            plugin_dir = "/data/plugins"
        elif classification == "mod":
            plugin_dir = "/data/mods"
        elif classification == "hybrid":
            # ohne Vorgabe: nehmen wir das Profil-Default
            plugin_dir = default_plugin_dir
        else:
            plugin_dir = default_plugin_dir

        files = version.get("files") or []
        if not files:
            raise HTTPException(404, "Modrinth-Version enthaelt keine Dateien")
        primary = next((f for f in files if f.get("primary")), files[0])
        download_url = primary.get("url")
        filename = primary.get("filename") or f"{payload.project_id}.jar"
        if not download_url or not filename.lower().endswith(".jar"):
            raise HTTPException(400, "Nur .jar-Dateien werden unterstuetzt")

        safe_name = _safe_plugin_filename(filename)

        # 2) Datei laden
        try:
            r = await client.get(download_url, headers={"User-Agent": MODRINTH_UA}, timeout=120, follow_redirects=True)
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"Download fehlgeschlagen: {exc}")
        if r.status_code >= 400:
            raise HTTPException(r.status_code, f"Download fehlgeschlagen: {r.text[:200]}")

    # 3) In den Container schreiben
    _ensure_plugin_dir(container, plugin_dir)
    _put_file_into_container(container, plugin_dir, safe_name, r.content)

    # v1.0.5 Fix: Sicherstellen, dass Minecraft die Datei laden darf.
    # itzg/minecraft-server laeuft typischerweise als UID 1000; um Permission-
    # Probleme zu vermeiden, setzen wir 0664 + ownership des MC-Users.
    target = f"{plugin_dir}/{safe_name}"
    _container_exec(container, ["chmod", "0664", target])
    _container_exec(container, ["sh", "-c", f"chown $(stat -c '%u:%g' {shlex.quote(plugin_dir)}) {shlex.quote(target)} 2>/dev/null || true"])

    log.info("Plugin %s (%s) installiert in %s:%s",
             safe_name, payload.project_id, container.name, plugin_dir)

    return {
        "status": "installed",
        "filename": safe_name,
        "project_id": payload.project_id,
        "version_id": version.get("id"),
        "version_number": version.get("version_number"),
        "directory": plugin_dir,
        "size": len(r.content),
        "needs_restart": True,
    }


# -------- API: Installierte Erweiterungen lesen / loeschen ------------------
@app.get("/api/servers/{server_id}/plugins/installed")
def list_installed_plugins(server_id: str) -> Dict[str, Any]:
    container = _get_container(server_id)
    sw = container.labels.get(SOFTWARE_LABEL) or "Vanilla"
    profile = _profile_for(sw)

    # Hybrid (Mohist/Arclight): Plugins UND Mods anzeigen
    dirs: List[str] = ["/data/plugins"] if profile["dir"] == "plugins" else ["/data/mods"]
    if profile["kind"] == "any":
        dirs = ["/data/plugins", "/data/mods"]

    items: List[Dict[str, Any]] = []
    for d in dirs:
        for jar in _list_jars(container, d):
            jar["directory"] = d
            items.append(jar)
    return {"directory": dirs[0], "directories": dirs, "items": items}


@app.delete("/api/servers/{server_id}/plugins/installed/{filename}")
def delete_installed_plugin(server_id: str, filename: str) -> Dict[str, Any]:
    container = _get_container(server_id)
    sw = container.labels.get(SOFTWARE_LABEL) or "Vanilla"
    profile = _profile_for(sw)
    safe_name = _safe_plugin_filename(filename)

    candidates = ["/data/plugins"] if profile["dir"] == "plugins" else ["/data/mods"]
    if profile["kind"] == "any":
        candidates = ["/data/plugins", "/data/mods"]

    for plugin_dir in candidates:
        target = f"{plugin_dir}/{safe_name}"
        code, _ = _container_exec(container, ["test", "-f", target])
        if code == 0:
            code, out = _container_exec(container, ["rm", "-f", target])
            if code != 0:
                raise HTTPException(500, f"Loeschen fehlgeschlagen: {out}")
            return {"status": "deleted", "filename": safe_name, "directory": plugin_dir, "needs_restart": True}

    raise HTTPException(404, "Datei nicht gefunden")


# -------- API: Eigene .jar hochladen ---------------------------------------
@app.post("/api/servers/{server_id}/plugins/upload")
async def upload_plugin(
    server_id: str,
    file: UploadFile = File(...),
    target: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Laedt eine vom Nutzer hochgeladene .jar in den Plugin-/Mod-Ordner.
    Zielordner: explizites 'target' (plugins|mods) oder Profil-Default.
    """
    container = _get_container(server_id)
    sw = container.labels.get(SOFTWARE_LABEL) or "Vanilla"
    profile = _profile_for(sw)

    filename = os.path.basename(file.filename or "")
    safe_name = _safe_plugin_filename(filename)

    if target in ("plugins", "mods"):
        plugin_dir = f"/data/{target}"
    else:
        plugin_dir = f"/data/{profile['dir']}"

    # Inhalt lesen mit Groessenlimit
    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    content = await file.read()
    if len(content) > max_bytes:
        raise HTTPException(413, f"Datei ueberschreitet Limit von {MAX_UPLOAD_MB} MB")
    if not content:
        raise HTTPException(400, "Leere Datei")

    _ensure_plugin_dir(container, plugin_dir)
    _put_file_into_container(container, plugin_dir, safe_name, content)

    # Permissions wie bei Modrinth-Install setzen (siehe install_plugin)
    full = f"{plugin_dir}/{safe_name}"
    _container_exec(container, ["chmod", "0664", full])
    _container_exec(container, ["sh", "-c", f"chown $(stat -c '%u:%g' {shlex.quote(plugin_dir)}) {shlex.quote(full)} 2>/dev/null || true"])

    log.info("Upload %s (%d bytes) -> %s:%s", safe_name, len(content), container.name, plugin_dir)
    return {
        "status": "installed",
        "filename": safe_name,
        "directory": plugin_dir,
        "size": len(content),
        "needs_restart": True,
    }


# ---------------------------------------------------------------------------
# v1.1.0: Echte Backups (tar.gz von /data, gespeichert auf dem Panel-Volume)
# ---------------------------------------------------------------------------
SAFE_BACKUP_RE = re.compile(r"^[A-Za-z0-9._\-]+\.tar\.gz$")


def _backup_dir_for(container: Container) -> Path:
    d = BACKUP_ROOT / container.name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_backup_name(name: str) -> str:
    base = os.path.basename(name or "")
    if not SAFE_BACKUP_RE.match(base):
        raise HTTPException(400, f"Ungueltiger Backup-Name: {name}")
    return base


def _backup_meta(path: Path) -> Dict[str, Any]:
    st = path.stat()
    return {
        "name": path.name,
        "size": st.st_size,
        "size_human": _human_size(st.st_size),
        "date": datetime.fromtimestamp(st.st_mtime).strftime("%d.%m.%Y, %H:%M"),
        "mtime": st.st_mtime,
    }


def _human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024 or unit == "TB":
            return f"{num:.0f} {unit}" if unit in ("B", "KB") else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


@app.get("/api/servers/{server_id}/backups")
def list_backups(server_id: str) -> Dict[str, Any]:
    container = _get_container(server_id)
    d = _backup_dir_for(container)
    items = [_backup_meta(p) for p in d.glob("*.tar.gz") if p.is_file()]
    items.sort(key=lambda m: m["mtime"], reverse=True)
    return {"items": items}


@app.post("/api/servers/{server_id}/backups", status_code=201)
def create_backup(server_id: str) -> Dict[str, Any]:
    """
    Erstellt ein tar.gz von /data des Containers. Bei laufendem Server wird
    vorher 'save-all flush' per RCON ausgeloest, damit die Welt konsistent ist.
    """
    container = _get_container(server_id)
    if (container.status or "").lower() == "running":
        _rcon(container, "save-all flush")

    d = _backup_dir_for(container)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_path = d / f"backup_{ts}.tar.gz"

    try:
        bits, _stat = container.get_archive("/data")
    except (APIError, NotFound) as exc:
        raise HTTPException(500, f"Backup fehlgeschlagen (get_archive): {exc}")

    try:
        with gzip.open(out_path, "wb") as gz:
            for chunk in bits:
                gz.write(chunk)
    except OSError as exc:
        out_path.unlink(missing_ok=True)
        raise HTTPException(500, f"Backup konnte nicht geschrieben werden: {exc}")

    log.info("Backup erstellt: %s (%d bytes)", out_path, out_path.stat().st_size)
    return _backup_meta(out_path)


@app.get("/api/servers/{server_id}/backups/{name}")
def download_backup(server_id: str, name: str) -> FileResponse:
    container = _get_container(server_id)
    safe = _safe_backup_name(name)
    path = _backup_dir_for(container) / safe
    if not path.is_file():
        raise HTTPException(404, "Backup nicht gefunden")
    return FileResponse(str(path), media_type="application/gzip", filename=safe)


@app.delete("/api/servers/{server_id}/backups/{name}")
def delete_backup(server_id: str, name: str) -> Dict[str, Any]:
    container = _get_container(server_id)
    safe = _safe_backup_name(name)
    path = _backup_dir_for(container) / safe
    if not path.is_file():
        raise HTTPException(404, "Backup nicht gefunden")
    path.unlink()
    return {"status": "deleted", "name": safe}


@app.post("/api/servers/{server_id}/backups/{name}/restore")
def restore_backup(server_id: str, name: str) -> Dict[str, Any]:
    """
    Spielt ein Backup zurueck. ACHTUNG: ueberschreibt /data. Der Server wird
    dafuer gestoppt und anschliessend wieder in den vorherigen Zustand versetzt.
    """
    container = _get_container(server_id)
    safe = _safe_backup_name(name)
    path = _backup_dir_for(container) / safe
    if not path.is_file():
        raise HTTPException(404, "Backup nicht gefunden")

    was_running = (container.status or "").lower() == "running"
    if was_running:
        try:
            container.stop(timeout=30)
            container.reload()
        except APIError as exc:
            raise HTTPException(500, f"Server konnte nicht gestoppt werden: {exc}")

    # tar.gz entpacken und als (unkomprimierten) tar-Stream zurueckschreiben.
    # get_archive('/data') liefert ein tar mit Top-Level-Ordner 'data/',
    # daher muss beim Restore nach '/' entpackt werden (-> landet in /data).
    try:
        with gzip.open(path, "rb") as gz:
            raw_tar = gz.read()
        if not container.put_archive("/", raw_tar):
            raise HTTPException(500, "Wiederherstellung fehlgeschlagen (put_archive)")
    except OSError as exc:
        raise HTTPException(500, f"Backup nicht lesbar: {exc}")

    if was_running:
        try:
            container.start()
        except APIError as exc:
            raise HTTPException(500, f"Server konnte nach Restore nicht gestartet werden: {exc}")

    log.info("Backup wiederhergestellt: %s", path)
    return {"status": "restored", "name": safe, "restarted": was_running}


# ---------------------------------------------------------------------------
# v1.0.4: Spieler-API (RCON list / op / kick / ban / deop)
# ---------------------------------------------------------------------------
PLAYER_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")


def _validate_player_name(name: str) -> str:
    if not PLAYER_NAME_RE.match(name or ""):
        raise HTTPException(400, "Ungueltiger Spielername")
    return name


@app.get("/api/servers/{server_id}/players")
def list_players(server_id: str) -> Dict[str, Any]:
    """Liefert die per RCON ermittelte Spielerliste (gecached)."""
    container = _get_container(server_id)
    info = _refresh_players_cache(container)
    return {
        "count": info.get("count", 0),
        "max": info.get("max", 20),
        "players": info.get("players", []),
    }


@app.post("/api/servers/{server_id}/players/op")
def player_op(server_id: str, payload: PlayerActionRequest) -> Dict[str, Any]:
    container = _get_container(server_id)
    name = _validate_player_name(payload.player)
    code, out = _rcon(container, f"op {name}")
    if code != 0:
        raise HTTPException(500, out or "RCON Fehler")
    return {"status": "ok", "action": "op", "player": name, "output": out.strip()}


@app.post("/api/servers/{server_id}/players/deop")
def player_deop(server_id: str, payload: PlayerActionRequest) -> Dict[str, Any]:
    container = _get_container(server_id)
    name = _validate_player_name(payload.player)
    code, out = _rcon(container, f"deop {name}")
    if code != 0:
        raise HTTPException(500, out or "RCON Fehler")
    return {"status": "ok", "action": "deop", "player": name, "output": out.strip()}


@app.post("/api/servers/{server_id}/players/kick")
def player_kick(server_id: str, payload: PlayerActionRequest) -> Dict[str, Any]:
    container = _get_container(server_id)
    name = _validate_player_name(payload.player)
    reason = (payload.reason or "Gekickt").replace('"', "'")
    code, out = _rcon(container, f"kick {name} {reason}")
    if code != 0:
        raise HTTPException(500, out or "RCON Fehler")
    return {"status": "ok", "action": "kick", "player": name, "output": out.strip()}


@app.post("/api/servers/{server_id}/players/ban")
def player_ban(server_id: str, payload: PlayerActionRequest) -> Dict[str, Any]:
    container = _get_container(server_id)
    name = _validate_player_name(payload.player)
    reason = (payload.reason or "Gebannt").replace('"', "'")
    code, out = _rcon(container, f"ban {name} {reason}")
    if code != 0:
        raise HTTPException(500, out or "RCON Fehler")
    return {"status": "ok", "action": "ban", "player": name, "output": out.strip()}


# ---------------------------------------------------------------------------
# v1.0.4: Datei-API (Web-FTP fuer wichtige Server-Dateien)
# ---------------------------------------------------------------------------
EDITABLE_FILES: Dict[str, Dict[str, Any]] = {
    "server.properties":   {"path": "/data/server.properties",         "max_kb": 64,  "optional": False},
    "whitelist.json":      {"path": "/data/whitelist.json",            "max_kb": 64,  "optional": False},
    "ops.json":            {"path": "/data/ops.json",                  "max_kb": 64,  "optional": False},
    "banned-players.json": {"path": "/data/banned-players.json",       "max_kb": 64,  "optional": False},
    "banned-ips.json":     {"path": "/data/banned-ips.json",           "max_kb": 64,  "optional": False},
    "bukkit.yml":          {"path": "/data/bukkit.yml",                "max_kb": 64,  "optional": True},
    "spigot.yml":          {"path": "/data/spigot.yml",                "max_kb": 64,  "optional": True},
    "paper-global.yml":    {"path": "/data/config/paper-global.yml",   "max_kb": 128, "optional": True},
    "paper-world-defaults.yml": {"path": "/data/config/paper-world-defaults.yml", "max_kb": 128, "optional": True},
}


class _FileMissing(Exception):
    pass


def _read_file_from_container(container: Container, path: str) -> str:
    try:
        bits, _stat = container.get_archive(path)
    except NotFound:
        raise _FileMissing(path)
    except APIError as exc:
        # Docker liefert fuer 'not found' i.d.R. NotFound, aber manche
        # Versionen verpacken es in einen generischen APIError.
        if "could not find the file" in (exc.explanation or "").lower() or "no such file" in str(exc).lower():
            raise _FileMissing(path)
        raise HTTPException(500, f"Datei nicht lesbar: {exc.explanation or exc}")
    buf = io.BytesIO(b"".join(bits))
    with tarfile.open(fileobj=buf, mode="r") as tar:
        members = [m for m in tar.getmembers() if m.isfile()]
        if not members:
            raise _FileMissing(path)
        f = tar.extractfile(members[0])
        if not f:
            raise HTTPException(500, "Datei nicht lesbar")
        return f.read().decode("utf-8", errors="replace")


def _write_file_into_container(container: Container, full_path: str, content: str) -> None:
    parent = full_path.rsplit("/", 1)[0]
    filename = full_path.rsplit("/", 1)[1]
    _ensure_plugin_dir(container, parent)  # mkdir -p (Helper passt - macht keine Annahmen)
    payload = content.encode("utf-8")
    _put_file_into_container(container, parent, filename, payload)


@app.get("/api/servers/{server_id}/files")
def list_editable_files(server_id: str) -> Dict[str, Any]:
    container = _get_container(server_id)
    result = []
    for name, meta in EDITABLE_FILES.items():
        code, _ = _container_exec(container, ["test", "-f", meta["path"]])
        result.append({
            "name": name,
            "path": meta["path"],
            "exists": code == 0,
            "optional": bool(meta.get("optional", False)),
        })
    return {"files": result}


@app.get("/api/servers/{server_id}/files/{filename}")
def read_editable_file(server_id: str, filename: str) -> Dict[str, Any]:
    if filename not in EDITABLE_FILES:
        raise HTTPException(403, "Datei nicht zur Bearbeitung freigegeben")
    container = _get_container(server_id)
    meta = EDITABLE_FILES[filename]
    is_optional = bool(meta.get("optional", False))
    try:
        content = _read_file_from_container(container, meta["path"])
        return {
            "name": filename,
            "path": meta["path"],
            "content": content,
            "exists": True,
            "optional": is_optional,
        }
    except _FileMissing:
        # Pflicht-Dateien: weiterhin 404 - Optional-Dateien: leer ausliefern.
        if not is_optional:
            raise HTTPException(404, f"Datei nicht gefunden: {meta['path']}")
        return {
            "name": filename,
            "path": meta["path"],
            "content": "",
            "exists": False,
            "optional": True,
            "hint": "Datei existiert noch nicht. Beim Speichern wird sie erstellt.",
        }


@app.put("/api/servers/{server_id}/files/{filename}")
def write_editable_file(server_id: str, filename: str, payload: FileWriteRequest) -> Dict[str, Any]:
    if filename not in EDITABLE_FILES:
        raise HTTPException(403, "Datei nicht zur Bearbeitung freigegeben")
    container = _get_container(server_id)
    meta = EDITABLE_FILES[filename]

    # Groessenlimit
    max_bytes = meta.get("max_kb", 64) * 1024
    if len(payload.content.encode("utf-8")) > max_bytes:
        raise HTTPException(413, f"Datei ueberschreitet Limit von {meta.get('max_kb', 64)} KB")

    _write_file_into_container(container, meta["path"], payload.content)
    return {"status": "saved", "name": filename, "path": meta["path"], "needs_restart": True}


# ---------------------------------------------------------------------------
# v1.0.4: RAM-Optimierer (Idle-Watchdog)
# v1.1.0: Status wird auf Platte persistiert (Labels lassen sich nach dem
# Create nicht mehr aendern), damit er Panel-Neustarts ueberlebt.
# ---------------------------------------------------------------------------
_optimizer_lock = threading.Lock()
_OPTIMIZER_STATE: Dict[str, bool] = {}


def _load_optimizer_state() -> None:
    try:
        if OPTIMIZER_STATE_FILE.is_file():
            data = json.loads(OPTIMIZER_STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                with _optimizer_lock:
                    _OPTIMIZER_STATE.update({k: bool(v) for k, v in data.items()})
                log.info("Optimizer-Status geladen (%d Eintraege)", len(data))
    except Exception as exc:  # noqa: BLE001
        log.warning("Optimizer-Status konnte nicht geladen werden: %s", exc)


def _save_optimizer_state() -> None:
    try:
        OPTIMIZER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _optimizer_lock:
            snapshot = dict(_OPTIMIZER_STATE)
        OPTIMIZER_STATE_FILE.write_text(json.dumps(snapshot), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        log.warning("Optimizer-Status konnte nicht gespeichert werden: %s", exc)


@app.put("/api/servers/{server_id}/optimizer")
def set_optimizer(server_id: str, payload: OptimizerRequest) -> Dict[str, Any]:
    """
    Schaltet den Auto-RAM-Optimierer um. Der Zustand wird im persistenten
    State-Store gehalten (Container-Labels sind nach Create unveraenderlich).
    Der Hintergrund-Watchdog liest ihn und leitet bei 0 Spielern fuer
    >= IDLE_OPTIMIZER_MINUTES eine GC-Hilfe ein (save-all flush).
    """
    container = _get_container(server_id)
    with _optimizer_lock:
        _OPTIMIZER_STATE[container.name] = bool(payload.enabled)
    _save_optimizer_state()
    return {"status": "ok", "enabled": bool(payload.enabled)}


def _optimizer_enabled_for(container: Container) -> bool:
    with _optimizer_lock:
        if container.name in _OPTIMIZER_STATE:
            return _OPTIMIZER_STATE[container.name]
    return (container.labels.get(OPTIMIZER_LABEL) or "false").lower() == "true"


def _idle_watchdog_loop() -> None:
    """Hintergrund-Thread: prueft alle 60 s, ob Server auf 0 Spielern stehen."""
    while True:
        try:
            for c in _list_managed_containers():
                if (c.status or "").lower() != "running":
                    _IDLE_SINCE.pop(c.name, None)
                    continue
                info = _refresh_players_cache(c)
                count = info.get("count", 0)
                now = time.time()
                if count > 0:
                    _IDLE_SINCE.pop(c.name, None)
                    continue
                first_idle = _IDLE_SINCE.setdefault(c.name, now)
                idle_minutes = (now - first_idle) / 60.0

                if not _optimizer_enabled_for(c):
                    continue

                if idle_minutes >= IDLE_OPTIMIZER_MINUTES:
                    last = _LAST_OPTIMIZED.get(c.name, 0)
                    # max einmal alle 30 Minuten
                    if (now - last) < IDLE_OPTIMIZER_MINUTES * 60:
                        continue
                    log.info("Idle-Optimierer triggert auf %s (idle %.1f min)", c.name, idle_minutes)
                    # Speichern und GC-Hilfe per RCON
                    _rcon(c, "save-all flush")
                    _rcon(c, "save-off")
                    _rcon(c, "save-on")
                    _LAST_OPTIMIZED[c.name] = now
        except Exception as exc:  # noqa: BLE001
            log.warning("Idle-Watchdog Fehler: %s", exc)
        time.sleep(60)


# Hinweis: Die Hintergrund-Threads (Idle-Watchdog + Stats-Refresher) werden
# im lifespan-Handler von FastAPI gestartet (siehe oben).


# ---------------------------------------------------------------------------
# v1.0.4/v1.0.7: playit.gg Tunnel (Sidecar-Container)
# Image: playitcloud/playit:latest  (offizielles Docker-Image laut Docker Hub)
# Pro Minecraft-Server ein Sidecar mit eigenem Config-Volume, damit der Agent
# seine Auth-Daten ueber Neustarts behaelt.
# ---------------------------------------------------------------------------
TUNNEL_NETWORK_MODE = os.getenv("CRAFTCONTROL_PLAYIT_NETWORK", "host")  # 'host' oder 'container:<name>'

# Erkennungsmuster fuer playit-Logs.
_PLAYIT_DOMAIN_RE = re.compile(r"([a-zA-Z0-9-]+\.(?:joinmc\.link|playit\.gg|gl\.at\.ply\.gg|at\.playit\.gg))(?::\d+)?")
_PLAYIT_CLAIM_RE = re.compile(r"https?://playit\.gg/(?:claim|mc/connect|connect|setup)/[A-Za-z0-9_-]+", re.IGNORECASE)
_PLAYIT_AUTH_HINT_RE = re.compile(r"(claim[- ]url|please claim|setup the agent|please visit|account|register|tunnel agent)", re.IGNORECASE)
_PLAYIT_ACTIVE_RE = re.compile(r"(tunnel established|connected to playit|agent online|listening on)", re.IGNORECASE)


def _tunnel_name(server_id: str) -> str:
    return f"craftcontrol-playit-{server_id}"


def _tunnel_volume(server_id: str) -> str:
    return f"craftcontrol-playit-{server_id}"


def _tunnel_container_for(server_id: str) -> Optional[Container]:
    """Findet den Sidecar-Tunnel fuer den Server (per Label)."""
    for c in docker_client().containers.list(all=True, filters={"label": f"{TUNNEL_FOR_LABEL}={server_id}"}):
        return c
    return None


def _read_playit_logs(tunnel: Container, tail: int = 400) -> str:
    try:
        return tunnel.logs(tail=tail).decode("utf-8", errors="replace")
    except APIError:
        return ""


def _tunnel_status_dto(parent: Container, tunnel: Optional[Container], extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Antwort-Format fuer GET/POST tunnel-Endpunkte (Wizard-fähig)."""
    base: Dict[str, Any] = {
        "ok": True,
        "image": PLAYIT_IMAGE,
        "container": None,
        "logs_tail": "",
        "domain": None,
        "claim_url": None,
        "status": "not_started",
        "message": "Kein Tunnel laeuft fuer diesen Server.",
    }
    if extra:
        base.update(extra)

    if not tunnel:
        return base

    try:
        tunnel.reload()
    except APIError:
        pass

    state = (tunnel.status or "unknown").lower()
    logs = _read_playit_logs(tunnel)
    base.update({
        "container": tunnel.name,
        "logs_tail": logs[-4000:],  # nur letzte ~4 KB ausliefern
    })

    domain_match = _PLAYIT_DOMAIN_RE.search(logs)
    claim_match = _PLAYIT_CLAIM_RE.search(logs)
    if domain_match:
        base["domain"] = domain_match.group(0)
    if claim_match:
        base["claim_url"] = claim_match.group(0)

    if state in ("created", "restarting"):
        base["status"] = "agent_started"
        base["message"] = "Agent startet ..."
    elif state == "running":
        if base["domain"] and _PLAYIT_ACTIVE_RE.search(logs):
            base["status"] = "active"
            base["message"] = f"Tunnel aktiv ueber {base['domain']}."
        elif base["claim_url"] or _PLAYIT_AUTH_HINT_RE.search(logs):
            base["status"] = "auth_required"
            base["message"] = (
                "Token/Auth nötig. Öffne den Claim-Link und verknüpfe den Agent "
                "mit deinem playit.gg-Account."
            )
        else:
            base["status"] = "agent_started"
            base["message"] = "Agent läuft. Domain wird gleich angezeigt."
    elif state in ("exited", "dead"):
        base["ok"] = False
        base["status"] = "error"
        base["message"] = f"Tunnel-Container gestoppt (Status: {state})."

    return base


@app.get("/api/servers/{server_id}/tunnel")
def tunnel_status(server_id: str) -> Dict[str, Any]:
    parent = _get_container(server_id)
    tunnel = _tunnel_container_for(server_id)
    return _tunnel_status_dto(parent, tunnel)


@app.post("/api/servers/{server_id}/tunnel/start")
def tunnel_start(server_id: str, payload: TunnelStartRequest) -> Dict[str, Any]:
    parent = _get_container(server_id)
    if (parent.status or "").lower() != "running":
        raise HTTPException(409, "Bitte zuerst den Minecraft-Server starten")

    client = docker_client()

    # Bestehenden Sidecar nur dann uebernehmen, wenn er laeuft - sonst neu.
    existing = _tunnel_container_for(server_id)
    if existing:
        existing.reload()
        if (existing.status or "").lower() == "running":
            return _tunnel_status_dto(parent, existing, {"status": "active", "message": "Sidecar laeuft bereits."})
        try:
            existing.remove(force=True)
        except APIError:
            pass

    # 1) Image pull - Fehler sauber im DTO zurueckgeben (kein 500 ohne Details).
    try:
        log.info("Pulling playit image %s", PLAYIT_IMAGE)
        client.images.pull(PLAYIT_IMAGE)
    except APIError as exc:
        return {
            "ok": False,
            "status": "error",
            "image": PLAYIT_IMAGE,
            "container": None,
            "logs_tail": "",
            "domain": None,
            "claim_url": None,
            "message": (
                f"Image-Pull fehlgeschlagen ({PLAYIT_IMAGE}): "
                f"{exc.explanation or exc}. Pruefe Internet/Registry."
            ),
        }

    # 2) Sidecar starten.
    tunnel_name = _tunnel_name(server_id)
    volume_name = _tunnel_volume(server_id)
    env: Dict[str, str] = {}
    if payload.secret:
        env["SECRET_KEY"] = payload.secret

    network_mode = TUNNEL_NETWORK_MODE
    if network_mode not in ("host",):
        # Fallback: Network namespace mit dem MC-Server teilen (legacy)
        network_mode = f"container:{parent.name}"

    try:
        tunnel = client.containers.run(
            image=PLAYIT_IMAGE,
            name=tunnel_name,
            detach=True,
            tty=True,
            stdin_open=True,
            environment=env,
            labels={
                MANAGED_LABEL: "false",        # nicht als MC-Server listen
                TUNNEL_FOR_LABEL: server_id,
                TUNNEL_KIND_LABEL: "playit",
            },
            network_mode=network_mode,
            volumes={volume_name: {"bind": "/etc/playit", "mode": "rw"}},
            restart_policy={"Name": "unless-stopped"},
        )
    except APIError as exc:
        return {
            "ok": False,
            "status": "error",
            "image": PLAYIT_IMAGE,
            "container": tunnel_name,
            "logs_tail": "",
            "domain": None,
            "claim_url": None,
            "message": f"Tunnel-Container konnte nicht gestartet werden: {exc.explanation or exc}",
        }

    # Kurz warten, damit playit Logs schreibt.
    time.sleep(1.2)
    tunnel.reload()
    dto = _tunnel_status_dto(parent, tunnel)

    # Wenn nach dem Start nur 'agent_started' steht und noch keine Auth/Active
    # erkennbar ist, kommunizieren wir das klar im Message-Feld.
    if dto.get("status") == "agent_started" and not dto.get("claim_url"):
        dto["message"] = (
            "Agent läuft. Wenn keine Domain erscheint, einen Moment warten "
            "und Status erneut abrufen oder die Logs unten pruefen."
        )
    return dto


@app.post("/api/servers/{server_id}/tunnel/stop")
def tunnel_stop(server_id: str) -> Dict[str, Any]:
    _get_container(server_id)  # validiert Berechtigung (Minecraft-Server bleibt unangetastet)
    tunnel = _tunnel_container_for(server_id)
    if not tunnel:
        return {"ok": True, "status": "not_started", "message": "Kein Tunnel aktiv."}
    try:
        tunnel.remove(force=True)
    except APIError as exc:
        raise HTTPException(500, f"Tunnel konnte nicht gestoppt werden: {exc}")
    return {"ok": True, "status": "stopped", "message": "Tunnel-Sidecar entfernt."}


# ---------------------------------------------------------------------------
# Statische Frontend-Dateien (serve nur, wenn vorhanden)
# ---------------------------------------------------------------------------
if WEB_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(str(WEB_DIR / "index.html"))
else:
    log.warning("Web-Verzeichnis %s existiert nicht - nur API verfuegbar.", WEB_DIR)
