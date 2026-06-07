// Generates public/icon.png — 1024×1024 Yap icon for macOS dock
// Run: node scripts/gen-icon.js
const zlib = require('zlib')
const fs = require('fs')
const path = require('path')

const crcTable = new Uint32Array(256)
for (let i = 0; i < 256; i++) {
  let c = i
  for (let j = 0; j < 8; j++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1
  crcTable[i] = c >>> 0
}
function crc32(buf) {
  let crc = 0xFFFFFFFF
  for (const b of buf) crc = crcTable[(crc ^ b) & 0xFF] ^ (crc >>> 8)
  return (crc ^ 0xFFFFFFFF) >>> 0
}
function chunk(type, data) {
  const t = Buffer.from(type, 'ascii')
  const l = Buffer.alloc(4); l.writeUInt32BE(data.length)
  const c = Buffer.alloc(4); c.writeUInt32BE(crc32(Buffer.concat([t, data])))
  return Buffer.concat([l, t, data, c])
}

const W = 1024, H = 1024
const px = new Uint8Array(W * H * 4) // RGBA

// CONTENT is the size of the purple squircle — smaller = more transparent padding around it
// 1024 = fills entire canvas, 832 = 10% padding each side (Reddit rule), 800 = a bit more padding
const CONTENT = 860
const OFF = (W - CONTENT) / 2  // transparent border on each side (112px at 800)

// Gradient: lighter indigo at top → deeper at bottom, only inside the squircle area
for (let y = 0; y < H; y++) {
  const t = Math.max(0, Math.min(1, (y - OFF) / (CONTENT - 1)))
  const r = Math.round(0x78 + t * (0x50 - 0x78))
  const g = Math.round(0x79 + t * (0x52 - 0x79))
  const bl = Math.round(0xf2 + t * (0xe0 - 0xf2))
  for (let x = 0; x < W; x++) {
    const i = (y * W + x) * 4
    px[i] = r; px[i+1] = g; px[i+2] = bl; px[i+3] = 255
  }
}

// Squircle mask — only the CONTENT×CONTENT area is opaque, rest is transparent
const cx = W / 2, cy = H / 2, rad = CONTENT / 2, N = 5
for (let y = 0; y < H; y++) {
  for (let x = 0; x < W; x++) {
    const nx = Math.abs((x - cx) / rad)
    const ny = Math.abs((y - cy) / rad)
    if (Math.pow(nx, N) + Math.pow(ny, N) > 1) {
      px[(y * W + x) * 4 + 3] = 0
    }
  }
}

// Draw a thick line segment with round caps (RGBA)
function drawLine(x0, y0, x1, y1, hw, r, g, b, a = 255) {
  const dx = x1 - x0, dy = y1 - y0
  const len = Math.hypot(dx, dy)
  const minX = Math.max(0, Math.floor(Math.min(x0, x1) - hw - 1))
  const maxX = Math.min(W-1, Math.ceil(Math.max(x0, x1) + hw + 1))
  const minY = Math.max(0, Math.floor(Math.min(y0, y1) - hw - 1))
  const maxY = Math.min(H-1, Math.ceil(Math.max(y0, y1) + hw + 1))
  for (let y = minY; y <= maxY; y++) {
    for (let x = minX; x <= maxX; x++) {
      const t = len === 0 ? 0 : Math.max(0, Math.min(1, ((x-x0)*dx + (y-y0)*dy) / (len*len)))
      const dist = Math.hypot(x - (x0 + t*dx), y - (y0 + t*dy))
      if (dist <= hw) {
        const i = (y * W + x) * 4
        if (px[i+3] === 0) continue // don't paint into transparent corner zone
        const alpha = Math.min(1, hw - dist + 1) * (a / 255)
        px[i]   = Math.round(px[i]   * (1-alpha) + r * alpha)
        px[i+1] = Math.round(px[i+1] * (1-alpha) + g * alpha)
        px[i+2] = Math.round(px[i+2] * (1-alpha) + b * alpha)
      }
    }
  }
}

// Scale brackets relative to CONTENT area, offset into center of canvas
// SVG viewbox 52×52 maps to CONTENT×CONTENT, then shifted by OFF
const s = CONTENT / 52
const hw = (3.4 * s) / 2
const p = (u) => OFF + u * s   // convert SVG unit to canvas pixel

const SO = Math.round(s * 0.8)
const SHW = hw + s * 0.1

// Shadows
drawLine(p(11)+SO, p(26)+SO, p(11)+SO, p(11)+SO, SHW, 0, 0, 40, 40)
drawLine(p(11)+SO, p(11)+SO, p(26)+SO, p(11)+SO, SHW, 0, 0, 40, 40)
drawLine(p(26)+SO, p(41)+SO, p(41)+SO, p(41)+SO, SHW, 0, 0, 40, 40)
drawLine(p(41)+SO, p(41)+SO, p(41)+SO, p(26)+SO, SHW, 0, 0, 40, 40)

// White brackets — same proportions, just scaled to CONTENT
// TL: M11 26 L11 11 L26 11
drawLine(p(11), p(26), p(11), p(11), hw, 255, 255, 255)
drawLine(p(11), p(11), p(26), p(11), hw, 255, 255, 255)
// BR: M26 41 L41 41 L41 26
drawLine(p(26), p(41), p(41), p(41), hw, 255, 255, 255)
drawLine(p(41), p(41), p(41), p(26), hw, 255, 255, 255)

// Pack RGBA PNG
const SIGN = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])
const ihdr = Buffer.alloc(13)
ihdr.writeUInt32BE(W, 0); ihdr.writeUInt32BE(H, 4)
ihdr[8] = 8; ihdr[9] = 6  // RGBA

const raw = Buffer.alloc(H * (1 + W * 4))
for (let y = 0; y < H; y++) {
  raw[y*(1+W*4)] = 0
  for (let x = 0; x < W; x++) {
    const d = y*(1+W*4)+1+x*4
    const s2 = (y*W+x)*4
    raw[d] = px[s2]; raw[d+1] = px[s2+1]; raw[d+2] = px[s2+2]; raw[d+3] = px[s2+3]
  }
}

const out = Buffer.concat([
  SIGN,
  chunk('IHDR', ihdr),
  chunk('IDAT', zlib.deflateSync(raw, { level: 9 })),
  chunk('IEND', Buffer.alloc(0)),
])

const dest = path.join(__dirname, '..', 'public', 'icon.png')
fs.writeFileSync(dest, out)
console.log(`✓ ${dest}  (${(out.length/1024).toFixed(1)} KB)`)
