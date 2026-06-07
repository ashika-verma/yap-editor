const { app, BrowserWindow, ipcMain, dialog, shell, Menu } = require('electron')
const { spawn } = require('child_process')
const path = require('path')
const http = require('http')
const fs = require('fs')

// Register yap:// deep link protocol BEFORE app is ready
if (process.defaultApp) {
  if (process.argv.length >= 2) {
    app.setAsDefaultProtocolClient('yap', process.execPath, [path.resolve(process.argv[1])])
  }
} else {
  app.setAsDefaultProtocolClient('yap')
}

// macOS: handle deep link when app is already running
app.on('open-url', (event, url) => {
  event.preventDefault()
  handleDeepLink(url)
})

function handleDeepLink(url) {
  if (mainWindow) {
    mainWindow.webContents.send('deep-link', url)
    mainWindow.focus()
  }
}

// electron-store uses ESM in v9+, so we lazy-import it
let store

const PORT = 3241
const isDev = process.env.NODE_ENV === 'development'

let mainWindow = null
let nextProcess = null

async function getStore() {
  if (!store) {
    const { default: Store } = await import('electron-store')
    store = new Store()
  }
  return store
}

function findFreePort(preferred) {
  return new Promise((resolve) => {
    const server = require('net').createServer()
    server.listen(preferred, () => {
      server.close(() => resolve(preferred))
    })
    server.on('error', () => resolve(preferred + 1))
  })
}

function startNextServer(port, env) {
  const cwd = app.getAppPath()
  const args = isDev
    ? ['node_modules/.bin/next', 'dev', '--port', port.toString()]
    : ['node_modules/.bin/next', 'start', '--port', port.toString()]

  const proc = spawn('node', args, {
    cwd,
    env: { ...process.env, ...env, PORT: port.toString() },
    stdio: 'pipe',
  })

  proc.stdout.on('data', (d) => process.stdout.write(`[next] ${d}`))
  proc.stderr.on('data', (d) => process.stderr.write(`[next] ${d}`))

  return proc
}

function waitForServer(url, timeout = 60000) {
  return new Promise((resolve, reject) => {
    const start = Date.now()
    const check = () => {
      http.get(url, (res) => {
        if (res.statusCode < 500) resolve()
        else retry()
      }).on('error', retry)
    }
    const retry = () => {
      if (Date.now() - start > timeout) return reject(new Error('Server timed out'))
      setTimeout(check, 500)
    }
    check()
  })
}

async function buildEnv(s) {
  // Only pass values that are actually set — let Next.js read .env.local for anything missing
  const env = {}
  const geminiKey = s.get('geminiApiKey', '')
  if (geminiKey) env.GEMINI_API_KEY = geminiKey
  return env
}

function createWindow(url) {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 900,
    minHeight: 600,
    titleBarStyle: 'hiddenInset',
    backgroundColor: '#080809',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
    },
  })

  mainWindow.loadURL(url)

  mainWindow.on('closed', () => {
    mainWindow = null
  })

  // Open external links in browser
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url)
    return { action: 'deny' }
  })

  setupMenu()
}

function setupMenu() {
  const template = [
    {
      label: 'Yap',
      submenu: [
        {
          label: 'Settings…',
          accelerator: 'CmdOrCtrl+,',
          click: () => openSettings(),
        },
        { type: 'separator' },
        { role: 'quit' },
      ],
    },
    {
      label: 'Edit',
      submenu: [
        { role: 'undo' }, { role: 'redo' }, { type: 'separator' },
        { role: 'cut' }, { role: 'copy' }, { role: 'paste' }, { role: 'selectAll' },
      ],
    },
    {
      label: 'View',
      submenu: [
        { role: 'reload' }, { role: 'forceReload' }, { role: 'toggleDevTools' },
        { type: 'separator' }, { role: 'resetZoom' }, { role: 'zoomIn' }, { role: 'zoomOut' },
        { type: 'separator' }, { role: 'togglefullscreen' },
      ],
    },
  ]
  Menu.setApplicationMenu(Menu.buildFromTemplate(template))
}

let settingsWindow = null

