'use strict';

const { contextBridge } = require('electron');

contextBridge.exposeInMainWorld('simpleOfficeDesktop', Object.freeze({
  desktop: true,
  platform: process.platform
}));
