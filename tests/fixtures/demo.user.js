// ==UserScript==
// @name         Demo Userscript
// @namespace    pythonbrowser.tests
// @version      1.2
// @description  Marks pages and counts runs with GM storage
// @match        *://*/*
// @match        file:///*
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_addStyle
// @grant        GM_xmlhttpRequest
// @run-at       document-end
// ==/UserScript==

const runs = GM_getValue('runs', 0) + 1;
GM_setValue('runs', runs);
GM_addStyle('html { border-top: 4px solid rgb(0, 160, 255) !important; }');
document.documentElement.dataset.demoUserscript = String(runs);
