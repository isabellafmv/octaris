import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { LineMaterial } from 'three/examples/jsm/lines/LineMaterial.js'
import { LineSegments2 } from 'three/examples/jsm/lines/LineSegments2.js'
import { LineSegmentsGeometry } from 'three/examples/jsm/lines/LineSegmentsGeometry.js'
import { api } from '../api'
import type { CategorySegments, GcodePreviewData } from '../gcode/categories'
import type { WorkerMessage } from '../gcode/parse.worker'
import { themeColor } from '../themeColor'

// TODO: take the bed from the printer profile once there are profiles
// (the backend has it in config.json "bed": X and Y from -30 to 30 mm).
const BED_SIZE_MM = 60
const BED_GRID_MM = 10

const EXTRUSION_WIDTH_PX = 2
const TRAVEL_OPACITY = 0.45

type Nozzle = 'left' | 'right'
const NOZZLES: Nozzle[] = ['left', 'right']
const NOZZLE_LABEL: Record<Nozzle, { name: string; swatch: string }> = {
  left: { name: 'Left nozzle', swatch: 'bg-nozzle-left' },
  right: { name: 'Right nozzle', swatch: 'bg-nozzle-right' }
}

interface View {
  layer: number
  onlyCurrent: boolean
  showTravel: boolean
}

interface Extrusion {
  segments: CategorySegments
  mesh: LineSegments2
  full: LineSegmentsGeometry
  material: LineMaterial
}

// The three.js side: one draw call per category (each nozzle's extrusion,
// and travel), with the layer range shown picked by drawing part of each
// buffer. Renders on demand rather than every frame.
class GcodeScene {
  private readonly scene = new THREE.Scene()
  private readonly camera = new THREE.PerspectiveCamera(40, 1, 0.1, 5000)
  private readonly renderer = new THREE.WebGLRenderer({ antialias: true })
  private readonly controls: OrbitControls
  private readonly resizeObserver: ResizeObserver
  private readonly toolpath = new THREE.Group()
  private extrusion: Partial<Record<Nozzle, Extrusion>> = {}
  private travel: { segments: CategorySegments; lines: THREE.LineSegments } | null = null
  private frame = 0

  constructor(private readonly mount: HTMLDivElement) {
    this.scene.background = themeColor('--color-surface-card')
    this.scene.add(this.toolpath, bedOutline())

    this.camera.up.set(0, 0, 1) // G-code is Z-up
    this.renderer.setPixelRatio(window.devicePixelRatio)
    mount.appendChild(this.renderer.domElement)

    this.controls = new OrbitControls(this.camera, this.renderer.domElement)
    this.controls.enableDamping = true
    this.controls.dampingFactor = 0.1
    this.controls.addEventListener('change', this.requestRender)
    this.fitCamera(new THREE.Box3())

    this.resizeObserver = new ResizeObserver(() => this.resize())
    this.resizeObserver.observe(mount)
    this.resize()
  }

  // While damping, controls.update() fires 'change' again, scheduling the
  // next frame until the camera settles.
  private readonly requestRender = (): void => {
    if (this.frame) return
    this.frame = requestAnimationFrame(() => {
      this.frame = 0
      this.controls.update()
      this.renderer.render(this.scene, this.camera)
    })
  }

  private resize(): void {
    const width = this.mount.clientWidth
    const height = this.mount.clientHeight
    if (!width || !height) return // hidden, e.g. while the text view is shown
    this.camera.aspect = width / height
    this.camera.updateProjectionMatrix()
    this.renderer.setSize(width, height)
    for (const e of Object.values(this.extrusion)) e.material.resolution.set(width, height)
    this.requestRender()
  }

  setData(data: GcodePreviewData): void {
    this.clearToolpath()
    const bounds = new THREE.Box3()

    for (const nozzle of NOZZLES) {
      const segments = data.categories[nozzle]
      if (segments.positions.length === 0) continue
      const full = new LineSegmentsGeometry()
      full.setPositions(segments.positions)
      const material = new LineMaterial({
        color: themeColor(`--color-nozzle-${nozzle}`),
        linewidth: EXTRUSION_WIDTH_PX
      })
      material.resolution.set(this.mount.clientWidth || 1, this.mount.clientHeight || 1)
      const mesh = new LineSegments2(full, material)
      this.toolpath.add(mesh)
      this.extrusion[nozzle] = { segments, mesh, full, material }
      bounds.union(full.boundingBox!)
    }

    const travel = data.categories.travel
    if (travel.positions.length > 0) {
      const geometry = new THREE.BufferGeometry()
      geometry.setAttribute('position', new THREE.BufferAttribute(travel.positions, 3))
      const lines = new THREE.LineSegments(
        geometry,
        new THREE.LineBasicMaterial({
          color: themeColor('--color-travel'),
          transparent: true,
          opacity: TRAVEL_OPACITY,
          depthWrite: false
        })
      )
      this.toolpath.add(lines)
      this.travel = { segments: travel, lines }
      if (bounds.isEmpty()) {
        geometry.computeBoundingBox()
        bounds.union(geometry.boundingBox!)
      }
    }

    this.fitCamera(bounds)
  }

