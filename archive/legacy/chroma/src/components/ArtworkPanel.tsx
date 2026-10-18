import React, { useEffect, useRef, useState } from "react";
import { Pin, Track } from "../types";
import { ColorPin } from "./ColorPin";

interface Props {
  track: Track | null;
  pins: Pin[];
  onAddPin: (x: number, y: number, color: string, primary: boolean) => void;
  onRemovePin: (id: string) => void;
}

function rgbToHex(r: number, g: number, b: number): string {
  return (
    "#" +
    [r, g, b]
      .map((v) => v.toString(16).padStart(2, "0"))
      .join("")
  );
}

export function ArtworkPanel({ track, pins, onAddPin, onRemovePin }: Props) {
  const imgRef = useRef<HTMLImageElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [imgSrc, setImgSrc] = useState<string>("");
  const [fade, setFade] = useState(false);

  useEffect(() => {
    if (!track?.artworkPath) {
      setImgSrc("");
      return;
    }
    // Cache-bust so the renderer re-reads the file on every track change.
    setFade(true);
    // Always go through the custom protocol so the path works regardless of
    // whether the renderer is served from http://localhost (dev) or file:// (prod).
    const next = `chroma-art://current/artwork.jpg?t=${Date.now()}`;
    const t = setTimeout(() => {
      setImgSrc(next);
      setFade(false);
    }, 180);
    return () => clearTimeout(t);
  }, [track?.artworkPath, track?.title]);

  const sampleColorAt = (clientX: number, clientY: number): string | null => {
    const img = imgRef.current;
    const wrap = wrapRef.current;
    if (!img || !wrap || !img.naturalWidth) return null;
    const rect = wrap.getBoundingClientRect();
    const fx = (clientX - rect.left) / rect.width;
    const fy = (clientY - rect.top) / rect.height;
    if (fx < 0 || fx > 1 || fy < 0 || fy > 1) return null;

    const canvas = document.createElement("canvas");
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;
    try {
      ctx.drawImage(img, 0, 0);
      const px = Math.floor(fx * img.naturalWidth);
      const py = Math.floor(fy * img.naturalHeight);
      const d = ctx.getImageData(px, py, 1, 1).data;
      return rgbToHex(d[0], d[1], d[2]);
    } catch {
      return null;
    }
  };

  const onClick = (e: React.MouseEvent) => {
    if (e.button !== 0) return;
    const wrap = wrapRef.current;
    if (!wrap) return;
    const rect = wrap.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width;
    const y = (e.clientY - rect.top) / rect.height;
    const color = sampleColorAt(e.clientX, e.clientY);
    if (!color) return;
    const primary = !(e.ctrlKey || e.metaKey);
    onAddPin(x, y, color, primary);
  };

  return (
    <div
      ref={wrapRef}
      className="relative w-full select-none"
      style={{ width: 380, height: 380, background: "#000" }}
      onClick={onClick}
      onContextMenu={(e) => e.preventDefault()}
    >
      {imgSrc ? (
        <img
          ref={imgRef}
          src={imgSrc}
          alt=""
          draggable={false}
          className="w-full h-full object-cover block"
          style={{
            opacity: fade ? 0 : 1,
            transition: "opacity 200ms linear",
          }}
        />
      ) : (
        <div className="w-full h-full flex items-center justify-center text-fg-dim font-body text-[12px]">
          No track playing
        </div>
      )}

      {/* Inner vignette */}
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background:
            "radial-gradient(circle at center, rgba(0,0,0,0) 0%, rgba(0,0,0,0.25) 100%)",
        }}
      />

      {/* Pin overlay */}
      <div className="absolute inset-0 pointer-events-none">
        {pins.map((p) => (
          <ColorPin key={p.id} pin={p} onRemove={onRemovePin} />
        ))}
      </div>
    </div>
  );
}
