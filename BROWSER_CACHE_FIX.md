# Browser Cache Issue - How to Fix

## Problem
The mode selector buttons are in the HTML and CSS, but your browser is showing a cached (old) version of the page.

## Verification Complete ✓
Server is serving the correct updated files:
- ✓ HTML contains mode-selector div
- ✓ HTML contains mode-analysis-btn button
- ✓ HTML contains mode-planner-btn button  
- ✓ CSS contains .mode-selector and .mode-btn styles
- ✓ planner.js loads and contains button handlers

## Solution: Clear Browser Cache

### Option 1: Hard Refresh (Quickest)
**Windows:**
- Press `Ctrl + F5` or `Ctrl + Shift + R`

**Mac:**
- Press `Cmd + Shift + R`

This forces the browser to reload all files from the server, bypassing cache.

### Option 2: Incognito/Private Window
Open a new incognito/private browsing window:
- **Chrome:** Ctrl+Shift+N (Windows) or Cmd+Shift+N (Mac)
- **Firefox:** Ctrl+Shift+P (Windows) or Cmd+Shift+P (Mac)
- **Edge:** Ctrl+Shift+N

Then navigate to: http://localhost:5000

### Option 3: Clear Browser Cache Completely

**Chrome:**
1. Press F12 to open DevTools
2. Right-click the refresh button
3. Select "Empty Cache and Hard Reload"

OR

1. Settings → Privacy and Security → Clear browsing data
2. Select "Cached images and files"
3. Click "Clear data"

**Firefox:**
1. Settings → Privacy & Security
2. Cookies and Site Data → Clear Data
3. Select only "Cached Web Content"
4. Click "Clear"

**Edge:**
1. Settings → Privacy, search, and services
2. Clear browsing data → Choose what to clear
3. Select "Cached images and files"
4. Click "Clear now"

### Option 4: Disable Cache in DevTools (For Testing)
1. Press F12 to open DevTools
2. Open Settings (F1 or click gear icon)
3. Check "Disable cache (while DevTools is open)"
4. Keep DevTools open and refresh the page

## What You Should See After Cache Clear

On the landing screen, below "NASA Space Apps Challenge · South Polar Site Analysis", you should see:

```
┌─────────────────┐  ┌──────────────────┐
│ Site Analysis   │  │ Mission Planner  │
└─────────────────┘  └──────────────────┘
```

Two buttons side by side. "Site Analysis" is initially highlighted (active).

Click "Mission Planner" and the form changes to show:
- Landing Target (Latitude / Longitude)
- Landing Date & Time
- Spacecraft Parameters (Total Mass, Dry Mass, Isp, Thrust)
- "Plan Mission" button

## Still Not Working?

If buttons still don't appear after clearing cache:

1. Open browser DevTools (F12)
2. Go to Console tab
3. Look for any JavaScript errors (red text)
4. Take a screenshot and check the error message

OR

Open DevTools → Network tab → Refresh page → Check:
- Is style.css loaded? (should be ~200 KB)
- Is planner.js loaded? (should be ~6 KB)
- Any files showing 304 (cached) instead of 200 (fresh)?

## Server Info
- URL: http://localhost:5000
- Server PID: 105492 (currently running)
- Files are up-to-date and verified ✓

The problem is definitely browser cache, not the server or files.
