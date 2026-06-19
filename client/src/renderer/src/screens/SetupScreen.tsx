import { useCallback, useEffect, useRef, useState } from "react";
import type { SyringeMode, UploadResult } from "../types";
import { api } from "../api";
import { PortSelector } from "../components/PortSelector";
import { JogPanel } from "../components/JogPanel";
import { STLUpload } from "../components/STLUpload";
import { STLPreview } from "../components/STLPreview";
import { GcodePreview } from "../components/GcodePreview";
import { GcodeUpload } from "../components/GcodeUpload";
import { SyringeModuleViz } from "../components/SyringeSelector";

type UploadMode = "stl" | "gcode";

interface SetupScreenProps {
  printerConnected: boolean;
  onConnect: (port: string) => void;
  onDisconnect: () => void;
  onStartPrint: () => void;
  externalError?: string | null;
  onClearExternalError?: () => void;
  syringeMode: SyringeMode;
  onSyringeModeChange: (mode: SyringeMode) => void;
  nozzleDiameter: string;
  onNozzleDiameterChange: (v: string) => void;
  syringeDiameter: string;
  onSyringeDiameterChange: (v: string) => void;
  layerHeight: string;
  onLayerHeightChange: (v: string) => void;
  pressurizeMm: string;
  onPressurizeMmChange: (v: string) => void;
  flowMultiplier: string;
  onFlowMultiplierChange: (v: string) => void;
}

