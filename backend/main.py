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

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import docker
from docker.errors import APIError, NotFound
from docker.models.containers import Container
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
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

MC_IMAGE = os.getenv("CRAFTCONTROL_IMAGE", "itzg/minecraft-server:latest")
DATA_ROOT = os.getenv("CRAFTCONTROL_DATA_ROOT", "/data/craftcontrol")
PORT_RANGE_START = int(os.getenv("CRAFTCONTROL_PORT_START", "25565"))
PORT_RANGE_END = int(os.getenv("CRAFTCONTROL_PORT_END", "25600"))
WEB_DIR = Path(os.getenv("CRAFTCONTROL_WEB_DIR", "/app/web"))

# Mapping von UI-Software-Namen zu itzg/minecraft-server TYPE-Werten
# https://docker-minecraft-server.readthedocs.io/en/latest/types-and-platforms/
SOFTWARE_TYPE_MAP: Dict[str, str] = {
    "Vanilla": "VANILLA",
    "Spigot": "SPIGOT",
    "Paper": "PAPER",
    "Forge": "FORGE",
    "Fabric": "FABRIC",
    "Mohist": "MOHIST",
}

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


def _stats_for(c: Container) -> Dict[str, float]:
    """Liest CPU/RAM aus einem Stats-Snapshot. Bei Fehler -> Nullen."""
    if (c.status or "").lower() != "running":
        return {"cpu": 0.0, "ram_mb": 0.0}
    try:
        stats = c.stats(stream=False)
    except APIError as exc:
        log.warning("Stats fuer %s nicht abrufbar: %s", c.name, exc)
        return {"cpu": 0.0, "ram_mb": 0.0}

    # CPU (Linux-Stil)
    cpu_pct = 0.0
    try:
        cpu = stats["cpu_stats"]
        pre = stats["precpu_stats"]
        cpu_delta = cpu["cpu_usage"]["total_usage"] - pre["cpu_usage"]["total_usage"]
        sys_delta = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
        online = cpu.get("online_cpus") or len(cpu["cpu_usage"].get("percpu_usage") or [1])
        if cpu_delta > 0 and sys_delta > 0:
            cpu_pct = (cpu_delta / sys_delta) * online * 100.0
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

    status = _status_from_container(c)
    stats = _stats_for(c) if with_stats else {"cpu": 0.0, "ram_mb": 0.0}
    ram_used_gb = round(stats["ram_mb"] / 1024, 2)

    return {
        "id": c.name,                  # stabiler Identifier nach aussen
        "container_id": c.id[:12],
        "name": name,
        "status": status,
        "software": software,
        "version": version,
        "ram_max": ram_max,
        "ram_used": ram_used_gb,
        "cpu_used": stats["cpu"],
        "players_current": 0,          # Mojang-Query (TBD) - aktuell unbekannt
        "players_max": 20,
        "port": _host_port(c),
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
app = FastAPI(title="CraftControl Backend", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


@app.delete("/api/servers/{server_id}", status_code=204)
def delete_server(server_id: str, purge: bool = False) -> None:
    c = _get_container(server_id)
    try:
        c.remove(force=True, v=purge)
    except APIError as exc:
        raise HTTPException(500, f"Loeschen fehlgeschlagen: {exc}")


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
# Statische Frontend-Dateien (serve nur, wenn vorhanden)
# ---------------------------------------------------------------------------
if WEB_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(str(WEB_DIR / "index.html"))
else:
    log.warning("Web-Verzeichnis %s existiert nicht - nur API verfuegbar.", WEB_DIR)
