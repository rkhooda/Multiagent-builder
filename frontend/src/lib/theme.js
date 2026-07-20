/**
 * Theme control. Dark is the default and the daily driver; light is supported.
 *
 * The whole mechanism is one attribute on <html> — index.css redefines the same
 * custom properties under [data-theme="light"], so nothing else in the app
 * knows a theme exists. Applied before React mounts (see main.jsx) so a light
 * user never sees a dark frame flash first.
 */
const KEY = 'mab-theme'

export function storedTheme() {
  try {
    const saved = localStorage.getItem(KEY)
    return saved === 'light' || saved === 'dark' ? saved : 'dark'
  } catch {
    return 'dark'                                  // private mode / storage off
  }
}

export function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme)
  try {
    localStorage.setItem(KEY, theme)
  } catch {
    /* preference simply will not persist — not worth failing over */
  }
  return theme
}
