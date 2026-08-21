# HermesMenuBar

Menu bar app per macOS che mostra lo stato di Hermes e pilota il servizio launchd
del gateway.

```
🟢 Hermes
──────────────────────────────
Gateway: running (1h 34m)
PID 14112 · ai.hermes.gateway
──────────────────────────────
Restart gateway
Stop gateway
──────────────────────────────
Dashboard: running
Ferma dashboard
Apri nel browser :9119
──────────────────────────────
Log gateway
Log errori
──────────────────────────────
Esci
```

Gateway e dashboard hanno entrambi un toggle avvia/ferma. "Apri nel browser"
resta grigio finché il dashboard non risponde davvero sulla porta.

## Cosa pilota

Hermes gira come **due** processi distinti:

| | Cos'è | Ciclo di vita |
|---|---|---|
| **gateway** | il servizio che fa funzionare Telegram & co. | launchd agent `ai.hermes.gateway`, parte al login, `KeepAlive` |
| **dashboard** | la web UI su :9119 | processo foreground, muore col terminale |

L'app segue il gateway via `launchctl` e sonda il dashboard con un connect TCP.
Il PID viene riletto a ogni tick (3s): `KeepAlive` può riavviare il gateway in
qualsiasi momento, quindi non viene mai memorizzato.

## Installazione

```bash
./install.sh      # installa rumps se manca, registra l'agent, avvia l'app
./uninstall.sh    # rimuove tutto
```

Dopo `install.sh` l'app è già in esecuzione e riparte a ogni login: non serve
avviarla a mano.

## Comandi

### Menu bar app

```bash
L=dev.trapias.hermes-menubar

launchctl list $L                    # stato + PID
launchctl kickstart -k gui/$UID/$L   # riavvia (anche per ricaricare il codice)
launchctl bootout   gui/$UID/$L      # ferma
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/$L.plist   # riavvia
```

A mano, fuori da launchd — utile per vedere gli errori a schermo:

```bash
cd ~/Dev/Priv/HermesMenuBar
~/.hermes/hermes-agent/venv/bin/python hermes_menubar.py
```

### Gateway

```bash
hermes gateway status
hermes gateway start | stop | restart
hermes gateway install --force       # rigenera il plist (serve dopo un cambio di PATH)
tail -f ~/.hermes/logs/gateway.log
```

### Dashboard

```bash
hermes dashboard --no-open           # avvia (foreground, muore col terminale)
hermes dashboard --status            # elenca i web server attivi
hermes dashboard --stop              # ferma tutti i web server
```

L'app la lancia staccata (`start_new_session`), quindi avviata dal menu
sopravvive sia al terminale sia alla menu bar app stessa.

### Log

| Cosa | Dove |
|---|---|
| menu bar app | `~/Library/Logs/hermes-menubar.error.log` |
| dashboard avviato dal menu | `~/Library/Logs/hermes-dashboard.log` |
| gateway | `~/.hermes/logs/gateway.log` · `gateway.error.log` |

L'agent è `dev.trapias.hermes-menubar` — namespace separato di proposito:
Hermes fa sweep su `ai.hermes.gateway*` e `hermes update` tocca `ai.hermes.*`,
quindi un plist lì dentro rischierebbe di essere rimosso da un aggiornamento.

`LimitLoadToSessionType: Aqua` perché una menu bar richiede una sessione grafica:
l'app non viene caricata in sessioni SSH o di background.

## Note

- Le operazioni lente (`hermes gateway restart`, avvio del dashboard) girano su
  un thread separato: rumps vive sul main thread di Cocoa e si congelerebbe.
- Il plist del **gateway** è statico. Se cambi PATH (nuovo Node via nvm, ffmpeg
  via brew) rilancia `hermes gateway install` o il gateway resta col PATH vecchio.
- I log si aprono in Console.app, che li segue in tempo reale.

## Configurazione

Via variabili d'ambiente, lette all'avvio:

| Var | Default |
|---|---|
| `HERMES_HOME` | `~/.hermes` |
| `HERMES_DASHBOARD_PORT` | `9119` |