  setView({ layer, onlyCurrent, showTravel }: View): void {
    for (const e of Object.values(this.extrusion)) {
      const { layerStart, positions } = e.segments
      const end = layerStart[layer + 1]
      if (onlyCurrent) {
        // Instanced lines can't start drawing mid-buffer, so the layer gets
        // its own small geometry, a view into the same array.
        const start = layerStart[layer]
        if (e.mesh.geometry !== e.full) e.mesh.geometry.dispose()
        const geometry = new LineSegmentsGeometry()
        if (end > start) geometry.setPositions(positions.subarray(start * 6, end * 6))
        e.mesh.geometry = geometry
        e.mesh.visible = end > start
      } else {
        if (e.mesh.geometry !== e.full) e.mesh.geometry.dispose()
        e.mesh.geometry = e.full
        e.full.instanceCount = end
        e.mesh.visible = end > 0
      }
    }

    if (this.travel) {
      const { layerStart } = this.travel.segments
      const start = onlyCurrent ? layerStart[layer] : 0
      this.travel.lines.geometry.setDrawRange(start * 2, (layerStart[layer + 1] - start) * 2)
      this.travel.lines.visible = showTravel
    }
    this.requestRender()
  }

  // Look at the print (or the bed, before there is one) from the front left
  private fitCamera(bounds: THREE.Box3): void {
    const half = BED_SIZE_MM / 2
    const box = bounds.isEmpty() ? new THREE.Box3() : bounds.clone()
    box.union(new THREE.Box3(new THREE.Vector3(-half, -half, 0), new THREE.Vector3(half, half, 0)))
    const center = bounds.isEmpty()
      ? box.getCenter(new THREE.Vector3())
      : bounds.getCenter(new THREE.Vector3())
    const size = box.getSize(new THREE.Vector3()).length()

    this.controls.target.copy(center)
    this.camera.position.set(center.x - size * 0.35, center.y - size * 0.95, center.z + size * 0.75)
    this.camera.far = size * 20
    this.camera.updateProjectionMatrix()
    this.controls.update()
    this.requestRender()
  }

  private clearToolpath(): void {
    for (const e of Object.values(this.extrusion)) {
      if (e.mesh.geometry !== e.full) e.mesh.geometry.dispose()
      e.full.dispose()
      e.material.dispose()
    }
    if (this.travel) {
      this.travel.lines.geometry.dispose()
      ;(this.travel.lines.material as THREE.Material).dispose()
    }
    this.toolpath.clear()
    this.extrusion = {}
    this.travel = null
  }

  dispose(): void {
    cancelAnimationFrame(this.frame)
    this.resizeObserver.disconnect()
    this.controls.dispose()
    this.clearToolpath()
    this.scene.traverse((o) => {
      if (o instanceof THREE.LineSegments || o instanceof THREE.LineLoop) {
        o.geometry.dispose()
        ;(o.material as THREE.Material).dispose()
      }
    })
    this.renderer.dispose()
    this.mount.removeChild(this.renderer.domElement)
  }
}

function bedOutline(): THREE.Group {
  const half = BED_SIZE_MM / 2
  const group = new THREE.Group()

  const grid: number[] = []
  for (let v = -half + BED_GRID_MM; v < half; v += BED_GRID_MM) {
    grid.push(v, -half, 0, v, half, 0, -half, v, 0, half, v, 0)
  }
  const gridGeometry = new THREE.BufferGeometry()
  gridGeometry.setAttribute('position', new THREE.Float32BufferAttribute(grid, 3))
  group.add(
    new THREE.LineSegments(
      gridGeometry,
      new THREE.LineBasicMaterial({ color: themeColor('--color-border') })
    )
  )

  const outline = new THREE.BufferGeometry().setFromPoints([
    new THREE.Vector3(-half, -half, 0),
    new THREE.Vector3(half, -half, 0),
    new THREE.Vector3(half, half, 0),
    new THREE.Vector3(-half, half, 0)
  ])
  group.add(
    new THREE.LineLoop(
      outline,
      new THREE.LineBasicMaterial({ color: themeColor('--color-border-strong') })
    )
  )
  return group
}

type Phase =
  | { kind: 'loading' }
  | { kind: 'parsing'; fraction: number }
  | { kind: 'ready'; data: GcodePreviewData }
  | { kind: 'error'; message: string }