export function SetupScreen({
  printerConnected,
  onConnect,
  onDisconnect,
  onStartPrint,
  externalError,
  onClearExternalError,
  syringeMode,
  onSyringeModeChange: setSyringeMode,
  nozzleDiameter,
  onNozzleDiameterChange: setNozzleDiameter,
  syringeDiameter,
  onSyringeDiameterChange: setSyringeDiameter,
  layerHeight,
  onLayerHeightChange: setLayerHeight,
  pressurizeMm,
  onPressurizeMmChange: setPressurizeMm,
  flowMultiplier,
  onFlowMultiplierChange: setFlowMultiplier,
}: SetupScreenProps) {
  const [uploadMode, setUploadMode] = useState<UploadMode>("stl");
  const [stlFile, setStlFile] = useState<File | null>(null);
  const [gcodeFile, setGcodeFile] = useState<File | null>(null);
  const [slicing, setSlicing] = useState(false);
  const [uploadResult, setUploadResult] = useState<UploadResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [calibrated, setCalibrated] = useState(false);
  const [syringeCardCompact, setSyringeCardCompact] = useState(false);
  const syringeCardRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = syringeCardRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      setSyringeCardCompact(entry.contentRect.height < 240);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Merge external errors (from failed print start) into local error state
  useEffect(() => {
    if (externalError) {
      setError(externalError);
      onClearExternalError?.();
    }
  }, [externalError, onClearExternalError]);

  // Check calibration status when printer connects
  useEffect(() => {
    if (printerConnected) {
      api
        .calibrationStatus()
        .then((r) => setCalibrated(r.calibrated))
        .catch(() => {});
    } else {
      setCalibrated(false);
    }
  }, [printerConnected]);

  const handleSetOrigin = useCallback(async () => {
    try {
      await api.calibrationZero();
      setCalibrated(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Calibration failed");
    }
  }, []);

  const handleModeChange = (mode: UploadMode): void => {
    setUploadMode(mode);
    setUploadResult(null);
    setError(null);
    setStlFile(null);
    setGcodeFile(null);
  };

  const handleFile = (f: File): void => {
    setStlFile(f);
    setUploadResult(null);
  };

  const handleSlice = useCallback(async () => {
    if (!stlFile) return;
    setSlicing(true);
    setUploadResult(null);
    setError(null);
    try {
      const result = await api.upload(stlFile, syringeMode, {
        nozzleDiameter: nozzleDiameter ? parseFloat(nozzleDiameter) : undefined,
        syringeDiameter: syringeDiameter
          ? parseFloat(syringeDiameter)
          : undefined,
        layerHeight: layerHeight ? parseFloat(layerHeight) : undefined,
        pressurizeMm: pressurizeMm ? parseFloat(pressurizeMm) : undefined,
        flowMultiplier: flowMultiplier ? parseFloat(flowMultiplier) : undefined,
      });
      setUploadResult(result);
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : "Slicing failed. Check that CuraEngine is installed.",
      );
    } finally {
      setSlicing(false);
    }
  }, [
    stlFile,
    syringeMode,
    nozzleDiameter,
    syringeDiameter,
    layerHeight,
    pressurizeMm,
    flowMultiplier,
  ]);

  const handleGcodeFile = useCallback(
    async (f: File) => {
      setGcodeFile(f);
      setUploadResult(null);
      setError(null);
      setSlicing(true);
      try {
        const result = await api.uploadGcode(f, syringeMode);
        setUploadResult(result);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Processing failed.");
      } finally {
        setSlicing(false);
      }
    },
    [syringeMode],
  );

  const handleGcodeReprocess = useCallback(async () => {
    if (!gcodeFile) return;
    await handleGcodeFile(gcodeFile);
  }, [gcodeFile, handleGcodeFile]);

  const canSlice = stlFile !== null && !slicing;
  const canStartPrint = uploadResult !== null && !slicing && calibrated;

  return (
    <div
      className="flex flex-col h-full overflow-hidden"
      style={{ color: "#2D3333" }}
    >
      {/* ── Header ── */}
      <div className="flex items-start justify-between px-8 pt-7 pb-4 shrink-0">
        <div>
          <h1
            className="text-3xl font-bold tracking-tight"
            style={{ color: "#1A8B8D" }}
          >
            Setup &amp; Configuration
          </h1>
          <p
            className="text-xs tracking-widest uppercase mt-1"
            style={{ color: "#8B9090" }}
          >
            Bioprinting System // Octaris
          </p>
        </div>
        <div className="mt-1">
          <PortSelector
            connected={printerConnected}
            onConnect={onConnect}
            onDisconnect={onDisconnect}
            onError={setError}
          />
        </div>
      </div>

      {error && (
        <div
          className="mx-8 mb-3 px-4 py-2 rounded-lg text-sm flex items-start justify-between gap-3"
          style={{ backgroundColor: "#FDEAEA", color: "#B54040" }}
        >
          <pre className="whitespace-pre-wrap break-all font-sans overflow-y-auto max-h-40 flex-1">
            {error}
          </pre>
          <button
            onClick={() => setError(null)}
            className="font-bold text-lg leading-none shrink-0"
          >
            &times;
          </button>
        </div>
      )}

      {/* ── Two-column body ── */}
      <div className="flex flex-1 min-h-0 px-8 pb-6 gap-6">
        {/* Left column — syringe viz + upload */}
        <div className="flex flex-col gap-4 w-[48%]">
          <div
            ref={syringeCardRef}
            className="flex-1 rounded-2xl flex flex-col items-center justify-center p-6 min-h-0"
            style={{ backgroundColor: "#EDE9DC" }}
          >
            <SyringeModuleViz
              selected={syringeMode}
              onSelect={setSyringeMode}
              compact={syringeCardCompact}
            />
          </div>

          {/* Nozzle / Syringe parameters */}
          <div
            className="rounded-2xl p-4 flex flex-col gap-2"
            style={{ backgroundColor: "#EDE9DC" }}
          >
            <span
              className="text-[10px] font-semibold tracking-widest uppercase"
              style={{ color: "#8B9090" }}
            >
              Print Parameters
            </span>
            <div className="flex gap-2">
              <label className="flex-1">
                <span
                  className="text-[10px] block mb-1"
                  style={{ color: "#8B9090" }}
                >
                  Nozzle ⌀ (mm)
                </span>
                <input
                  type="number"
                  step="0.01"
                  min="0"
                  placeholder="e.g. 0.41"
                  value={nozzleDiameter}
                  onChange={(e) => setNozzleDiameter(e.target.value)}
                  className="w-full px-2 py-1.5 rounded-lg text-xs outline-none"
                  style={{
                    backgroundColor: "#F5F1E6",
                    color: "#2D3333",
                    border: "1px solid #D8D3C8",
                  }}
                />
              </label>
              <label className="flex-1">
                <span
                  className="text-[10px] block mb-1"
                  style={{ color: "#8B9090" }}
                >
                  Syringe ⌀ (mm)
                </span>
                <input
                  type="number"
                  step="0.01"
                  min="0"
                  placeholder="e.g. 4.7"
                  value={syringeDiameter}
                  onChange={(e) => setSyringeDiameter(e.target.value)}
                  className="w-full px-2 py-1.5 rounded-lg text-xs outline-none"
                  style={{
                    backgroundColor: "#F5F1E6",
                    color: "#2D3333",
                    border: "1px solid #D8D3C8",
                  }}
                />
              </label>
              <label className="flex-1">
                <span
                  className="text-[10px] block mb-1"
                  style={{ color: "#8B9090" }}
                >
                  Layer H (mm)
                </span>
                <input
                  type="number"
                  step="0.01"
                  min="0"
                  placeholder="auto"
                  value={layerHeight}
                  onChange={(e) => setLayerHeight(e.target.value)}
                  className="w-full px-2 py-1.5 rounded-lg text-xs outline-none"
                  style={{
                    backgroundColor: "#F5F1E6",
                    color: "#2D3333",
                    border: "1px solid #D8D3C8",
                  }}
                />
              </label>
            </div>
            <div className="flex gap-2">
              <label className="flex-1">
                <span
                  className="text-[10px] block mb-1"
                  style={{ color: "#8B9090" }}
                >
                  Pressurize (mm)
                </span>
                <input
                  type="number"
                  step="0.1"
                  min="0"
                  placeholder="0.2"
                  value={pressurizeMm}
                  onChange={(e) => setPressurizeMm(e.target.value)}
                  className="w-full px-2 py-1.5 rounded-lg text-xs outline-none"
                  style={{
                    backgroundColor: "#F5F1E6",
                    color: "#2D3333",
                    border: "1px solid #D8D3C8",
                  }}
                />
              </label>
              <label className="flex-1">
                <span
                  className="text-[10px] block mb-1"
                  style={{ color: "#8B9090" }}
                >
                  Flow multiplier
                </span>
                <input
                  type="number"
                  step="0.1"
                  min="0"
                  placeholder="1.0"
                  value={flowMultiplier}
                  onChange={(e) => setFlowMultiplier(e.target.value)}
                  className="w-full px-2 py-1.5 rounded-lg text-xs outline-none"
                  style={{
                    backgroundColor: "#F5F1E6",
                    color: "#2D3333",
                    border: "1px solid #D8D3C8",
                  }}
                />
              </label>
            </div>
            <span className="text-[9px]" style={{ color: "#A0A8A8" }}>
              Layer height defaults to 80% of nozzle diameter.
            </span>
          </div>

          {/* Upload mode toggle */}
          <div
            className="flex rounded-xl p-1 gap-1"
            style={{ backgroundColor: "#EDE9DC" }}
          >
            {(["stl", "gcode"] as UploadMode[]).map((mode) => (
              <button
                key={mode}
                onClick={() => handleModeChange(mode)}
                className="flex-1 py-1.5 rounded-lg text-xs font-semibold transition-all"
                style={
                  uploadMode === mode
                    ? {
                        backgroundColor: "white",
                        color: "#1A8B8D",
                        boxShadow: "0 1px 3px rgba(0,0,0,0.08)",
                      }
                    : { color: "#8B9090" }
                }
              >
                {mode === "stl" ? "STL File" : "G-Code File"}
              </button>
            ))}
          </div>

          {uploadMode === "stl" ? (
            <STLUpload file={stlFile} onFile={handleFile} onError={setError} />
          ) : (
            <GcodeUpload
              file={gcodeFile}
              loading={slicing}
              onFile={handleGcodeFile}
              onError={setError}
            />
          )}
        </div>

        {/* Right column */}
        <div className="flex flex-col gap-4 flex-1 min-h-0 overflow-y-auto">
          {/* Calibration + Jog */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <span
                  className="text-xs font-semibold tracking-widest uppercase"
                  style={{ color: "#8B9090" }}
                >
                  {uploadResult ? "Calibration" : "Manual Navigation"}
                </span>
                {calibrated && (
                  <span
                    className="text-[10px] font-semibold px-2 py-0.5 rounded-full"
                    style={{ backgroundColor: "#D4EAE9", color: "#1A8B8D" }}
                  >
                    Origin set
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2">
                <button
                  className="text-xs font-semibold px-3 py-1 rounded-lg transition-all active:scale-95 disabled:opacity-40"
                  style={{ backgroundColor: "#E8E3D8", color: "#8B9090" }}
                  disabled={!printerConnected}
                  onClick={() => api.sendGcode("G1 X0 Y0 F300")}
                >
                  Go to Origin
                </button>
                <button
                  className="text-xs font-semibold px-3 py-1 rounded-lg transition-all active:scale-95 disabled:opacity-40"
                  style={{
                    backgroundColor: calibrated ? "#E8E3D8" : "#1A8B8D",
                    color: calibrated ? "#8B9090" : "white",
                  }}
                  disabled={!printerConnected}
                  onClick={handleSetOrigin}
                >
                  {calibrated ? "Re-zero Origin" : "Set Origin"}
                </button>
              </div>
            </div>
            {(syringeMode === "right" || syringeMode === "both") && (
              <p className="text-[9px] mt-1 mb-1" style={{ color: "#B5614A" }}>
                Always zero at the left nozzle the right nozzle offset is
                applied automatically.
              </p>
            )}
            {!uploadResult && (
              <JogPanel
                syringeMode={syringeMode}
                disabled={!printerConnected}
              />
            )}
          </div>

          {/* STL 3D Preview — shown while waiting to slice */}
          {uploadMode === "stl" && stlFile && !uploadResult && (
            <div
              className="rounded-2xl overflow-hidden shrink-0"
              style={{ height: "180px", backgroundColor: "#EDE9DC" }}
            >
              <STLPreview file={stlFile} />
            </div>
          )}

          {/* Slice Now button — STL path only */}
          {uploadMode === "stl" && stlFile && !uploadResult && (
            <button
              onClick={handleSlice}
              disabled={!canSlice}
              className="w-full flex items-center justify-center gap-2 py-3 rounded-2xl font-semibold text-sm transition-all active:scale-[0.98] disabled:opacity-40"
              style={{
                backgroundColor: "#EDE9DC",
                color: "#1A8B8D",
                border: "1.5px solid #1A8B8D",
              }}
            >
              {slicing ? (
                <>
                  <svg
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    className="w-4 h-4 animate-spin"
                  >
                    <path strokeLinecap="round" d="M12 3a9 9 0 1 0 9 9" />
                  </svg>
                  Slicing…
                </>
              ) : (
                "Click to Slice"
              )}
            </button>
          )}

          {/* G-code preview — shown after upload/slicing */}
          {uploadResult && <GcodePreview result={uploadResult} />}

          {/* Re-slice / Re-process button */}
          {uploadResult && (
            <button
              onClick={
                uploadMode === "stl" ? handleSlice : handleGcodeReprocess
              }
              disabled={
                uploadMode === "stl" ? !canSlice : !gcodeFile || slicing
              }
              className="w-full py-2 rounded-xl text-xs font-medium transition-opacity active:opacity-60 disabled:opacity-40"
              style={{ backgroundColor: "#EDE9DC", color: "#8B9090" }}
            >
              {uploadMode === "stl" ? "Re-slice" : "Re-process"}
            </button>
          )}

          <div className="flex-1" />

          {/* Proceed CTA */}
          <button
            onClick={onStartPrint}
            disabled={!canStartPrint}
            className="w-full flex items-center justify-center gap-2 py-3.5 rounded-2xl text-white font-semibold text-base transition-all active:scale-[0.98] disabled:opacity-40 shrink-0"
            style={{ backgroundColor: "#1A8B8D" }}
          >
            <span>Proceed to Preview</span>
            <span className="text-lg">→</span>
          </button>
          <p className="text-center text-xs -mt-2" style={{ color: "#A0A8A8" }}>
            {uploadResult && !calibrated
              ? "Jog to position and set origin to continue"
              : uploadResult
                ? "Ready to print"
                : uploadMode === "stl"
                  ? stlFile
                    ? "Slice the file to continue"
                    : "Upload an STL to get started"
                  : gcodeFile
                    ? "Processing…"
                    : "Upload a pre-sliced G-code file"}
          </p>
        </div>
      </div>
    </div>
  );
}