async function openSettings() {
  if (settingsWindow) { settingsWindow.focus(); return }

  const s = await getStore()

  settingsWindow = new BrowserWindow({
    width: 480,
    height: 400,
    resizable: false,
    titleBarStyle: 'hiddenInset',
    backgroundColor: '#080809',
    parent: mainWindow,
    modal: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
    },
  })

  settingsWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(settingsHTML(s))}`)
  settingsWindow.on('closed', () => { settingsWindow = null })
}

function settingsHTML(s) {
  const geminiKey = s.get('geminiApiKey', '')
  return `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #080809; color: #fafafa; padding: 32px; padding-top: 48px; }
  h2 { font-size: 16px; font-weight: 600; margin-bottom: 24px; }
  label { display: block; font-size: 12px; color: #888; margin-bottom: 6px; margin-top: 16px; }
  input { width: 100%; padding: 8px 12px; background: #1a1a1b; border: 1px solid #333; border-radius: 6px; color: #fafafa; font-size: 13px; outline: none; font-family: 'SF Mono', monospace; }
  input:focus { border-color: #6366f1; }
  .hint { font-size: 11px; color: #666; margin-top: 4px; }
  button { margin-top: 24px; width: 100%; padding: 10px; background: #6366f1; color: white; border: none; border-radius: 6px; font-size: 13px; font-weight: 600; cursor: pointer; }
  button:hover { background: #5558e8; }
  .saved { color: #22c55e; font-size: 12px; text-align: center; margin-top: 12px; display: none; }
</style>
</head>
<body>
<div style="display:flex;align-items:center;gap:10px;margin-bottom:24px">
  <div style="width:28px;height:28px;border-radius:7px;background:#6366f1;display:flex;align-items:center;justify-content:center;flex-shrink:0">
    <svg width="17" height="17" viewBox="0 0 52 52" fill="none">
      <path d="M7 25 L7 7 L25 7" stroke="white" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>
      <path d="M27 45 L45 45 L45 27" stroke="white" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>
  </div>
  <span style="font-size:16px;font-weight:700;letter-spacing:-0.5px">Settings</span>
</div>
<label>Gemini API Key</label>
<input type="password" id="key" value="${geminiKey}" placeholder="AIza..." />
<p class="hint">Get a free key at <a href="#" onclick="window.electronAPI.openExternal('https://aistudio.google.com/app/apikey')" style="color:#6366f1;">aistudio.google.com</a></p>
<button onclick="save()">Save</button>
<p class="saved" id="saved">Saved! Restart the app to apply.</p>
<script>
function save() {
  const key = document.getElementById('key').value.trim()
  window.electronAPI.saveSetting('geminiApiKey', key)
  document.getElementById('saved').style.display = 'block'
}
</script>
</body>
</html>`
}

function runSetup(cwd) {
  return new Promise((resolve, reject) => {
    const setupWindow = new BrowserWindow({
      width: 480,
      height: 320,
      frame: false,
      backgroundColor: '#080809',
      webPreferences: { contextIsolation: true },
    })

    setupWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(`
      <html><body style="background:#080809;color:#fafafa;font-family:-apple-system,sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;gap:16px;margin:0;padding:32px;text-align:center">
      <div style="font-size:24px;font-weight:700;letter-spacing:-1px">Yap</div>
      <div style="font-size:13px;color:#888">First-run setup — installing Python dependencies…</div>
      <div id="log" style="font-size:11px;color:#666;font-family:monospace;max-width:400px;word-break:break-all;margin-top:8px"></div>
      </body></html>
    `)}`)

    const python = spawn('python3', ['-m', 'venv', '.venv'], { cwd, stdio: 'pipe' })
    python.on('close', (code) => {
      if (code !== 0) { setupWindow.destroy(); return reject(new Error('venv creation failed')) }

      const pip = spawn('.venv/bin/pip', ['install', '-e', '.', '--quiet'], { cwd, stdio: 'pipe' })
      pip.stderr.on('data', (d) => {
        setupWindow.webContents.executeJavaScript(
          `document.getElementById('log').textContent = ${JSON.stringify(d.toString().slice(-80))}`
        ).catch(() => {})
      })
      pip.on('close', (c) => {
        setupWindow.destroy()
        if (c === 0) resolve()
        else reject(new Error('pip install failed'))
      })
    })
  })
}

// IPC handlers
ipcMain.handle('get-setting', async (_, key) => {
  const s = await getStore()
  return s.get(key)
})

ipcMain.handle('save-setting', async (_, key, value) => {
  const s = await getStore()
  s.set(key, value)
})

ipcMain.handle('open-external', async (_, url) => {
  shell.openExternal(url)
})

app.whenReady().then(async () => {
  // Set dock icon (macOS dev mode uses Electron's default otherwise)
  if (process.platform === 'darwin') {
    const iconPath = path.join(app.getAppPath(), 'public', 'icon.png')
    if (fs.existsSync(iconPath)) app.dock.setIcon(iconPath)
  }

  const s = await getStore()
  const cwd = app.getAppPath()

  // First-run: set up Python venv if missing
  const venvPython = path.join(cwd, '.venv', 'bin', 'python3')
  if (!fs.existsSync(venvPython)) {
    try {
      await runSetup(cwd)
    } catch (e) {
      dialog.showErrorBox('Setup failed', `Could not install Python dependencies:\n\n${e.message}\n\nMake sure Python 3 is installed (python3 --version).`)
      app.quit()
      return
    }
  }

  const env = await buildEnv(s)
  const port = await findFreePort(PORT)
  const url = `http://localhost:${port}`

  nextProcess = startNextServer(port, env)

  // Show a loading window while Next.js boots
  const splash = new BrowserWindow({
    width: 360,
    height: 240,
    frame: false,
    backgroundColor: '#080809',
    webPreferences: { contextIsolation: true },
  })
  splash.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(`
    <html>
    <head>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Syne:wght@800&display=swap" rel="stylesheet">
    <style>
      * { margin: 0; padding: 0; box-sizing: border-box; }
      body {
        background: #080809;
        font-family: -apple-system, BlinkMacSystemFont, sans-serif;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        height: 100vh;
        gap: 0;
        user-select: none;
        overflow: hidden;
      }

      /* Icon: spring bounce in */
      .icon {
        width: 80px;
        height: 80px;
        border-radius: 20px;
        background: #6366f1;
        display: flex;
        align-items: center;
        justify-content: center;
        opacity: 0;
        animation: springIn 0.55s cubic-bezier(0.175, 0.885, 0.32, 1.6) 0.05s forwards;
        box-shadow: 0 0 0 0 rgba(99,102,241,0.5);
      }
      @keyframes springIn {
        0%   { opacity: 0; transform: scale(0.4) rotate(-8deg); }
        60%  { opacity: 1; transform: scale(1.08) rotate(2deg); box-shadow: 0 0 0 12px rgba(99,102,241,0); }
        80%  { transform: scale(0.97) rotate(-0.5deg); }
        100% { opacity: 1; transform: scale(1) rotate(0deg); }
      }

      /* Bracket draw-on */
      .tl {
        stroke-dasharray: 46;
        stroke-dashoffset: 46;
        animation: draw 0.4s cubic-bezier(0.4, 0, 0.2, 1) 0.45s forwards;
      }
      .br {
        stroke-dasharray: 46;
        stroke-dashoffset: 46;
        animation: draw 0.4s cubic-bezier(0.4, 0, 0.2, 1) 0.65s forwards;
      }
      @keyframes draw { to { stroke-dashoffset: 0; } }

      /* Wordmark */
      .wordmark {
        font-family: 'Syne', -apple-system, sans-serif;
        font-weight: 800;
        font-size: 24px;
        letter-spacing: -0.04em;
        color: #fafafa;
        opacity: 0;
        margin-top: 14px;
        animation: fadeUp 0.35s ease-out 0.8s forwards;
      }
      @keyframes fadeUp {
        from { opacity: 0; transform: translateY(5px); }
        to   { opacity: 1; transform: translateY(0); }
      }

      /* Rotating fun messages */
      .msg-wrap {
        height: 14px;
        margin-top: 10px;
        position: relative;
        overflow: hidden;
        width: 220px;
        text-align: center;
      }
      .msg {
        font-size: 11px;
        color: #3a3a3c;
        letter-spacing: 0.04em;
        position: absolute;
        width: 100%;
        text-align: center;
        opacity: 0;
        transform: translateY(8px);
        transition: opacity 0.35s ease, transform 0.35s ease;
      }
      .msg.visible { opacity: 1; transform: translateY(0); }
      .msg.gone    { opacity: 0; transform: translateY(-8px); }

      /* Bouncing dots */
      .dots {
        display: flex;
        gap: 5px;
        margin-top: 18px;
        opacity: 0;
        animation: fadeUp 0.3s ease-out 1s forwards;
      }
      .dot {
        width: 5px;
        height: 5px;
        border-radius: 50%;
        background: #252527;
        animation: bounce 1.1s ease-in-out infinite;
      }
      .dot:nth-child(1) { animation-delay: 0s; }
      .dot:nth-child(2) { animation-delay: 0.18s; }
      .dot:nth-child(3) { animation-delay: 0.36s; }
      @keyframes bounce {
        0%, 60%, 100% { transform: translateY(0);   background: #252527; }
        30%           { transform: translateY(-7px); background: #6366f1; }
      }

      /* Sparkles */
      .spark {
        position: fixed;
        width: 5px;
        height: 5px;
        border-radius: 50%;
        background: #6366f1;
        opacity: 0;
        pointer-events: none;
      }
    </style>
    </head>
    <body>
      <div class="icon" id="icon">
        <svg width="50" height="50" viewBox="0 0 52 52" fill="none">
          <path class="tl" d="M8 27 L8 8 L27 8" stroke="white" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>
          <path class="br" d="M25 44 L44 44 L44 25" stroke="white" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </div>

      <div class="wordmark">yap</div>

      <div class="msg-wrap" id="msgWrap">
        <div class="msg" id="msg">warming up the timeline…</div>
      </div>

      <div class="dots">
        <div class="dot"></div>
        <div class="dot"></div>
        <div class="dot"></div>
      </div>

    <script>
      const lines = [
        'warming up the timeline…',
        'sharpening the scissors…',
        'teaching the AI to listen…',
        'loading brilliance…',
        'almost ready…',
      ]
      let idx = 0
      const wrap = document.getElementById('msgWrap')

      // Show first message after icon lands
      setTimeout(() => {
        const first = wrap.querySelector('.msg')
        first.classList.add('visible')
      }, 900)

      // Rotate messages every 2s
      setInterval(() => {
        const old = wrap.querySelector('.visible')
        if (old) { old.classList.remove('visible'); old.classList.add('gone'); setTimeout(() => old.remove(), 400) }
        idx = (idx + 1) % lines.length
        const el = document.createElement('div')
        el.className = 'msg'
        el.textContent = lines[idx]
        wrap.appendChild(el)
        requestAnimationFrame(() => requestAnimationFrame(() => el.classList.add('visible')))
      }, 2200)

      // Sparkle burst when icon pops in
      setTimeout(() => {
        const icon = document.getElementById('icon')
        const rect = icon.getBoundingClientRect()
        const cx = rect.left + rect.width/2
        const cy = rect.top + rect.height/2
        const colors = ['#6366f1','#818cf8','#a5b4fc','#c7d2fe']
        for (let i = 0; i < 10; i++) {
          const s = document.createElement('div')
          s.className = 'spark'
          const angle = (i / 10) * Math.PI * 2
          const dist = 55 + Math.random() * 25
          const tx = Math.cos(angle) * dist
          const ty = Math.sin(angle) * dist
          const size = 3 + Math.random() * 4
          s.style.cssText = 'left:'+cx+'px;top:'+cy+'px;width:'+size+'px;height:'+size+'px;background:'+colors[i%colors.length]+';transform:translate(-50%,-50%)'
          document.body.appendChild(s)
          s.animate([
            { opacity: 1, transform: 'translate(-50%,-50%) translate(0,0) scale(1)' },
            { opacity: 0, transform: 'translate(-50%,-50%) translate('+tx+'px,'+ty+'px) scale(0)' }
          ], { duration: 600, delay: i*30, easing: 'cubic-bezier(0,0,0.2,1)', fill: 'forwards' })
        }
      }, 420)
    </script>
    </body>
    </html>
  `)}`)

  try {
    await waitForServer(url)
    createWindow(url)
    splash.destroy()
  } catch (e) {
    splash.destroy()
    dialog.showErrorBox('Startup failed', 'Could not start the Yap server. Make sure Node.js is installed.')
    app.quit()
  }
})

app.on('window-all-closed', () => {
  if (nextProcess) nextProcess.kill()
  app.quit()
})

app.on('before-quit', () => {
  if (nextProcess) nextProcess.kill()
})