// Fetches the loaded print's G-code and parses it in a worker. Mounted once
// per upload (the preview unmounts while a new file is processed).
function useParsedGcode(): Phase {
  const [phase, setPhase] = useState<Phase>({ kind: 'loading' })

  useEffect(() => {
    const abort = new AbortController()
    let worker: Worker | null = null

    api
      .getLoadedGcode(abort.signal)
      .then((text) => {
        if (abort.signal.aborted) return
        setPhase({ kind: 'parsing', fraction: 0 })
        worker = new Worker(new URL('../gcode/parse.worker.ts', import.meta.url), {
          type: 'module'
        })
        worker.onmessage = (event: MessageEvent<WorkerMessage>) => {
          const message = event.data
          if (message.type === 'progress') setPhase({ kind: 'parsing', fraction: message.fraction })
          else if (message.type === 'done') setPhase({ kind: 'ready', data: message.data })
          else setPhase({ kind: 'error', message: message.message })
          if (message.type !== 'progress') worker?.terminate()
        }
        worker.onerror = (event) => setPhase({ kind: 'error', message: event.message })
        worker.postMessage(text)
      })
      .catch((e: unknown) => {
        if (abort.signal.aborted) return
        setPhase({ kind: 'error', message: e instanceof Error ? e.message : String(e) })
      })

    return () => {
      abort.abort()
      worker?.terminate()
    }
  }, [])

  return phase
}

function Toggle({
  on,
  onChange,
  children
}: {
  on: boolean
  onChange: (on: boolean) => void
  children: React.ReactNode
}): React.JSX.Element {
  return (
    <button
      onClick={() => onChange(!on)}
      aria-pressed={on}
      className={`px-2.5 py-1 rounded-lg text-xs font-medium transition-colors ${
        on ? 'bg-primary text-white' : 'bg-surface-sunken text-text-muted'
      }`}
    >
      {children}
    </button>
  )
}

export function GcodeViewer(): React.JSX.Element {
  const mountRef = useRef<HTMLDivElement>(null)
  const sceneRef = useRef<GcodeScene | null>(null)
  const phase = useParsedGcode()
  const data = phase.kind === 'ready' ? phase.data : null
  const layerCount = data?.layerHeights.length ?? 0

  // null: the top layer, so the whole print shows until the slider is moved
  const [chosenLayer, setChosenLayer] = useState<number | null>(null)
  const [onlyCurrent, setOnlyCurrent] = useState(false)
  const [showTravel, setShowTravel] = useState(true)
  const layer = Math.max(0, Math.min(chosenLayer ?? layerCount - 1, layerCount - 1))

  useEffect(() => {
    const scene = new GcodeScene(mountRef.current!)
    sceneRef.current = scene
    return () => {
      scene.dispose()
      sceneRef.current = null
    }
  }, [])

  useEffect(() => {
    if (data) sceneRef.current?.setData(data)
  }, [data])

  useEffect(() => {
    if (data) sceneRef.current?.setView({ layer, onlyCurrent, showTravel })
  }, [data, layer, onlyCurrent, showTravel])

  const nozzles = data ? NOZZLES.filter((n) => data.categories[n].positions.length > 0) : []

  return (
    <div className="flex flex-col bg-surface">
      <div className="relative h-[300px]">
        <div ref={mountRef} className="absolute inset-0" />
        {phase.kind !== 'ready' && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 px-10 bg-surface-card/80">
            {phase.kind === 'error' ? (
              <span className="text-xs text-danger text-center">
                Couldn&apos;t load the preview: {phase.message}
              </span>
            ) : (
              <>
                <span className="text-xs text-text-muted">
                  {phase.kind === 'loading'
                    ? 'Loading G-code…'
                    : `Building preview… ${Math.round(phase.fraction * 100)}%`}
                </span>
                <div className="w-full max-w-[220px] h-1 rounded-full overflow-hidden bg-border">
                  <div
                    className="h-full bg-primary transition-[width] duration-150"
                    style={{ width: `${phase.kind === 'parsing' ? phase.fraction * 100 : 0}%` }}
                  />
                </div>
              </>
            )}
          </div>
        )}
        {nozzles.length > 0 && (
          <div className="absolute top-2 left-3 flex gap-3 text-[11px] text-text-muted pointer-events-none">
            {nozzles.map((n) => (
              <span key={n} className="flex items-center gap-1.5">
                <span className={`w-2.5 h-0.5 rounded-full ${NOZZLE_LABEL[n].swatch}`} />
                {NOZZLE_LABEL[n].name}
              </span>
            ))}
          </div>
        )}
      </div>

      {data && layerCount > 0 && (
        <div className="flex flex-col gap-2 px-3 py-2.5 border-t border-t-border">
          <div className="flex items-center justify-between text-xs">
            <span className="text-text-secondary">
              Layer <span className="font-semibold text-text">{layer + 1}</span> / {layerCount}
              <span className="text-text-muted"> · {data.layerHeights[layer].toFixed(2)} mm</span>
            </span>
            <div className="flex gap-1.5">
              <Toggle on={onlyCurrent} onChange={setOnlyCurrent}>
                This layer only
              </Toggle>
              <Toggle on={showTravel} onChange={setShowTravel}>
                Travel
              </Toggle>
            </div>
          </div>
          <input
            type="range"
            min={0}
            max={layerCount - 1}
            value={layer}
            onChange={(e) => setChosenLayer(Number(e.target.value))}
            disabled={layerCount < 2}
            aria-label="Show layers up to"
            className="slider-primary w-full"
          />
        </div>
      )}
    </div>
  )
}
