const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('electronAPI', {
  getSetting: (key) => ipcRenderer.invoke('get-setting', key),
  saveSetting: (key, value) => ipcRenderer.invoke('save-setting', key, value),
  openExternal: (url) => ipcRenderer.invoke('open-external', url),
  isElectron: true,
  onDeepLink: (callback) => {
    ipcRenderer.on('deep-link', (_, url) => callback(url))
    // Return cleanup function
    return () => ipcRenderer.removeAllListeners('deep-link')
  },
})
