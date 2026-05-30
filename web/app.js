        
        /* D.1 CORE MOCK DATA SYSTEM */
        const INITIAL_SOFTWARE = [
            { id: 'Vanilla', name: 'Vanilla', desc: 'Die offizielle, unveränderte Minecraft Serversoftware von Mojang.', color: '#d32f2f' },
            { id: 'Spigot', name: 'Spigot', desc: 'Die klassische, weit verbreitete Software mit solider Plugin-Kompatibilität.', color: '#e65100' },
            { id: 'Paper', name: 'Paper', desc: 'Hochgradig optimierter Fork von Spigot, ideal für performance-kritische Server.', color: '#0288d1' },
            { id: 'Purpur', name: 'Purpur', desc: 'Paper-Fork mit zahlreichen zusätzlichen Gameplay- und Performance-Optionen.', color: '#8e24aa' },
            { id: 'Forge', name: 'Forge', desc: 'Der unangefochtene Standard für komplexe, umfangreiche Mods und Modpacks.', color: '#7b1fa2' },
            { id: 'Fabric', name: 'Fabric', desc: 'Die moderne, extrem leichtgewichtige Modding-API mit blitzschnellem Laden.', color: '#388e3c' },
            { id: 'Mohist', name: 'Mohist', desc: 'Smarter Hybrid, der sowohl Spigot-Plugins als auch Forge-Mods gleichzeitig ausführen kann.', color: '#fbc02d' },
            { id: 'Arclight', name: 'Arclight', desc: 'Hybrid-Server, der Bukkit/Spigot-Plugins zusammen mit Forge/Fabric-Mods lädt.', color: '#00897b' }
        ];

        const PLUGINS_DATABASE = [
            { id: 'EssentialsX', name: 'EssentialsX', desc: 'Bietet über 100 unverzichtbare Befehle wie /teleport, /warp, /home und Eco.', category: 'PLUGIN', version: '2.20.1' },
            { id: 'WorldEdit', name: 'WorldEdit', desc: 'Das ultimative Tool zur Ingame-Weltenbearbeitung mit Pinseln und Selektionen.', category: 'PLUGIN', version: '7.3.0' },
            { id: 'LuckPerms', name: 'LuckPerms', desc: 'Hochwertiges, sicheres und performantes Permission-Plugin zur Rechteverwaltung.', category: 'PLUGIN', version: '5.4.12' },
            { id: 'Vault', name: 'Vault', desc: 'Essenzielle Schnittstelle (API) zur einfachen Verknüpfung von Economy und Rechten.', category: 'PLUGIN', version: '1.7.3' },
            { id: 'Dynmap', name: 'Dynmap', desc: 'Generiert eine voll interaktive, zoombare 2D/3D Google-Maps-Karte deiner Welten im Web.', category: 'PLUGIN', version: '3.6.0' },
            { id: 'GeyserMC', name: 'GeyserMC', desc: 'Ermöglicht es Minecraft Bedrock (Handy/Konsole) Spielern, deinem Java-Server beizutreten.', category: 'PLUGIN', version: '2.2.0' },
            { id: 'Create', name: 'Create Mod', desc: 'Erweitert Minecraft um phantastische Zahnräder, mechanische Antriebe und Automationen.', category: 'MOD', version: '0.5.1' },
            { id: 'JEI', name: 'Just Enough Items', desc: 'Das unverzichtbare Ingame-Rezeptbuch zum Durchsuchen aller Items und Crafting-Wege.', category: 'MOD', version: '15.2.0' },
            { id: 'BiomesOPlenty', name: 'Biomes O\' Plenty', desc: 'Fügt Dutzende atemberaubende, realistische und fantasievolle neue Biome hinzu.', category: 'MOD', version: '19.0.0' },
            { id: 'Waystones', name: 'Waystones Mod', desc: 'Ermöglicht das Aufstellen von Teleportations-Steinen für schnelles Ingame-Reisen.', category: 'MOD', version: '14.1.0' }
        ];

        /* ---------------------------------------------------------------
           SICHERHEIT: HTML-Escaping
           Wird auf alle dynamischen Werte angewendet, die per innerHTML in
           Templates landen (Server-/Spielernamen, Logs, Modrinth-Titel,
           Backup-Namen, Tunnel-Domains, Dateinamen). Verhindert XSS.
           --------------------------------------------------------------- */
        function escapeHtml(value) {
            if (value === null || value === undefined) return '';
            return String(value)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }

        /* ---------------------------------------------------------------
           D.0 BACKEND-API CLIENT
           Spricht das FastAPI-Backend an. Wenn das Frontend aus dem Container
           ausgeliefert wird, ist Origin gleich -> relative URL reicht.
           --------------------------------------------------------------- */
        const AUTH_TOKEN_KEY = 'craftcontrol_token';
        const API = (() => {
            const base = window.CRAFTCONTROL_API || '';
            function authHeaders() {
                const token = localStorage.getItem(AUTH_TOKEN_KEY);
                return token ? { 'Authorization': `Bearer ${token}` } : {};
            }
            async function req(method, path, body) {
                const opts = { method, headers: { 'Accept': 'application/json', ...authHeaders() } };
                if (body !== undefined) {
                    opts.headers['Content-Type'] = 'application/json';
                    opts.body = JSON.stringify(body);
                }
                const res = await fetch(base + path, opts);
                if (res.status === 401) {
                    // Token fehlt/ungueltig -> Login erzwingen
                    if (window.app && typeof window.app.requireLogin === 'function') {
                        window.app.requireLogin();
                    }
                    throw new Error('Nicht autorisiert');
                }
                if (res.status === 204) return null;
                let data = null;
                try { data = await res.json(); } catch (_) { /* ignore */ }
                if (!res.ok) {
                    const msg = (data && (data.detail || data.message)) || res.statusText;
                    throw new Error(msg);
                }
                return data;
            }
            return {
                _base: base,
                authHeaders,
                authCheck: () => req('GET', '/api/auth/check'),
                listServers: () => req('GET', '/api/servers'),
                getServer:   (id) => req('GET', `/api/servers/${id}`),
                createServer:(payload) => req('POST', '/api/servers', payload),
                deleteServer:(id) => req('DELETE', `/api/servers/${id}`),
                start:       (id) => req('POST', `/api/servers/${id}/start`),
                stop:        (id) => req('POST', `/api/servers/${id}/stop`),
                restart:     (id) => req('POST', `/api/servers/${id}/restart`),
                logs:        (id, tail = 200) => req('GET', `/api/servers/${id}/logs?tail=${tail}`),
                command:     (id, command) => req('POST', `/api/servers/${id}/command`, { command }),
                stats:       () => req('GET', '/api/stats'),
                minecraftVersions: () => req('GET', '/api/minecraft/versions'),
                // Plugin / Mod (Modrinth)
                searchPlugins: (id, query, type = 'auto', limit = 25) =>
                    req('GET', `/api/servers/${id}/plugins/search?query=${encodeURIComponent(query || '')}&type=${type}&limit=${limit}`),
                installPlugin: (id, projectId, versionId, target) =>
                    req('POST', `/api/servers/${id}/plugins/install`, { project_id: projectId, version_id: versionId || null, target: target || null }),
                installedPlugins: (id) => req('GET', `/api/servers/${id}/plugins/installed`),
                deleteInstalledPlugin: (id, filename) =>
                    req('DELETE', `/api/servers/${id}/plugins/installed/${encodeURIComponent(filename)}`),
                // v1.0.4: Spieler / Dateien / Optimierer / Tunnel
                listPlayers: (id) => req('GET', `/api/servers/${id}/players`),
                playerOp:    (id, player) => req('POST', `/api/servers/${id}/players/op`,    { player }),
                playerDeop:  (id, player) => req('POST', `/api/servers/${id}/players/deop`,  { player }),
                playerKick:  (id, player, reason) => req('POST', `/api/servers/${id}/players/kick`, { player, reason }),
                playerBan:   (id, player, reason) => req('POST', `/api/servers/${id}/players/ban`,  { player, reason }),
                listFiles:   (id) => req('GET', `/api/servers/${id}/files`),
                readFile:    (id, name) => req('GET', `/api/servers/${id}/files/${encodeURIComponent(name)}`),
                writeFile:   (id, name, content) =>
                    req('PUT', `/api/servers/${id}/files/${encodeURIComponent(name)}`, { content }),
                setOptimizer:(id, enabled) => req('PUT', `/api/servers/${id}/optimizer`, { enabled }),
                tunnelStatus:(id) => req('GET', `/api/servers/${id}/tunnel`),
                tunnelStart: (id, secret) => req('POST', `/api/servers/${id}/tunnel/start`, { secret: secret || null }),
                tunnelStop:  (id) => req('POST', `/api/servers/${id}/tunnel/stop`),
                // v1.1.0: Echte Backups
                listBackups:   (id) => req('GET', `/api/servers/${id}/backups`),
                createBackup:  (id) => req('POST', `/api/servers/${id}/backups`),
                deleteBackup:  (id, name) => req('DELETE', `/api/servers/${id}/backups/${encodeURIComponent(name)}`),
                restoreBackup: (id, name) => req('POST', `/api/servers/${id}/backups/${encodeURIComponent(name)}/restore`),
                backupDownloadUrl: (id, name) => `${base}/api/servers/${id}/backups/${encodeURIComponent(name)}`,
            };
        })();

        // In-Memory-Cache der vom Backend gelieferten Server (DTOs).
        // Pro Server haengen wir clientseitig logs/installedExtensions/backups an,
        // bis diese Bereiche ebenfalls vom Backend gepflegt werden.
        let servers = [];


        /* D.2 VIEW & SIMULATION CONTROLLER */
        class DashboardApp {
            constructor() {
                this.activeServerId = null;
                this.activeTab = 'status';
                this.simulationInterval = null;
                this._statusSub = 'console';
                
                // Form element references
                this.ramSlider = document.getElementById('new-server-ram');
                this.ramValueLabel = document.getElementById('new-ram-value-label');
                
                // Modal RAM Slider event
                this.ramSlider.addEventListener('input', (e) => {
                    this.ramValueLabel.innerText = e.target.value + ' GB';
                });

                // Version Panel RAM Slider event
                document.getElementById('input-allocated-ram').addEventListener('input', (e) => {
                    document.getElementById('label-allocated-ram').innerText = e.target.value + ' GB';
                });

                // Listen for drag & drop uploads
                this.setupDragAndDrop();

                // v1.0.4: globaler Klick-Handler schliesst offene Spieler-Menues
                document.addEventListener('click', () => this._closeAllPlayerMenus());
            }

            async init() {
                // v1.1.0: Auth-Gate. Prueft, ob das Backend einen Token verlangt.
                this._setupLoginForm();
                let authed = false;
                try {
                    const check = await API.authCheck();
                    this._authRequired = !!(check && check.auth_required);
                    authed = !this._authRequired || !!(check && check.ok);
                } catch (_) {
                    authed = true;  // /auth/check ist nie geschuetzt; Fehler -> App trotzdem starten
                }
                if (!authed) {
                    this.requireLogin();
                    return;
                }
                this._startApp();
            }

            _startApp() {
                if (this._appStarted) return;
                this._appStarted = true;
                this._hideLogin();
                this._applySavedTheme();

                // Abmelden-Button nur zeigen, wenn ein Token verlangt wird
                const logoutBtn = document.getElementById('btn-logout');
                if (logoutBtn) logoutBtn.style.display = this._authRequired ? 'inline-flex' : 'none';

                // Initial Software-Karten anzeigen (Default Vanilla)
                this.renderSoftwareCards('Vanilla');

                // v1.0.7: Minecraft-Versionsliste laden (Mojang-Manifest oder Fallback)
                this.loadMinecraftVersions();

                // Erstes Laden + Polling
                this.refreshServers().then(() => {
                    this.renderServerGrid();
                    this.renderGlobalStats();
                });
                this.startResourceSimulation();
            }

            /* --- v1.1.0: LOGIN / TOKEN-HANDLING --- */
            _setupLoginForm() {
                const form = document.getElementById('login-form');
                if (form && !form._wired) {
                    form._wired = true;
                    form.addEventListener('submit', (e) => {
                        e.preventDefault();
                        this.attemptLogin();
                    });
                }
            }

            requireLogin() {
                const overlay = document.getElementById('login-overlay');
                if (overlay) overlay.style.display = 'flex';
                const input = document.getElementById('login-token-input');
                if (input) { input.value = ''; setTimeout(() => input.focus(), 50); }
            }

            _hideLogin() {
                const overlay = document.getElementById('login-overlay');
                if (overlay) overlay.style.display = 'none';
            }

            async attemptLogin() {
                const input = document.getElementById('login-token-input');
                const errEl = document.getElementById('login-error');
                const token = (input && input.value || '').trim();
                if (!token) return;
                localStorage.setItem(AUTH_TOKEN_KEY, token);
                try {
                    const check = await API.authCheck();
                    if (check && check.ok) {
                        if (errEl) errEl.style.display = 'none';
                        this._startApp();
                    } else {
                        throw new Error('Token ungültig');
                    }
                } catch (_) {
                    localStorage.removeItem(AUTH_TOKEN_KEY);
                    if (errEl) { errEl.textContent = 'Token ungültig. Bitte erneut versuchen.'; errEl.style.display = 'block'; }
                }
            }

            logout() {
                localStorage.removeItem(AUTH_TOKEN_KEY);
                location.reload();
            }

            // v1.0.7: Versions-Dropdowns aus dem Backend befuellen.
            // Aufgerufen beim Init und nochmal beim Oeffnen des Dashboards
            // (zum Vorbelegen der aktuellen Server-Version).
            async loadMinecraftVersions() {
                try {
                    const data = await API.minecraftVersions();
                    const versions = data && Array.isArray(data.versions) ? data.versions : [];
                    if (!versions.length) return;
                    this._mcVersions = versions;
                    this._mcLatest = data.latest_release || versions[0];

                    const buildOptions = () => versions.map(v => {
                        const isLatest = v === this._mcLatest;
                        const label = isLatest ? `${v} (Neueste Version)` : v;
                        return `<option value="${v}">${label}</option>`;
                    }).join('');

                    const sel1 = document.getElementById('select-mc-version');
                    const sel2 = document.getElementById('new-server-version');
                    if (sel1) {
                        const prev = sel1.value;
                        sel1.innerHTML = buildOptions();
                        sel1.value = prev && versions.includes(prev) ? prev : this._mcLatest;
                    }
                    if (sel2) {
                        sel2.innerHTML = buildOptions();
                        sel2.value = this._mcLatest;
                    }
                } catch (err) {
                    console.warn('Konnte Minecraft-Versionen nicht laden:', err);
                }
            }

            /* --- BACKEND SYNC --- */
            // Holt die aktuelle Server-Liste vom Backend und merged sie mit
            // den client-only-Feldern (logs/installedExtensions/backups).
            async refreshServers() {
                let dtos = [];
                try {
                    dtos = await API.listServers();
                } catch (err) {
                    console.error('Server-Liste konnte nicht geladen werden:', err);
                    this.showToast('Backend nicht erreichbar: ' + err.message, 'error');
                    return;
                }

                const previous = new Map(servers.map(s => [s.id, s]));
                servers = dtos.map(dto => this._mergeDto(dto, previous.get(dto.id)));
                return servers;
            }

            async refreshServer(id) {
                try {
                    const dto = await API.getServer(id);
                    const idx = servers.findIndex(s => s.id === id);
                    const prev = idx >= 0 ? servers[idx] : null;
                    const merged = this._mergeDto(dto, prev);
                    if (idx >= 0) servers[idx] = merged;
                    else servers.push(merged);
                    return merged;
                } catch (err) {
                    console.warn('refreshServer:', err);
                    return null;
                }
            }

            // Mappt ein Backend-DTO auf das vom UI erwartete Schema.
            _mergeDto(dto, prev) {
                return {
                    id: dto.id,
                    containerId: dto.container_id,
                    name: dto.name,
                    status: dto.status,                // running | starting | stopping | offline
                    software: dto.software,
                    version: dto.version,
                    ramMax: dto.ram_max,
                    ramUsed: dto.ram_used,
                    ramPct: dto.ram_pct || 0,
                    cpuUsed: dto.cpu_used,
                    overloaded: !!dto.overloaded,
                    optimizer: !!dto.optimizer,
                    playersCurrent: dto.players_current,
                    playersMax: dto.players_max,
                    port: dto.port,
                    installedExtensions: prev ? prev.installedExtensions : [],
                    backups: prev ? prev.backups : [],
                    logs: prev ? prev.logs : [],
                    onlinePlayers: prev ? prev.onlinePlayers : [],
                    tunnel: prev ? prev.tunnel : null,
                };
            }

            /* --- THEME SWITCHING --- */
            switchTheme(themeName) {
                document.documentElement.setAttribute('data-theme', themeName);
                localStorage.setItem('craftcontrol_theme', themeName);

                // Toggle active styling on buttons
                document.querySelectorAll('.theme-btn').forEach(btn => btn.classList.remove('active'));
                const targetBtn = document.getElementById(`theme-btn-${themeName}`);
                if (targetBtn) targetBtn.classList.add('active');

                this.showToast(`Theme gewechselt zu: ${themeName.replace('-', ' ').toUpperCase()}`, 'success');
            }

            _applySavedTheme() {
                const saved = localStorage.getItem('craftcontrol_theme');
                const theme = saved || document.documentElement.getAttribute('data-theme') || 'nether';
                document.documentElement.setAttribute('data-theme', theme);
                document.querySelectorAll('.theme-btn').forEach(btn => btn.classList.remove('active'));
                const targetBtn = document.getElementById(`theme-btn-${theme}`);
                if (targetBtn) targetBtn.classList.add('active');
            }

            /* --- VIEW NAVIGATION --- */
            showOverview() {
                document.getElementById('view-dashboard').classList.remove('active');
                document.getElementById('view-overview').classList.add('active');
                this.activeServerId = null;
                this.refreshServers().then(() => {
                    this.renderServerGrid();
                    this.renderGlobalStats();
                });
            }

            async showDashboard(serverId) {
                this.activeServerId = serverId;
                let server = this.getServer(serverId);
                if (!server) {
                    server = await this.refreshServer(serverId);
                }
                if (!server) return;

                document.getElementById('view-overview').classList.remove('active');
                document.getElementById('view-dashboard').classList.add('active');

                // Populate Server specific sidebar metadata
                document.getElementById('dash-server-title').innerText = server.name;
                document.getElementById('dash-server-software-label').innerText = `${server.software} ${server.version}`;

                // Re-render server icon
                const iconContainer = document.getElementById('dash-server-icon');
                iconContainer.innerHTML = this.getSoftwareSVG(server.software, server.status === 'running');

                this.updateDashboardHeaderAndControls(server);

                // Switch to default console tab
                this.switchTab('status');
                this.updateResourceMeters(server);
                this.loadBackups(server.id);
                this.loadInstalledExtensions(server);
                this.renderSoftwareCards(server.software);

                // Pre-fill versions configuration
                // v1.0.7: Versionsliste sicherstellen, bevor wir den Wert setzen.
                if (!this._mcVersions || !this._mcVersions.length) {
                    await this.loadMinecraftVersions();
                }
                const sel = document.getElementById('select-mc-version');
                if (sel && server.version && !Array.from(sel.options).some(o => o.value === server.version)) {
                    const opt = document.createElement('option');
                    opt.value = server.version;
                    opt.text = `${server.version} (aktiv)`;
                    sel.insertBefore(opt, sel.firstChild);
                }
                if (sel) sel.value = server.version;
                document.getElementById('input-allocated-ram').value = server.ramMax;
                document.getElementById('label-allocated-ram').innerText = server.ramMax + ' GB';

                // Live-Logs vom Backend
                this.refreshLogs(server.id);
                // v1.0.4: Spieler + Tunnel-Status
                this.loadPlayers(server.id);
                this.refreshTunnel(server.id);
            }

            /* --- TAB CONTROLLER --- */
            switchTab(tabId) {
                this.activeTab = tabId;
                
                // Deactivate all nav buttons and panels
                document.querySelectorAll('.dash-nav-btn').forEach(btn => btn.classList.remove('active'));
                document.querySelectorAll('.tab-panel').forEach(panel => panel.classList.remove('active'));

                // Activate specific
                document.getElementById(`tab-btn-${tabId}`).classList.add('active');
                document.getElementById(`panel-${tabId}`).classList.add('active');

                if (tabId === 'plugins') {
                    this.renderPluginsCatalog();
                    const server = this.getServer(this.activeServerId);
                    if (server) this.loadInstalledExtensions(server);
                }
                if (tabId === 'files') {
                    this.loadFilesIndex();
                }
            }

            /* --- SERVER MANAGEMENT & LISTING --- */
            getServer(id) {
                return servers.find(s => s.id === id);
            }

            renderGlobalStats() {
                const total = servers.length;
                const online = servers.filter(s => s.status === 'running').length;
                let activePlayers = 0;
                let maxPlayers = 0;

                servers.forEach(s => {
                    if (s.status === 'running') {
                        activePlayers += s.playersCurrent;
                        maxPlayers += s.playersMax;
                    }
                });

                document.getElementById('stat-total-servers').innerText = total;
                document.getElementById('stat-online-servers').innerText = online;
                document.getElementById('stat-total-players').innerText = `${activePlayers} / ${maxPlayers}`;
            }

            renderServerGrid() {
                const container = document.getElementById('server-list-container');
                container.innerHTML = '';

                servers.forEach(server => {
                    const isRunning = server.status === 'running';
                    const isStarting = server.status === 'starting';
                    const statusClass = server.status; // running, starting, stopping, offline
                    const displayStatus = server.status.toUpperCase();

                    const card = document.createElement('div');
                    card.className = `server-card glass-panel ${statusClass}`;
                    card.setAttribute('onclick', `app.handleCardClick(event, '${server.id}')`);

                    // Resource display logic
                    const ramPercent = isRunning ? (server.ramUsed / server.ramMax) * 100 : 0;
                    const cpuVal = isRunning ? server.cpuUsed : 0;
                    const playersVal = isRunning ? `${server.playersCurrent}/${server.playersMax}` : '0/0';

                    card.innerHTML = `
                        <div>
                            <div class="server-card-top">
                                <div class="server-icon">
                                    ${this.getSoftwareSVG(server.software, isRunning)}
                                </div>
                                <div class="status-badge">
                                    <span class="status-dot"></span>
                                    <span class="status-text">${displayStatus}</span>
                                </div>
                            </div>
                            
                            <div class="server-card-info">
                                <h3>${this._escape(server.name)}</h3>
                                <p>
                                    <span class="badge-software">${this._escape(server.software)}</span>
                                    <span>Minecraft ${this._escape(server.version)}</span>
                                </p>
                            </div>
                        </div>

                        <div>
                            <div class="server-card-meters">
                                <div class="meter-row">
                                    <span class="meter-label">
                                        <svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5.121 17.804A13.937 13.937 0 0112 16c2.5 0 4.847.655 6.879 1.804M15 10a3 3 0 11-6 0 3 3 0 016 0zm6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg> Spieler
                                    </span>
                                    <span>${playersVal}</span>
                                </div>
                                <div class="meter-row">
                                    <span class="meter-label">CPU</span>
                                    <span>${cpuVal}%</span>
                                </div>
                                <div class="meter-bar-container">
                                    <div class="meter-bar-fill" style="width: ${cpuVal}%"></div>
                                </div>
                                <div class="meter-row" style="margin-top: 4px;">
                                    <span class="meter-label">RAM</span>
                                    <span>${isRunning ? server.ramUsed.toFixed(1) : 0} / ${server.ramMax} GB</span>
                                </div>
                                <div class="meter-bar-container">
                                    <div class="meter-bar-fill" style="width: ${ramPercent}%"></div>
                                </div>
                            </div>

                            <div class="server-card-actions">
                                <button class="btn btn-primary" style="flex-grow:1;" onclick="app.manageServerDirectly(event, '${server.id}')">Verwalten</button>
                                ${isRunning ? `
                                    <button class="btn btn-danger btn-circle" title="Server Stoppen" onclick="app.quickTogglePower(event, '${server.id}', 'stop')">
                                        <svg width="14" height="14" fill="currentColor" viewBox="0 0 16 16"><path d="M5 3.5h6A1.5 1.5 0 0 1 12.5 5v6a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 11V5A1.5 1.5 0 0 1 5 3.5z"/></svg>
                                    </button>
                                ` : `
                                    <button class="btn btn-success btn-circle" title="Server Starten" ${isStarting ? 'disabled' : ''} onclick="app.quickTogglePower(event, '${server.id}', 'start')">
                                        <svg width="14" height="14" fill="currentColor" viewBox="0 0 16 16"><path d="m11.596 8.697-6.363 3.692c-.54.313-1.233-.066-1.233-.697V4.308c0-.63.692-1.01 1.233-.696l6.363 3.692a.802.802 0 0 1 0 1.393z"/></svg>
                                    </button>
                                `}
                                <button class="btn btn-danger btn-circle" title="Server löschen" onclick="app.deleteServer(event, '${server.id}', '${this._escape ? this._escape(server.name) : server.name}')">
                                    <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>
                                </button>
                            </div>
                        </div>
                    `;

                    container.appendChild(card);
                });

                // Add Plus card to grid
                const plusCard = document.createElement('div');
                plusCard.className = 'add-server-card';
                plusCard.setAttribute('onclick', 'app.openCreateModal()');
                plusCard.innerHTML = `
                    <div class="plus-icon">
                        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                    </div>
                    <span>Neuen Server anlegen</span>
                `;
                container.appendChild(plusCard);
            }

            handleCardClick(event, serverId) {
                // Prevent routing to dashboard if a button is clicked
                if (event.target.closest('.btn') || event.target.closest('.server-card-actions')) {
                    return;
                }
                this.showDashboard(serverId);
            }

            manageServerDirectly(event, serverId) {
                event.stopPropagation();
                this.showDashboard(serverId);
            }

            /* --- LOESCHEN --- */
            // Aufruf von der Karte (Overview)
            async deleteServer(event, serverId, displayName) {
                if (event) event.stopPropagation();
                const ok = confirm(
                    `Server "${displayName || serverId}" wirklich endgueltig loeschen?\n\n` +
                    `Der Container UND das zugehoerige Welt-Volume werden entfernt. ` +
                    `Diese Aktion kann nicht rueckgaengig gemacht werden.`
                );
                if (!ok) return;
                await this._performDelete(serverId, displayName);
            }

            // Aufruf vom Dashboard-Sidebar
            async deleteActiveServer() {
                if (!this.activeServerId) return;
                const server = this.getServer(this.activeServerId);
                await this.deleteServer(null, this.activeServerId, server ? server.name : null);
            }

            async _performDelete(serverId, displayName) {
                this.showToast(`Loesche "${displayName || serverId}" ...`, 'warn');
                try {
                    await API.deleteServer(serverId);
                    this.showToast(`Server "${displayName || serverId}" wurde entfernt.`, 'success');
                    if (this.activeServerId === serverId) {
                        this.activeServerId = null;
                        document.getElementById('view-dashboard').classList.remove('active');
                        document.getElementById('view-overview').classList.add('active');
                    }
                    await this.refreshServers();
                    this.renderServerGrid();
                    this.renderGlobalStats();
                } catch (err) {
                    console.error(err);
                    this.showToast('Loeschen fehlgeschlagen: ' + err.message, 'error');
                }
            }

            /* --- MODAL LOGIC --- */
            openCreateModal() {
                document.getElementById('createServerModal').classList.add('active');
            }

            closeCreateModal() {
                document.getElementById('createServerModal').classList.remove('active');
                document.getElementById('create-server-form').reset();
                this.ramValueLabel.innerText = '4 GB';
            }

            async handleCreateServer(event) {
                event.preventDefault();
                const name = document.getElementById('new-server-name').value.trim();
                const software = document.getElementById('new-server-software').value;
                const version = document.getElementById('new-server-version').value;
                const ram = parseInt(document.getElementById('new-server-ram').value);

                if (!name) return;

                const submitBtn = event.submitter || event.target.querySelector('button[type="submit"]');
                if (submitBtn) submitBtn.disabled = true;
                this.showToast(`Lege Server "${name}" an...`, 'warn');

                try {
                    await API.createServer({ name, software, version, ram });
                    this.closeCreateModal();
                    await this.refreshServers();
                    this.renderServerGrid();
                    this.renderGlobalStats();
                    this.showToast(`Server "${name}" wurde angelegt.`, 'success');
                } catch (err) {
                    console.error(err);
                    this.showToast('Fehler beim Anlegen: ' + err.message, 'error');
                } finally {
                    if (submitBtn) submitBtn.disabled = false;
                }
            }

            /* --- POWER STATE CONTROLLER (BACKEND) --- */
            async quickTogglePower(event, serverId, action) {
                event.stopPropagation();
                await this._powerAction(serverId, action);
            }

            async triggerActiveServerPower(action) {
                if (!this.activeServerId) return;
                await this._powerAction(this.activeServerId, action);
            }

            async _powerAction(serverId, action) {
                const server = this.getServer(serverId);
                if (!server) return;
                const map = { start: API.start, stop: API.stop, restart: API.restart };
                const fn = map[action];
                if (!fn) return;

                // Optimistisches UI-Feedback
                if (action === 'start') server.status = 'starting';
                else if (action === 'stop') server.status = 'stopping';
                else server.status = 'starting';
                this.syncAllUIs();

                this.showToast(`Aktion "${action}" wird ausgefuehrt...`, 'warn');
                try {
                    await fn(serverId);
                    await this.refreshServer(serverId);
                    this.syncAllUIs();
                    this.refreshLogs(serverId);
                    this.showToast(`"${server.name}" -> ${action} ausgefuehrt.`, 'success');
                } catch (err) {
                    console.error(err);
                    this.showToast('Fehler: ' + err.message, 'error');
                    await this.refreshServer(serverId);
                    this.syncAllUIs();
                }
            }

            // Holt Logs vom Backend und schreibt sie in das logs-Array.
            async refreshLogs(serverId) {
                const server = this.getServer(serverId);
                if (!server) return;
                try {
                    const data = await API.logs(serverId, 200);
                    server.logs = data.lines || [];
                } catch (err) {
                    server.logs = [`[SYSTEM]: Logs konnten nicht geladen werden: ${err.message}`];
                }
                if (this.activeServerId === serverId && this.activeTab === 'status') {
                    this.renderConsoleLogs(server);
                }
            }

            executeStartupSequence(server) {
                if (server.status === 'starting' || server.status === 'running') return;

                server.status = 'starting';
                server.cpuUsed = 15;
                server.ramUsed = 0.5;
                
                this.addLog(server, 'SYSTEM', 'Serverstart initiiert...');
                this.addLog(server, 'INFO', 'Loading libraries, please wait...');
                
                // Live UI Sync
                this.syncAllUIs();

                let step = 0;
                const startupTimer = setInterval(() => {
                    if (this.activeServerId === server.id && this.activeTab === 'status') {
                        this.renderConsoleLogs(server);
                    }
                    
                    step++;
                    if (step === 1) {
                        this.addLog(server, 'INFO', `Starting minecraft server version ${server.version}`);
                        server.cpuUsed = 45;
                        server.ramUsed = 1.2;
                    } else if (step === 2) {
                        this.addLog(server, 'INFO', 'Loading properties and binding server port 25565...');
                        server.cpuUsed = 75;
                        server.ramUsed = 2.4;
                    } else if (step === 3) {
                        this.addLog(server, 'INFO', `Preparing level "world" under ${server.software} environment`);
                        server.cpuUsed = 92;
                        server.ramUsed = 3.6;
                    } else if (step === 4) {
                        this.addLog(server, 'INFO', 'Preparing start region for dimension minecraft:overworld (0%)');
                        this.addLog(server, 'INFO', 'Preparing start region for dimension minecraft:the_nether (42%)');
                        this.addLog(server, 'INFO', 'Preparing start region for dimension minecraft:the_end (86%)');
                        server.ramUsed = server.ramMax * 0.75;
                    } else if (step === 5) {
                        this.addLog(server, 'INFO', `Done (8.42s)! Server binds on port 25565. For help, type "help" or "/plugins"`);
                        
                        server.status = 'running';
                        server.cpuUsed = 8;
                        server.playersCurrent = 0;
                        
                        clearInterval(startupTimer);
                        this.syncAllUIs();
                        this.showToast(`Server "${server.name}" ist jetzt ONLINE!`, 'success');
                    }
                    
                    this.syncAllUIs();
                }, 1200);
            }

            executeShutdownSequence(server) {
                if (server.status === 'stopping' || server.status === 'offline') return;

                server.status = 'stopping';
                server.playersCurrent = 0;
                server.cpuUsed = 30;
                
                this.addLog(server, 'SYSTEM', 'Server-Shutdown initiiert...');
                this.addLog(server, 'INFO', 'Saving players and saving worlds...');
                
                this.syncAllUIs();

                let step = 0;
                const shutdownTimer = setInterval(() => {
                    step++;
                    if (step === 1) {
                        this.addLog(server, 'INFO', 'Saving chunks to disk...');
                        server.ramUsed = server.ramUsed * 0.5;
                        server.cpuUsed = 50;
                    } else if (step === 2) {
                        this.addLog(server, 'INFO', 'Closing server socket listener on 25565');
                        server.ramUsed = 0.5;
                        server.cpuUsed = 10;
                    } else if (step === 3) {
                        this.addLog(server, 'INFO', 'Server shutdown successfully completed.');
                        
                        server.status = 'offline';
                        server.cpuUsed = 0;
                        server.ramUsed = 0;
                        
                        clearInterval(shutdownTimer);
                        this.syncAllUIs();
                        this.showToast(`Server "${server.name}" wurde gestoppt.`, 'warn');
                    }
                    
                    this.syncAllUIs();
                }, 1000);
            }

            executeRestartSequence(server) {
                this.showToast(`Server "${server.name}" wird neugestartet...`, 'warn');
                this.executeShutdownSequence(server);
                
                // Queue startup after shutdown completes
                const restartInterval = setInterval(() => {
                    if (server.status === 'offline') {
                        clearInterval(restartInterval);
                        this.executeStartupSequence(server);
                    }
                }, 1000);
            }

            syncAllUIs() {
                this.renderServerGrid();
                this.renderGlobalStats();
                
                if (this.activeServerId) {
                    const server = this.getServer(this.activeServerId);
                    if (server) {
                        this.updateDashboardHeaderAndControls(server);
                        this.updateResourceMeters(server);
                        this.renderConsoleLogs(server);
                    }
                }
            }

            updateDashboardHeaderAndControls(server) {
                // Background indicators and texts
                const badge = document.getElementById('dash-server-badge');
                const badgeText = badge.querySelector('.status-text');
                const headerText = document.getElementById('dash-header-status-text');
                const sidebarIcon = document.getElementById('dash-server-icon');

                // Reset statuses
                badge.className = `status-badge ${server.status}`;
                badgeText.innerText = server.status.toUpperCase();
                headerText.innerText = `Steuerung: ${server.name}`;
                
                // Enable/disable buttons based on status
                const btnStart = document.getElementById('dash-btn-start');
                const btnStop = document.getElementById('dash-btn-stop');
                const btnRestart = document.getElementById('dash-btn-restart');

                sidebarIcon.innerHTML = this.getSoftwareSVG(server.software, server.status === 'running');

                if (server.status === 'running') {
                    btnStart.disabled = true;
                    btnStop.disabled = false;
                    btnRestart.disabled = false;
                } else if (server.status === 'offline') {
                    btnStart.disabled = false;
                    btnStop.disabled = true;
                    btnRestart.disabled = true;
                } else {
                    // stopping or starting
                    btnStart.disabled = true;
                    btnStop.disabled = true;
                    btnRestart.disabled = true;
                }

                // Verbindungs-Info aktualisieren
                this._renderConnectionInfo(server);
            }

            /* --- v1.0.5 PERFORMANCE-VERLAUF (Sub-Tab) --- */
            // Wir sammeln alle 4 s einen Datenpunkt und behalten max 1800
            // (= 2 h bei 4 s). Der Range-Selector entscheidet, wie viele
            // Punkte gerendert werden. Pro Server eigene History.
            _perfBucket(serverId) {
                if (!this._perf) this._perf = {};
                if (!this._perf[serverId]) this._perf[serverId] = { points: [] };
                return this._perf[serverId];
            }

            _recordPerfPoint(server) {
                if (!server) return;
                const bucket = this._perfBucket(server.id);
                bucket.points.push({
                    t: Date.now(),
                    cpu: Number(server.cpuUsed) || 0,
                    ramPct: Number(server.ramPct) || 0,
                    ramUsed: Number(server.ramUsed) || 0,
                    ramMax: Number(server.ramMax) || 0,
                    overloaded: !!server.overloaded,
                });
                // 2 h * 60/4 = 1800 Punkte hard cap
                if (bucket.points.length > 1800) bucket.points.shift();

                if (this.activeServerId === server.id && this._statusSub === 'perf') {
                    this.renderPerfChart();
                }
            }

            switchStatusSub(sub) {
                this._statusSub = sub;
                document.querySelectorAll('.status-sub-btn').forEach(b => b.classList.toggle('active', b.dataset.sub === sub));
                document.querySelectorAll('.status-sub-pane').forEach(p => p.classList.remove('active'));
                const target = document.getElementById(`status-sub-${sub}`);
                if (target) target.classList.add('active');
                if (sub === 'perf') this.renderPerfChart();
            }

            changePerfRange() {
                this.renderPerfChart();
            }

            renderPerfChart() {
                const cpuRow = document.getElementById('perf-cpu-bars');
                const ramRow = document.getElementById('perf-ram-bars');
                const cpuNow = document.getElementById('perf-cpu-now');
                const ramNow = document.getElementById('perf-ram-now');
                const empty = document.getElementById('perf-empty-hint');
                const rangeSel = document.getElementById('perf-range');
                if (!cpuRow || !ramRow || !rangeSel) return;

                if (!this.activeServerId) return;
                const server = this.getServer(this.activeServerId);
                const bucket = this._perfBucket(this.activeServerId);

                const minutes = parseInt(rangeSel.value, 10) || 5;
                const cutoff = Date.now() - minutes * 60_000;
                const points = bucket.points.filter(p => p.t >= cutoff);

                // Aktuell-Werte oben rechts
                if (server) {
                    cpuNow.innerText = `${(server.cpuUsed || 0).toFixed ? server.cpuUsed.toFixed(1) : server.cpuUsed}%`;
                    const ramMax = server.ramMax || 0;
                    const ramUsed = server.ramUsed || 0;
                    ramNow.innerText = `${ramUsed.toFixed(1)} / ${ramMax}.0 GB`;
                }

                if (!points.length) {
                    cpuRow.innerHTML = '';
                    ramRow.innerHTML = '';
                    empty.style.display = 'block';
                    return;
                }
                empty.style.display = 'none';

                // Wir samplen die Punkte auf max 60 Balken pro Chart, damit
                // auch 2-h-Verlaeufe sauber dargestellt werden.
                const target = 60;
                const stride = Math.max(1, Math.floor(points.length / target));
                const sampled = [];
                for (let i = 0; i < points.length; i += stride) {
                    const slice = points.slice(i, i + stride);
                    const avgCpu = slice.reduce((s, p) => s + p.cpu, 0) / slice.length;
                    const avgRam = slice.reduce((s, p) => s + p.ramPct, 0) / slice.length;
                    const overloaded = slice.some(p => p.overloaded);
                    sampled.push({ cpu: avgCpu, ram: avgRam, overloaded });
                }

                cpuRow.innerHTML = sampled.map(p => {
                    const h = Math.max(0, Math.min(100, p.cpu));
                    const cls = p.overloaded ? 'cpu overloaded' : 'cpu';
                    return `<div class="perf-bar ${cls}" style="height:${h}%" title="${p.cpu.toFixed(1)}%"></div>`;
                }).join('');

                ramRow.innerHTML = sampled.map(p => {
                    const h = Math.max(0, Math.min(100, p.ram));
                    return `<div class="perf-bar ram" style="height:${h}%" title="${p.ram.toFixed(1)}%"></div>`;
                }).join('');
            }

            /* --- (zurueck zu v1.0.4-Methoden) --- */

            _renderConnectionInfo(server) {
                const portBadge = document.getElementById('dash-server-port-badge');
                const localEl = document.getElementById('conn-local-address');
                const copyBtn = document.getElementById('conn-local-copy');
                if (!localEl) return;

                const host = window.location.hostname || 'localhost';
                const port = server.port || '?';
                const fullAddr = `${host}:${port}`;

                localEl.innerText = server.port ? fullAddr : 'Port noch nicht zugewiesen';
                if (portBadge) portBadge.innerText = `Port: ${server.port || '--'}`;

                if (copyBtn) {
                    copyBtn.onclick = (ev) => {
                        ev.stopPropagation();
                        if (!server.port) return;
                        navigator.clipboard.writeText(fullAddr).then(
                            () => this.showToast(`"${fullAddr}" kopiert.`, 'success'),
                            () => this.showToast('Kopieren fehlgeschlagen', 'error')
                        );
                    };
                }

                this._renderTunnelInfo(server);
            }

            _renderTunnelInfo(server) {
                const status = document.getElementById('conn-tunnel-status');
                const domainEl = document.getElementById('conn-tunnel-domain');
                const copyBtn = document.getElementById('conn-tunnel-copy');
                const startBtn = document.getElementById('btn-tunnel-start');
                const stopBtn = document.getElementById('btn-tunnel-stop');
                const wizard = document.getElementById('tunnel-wizard');
                const claimRow = document.getElementById('tunnel-claim-row');
                const claimLink = document.getElementById('tunnel-claim-link');
                const logsDetails = document.getElementById('tunnel-logs-details');
                const logsPre = document.getElementById('tunnel-logs-pre');
                const messageEl = document.getElementById('tunnel-message');
                if (!status) return;

                const t = server.tunnel || { status: 'not_started' };
                const s = (t.status || 'not_started').toLowerCase();
                const hasDomain = !!t.domain;

                status.innerText = s.replace('_', ' ');
                if (hasDomain) {
                    domainEl.innerText = t.domain;
                    copyBtn.style.display = 'inline-flex';
                    copyBtn.onclick = (ev) => {
                        ev.stopPropagation();
                        navigator.clipboard.writeText(t.domain).then(
                            () => this.showToast('Domain kopiert.', 'success'),
                            () => this.showToast('Kopieren fehlgeschlagen', 'error')
                        );
                    };
                } else {
                    copyBtn.style.display = 'none';
                    if (s === 'active') {
                        domainEl.innerText = 'Tunnel aktiv. Domain wird gleich angezeigt …';
                    } else if (s === 'agent_started') {
                        domainEl.innerText = 'Agent läuft – Domain wird erkannt …';
                    } else if (s === 'auth_required') {
                        domainEl.innerText = 'Token / Auth nötig.';
                    } else if (s === 'not_started') {
                        domainEl.innerText = 'Noch kein Tunnel aktiv.';
                    } else if (s === 'error') {
                        domainEl.innerText = 'Tunnel-Fehler.';
                    } else {
                        domainEl.innerText = `Tunnel Status: ${s}`;
                    }
                }

                // Wizard-Schritte
                if (wizard) {
                    const order = ['image_pull', 'agent_started', 'auth_required', 'active'];
                    const reachedIndex = {
                        'not_started': -1,
                        'image_pull':   0,
                        'agent_started':1,
                        'auth_required':2,
                        'active':       3,
                        'error':       -2,
                    }[s] ?? -1;
                    wizard.style.display = (s === 'not_started' && !t.container) ? 'none' : 'flex';
                    wizard.querySelectorAll('li').forEach((li) => {
                        const step = li.dataset.step;
                        const idx = order.indexOf(step);
                        li.classList.remove('done', 'active', 'warn', 'error');
                        if (s === 'error') {
                            if (idx === 0) li.classList.add('error');
                        } else if (idx <= reachedIndex - 1) {
                            li.classList.add('done');
                        } else if (idx === reachedIndex) {
                            li.classList.add(step === 'auth_required' ? 'warn' : 'active');
                        }
                    });
                }

                // Claim-Link
                if (claimRow && claimLink) {
                    if (t.claim_url) {
                        claimRow.style.display = 'block';
                        claimLink.href = t.claim_url;
                        claimLink.innerText = t.claim_url;
                    } else {
                        claimRow.style.display = 'none';
                    }
                }

                // Log-Auszug
                if (logsDetails && logsPre) {
                    if (t.logs_tail) {
                        logsDetails.style.display = 'block';
                        logsPre.textContent = t.logs_tail;
                    } else {
                        logsDetails.style.display = 'none';
                    }
                }

                // Fehlermeldung / Hinweis
                if (messageEl) {
                    if (t.message && (s === 'error' || s === 'auth_required' || s === 'agent_started')) {
                        messageEl.style.display = 'block';
                        messageEl.innerText = t.message;
                    } else {
                        messageEl.style.display = 'none';
                    }
                }

                // Buttons
                const sidecarLive = !!t.container && (s === 'agent_started' || s === 'auth_required' || s === 'active');
                startBtn.style.display = sidecarLive ? 'none' : 'inline-flex';
                stopBtn.style.display  = sidecarLive ? 'inline-flex' : 'none';
                if (startBtn._loading) {
                    startBtn.disabled = true;
                } else {
                    startBtn.disabled = false;
                }
            }

            async refreshTunnel(serverId) {
                const id = serverId || this.activeServerId;
                if (!id) return;
                const server = this.getServer(id);
                if (!server) return;
                try {
                    const data = await API.tunnelStatus(id);
                    server.tunnel = data;
                } catch (err) {
                    server.tunnel = { status: 'error', message: err.message };
                }
                if (this.activeServerId === id) this._renderTunnelInfo(server);
            }

            async startTunnel() {
                if (!this.activeServerId) return;
                const startBtn = document.getElementById('btn-tunnel-start');
                if (startBtn) { startBtn._loading = true; startBtn.disabled = true; startBtn.innerText = 'Starte ...'; }
                this.showToast('Starte playit-Tunnel ...', 'warn');
                try {
                    const secret = (window.prompt(
                        'Optional: playit.gg Secret-Key einfügen (oder leer lassen und Agent danach unter https://playit.gg/claim verknüpfen):',
                        ''
                    ) || '').trim();
                    const data = await API.tunnelStart(this.activeServerId, secret || null);
                    const server = this.getServer(this.activeServerId);
                    if (server) server.tunnel = data;
                    this._renderTunnelInfo(server);

                    if (data && data.ok === false) {
                        this.showToast('Tunnel: ' + (data.message || data.status || 'Fehler'), 'error');
                    } else if (data && data.status === 'auth_required') {
                        this.showToast('Tunnel braucht Auth – Claim-Link unten öffnen.', 'warn');
                    } else {
                        this.showToast('Tunnel gestartet. Domain wird gleich angezeigt.', 'success');
                    }
                } catch (err) {
                    this.showToast('Tunnel-Start fehlgeschlagen: ' + (err && err.message ? err.message : err), 'error');
                } finally {
                    if (startBtn) { startBtn._loading = false; startBtn.disabled = false; startBtn.innerText = 'Tunnel starten'; }
                }
            }

            async stopTunnel() {
                if (!this.activeServerId) return;
                if (!confirm('Tunnel wirklich stoppen? (Minecraft-Server bleibt unangetastet)')) return;
                try {
                    await API.tunnelStop(this.activeServerId);
                    const server = this.getServer(this.activeServerId);
                    if (server) server.tunnel = { status: 'not_started' };
                    this._renderTunnelInfo(server);
                    this.showToast('Tunnel gestoppt.', 'warn');
                } catch (err) {
                    this.showToast('Tunnel-Stop fehlgeschlagen: ' + err.message, 'error');
                }
            }

            /* --- v1.0.4 SPIELER --- */
            async loadPlayers(serverId) {
                const id = serverId || this.activeServerId;
                if (!id) return;
                const server = this.getServer(id);
                if (!server) return;
                try {
                    const data = await API.listPlayers(id);
                    server.onlinePlayers = data.players || [];
                    server.playersCurrent = data.count || 0;
                    server.playersMax = data.max || server.playersMax || 20;
                } catch (err) {
                    server.onlinePlayers = [];
                }
                if (this.activeServerId === id) this.renderPlayers(server);
            }

            renderPlayers(server) {
                const list = document.getElementById('players-list-container');
                const badge = document.getElementById('players-count-badge');
                if (!list) return;
                const players = server.onlinePlayers || [];

                if (badge) badge.innerText = `${players.length} / ${server.playersMax || 20}`;

                if (!players.length) {
                    list.innerHTML = '<div class="player-empty">Keine Spieler online.</div>';
                    return;
                }

                list.innerHTML = '';
                players.forEach((name) => {
                    const safe = this._escape(name);
                    const row = document.createElement('div');
                    row.className = 'player-row';
                    row.innerHTML = `
                        <div class="player-info">
                            <img class="player-avatar" alt="" loading="lazy"
                                 src="https://mc-heads.net/avatar/${encodeURIComponent(name)}/32"
                                 onerror="this.style.visibility='hidden'">
                            <span class="player-name">${safe}</span>
                        </div>
                        <button class="player-menu-btn" title="Aktionen" data-name="${safe}">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/></svg>
                        </button>
                        <div class="player-menu" data-menu-for="${safe}">
                            <button class="player-menu-item"        data-action="op"   data-name="${safe}">Adminrechte vergeben (OP)</button>
                            <button class="player-menu-item"        data-action="deop" data-name="${safe}">OP entziehen</button>
                            <button class="player-menu-item danger" data-action="kick" data-name="${safe}">Kicken</button>
                            <button class="player-menu-item danger" data-action="ban"  data-name="${safe}">Bannen</button>
                        </div>
                    `;
                    row.querySelector('.player-menu-btn').addEventListener('click', (e) => {
                        e.stopPropagation();
                        this._togglePlayerMenu(row);
                    });
                    row.querySelectorAll('.player-menu-item').forEach(btn => {
                        btn.addEventListener('click', (e) => {
                            e.stopPropagation();
                            this._closeAllPlayerMenus();
                            this.playerAction(btn.dataset.action, btn.dataset.name);
                        });
                    });
                    list.appendChild(row);
                });
            }

            _togglePlayerMenu(row) {
                const menu = row.querySelector('.player-menu');
                const open = menu.classList.contains('open');
                this._closeAllPlayerMenus();
                if (!open) menu.classList.add('open');
            }

            _closeAllPlayerMenus() {
                document.querySelectorAll('.player-menu.open').forEach(m => m.classList.remove('open'));
            }

            async playerAction(action, name) {
                if (!this.activeServerId || !name) return;
                const id = this.activeServerId;
                try {
                    if (action === 'op')   await API.playerOp(id, name);
                    if (action === 'deop') await API.playerDeop(id, name);
                    if (action === 'kick') await API.playerKick(id, name, 'Vom Admin gekickt');
                    if (action === 'ban')  await API.playerBan(id, name, 'Vom Admin gebannt');
                    this.showToast(`${action.toUpperCase()} an "${name}" ausgeführt.`, 'success');
                    this.loadPlayers(id);
                } catch (err) {
                    this.showToast(`${action} fehlgeschlagen: ` + err.message, 'error');
                }
            }

            /* --- v1.0.4 OPTIMIZER --- */
            async toggleOptimizer(enabled) {
                if (!this.activeServerId) return;
                try {
                    await API.setOptimizer(this.activeServerId, !!enabled);
                    const server = this.getServer(this.activeServerId);
                    if (server) server.optimizer = !!enabled;
                    this.showToast(`RAM-Optimierer: ${enabled ? 'aktiv' : 'deaktiviert'}.`, 'success');
                } catch (err) {
                    this.showToast('Optimierer-Update fehlgeschlagen: ' + err.message, 'error');
                }
            }

            /* --- v1.0.4 / v1.0.7 DATEI-MANAGER --- */
            async loadFilesIndex() {
                const id = this.activeServerId;
                if (!id) return;
                const list = document.getElementById('file-list-container');
                if (!list) return;
                list.innerHTML = '<div style="color:var(--text-muted); padding: 0.75rem; font-size: 0.85rem;">Lade Dateien...</div>';

                let data;
                try {
                    data = await API.listFiles(id);
                } catch (err) {
                    list.innerHTML = `<div style="color:var(--status-offline); padding: 0.75rem; font-size: 0.85rem;">${this._escape(err.message)}</div>`;
                    return;
                }

                list.innerHTML = '';
                this._fileMeta = {};
                (data.files || []).forEach(f => {
                    this._fileMeta[f.name] = f;
                    const btn = document.createElement('button');
                    btn.className = 'file-list-item';
                    btn.dataset.name = f.name;
                    let badge = '';
                    if (!f.exists) {
                        badge = f.optional
                            ? '<span class="badge-missing optional" title="Optional - existiert nicht">optional</span>'
                            : '<span class="badge-missing">leer</span>';
                    }
                    btn.innerHTML = `
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                        <span>${this._escape(f.name)}</span>
                        ${badge}
                    `;
                    btn.onclick = () => this.openFile(f.name);
                    list.appendChild(btn);
                });
            }

            async openFile(name) {
                const id = this.activeServerId;
                if (!id) return;
                document.querySelectorAll('#file-list-container .file-list-item').forEach(b => {
                    b.classList.toggle('active', b.dataset.name === name);
                });
                const ta = document.getElementById('file-editor-textarea');
                const nameEl = document.getElementById('file-editor-name');
                const pathEl = document.getElementById('file-editor-path');
                const saveBtn = document.getElementById('btn-file-save');
                const hint = document.getElementById('file-editor-hint');

                ta.value = '// Lade ...';
                nameEl.innerText = name;
                pathEl.innerText = 'Lade ...';
                saveBtn.disabled = true;
                if (hint) hint.style.display = 'none';
                this._activeFile = name;

                const meta = (this._fileMeta || {})[name] || {};
                const isOptional = !!meta.optional;

                try {
                    const data = await API.readFile(id, name);
                    ta.value = data.content || '';
                    pathEl.innerText = data.path || '';
                    saveBtn.disabled = false;

                    // v1.0.7: optionale Datei existiert noch nicht -> Banner statt Toast.
                    if (data && data.exists === false && data.optional && hint) {
                        hint.style.display = 'block';
                        hint.innerText = data.hint || 'Datei existiert noch nicht. Beim Speichern wird sie erstellt.';
                    }
                } catch (err) {
                    // Wenn der Server doch hart 404 wirft (z.B. Pflicht-Datei),
                    // bei Optionalen *kein* roter Toast - nur leerer Editor + Hinweis.
                    if (isOptional) {
                        ta.value = '';
                        pathEl.innerText = meta.path || '';
                        saveBtn.disabled = false;
                        if (hint) {
                            hint.style.display = 'block';
                            hint.innerText = 'Datei existiert noch nicht. Beim Speichern wird sie erstellt.';
                        }
                    } else {
                        ta.value = '';
                        pathEl.innerText = 'Fehler';
                        this.showToast('Datei konnte nicht geladen werden: ' + err.message, 'error');
                    }
                }
            }

            async saveActiveFile() {
                if (!this.activeServerId || !this._activeFile) return;
                const ta = document.getElementById('file-editor-textarea');
                const saveBtn = document.getElementById('btn-file-save');
                const hint = document.getElementById('file-editor-hint');
                saveBtn.disabled = true;
                try {
                    await API.writeFile(this.activeServerId, this._activeFile, ta.value);
                    this.showToast(`"${this._activeFile}" gespeichert. Neustart empfohlen.`, 'success');
                    if (hint) hint.style.display = 'none';
                    this.loadFilesIndex();   // 'optional'/'leer'-Badge ggf. entfernen
                } catch (err) {
                    this.showToast('Speichern fehlgeschlagen: ' + err.message, 'error');
                } finally {
                    saveBtn.disabled = false;
                }
            }

            updateResourceMeters(server) {
                const isRunning = server.status === 'running';

                // CPU
                const cpuVal = isRunning ? server.cpuUsed : 0;
                document.getElementById('dash-gauge-cpu').innerText = `${cpuVal}%`;
                const cpuBar = document.getElementById('dash-gauge-cpu-bar');
                if (cpuBar) {
                    cpuBar.style.width = `${Math.max(0, Math.min(100, cpuVal))}%`;
                    cpuBar.classList.toggle('overloaded', !!server.overloaded);
                }

                // RAM
                const ramMax = server.ramMax;
                const ramUsed = isRunning ? server.ramUsed.toFixed(1) : '0.0';
                document.getElementById('dash-gauge-ram').innerText = `${ramUsed} / ${ramMax}.0 GB`;
                const ramBar = document.getElementById('dash-gauge-ram-bar');
                if (ramBar) {
                    const ramPct = isRunning ? (server.ramPct || (server.ramUsed / Math.max(ramMax, 1) * 100)) : 0;
                    ramBar.style.width = `${Math.max(0, Math.min(100, ramPct))}%`;
                    ramBar.classList.toggle('overloaded', ramPct >= 90);
                }

                // Players
                const playMax = server.playersMax;
                const playCur = isRunning ? server.playersCurrent : 0;
                document.getElementById('dash-gauge-players').innerText = `${playCur} / ${playMax}`;

                // Overload-Banner
                const banner = document.getElementById('overload-banner');
                if (banner) banner.classList.toggle('active', !!server.overloaded && isRunning);

                // Optimizer-Toggle nachziehen, falls man im Software-Tab ist
                const opt = document.getElementById('toggle-optimizer');
                if (opt) opt.checked = !!server.optimizer;
            }


            /* --- LIVE METRIC POLLER (Backend) --- */
            startResourceSimulation() {
                // Periodisches Refresh statt Mock-Simulation.
                this._tick = 0;
                this.simulationInterval = setInterval(async () => {
                    this._tick++;
                    if (this.activeServerId) {
                        // Detail-Refresh + Logs fuer aktiven Server
                        const updated = await this.refreshServer(this.activeServerId);
                        if (updated) {
                            this.updateDashboardHeaderAndControls(updated);
                            this.updateResourceMeters(updated);
                            this._recordPerfPoint(updated);
                        }
                        if (this.activeTab === 'status') {
                            this.refreshLogs(this.activeServerId);
                            this.loadPlayers(this.activeServerId);
                            // Tunnel-Status seltener pollen (jede 2. Runde ~8s); der
                            // Verbindungs-Block ist im Status-Tab immer sichtbar.
                            if (this._tick % 2 === 0) {
                                this.refreshTunnel(this.activeServerId);
                            }
                        }
                    } else {
                        await this.refreshServers();
                        this.renderServerGrid();
                        this.renderGlobalStats();
                        // Auch im Overview Performance sammeln (alle Server)
                        servers.forEach(s => this._recordPerfPoint(s));
                    }
                }, 4000);
            }

            /* --- CONSOLE TERMINAL ENGINE --- */
            addLog(server, level, message) {
                const time = new Date().toLocaleTimeString();
                let entry = `[${time} ${level}]: ${message}`;
                server.logs.push(entry);
                
                // Cap log arrays to preserve memory mock
                if (server.logs.length > 100) {
                    server.logs.shift();
                }
            }

            renderConsoleLogs(server) {
                const pane = document.getElementById('console-logs-pane');
                if (!pane) return;

                // v1.0.6: Wenn der Nutzer gerade Text im Terminal markiert hat,
                // ueberspringen wir den Repaint, damit die Auswahl nicht verloren geht.
                try {
                    const sel = window.getSelection && window.getSelection();
                    if (sel && sel.rangeCount && !sel.isCollapsed && sel.anchorNode && pane.contains(sel.anchorNode)) {
                        return;
                    }
                } catch (_) { /* noop */ }

                pane.innerHTML = '';

                server.logs.forEach(log => {
                    const line = document.createElement('div');
                    line.className = 'log-entry';

                    // SICHERHEIT: Log-Zeilen kommen roh aus dem Container (inkl.
                    // Spieler-Chat) -> erst HTML-escapen, DANN nur den Zeitstempel
                    // und das Level mit <span> einfaerben. Kein Roh-HTML mehr.
                    const safe = escapeHtml(log);
                    const withTime = safe.replace(/(\[\d{2}:\d{2}:\d{2}\])/, '<span class="log-time">$1</span>');

                    if (log.includes('INFO')) {
                        line.innerHTML = withTime.replace(/(INFO):/, '<span class="log-info">$1:</span>');
                    } else if (log.includes('WARN')) {
                        line.innerHTML = withTime.replace(/(WARN):/, '<span class="log-warn">$1:</span>');
                    } else if (log.includes('ERROR')) {
                        line.innerHTML = withTime.replace(/(ERROR):/, '<span class="log-error">$1:</span>');
                    } else if (log.includes('COMMAND')) {
                        line.innerHTML = withTime.replace(/(COMMAND):/, '<span class="log-command">$1:</span>');
                    } else if (log.includes('SYSTEM')) {
                        line.innerHTML = withTime.replace(/(SYSTEM):/, '<span class="log-system">$1:</span>');
                    } else {
                        line.textContent = log;
                    }
                    pane.appendChild(line);
                });

                // Auto-scroll to bottom of console logs
                pane.scrollTop = pane.scrollHeight;
            }

            async submitConsoleCommand() {
                const input = document.getElementById('console-cmd-input');
                const command = input.value.trim();
                if (!command) return;

                const server = this.getServer(this.activeServerId);
                if (!server) return;

                input.value = '';
                if (server.status !== 'running') {
                    this.showToast('Server laeuft nicht.', 'warn');
                    return;
                }

                // Befehl an Backend (rcon-cli)
                try {
                    const result = await API.command(server.id, command.replace(/^\//, ''));
                    if (result && result.output) {
                        // Output direkt anhaengen, danach Logs nachladen
                        server.logs.push(`> ${command}`);
                        result.output.split('\n').forEach(line => {
                            if (line.trim()) server.logs.push(line);
                        });
                        this.renderConsoleLogs(server);
                    }
                    setTimeout(() => this.refreshLogs(server.id), 600);
                } catch (err) {
                    this.showToast('Befehl fehlgeschlagen: ' + err.message, 'error');
                }
            }

            executeSimulatedCommand(server, commandString) {
                const command = commandString.toLowerCase();
                
                if (server.status !== 'running' && !command.startsWith('/start')) {
                    this.addLog(server, 'WARN', 'Befehl konnte nicht gesendet werden. Server ist offline.');
                    this.renderConsoleLogs(server);
                    return;
                }

                if (command.startsWith('/help')) {
                    this.addLog(server, 'INFO', '--- Verfügbare CraftControl Simulator Befehle ---');
                    this.addLog(server, 'INFO', '/help - Listet alle Befehle auf.');
                    this.addLog(server, 'INFO', '/op [Spielername] - Erhebt einen Spieler zum Operator.');
                    this.addLog(server, 'INFO', '/say [Text] - Sendet eine Broadcast-Nachricht.');
                    this.addLog(server, 'INFO', '/plugins - Listet alle installierten Erweiterungen auf.');
                    this.addLog(server, 'INFO', '/stop - Stoppt den Server.');
                    this.addLog(server, 'INFO', '/tps - Zeigt die simulierten Server-Ticks pro Sekunde.');
                } else if (command.startsWith('/op ')) {
                    const player = commandString.substring(4).trim();
                    this.addLog(server, 'INFO', `Made ${player} a server operator`);
                    this.showToast(`Spieler "${player}" wurde OP-Rechte zugewiesen!`, 'success');
                } else if (command.startsWith('/say ')) {
                    const msg = commandString.substring(5).trim();
                    this.addLog(server, 'INFO', `[Server] Broadcast: ${msg}`);
                } else if (command.startsWith('/plugins')) {
                    const list = server.installedExtensions.join(', ');
                    this.addLog(server, 'INFO', `Installierte Erweiterungen (${server.installedExtensions.length}): ${list || 'Keine'}`);
                } else if (command.startsWith('/stop')) {
                    this.executeShutdownSequence(server);
                } else if (command.startsWith('/tps')) {
                    const randTps = (19.8 + Math.random() * 0.2).toFixed(2);
                    this.addLog(server, 'INFO', `TPS: ${randTps} (100% stable, Allocation RAM: ${server.ramMax}GB)`);
                } else {
                    this.addLog(server, 'INFO', `Befehl "${commandString}" wurde an Konsole gesendet. (Keine Simulation hinterlegt, aber registriert!)`);
                }
                this.renderConsoleLogs(server);
            }

            clearConsoleLogs() {
                const server = this.getServer(this.activeServerId);
                if (server) {
                    server.logs = [`[${new Date().toLocaleTimeString()} SYSTEM]: Konsole geleert.`];
                    this.renderConsoleLogs(server);
                }
            }

            /* --- v1.0.6: Terminal kopieren / herunterladen ---------------- */
            // Liest den sichtbaren Terminal-Inhalt als reinen Text (kein HTML).
            // Bevorzugt das im Server gehaltene logs-Array, faellt auf das
            // DOM zurueck, falls das Frontend gerade keinen Server geladen hat.
            getTerminalText() {
                const server = this.getServer(this.activeServerId);
                if (server && Array.isArray(server.logs) && server.logs.length) {
                    return server.logs.join('\n');
                }
                const pane = document.getElementById('console-logs-pane');
                if (!pane) return '';
                // textContent statt innerHTML -> nie HTML in der Ausgabe
                const lines = Array.from(pane.children).map(el => el.textContent.replace(/\s+$/g, ''));
                return lines.join('\n');
            }

            // Kopiert die aktuelle Auswahl (wenn vorhanden) oder den ganzen Terminal-Text.
            async copyTerminalText() {
                const sel = window.getSelection ? window.getSelection().toString() : '';
                const pane = document.getElementById('console-logs-pane');
                let text = '';
                if (sel && pane && pane.contains(window.getSelection().anchorNode)) {
                    text = sel;
                } else {
                    text = this.getTerminalText();
                }

                if (!text) {
                    this.showToast('Kein Terminal-Inhalt zum Kopieren.', 'warn');
                    return;
                }

                try {
                    if (navigator.clipboard && navigator.clipboard.writeText) {
                        await navigator.clipboard.writeText(text);
                    } else {
                        // Fallback: unsichtbare textarea + execCommand('copy')
                        const ta = document.createElement('textarea');
                        ta.value = text;
                        ta.setAttribute('readonly', '');
                        ta.style.position = 'fixed';
                        ta.style.opacity = '0';
                        ta.style.left = '-9999px';
                        document.body.appendChild(ta);
                        ta.select();
                        const ok = document.execCommand && document.execCommand('copy');
                        document.body.removeChild(ta);
                        if (!ok) throw new Error('execCommand copy nicht erlaubt');
                    }
                    this.showToast('Terminal-Text kopiert', 'success');
                } catch (err) {
                    this.showToast('Kopieren fehlgeschlagen: ' + (err && err.message ? err.message : err), 'error');
                }
            }

            // Laedt den Terminal-Inhalt als .log-Datei herunter (rein clientseitig).
            downloadTerminalLog() {
                const text = this.getTerminalText();
                if (!text) {
                    this.showToast('Kein Terminal-Inhalt zum Speichern.', 'warn');
                    return;
                }

                const server = this.getServer(this.activeServerId);
                const safeName = server && server.name
                    ? server.name.toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '')
                    : '';
                const now = new Date();
                const pad = n => String(n).padStart(2, '0');
                const ts = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}_${pad(now.getHours())}-${pad(now.getMinutes())}-${pad(now.getSeconds())}`;
                const filename = safeName
                    ? `craftcontrol-${safeName}-terminal-${ts}.log`
                    : `craftcontrol-terminal-${ts}.log`;

                try {
                    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = filename;
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                    URL.revokeObjectURL(url);
                    this.showToast(`Log gespeichert: ${filename}`, 'success');
                } catch (err) {
                    this.showToast('Download fehlgeschlagen: ' + (err && err.message ? err.message : err), 'error');
                }
            }


            /* --- TAB 2: SOFTWARE CONFIGURATION PANEL --- */
            renderSoftwareCards(activeSoftware) {
                const container = document.getElementById('software-selector-cards');
                container.innerHTML = '';

                INITIAL_SOFTWARE.forEach(sw => {
                    const card = document.createElement('div');
                    card.className = `software-card ${sw.id === activeSoftware ? 'active' : ''}`;
                    card.setAttribute('onclick', `app.selectSoftware('${sw.id}')`);

                    card.innerHTML = `
                        ${this.getSoftwareSVG(sw.id, false)}
                        <h4>${sw.name}</h4>
                        <p>${sw.desc}</p>
                    `;
                    container.appendChild(card);
                });
            }

            selectSoftware(softwareId) {
                this.renderSoftwareCards(softwareId);
                this.showToast(`Engine "${softwareId}" ausgewählt. Drücke "Speichern" zum Übernehmen.`, 'warn');
            }

            saveSoftwareConfig() {
                const server = this.getServer(this.activeServerId);
                if (!server) return;

                // Find currently active card selection
                const activeCard = document.querySelector('.software-card.active h4');
                const selectedSoftware = activeCard ? activeCard.innerText : server.software;
                const selectedVersion = document.getElementById('select-mc-version').value;
                const selectedRam = parseInt(document.getElementById('input-allocated-ram').value);

                server.software = selectedSoftware;
                server.version = selectedVersion;
                server.ramMax = selectedRam;

                this.addLog(server, 'SYSTEM', `Softwarekonfiguration aktualisiert auf: ${selectedSoftware} ${selectedVersion} mit ${selectedRam}GB RAM.`);
                
                this.syncAllUIs();
                this.showToast('Konfiguration gespeichert! Starte den Server neu zum Übernehmen.', 'success');

                // If running, warn user or trigger automatic simulation reboot!
                if (server.status === 'running') {
                    this._powerAction(server.id, 'restart');
                }
            }


            /* --- TAB 3: PLUGINS & MODS WORKFLOW (Modrinth) --- */
            // Wird aus dem Tab-Wechsel und durch das Suchfeld aufgerufen.
            async renderPluginsCatalog(searchQuery = '', filterCategory = 'auto') {
                const container = document.getElementById('plugin-catalog-list');
                if (!container) return;

                const server = this.getServer(this.activeServerId);
                if (!server) return;

                // Vanilla unterstuetzt von Haus aus weder Plugins noch Mods.
                if ((server.software || '').toLowerCase() === 'vanilla') {
                    container.innerHTML = `<div style="color:var(--text-muted); text-align:center; padding: 2rem;">
                        Vanilla unterstuetzt keine Plugins oder Mods. Wechsle in den Software-Tab z.B. zu Paper, Forge oder Fabric.
                    </div>`;
                    return;
                }

                container.innerHTML = '<div style="color:var(--text-muted); text-align:center; padding: 1.5rem;">Suche auf modrinth.com ...</div>';

                let data;
                try {
                    data = await API.searchPlugins(server.id, searchQuery, filterCategory, 25);
                } catch (err) {
                    container.innerHTML = `<div style="color:var(--status-offline); text-align:center; padding: 1.5rem;">Modrinth-Suche fehlgeschlagen: ${this._escape(err.message)}</div>`;
                    return;
                }

                const installed = new Set((server.installedExtensions || []).map(x => (x.id || x).toLowerCase()));
                const results = data.results || [];
                container.innerHTML = '';

                if (results.length === 0) {
                    container.innerHTML = `<div style="color:var(--text-muted); text-align:center; padding: 2rem;">
                        Keine Treffer fuer "${this._escape(searchQuery || '')}" (${this._escape(data.kind || '')} fuer ${this._escape(data.version || 'beliebig')}).
                    </div>`;
                    return;
                }

                results.forEach(plugin => {
                    const candidate = (plugin.slug || '').toLowerCase();
                    const isInstalledHint = [...installed].some(name => name.includes(candidate));
                    const cls = (plugin.classification || 'unknown').toLowerCase();
                    const clsLabel = cls.toUpperCase();
                    const loaderPills = (plugin.loaders || [])
                        .map(l => `<span class="plugin-loader-pill">${this._escape(l)}</span>`)
                        .join('');
                    const targetLabel = plugin.target_dir
                        ? `Ziel: <code>${this._escape(plugin.target_dir)}</code>`
                        : (cls === 'hybrid' ? 'Ziel: bei Klick wählbar' : 'Ziel: unbekannt');
                    const clientWarn = plugin.server_side === 'unsupported'
                        ? '<div class="plugin-clientside-warn">⚠ Reines Client-Mod – auf dem Server nicht aktiv.</div>'
                        : '';

                    const installable = cls !== 'unknown';
                    const buttonLabel = !installable
                        ? 'Nicht installierbar'
                        : (isInstalledHint ? 'Erneut laden' : 'Installieren');

                    const item = document.createElement('div');
                    item.className = 'plugin-item';
                    item.innerHTML = `
                        <div class="plugin-info" style="display:flex; gap:0.85rem; align-items:flex-start;">
                            ${plugin.icon_url
                                ? `<img src="${this._escape(plugin.icon_url)}" alt="" loading="lazy" style="width:42px; height:42px; border-radius:8px; object-fit:cover; flex:0 0 42px; background:var(--bg-tertiary);">`
                                : ''}
                            <div style="flex:1; min-width:0;">
                                <h4 style="display:flex; align-items:center; gap:6px; flex-wrap:wrap;">
                                    ${this._escape(plugin.title || plugin.slug)}
                                    <span class="plugin-classification ${this._escape(cls)}">${this._escape(clsLabel)}</span>
                                </h4>
                                <p style="margin-top:2px;">${this._escape(plugin.description || '')}</p>
                                <div style="margin-top:6px;">${loaderPills}</div>
                                <div class="plugin-target-line">${targetLabel}</div>
                                ${clientWarn}
                                <div class="plugin-meta-row" style="margin-top:6px;">
                                    <span style="font-size:0.75rem; color: var(--text-muted);">
                                        ${plugin.downloads ? plugin.downloads.toLocaleString('de-DE') + ' Downloads' : ''}
                                    </span>
                                    <a href="${this._escape(plugin.url)}" target="_blank" rel="noopener"
                                       style="font-size:0.75rem; color: var(--accent-color); text-decoration:none;">
                                        modrinth.com
                                    </a>
                                </div>
                            </div>
                        </div>
                        <div>
                            <button class="btn btn-success" style="padding: 6px 12px; font-size: 0.8rem;"
                                    ${installable ? '' : 'disabled'}
                                    data-pid="${this._escape(plugin.project_id)}"
                                    data-name="${this._escape(plugin.title || plugin.slug)}"
                                    data-class="${this._escape(cls)}">
                                ${this._escape(buttonLabel)}
                            </button>
                        </div>
                    `;
                    const btn = item.querySelector('button');
                    if (installable) {
                        btn.addEventListener('click', () => this.installExtension(btn.dataset.pid, btn.dataset.name, btn, btn.dataset.class));
                    }
                    container.appendChild(item);
                });
            }

            filterPluginsCatalog() {
                const query = document.getElementById('search-plugin-input').value;
                const cat = document.getElementById('filter-plugin-category').value;
                clearTimeout(this._pluginSearchTimer);
                this._pluginSearchTimer = setTimeout(() => {
                    this.renderPluginsCatalog(query, cat);
                }, 350);
            }

            async installExtension(projectId, displayName, btn, classification) {
                const server = this.getServer(this.activeServerId);
                if (!server || !projectId) return;

                let target = null;  // 'plugins' | 'mods' (Override fuer Hybrid)
                if ((classification || '').toLowerCase() === 'hybrid') {
                    const ans = window.prompt(
                        'Hybrid-Erweiterung. Als "plugins" oder "mods" installieren?',
                        'plugins'
                    );
                    if (ans === null) return;
                    target = (ans || '').trim().toLowerCase();
                    if (!['plugins', 'mods'].includes(target)) {
                        this.showToast('Ungültige Auswahl. Bitte plugins oder mods.', 'warn');
                        return;
                    }
                }

                if (btn) {
                    btn.disabled = true;
                    btn.innerText = 'Lade...';
                }
                this.showToast(`Installiere ${displayName || projectId} ...`, 'warn');

                try {
                    const res = await API.installPlugin(server.id, projectId, null, target);
                    this.addLog(server, 'INFO', `[CraftControl] Installiert: ${res.filename} (${res.version_number || 'latest'}) -> ${res.directory}`);
                    this.showToast(`${res.filename} installiert (${res.directory}). Neustart empfohlen.`, 'success');
                    await this.loadInstalledExtensions(server);
                } catch (err) {
                    console.error(err);
                    this.showToast('Installation fehlgeschlagen: ' + err.message, 'error');
                } finally {
                    if (btn) {
                        btn.disabled = false;
                        btn.innerText = 'Erneut laden';
                    }
                }
            }

            async uninstallExtension(filename) {
                const server = this.getServer(this.activeServerId);
                if (!server || !filename) return;

                if (!confirm(`"${filename}" wirklich entfernen?`)) return;

                try {
                    await API.deleteInstalledPlugin(server.id, filename);
                    this.addLog(server, 'INFO', `[CraftControl] Erweiterung entfernt: ${filename}`);
                    this.showToast(`${filename} wurde entfernt.`, 'warn');
                    await this.loadInstalledExtensions(server);
                } catch (err) {
                    this.showToast('Entfernen fehlgeschlagen: ' + err.message, 'error');
                }
            }

            // Holt die echten installierten .jar-Dateien aus dem Container
            async loadInstalledExtensions(server) {
                if (!server) return;
                try {
                    const data = await API.installedPlugins(server.id);
                    server.installedExtensions = (data.items || []).map(it => ({
                        id: it.name,
                        name: it.name,
                        size: it.size,
                    }));
                } catch (err) {
                    server.installedExtensions = [];
                    console.warn('installedPlugins failed:', err);
                }
                this.renderInstalledExtensionsList(server);
            }

            renderInstalledExtensionsList(server) {
                const panel = document.getElementById('installed-plugins-panel');
                if (!panel) return;
                panel.innerHTML = '';

                const items = server.installedExtensions || [];
                if (items.length === 0) {
                    panel.innerHTML = '<div style="color:var(--text-muted); font-size:0.85rem; text-align:center; padding:1rem 0;">Keine Erweiterungen installiert.</div>';
                    return;
                }

                items.forEach(ext => {
                    const filename = (ext && (ext.id || ext.name)) || ext;
                    const sizeKb = ext && ext.size ? ` (${(ext.size / 1024).toFixed(0)} KB)` : '';
                    const item = document.createElement('div');
                    item.className = 'installed-item';
                    item.innerHTML = `
                        <span title="${this._escape(filename)}">${this._escape(filename)}<span style="color:var(--text-muted); font-size:0.75rem;">${sizeKb}</span></span>
                        <button class="btn btn-danger btn-circle" style="width:24px; height:24px; font-size: 0.75rem;" title="Deinstallieren" data-file="${this._escape(filename)}">
                            &times;
                        </button>
                    `;
                    item.querySelector('button').addEventListener('click', (e) => {
                        e.stopPropagation();
                        this.uninstallExtension(e.currentTarget.dataset.file);
                    });
                    panel.appendChild(item);
                });
            }

            // Mini-Helper gegen XSS in den eingefuegten Modrinth-Strings
            _escape(s) {
                return String(s == null ? '' : s)
                    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
                    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
            }

            /* --- PLUGIN DRAG & DROP JAR FILE UPLOAD SYSTEM --- */
            setupDragAndDrop() {
                const dropzone = document.getElementById('plugin-upload-dropzone');
                const fileInput = document.getElementById('plugin-file-upload-input');

                dropzone.addEventListener('click', () => {
                    fileInput.click();
                });

                fileInput.addEventListener('change', (e) => {
                    if (e.target.files.length > 0) {
                        this.uploadJarFile(e.target.files[0]);
                        e.target.value = '';  // erlaubt erneutes Hochladen derselben Datei
                    }
                });

                dropzone.addEventListener('dragover', (e) => {
                    e.preventDefault();
                    dropzone.classList.add('dragover');
                });

                dropzone.addEventListener('dragleave', () => {
                    dropzone.classList.remove('dragover');
                });

                dropzone.addEventListener('drop', (e) => {
                    e.preventDefault();
                    dropzone.classList.remove('dragover');
                    if (e.dataTransfer.files.length > 0) {
                        this.uploadJarFile(e.dataTransfer.files[0]);
                    }
                });
            }

            /* v1.1.0: Echter .jar-Upload via XHR (mit Fortschrittsanzeige). */
            uploadJarFile(file) {
                const server = this.getServer(this.activeServerId);
                if (!server) return;

                if (!file.name.toLowerCase().endsWith('.jar')) {
                    this.showToast('Nur .jar-Dateien sind für Minecraft Server zulässig!', 'error');
                    return;
                }

                const filename = file.name;
                const progressBox = document.getElementById('plugin-upload-progress');
                const progressBar = document.getElementById('plugin-upload-progress-bar-fill');
                const filenameLabel = document.getElementById('plugin-upload-filename');
                const percentLabel = document.getElementById('plugin-upload-percent');

                filenameLabel.innerText = filename;
                progressBox.style.display = 'flex';
                progressBar.style.width = '0%';
                percentLabel.innerText = '0%';

                const form = new FormData();
                form.append('file', file, filename);

                const xhr = new XMLHttpRequest();
                xhr.open('POST', `${API._base}/api/servers/${server.id}/plugins/upload`);
                const auth = API.authHeaders();
                if (auth.Authorization) xhr.setRequestHeader('Authorization', auth.Authorization);

                xhr.upload.onprogress = (e) => {
                    if (e.lengthComputable) {
                        const pct = Math.round((e.loaded / e.total) * 100);
                        progressBar.style.width = `${pct}%`;
                        percentLabel.innerText = `${pct}%`;
                    }
                };

                xhr.onload = () => {
                    progressBox.style.display = 'none';
                    if (xhr.status >= 200 && xhr.status < 300) {
                        let res = {};
                        try { res = JSON.parse(xhr.responseText); } catch (_) { /* noop */ }
                        this.showToast(`"${filename}" hochgeladen (${res.directory || 'Ziel'}). Neustart empfohlen.`, 'success');
                        this.loadInstalledExtensions(server);
                    } else if (xhr.status === 401) {
                        this.requireLogin();
                    } else {
                        let msg = xhr.statusText;
                        try { msg = JSON.parse(xhr.responseText).detail || msg; } catch (_) { /* noop */ }
                        this.showToast('Upload fehlgeschlagen: ' + msg, 'error');
                    }
                };
                xhr.onerror = () => {
                    progressBox.style.display = 'none';
                    this.showToast('Upload fehlgeschlagen (Netzwerkfehler).', 'error');
                };
                xhr.send(form);
            }


            /* --- TAB 4: BACKUPS MANAGEMENT WORKFLOW (v1.1.0: echtes Backend) --- */
            async loadBackups(serverId) {
                const server = this.getServer(serverId);
                if (!server) return;
                try {
                    const data = await API.listBackups(serverId);
                    server.backups = (data && data.items) ? data.items : [];
                } catch (err) {
                    console.warn('listBackups failed:', err);
                    server.backups = server.backups || [];
                }
                if (this.activeServerId === serverId) this.renderBackupsTable(server);
            }

            renderBackupsTable(server) {
                const tbody = document.getElementById('backup-list-tbody');
                if (!tbody) return;
                tbody.innerHTML = '';

                const backups = server.backups || [];
                if (backups.length === 0) {
                    tbody.innerHTML = `
                        <tr>
                            <td colspan="4" style="text-align:center; color: var(--text-muted); font-size:0.9rem; padding: 2rem;">
                                Keine Backups vorhanden. Klicke oben rechts auf "Backup Erstellen".
                            </td>
                        </tr>
                    `;
                    return;
                }

                backups.forEach(backup => {
                    const name = this._escape(backup.name);
                    const row = document.createElement('tr');
                    row.innerHTML = `
                        <td>
                            <div class="backup-file-name">
                                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color:var(--accent-color);"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                                <span>${name}</span>
                            </div>
                        </td>
                        <td style="color:var(--text-muted); font-size:0.9rem;">${this._escape(backup.date)}</td>
                        <td style="color:var(--text-muted); font-size:0.9rem;">${this._escape(backup.size_human || '')}</td>
                        <td>
                            <div style="display:flex; gap: 8px;">
                                <button class="btn btn-secondary" style="padding: 6px 12px; font-size:0.75rem;" title="Einspielen" data-backup-restore="${name}">Restore</button>
                                <button class="btn btn-secondary btn-circle" style="width:30px; height:30px;" title="Herunterladen" data-backup-download="${name}">
                                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                                </button>
                                <button class="btn btn-danger btn-circle" style="width:30px; height:30px;" title="Löschen" data-backup-delete="${name}">
                                    &times;
                                </button>
                            </div>
                        </td>
                    `;
                    row.querySelector('[data-backup-restore]').addEventListener('click', () => this.restoreBackup(backup.name));
                    row.querySelector('[data-backup-download]').addEventListener('click', () => this.downloadBackup(backup.name));
                    row.querySelector('[data-backup-delete]').addEventListener('click', () => this.deleteBackup(backup.name));
                    tbody.appendChild(row);
                });
            }

            async createNewBackup() {
                const server = this.getServer(this.activeServerId);
                if (!server) return;

                const btn = document.getElementById('btn-create-backup');
                const progressBox = document.getElementById('backup-generation-progress');
                const progressFill = document.getElementById('backup-progress-fill');
                const percentLabel = document.getElementById('backup-progress-percent');

                // UI sperren + unbestimmten Fortschritt zeigen (Backend liefert kein %).
                btn.disabled = true;
                progressBox.style.display = 'flex';
                progressFill.style.width = '85%';
                percentLabel.innerText = 'läuft...';
                this.showToast('Backup wird erstellt – das kann je nach Welt-Größe etwas dauern.', 'warn');

                try {
                    await API.createBackup(server.id);
                    this.showToast('Backup wurde erfolgreich erstellt!', 'success');
                    await this.loadBackups(server.id);
                } catch (err) {
                    this.showToast('Backup fehlgeschlagen: ' + err.message, 'error');
                } finally {
                    progressFill.style.width = '100%';
                    setTimeout(() => {
                        progressBox.style.display = 'none';
                        progressFill.style.width = '0%';
                        btn.disabled = false;
                    }, 400);
                }
            }

            async restoreBackup(backupName) {
                const server = this.getServer(this.activeServerId);
                if (!server) return;

                const confirmRestore = confirm(`Möchtest du das Backup "${backupName}" einspielen? Der aktuelle Serverzustand wird überschrieben. Der Server wird dafür gestoppt und danach wieder gestartet.`);
                if (!confirmRestore) return;

                this.showToast('Backup-Wiederherstellung läuft...', 'warn');
                try {
                    await API.restoreBackup(server.id, backupName);
                    this.showToast('Backup wurde eingespielt.', 'success');
                    await this.refreshServer(server.id);
                } catch (err) {
                    this.showToast('Wiederherstellung fehlgeschlagen: ' + err.message, 'error');
                }
            }

            downloadBackup(backupName) {
                // Echter Download. Token muss per Query nicht uebergeben werden,
                // da der Browser den Authorization-Header bei <a> nicht setzen kann;
                // wir holen die Datei daher per fetch (mit Header) und triggern blob.
                this.showToast(`Download startet: ${backupName}`, 'success');
                const url = API.backupDownloadUrl(this.activeServerId, backupName);
                fetch(url, { headers: API.authHeaders() })
                    .then(res => {
                        if (!res.ok) throw new Error(res.statusText);
                        return res.blob();
                    })
                    .then(blob => {
                        const a = document.createElement('a');
                        a.href = URL.createObjectURL(blob);
                        a.download = backupName;
                        document.body.appendChild(a);
                        a.click();
                        URL.revokeObjectURL(a.href);
                        document.body.removeChild(a);
                    })
                    .catch(err => this.showToast('Download fehlgeschlagen: ' + err.message, 'error'));
            }

            async deleteBackup(backupName) {
                const server = this.getServer(this.activeServerId);
                if (!server) return;
                if (!confirm(`Backup "${backupName}" wirklich löschen?`)) return;
                try {
                    await API.deleteBackup(server.id, backupName);
                    this.showToast(`Backup "${backupName}" wurde gelöscht.`, 'warn');
                    await this.loadBackups(server.id);
                } catch (err) {
                    this.showToast('Löschen fehlgeschlagen: ' + err.message, 'error');
                }
            }


            /* --- E. GLOBAL HELPER UTILS & TOASTS --- */
            showToast(message, type = 'success') {
                const wrapper = document.getElementById('toast-wrapper');
                const toast = document.createElement('div');
                toast.className = `toast-msg ${type}`;
                
                let icon = '';
                if (type === 'success') icon = '✓';
                if (type === 'error') icon = '✗';
                if (type === 'warn') icon = '⚠';

                toast.innerText = `${icon} ${message}`;
                wrapper.appendChild(toast);

                // Auto destroy toast after 4s
                setTimeout(() => {
                    toast.style.animation = 'slideInToast 0.3s cubic-bezier(0.16, 1, 0.3, 1) reverse forwards';
                    setTimeout(() => {
                        toast.remove();
                    }, 300);
                }, 3500);
            }

            // Beautiful SVG selectors representing server software engines
            getSoftwareSVG(softwareId, isActive) {
                const activeColor = 'var(--accent-color)';
                const inactiveColor = 'var(--text-muted)';
                const color = isActive ? activeColor : inactiveColor;

                switch(softwareId) {
                    case 'Vanilla':
                        return `
                            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>
                                <polyline points="3.27 6.96 12 12.01 20.73 6.96"/>
                                <line x1="12" y1="22.08" x2="12" y2="12"/>
                            </svg>
                        `;
                    case 'Spigot':
                        return `
                            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>
                            </svg>
                        `;
                    case 'Paper':
                        return `
                            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                                <polyline points="14 2 14 8 20 8"/>
                                <line x1="16" y1="13" x2="8" y2="13"/>
                                <line x1="16" y1="17" x2="8" y2="17"/>
                                <polyline points="10 9 9 9 8 9"/>
                            </svg>
                        `;
                    case 'Forge':
                        return `
                            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <polygon points="6 2 18 2 18 6 12 11 18 16 18 20 6 20 6 16 12 11 6 6"/>
                            </svg>
                        `;
                    case 'Fabric':
                        return `
                            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
                                <path d="m9 12 2 2 4-4"/>
                            </svg>
                        `;
                    case 'Mohist':
                        return `
                            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <circle cx="12" cy="12" r="10"/>
                                <path d="M8 14s1.5 2 4 2 4-2 4-2"/>
                                <line x1="9" y1="9" x2="9.01" y2="9"/>
                                <line x1="15" y1="9" x2="15.01" y2="9"/>
                            </svg>
                        `;
                    default:
                        return `
                            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="${color}" stroke-width="2">
                                <circle cx="12" cy="12" r="10"/>
                            </svg>
                        `;
                }
            }
        }

        // Mount global App controller
        const app = new DashboardApp();
        window.app = app;  // explizit, damit z.B. der 401-Handler app.requireLogin findet
        window.onload = () => {
            app.init();
        };

