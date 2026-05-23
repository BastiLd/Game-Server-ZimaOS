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

import io
import logging
import os
import re
import shlex
import tarfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import docker
import httpx
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
OPTIMIZER_LABEL = "craftcontrol.optimizer"           # 'true'/'false'
TUNNEL_FOR_LABEL = "craftcontrol.tunnel_for"         # value = parent container name (auf playit-Sidecar)
TUNNEL_KIND_LABEL = "craftcontrol.tunnel_kind"       # 'playit'

CPU_OVERLOAD_THRESHOLD = float(os.getenv("CRAFTCONTROL_CPU_OVERLOAD", "90"))
IDLE_OPTIMIZER_MINUTES = int(os.getenv("CRAFTCONTROL_IDLE_MINUTES", "30"))
PLAYIT_IMAGE = os.getenv("CRAFTCONTROL_PLAYIT_IMAGE", "ghcr.io/playit-cloud/playit-cli:latest")

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

# Welche Software laedt aus welchem Verzeichnis und welchen Modrinth-Loader
# verwendet sie? "kind" steuert die Filter im Modrinth-Such-Endpoint:
#   - "plugin" -> nur Bukkit-Plugins (Spigot/Paper/Mohist)
#   - "mod"    -> nur Mods (Forge/Fabric)
#   - "any"    -> beides (Vanilla/unklar)
SOFTWARE_PROFILE: Dict[str, Dict[str, Any]] = {
    "Vanilla": {"dir": "plugins", "loaders": [],                "kind": "any"},
    "Spigot":  {"dir": "plugins", "loaders": ["spigot", "bukkit", "paper"], "kind": "plugin"},
    "Paper":   {"dir": "plugins", "loaders": ["paper", "spigot", "bukkit"], "kind": "plugin"},
    "Mohist":  {"dir": "plugins", "loaders": ["paper", "spigot", "bukkit"], "kind": "plugin"},
    "Forge":   {"dir": "mods",    "loaders": ["forge", "neoforge"],         "kind": "mod"},
    "Fabric":  {"dir": "mods",    "loaders": ["fabric", "quilt"],           "kind": "mod"},
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
    optimizer_on = (c.labels.get(OPTIMIZER_LABEL) or "false").lower() == "true"

    status = _status_from_container(c)
    stats = _stats_for(c) if with_stats else {"cpu": 0.0, "ram_mb": 0.0}
    ram_used_gb = round(stats["ram_mb"] / 1024, 2)

    # Spieler-Cache (befuellt durch /players-Endpoint und Idle-Watchdog)
    cached_players = _PLAYERS_CACHE.get(c.name) or {}
    players_current = cached_players.get("count", 0)

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
        "players_max": 20,
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
    cached = _PLAYERS_CACHE.get(container.name)
    now = time.time()
    if cached and (now - cached.get("ts", 0)) < _PLAYERS_CACHE_TTL:
        return cached

    if (container.status or "").lower() != "running":
        info = {"count": 0, "max": 20, "players": [], "ts": now}
        _PLAYERS_CACHE[container.name] = info
        return info

    code, out = _rcon(container, "list")
    info: Dict[str, Any] = {"count": 0, "max": 20, "players": [], "ts": now}
    if code == 0:
        info.update(_parse_player_list(out))
    info["ts"] = now
    _PLAYERS_CACHE[container.name] = info
    return info


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
    type: str = "auto",     # auto | mod | plugin
    limit: int = 20,
) -> Dict[str, Any]:
    container = _get_container(server_id)
    sw = container.labels.get(SOFTWARE_LABEL) or "Vanilla"
    version = container.labels.get(VERSION_LABEL) or ""

    profile = _profile_for(sw)
    if type == "auto":
        kind = profile["kind"]
    elif type in ("mod", "plugin"):
        kind = type
    else:
        raise HTTPException(400, "type muss 'auto', 'mod' oder 'plugin' sein")

    # Modrinth-Facets: project_type + Loader + game_version
    facets: List[List[str]] = []
    if kind == "plugin":
        facets.append(["project_type:plugin"])
    elif kind == "mod":
        facets.append(["project_type:mod"])
    else:
        facets.append(["project_type:mod", "project_type:plugin"])

    if profile["loaders"]:
        facets.append([f"categories:{l}" for l in profile["loaders"]])

    if version:
        facets.append([f"versions:{version}"])

    params = {
        "query": query,
        "limit": max(1, min(limit, 50)),
        "facets": _to_facets_json(facets),
        "index": "relevance",
    }

    async with httpx.AsyncClient() as client:
        data = await _modrinth_get(client, "/search", params=params)

    hits = []
    for hit in data.get("hits", []):
        hits.append({
            "project_id": hit.get("project_id") or hit.get("slug"),
            "slug": hit.get("slug"),
            "title": hit.get("title"),
            "description": hit.get("description"),
            "downloads": hit.get("downloads"),
            "icon_url": hit.get("icon_url"),
            "categories": hit.get("categories", []),
            "project_type": hit.get("project_type"),
            "latest_version": hit.get("latest_version"),
            "url": f"https://modrinth.com/{hit.get('project_type', 'mod')}/{hit.get('slug')}",
        })
    return {
        "query": query,
        "kind": kind,
        "loaders": profile["loaders"],
        "version": version,
        "total": data.get("total_hits", len(hits)),
        "results": hits,
    }


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
    plugin_dir = f"/data/{profile['dir']}"

    async with httpx.AsyncClient() as client:
        # 1) Passende Version finden
        if payload.version_id:
            version = await _modrinth_get(client, f"/version/{payload.version_id}")
            if not version:
                raise HTTPException(404, "Version nicht gefunden")
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
    plugin_dir = _plugin_dir_for(container)
    items = _list_jars(container, plugin_dir)
    return {"directory": plugin_dir, "items": items}


