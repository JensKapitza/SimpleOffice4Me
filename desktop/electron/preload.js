'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('simpleOfficeDesktop', Object.freeze({
  desktop: true,
  platform: process.platform,
  screenStatus: () => ipcRenderer.invoke('screen:status'),
  screenAction: (action) => ipcRenderer.invoke('screen:action', String(action || ''))
}));
