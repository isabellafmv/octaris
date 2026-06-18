import { app, shell, BrowserWindow, ipcMain, dialog } from 'electron'
import { join } from 'path'
import { electronApp, optimizer, is } from '@electron-toolkit/utils'
import { autoUpdater } from 'electron-updater'
import { spawn, ChildProcess } from 'child_process'
import icon from '../../resources/icon.png?asset'

// ---------------------------------------------------------------------------
// Backend process management
// ---------------------------------------------------------------------------

const BACKEND_PORT = 8000
const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`
let backendProcess: ChildProcess | null = null

function getBackendPath(): string {
  // In production the backend binary is bundled as an extraResource
  const resourcesPath = process.resourcesPath
  return join(resourcesPath, 'backend', 'octaris-backend', 'octaris-backend')
}

function startBackend(): void {
  if (is.dev) {
    // In development, assume the backend is started manually
    console.log('[main] Dev mode — skipping backend spawn (start it manually)')
    return
  }

  const backendPath = getBackendPath()
  console.log(`[main] Starting backend: ${backendPath}`)

  backendProcess = spawn(backendPath, [], {
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' }
  })

  backendProcess.stdout?.on('data', (data) => {
    console.log(`[backend] ${data.toString().trimEnd()}`)
  })

  backendProcess.stderr?.on('data', (data) => {
    console.error(`[backend] ${data.toString().trimEnd()}`)
  })

  backendProcess.on('exit', (code, signal) => {
    console.log(`[main] Backend exited: code=${code} signal=${signal}`)
    backendProcess = null
  })
}

function stopBackend(): void {
  if (!backendProcess) return
  console.log('[main] Stopping backend...')
  backendProcess.kill('SIGTERM')

  // Force kill after 3 seconds if it doesn't exit gracefully
  const forceKillTimeout = setTimeout(() => {
    if (backendProcess) {
      console.log('[main] Force killing backend')
      backendProcess.kill('SIGKILL')
    }
  }, 3000)

  backendProcess.on('exit', () => {
    clearTimeout(forceKillTimeout)
  })
}

async function waitForBackend(timeoutMs = 15000): Promise<boolean> {
  const start = Date.now()
  while (Date.now() - start < timeoutMs) {
    try {
      const response = await fetch(`${BACKEND_URL}/`)
      if (response.ok) {
        console.log('[main] Backend is ready')
        return true
      }
    } catch {
      // Backend not ready yet
    }
    await new Promise((resolve) => setTimeout(resolve, 300))
  }
  console.error('[main] Backend failed to start within timeout')
  return false
}

// ---------------------------------------------------------------------------
// Auto-updater
// ---------------------------------------------------------------------------

function setupAutoUpdater(): void {
  autoUpdater.autoDownload = true
  autoUpdater.autoInstallOnAppQuit = true

  autoUpdater.on('update-available', (info) => {
    console.log(`[updater] Update available: ${info.version}`)
  })

  autoUpdater.on('update-downloaded', (info) => {
    // Prompt user to restart
    const mainWindow = BrowserWindow.getAllWindows()[0]
    if (mainWindow) {
      dialog
        .showMessageBox(mainWindow, {
          type: 'info',
          title: 'Update Ready',
          message: `Octaris ${info.version} has been downloaded. Restart to apply the update.`,
          buttons: ['Restart Now', 'Later']
        })
        .then(({ response }) => {
          if (response === 0) {
            autoUpdater.quitAndInstall()
          }
        })
    }
  })

  autoUpdater.on('error', (err) => {
    console.error('[updater] Error:', err.message)
  })

  // Check for updates (silently — no error dialogs)
  autoUpdater.checkForUpdates().catch((err) => {
    console.log('[updater] Update check failed (offline?):', err.message)
  })
}

// ---------------------------------------------------------------------------
// Window
// ---------------------------------------------------------------------------

function createWindow(): void {
  const mainWindow = new BrowserWindow({
    width: 900,
    height: 670,
    show: false,
    autoHideMenuBar: true,
    ...(process.platform === 'linux' ? { icon } : {}),
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false,
      webSecurity: false
    }
  })

  mainWindow.on('ready-to-show', () => {
    mainWindow.show()
  })

  mainWindow.webContents.setWindowOpenHandler((details) => {
    shell.openExternal(details.url)
    return { action: 'deny' }
  })

  if (is.dev && process.env['ELECTRON_RENDERER_URL']) {
    mainWindow.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

// ---------------------------------------------------------------------------
// App lifecycle
// ---------------------------------------------------------------------------

app.whenReady().then(async () => {
  electronApp.setAppUserModelId('com.octaris.bioprinter')

  app.on('browser-window-created', (_, window) => {
    optimizer.watchWindowShortcuts(window)
  })

  ipcMain.on('ping', () => console.log('pong'))

  // Start the backend
  startBackend()

  // Wait for backend before showing the window
  if (!is.dev) {
    const ready = await waitForBackend()
    if (!ready) {
      dialog.showErrorBox(
        'Octaris Backend Error',
        'The backend failed to start. Please check the logs and try again.'
      )
      app.quit()
      return
    }
  }

  createWindow()

  // Check for updates (production only)
  if (!is.dev) {
    setupAutoUpdater()
  }

  app.on('activate', function () {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

app.on('will-quit', () => {
  stopBackend()
})