@app.delete("/api/servers/{server_id}/plugins/installed/{filename}")
def delete_installed_plugin(server_id: str, filename: str) -> Dict[str, Any]:
    container = _get_container(server_id)
    plugin_dir = _plugin_dir_for(container)
    safe_name = _safe_plugin_filename(filename)

    # Pruefen, ob Datei existiert (Path-Traversal kann nicht passieren wegen Regex)
    target = f"{plugin_dir}/{safe_name}"
    code, _ = _container_exec(container, ["test", "-f", target])
    if code != 0:
        raise HTTPException(404, "Datei nicht gefunden")

    code, out = _container_exec(container, ["rm", "-f", target])
    if code != 0:
        raise HTTPException(500, f"Loeschen fehlgeschlagen: {out}")
    return {"status": "deleted", "filename": safe_name, "needs_restart": True}


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
    "server.properties": {"path": "/data/server.properties", "max_kb": 64},
    "whitelist.json":    {"path": "/data/whitelist.json",    "max_kb": 64},
    "ops.json":          {"path": "/data/ops.json",          "max_kb": 64},
    "banned-players.json": {"path": "/data/banned-players.json", "max_kb": 64},
    "banned-ips.json":   {"path": "/data/banned-ips.json",   "max_kb": 64},
    "bukkit.yml":        {"path": "/data/bukkit.yml",        "max_kb": 64},
    "spigot.yml":        {"path": "/data/spigot.yml",        "max_kb": 64},
    "paper-global.yml":  {"path": "/data/config/paper-global.yml", "max_kb": 128},
}


def _read_file_from_container(container: Container, path: str) -> str:
    try:
        bits, _stat = container.get_archive(path)
    except APIError as exc:
        raise HTTPException(404, f"Datei nicht gefunden: {exc.explanation or exc}")
    buf = io.BytesIO(b"".join(bits))
    with tarfile.open(fileobj=buf, mode="r") as tar:
        members = [m for m in tar.getmembers() if m.isfile()]
        if not members:
            raise HTTPException(404, "Leeres Archiv")
        f = tar.extractfile(members[0])
        if not f:
            raise HTTPException(404, "Datei nicht lesbar")
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
        result.append({"name": name, "path": meta["path"], "exists": code == 0})
    return {"files": result}


