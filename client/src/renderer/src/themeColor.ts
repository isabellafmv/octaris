import * as THREE from 'three'

// Theme colors from main.css, for three.js which can't use CSS classes
export function themeColor(name: string): THREE.Color {
  return new THREE.Color(getComputedStyle(document.documentElement).getPropertyValue(name).trim())
}