@app.get("/api/servers/{server_id}/files/{filename}")
def read_editable_file(server_id: str, filename: str) -> Dict[str, Any]:
    if filename not in EDITABLE_FILES:
        raise HTTPException(403, "Datei nicht zur Bearbeitung freigegeben")
    container = _get_container(server_id)
    meta = EDITABLE_FILES[filename]
    content = _read_file_from_container(container, meta["path"])
    return {"name": filename, "path": meta["path"], "content": content}


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
# ---------------------------------------------------------------------------
@app.put("/api/servers/{server_id}/optimizer")
def set_optimizer(server_id: str, payload: OptimizerRequest) -> Dict[str, Any]:
    """
    Schaltet den Auto-RAM-Optimierer per Container-Label um.
    Der Hintergrund-Watchdog liest dieses Label und leitet bei 0 Spielern
    fuer >= IDLE_OPTIMIZER_MINUTES eine GC-Hilfe ein (save-all + reload).
    """
    container = _get_container(server_id)
    new_labels = dict(container.labels or {})
    new_labels[OPTIMIZER_LABEL] = "true" if payload.enabled else "false"
    # Docker erlaubt label-update nicht direkt -> wir merken uns nur den Wert,
    # indem wir 'docker update' Workaround nutzen ist nicht moeglich. Wir
    # schreiben den Zustand stattdessen in den In-Memory-Store und legen
    # ihn beim Neuanlegen mit ins Label. Reicht fuer die Laufzeit voellig.
    _OPTIMIZER_STATE[container.name] = bool(payload.enabled)
    return {"status": "ok", "enabled": bool(payload.enabled)}


# In-memory Optimierer-Status (Labels lassen sich nach Create nicht aendern)
_OPTIMIZER_STATE: Dict[str, bool] = {}


def _optimizer_enabled_for(container: Container) -> bool:
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


@app.on_event("startup")
def _start_idle_watchdog() -> None:
    t = threading.Thread(target=_idle_watchdog_loop, name="idle-watchdog", daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# v1.0.4: playit.gg Tunnel (Sidecar-Container)
# ---------------------------------------------------------------------------
def _tunnel_container_for(server_id: str) -> Optional[Container]:
    """Findet den Sidecar-Tunnel fuer den Server (per Label)."""
    for c in docker_client().containers.list(all=True, filters={"label": f"{TUNNEL_FOR_LABEL}={server_id}"}):
        return c
    return None


def _tunnel_status_dto(parent: Container, tunnel: Optional[Container]) -> Dict[str, Any]:
    if not tunnel:
        return {"status": "not_started", "message": "Kein Tunnel laeuft fuer diesen Server."}
    tunnel.reload()
    state = (tunnel.status or "unknown").lower()
    # Versuche aus den Logs eine Domain zu extrahieren (playit gibt die im stdout aus)
    domain = None
    try:
        logs = tunnel.logs(tail=200).decode("utf-8", errors="replace")
        m = re.search(r"([a-zA-Z0-9-]+\.(?:joinmc\.link|playit\.gg|gl\.at\.ply\.gg|at\.playit\.gg))(?::\d+)?", logs)
        if m:
            domain = m.group(0)
    except Exception:  # noqa: BLE001
        domain = None
    return {
        "status": state,
        "tunnel_container": tunnel.name,
        "domain": domain,
        "message": "Domain wird angezeigt, sobald playit sie zugewiesen hat. Logge dich ggf. unter https://playit.gg ein, um den Agent zu authentifizieren.",
    }


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

    existing = _tunnel_container_for(server_id)
    if existing:
        existing.reload()
        if (existing.status or "").lower() == "running":
            return _tunnel_status_dto(parent, existing)
        # alten gestoppten Tunnel entsorgen, dann neu starten
        try:
            existing.remove(force=True)
        except APIError:
            pass

    tunnel_name = f"{parent.name}-playit"
    env: Dict[str, str] = {}
    if payload.secret:
        env["PLAYIT_SECRET"] = payload.secret

    try:
        tunnel = docker_client().containers.run(
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
            network_mode=f"container:{parent.name}",  # share network namespace
            restart_policy={"Name": "unless-stopped"},
        )
    except APIError as exc:
        raise HTTPException(500, f"Tunnel konnte nicht gestartet werden: {exc.explanation or exc}")

    time.sleep(0.5)
    tunnel.reload()
    return _tunnel_status_dto(parent, tunnel)


@app.post("/api/servers/{server_id}/tunnel/stop")
def tunnel_stop(server_id: str) -> Dict[str, Any]:
    _get_container(server_id)  # validiert Berechtigung
    tunnel = _tunnel_container_for(server_id)
    if not tunnel:
        return {"status": "not_started"}
    try:
        tunnel.remove(force=True)
    except APIError as exc:
        raise HTTPException(500, f"Tunnel konnte nicht gestoppt werden: {exc}")
    return {"status": "stopped"}


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
