import React, { useEffect, useState } from 'react';
import { Button } from './ui/button';
import { Download, ExternalLink, Share2, Copy, FileText, Image as ImageIcon, FileArchive, Apple, X } from 'lucide-react';
import { toast } from '../hooks/use-toast';

/**
 * Cross-platform result viewer with iOS-friendly save options.
 * - Preview (image / PDF iframe / text / zip listing)
 * - Download (Blob + <a download>)
 * - Open in new tab (works on iOS Safari; user can then tap Share → Save to Files)
 * - Share via Web Share API (mobile native share sheet — saves to Files on iOS)
 * - Copy text (for text outputs)
 */

const isIOS = () => /iPad|iPhone|iPod/.test(navigator.userAgent) ||
  (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
const isMobile = () => /Mobi|Android|iPhone|iPad|iPod/.test(navigator.userAgent);

const kindFromName = (name='') => {
  const n = name.toLowerCase();
  if (n.match(/\.(png|jpg|jpeg|webp|gif|bmp|svg|ico)$/)) return 'image';
  if (n.endsWith('.pdf')) return 'pdf';
  if (n.match(/\.(zip|tar|gz|7z)$/)) return 'archive';
  if (n.match(/\.(txt|csv|html|htm|xml|json|svg|md)$/)) return 'text';
  if (n.match(/\.(docx|xlsx|pptx)$/)) return 'office';
  return 'binary';
};

export default function ResultPanel({ blob, filename, onClose }) {
  const [url, setUrl] = useState('');
  const [textPreview, setTextPreview] = useState('');
  const kind = kindFromName(filename);

  useEffect(() => {
    if (!blob) return;
    const u = URL.createObjectURL(blob);
    setUrl(u);
    if (kind === 'text' && blob.size < 200_000) {
      blob.text().then(t => setTextPreview(t.slice(0, 100_000)));
    }
    return () => URL.revokeObjectURL(u);
  }, [blob, kind]);

  const handleDownload = () => {
    try {
      const a = document.createElement('a');
      a.href = url; a.download = filename;
      document.body.appendChild(a); a.click(); a.remove();
      toast({ title: 'Download started' });
    } catch (e) {
      toast({ title: 'Download failed, use Open in new tab' });
    }
  };

  const handleOpenNew = () => {
    // Convert blob to a typed URL; iOS Safari handles this well
    window.open(url, '_blank', 'noopener,noreferrer');
  };

  const handleShare = async () => {
    try {
      if (navigator.share && navigator.canShare?.({ files: [new File([blob], filename, { type: blob.type })] })) {
        const file = new File([blob], filename, { type: blob.type });
        await navigator.share({ files: [file], title: filename });
        toast({ title: 'Shared' });
      } else if (navigator.share) {
        // Fallback: share URL
        await navigator.share({ url, title: filename });
      } else {
        toast({ title: 'Sharing not supported on this device' });
      }
    } catch (e) {
      // user cancelled or unsupported
    }
  };

  const handleCopyText = async () => {
    if (textPreview) {
      await navigator.clipboard.writeText(textPreview);
      toast({ title: 'Text copied to clipboard' });
    }
  };

  if (!blob || !url) return null;

  const sizeKB = (blob.size / 1024).toFixed(1);

  return (
    <div className="mt-5 rounded-2xl border border-emerald-400/20 bg-gradient-to-br from-emerald-500/5 to-cyan-500/5 p-5">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-emerald-400 to-cyan-500 flex items-center justify-center">
            {kind === 'image' ? <ImageIcon className="w-5 h-5 text-white"/> :
             kind === 'pdf' ? <FileText className="w-5 h-5 text-white"/> :
             kind === 'archive' ? <FileArchive className="w-5 h-5 text-white"/> :
             <FileText className="w-5 h-5 text-white"/>}
          </div>
          <div>
            <p className="text-white font-medium text-sm">{filename}</p>
            <p className="text-zinc-400 text-xs">{sizeKB} KB · ready</p>
          </div>
        </div>
        {onClose && (
          <button onClick={onClose} className="text-zinc-500 hover:text-white p-1" aria-label="Close result"><X className="w-4 h-4"/></button>
        )}
      </div>

      {/* Preview */}
      <div className="rounded-xl bg-zinc-950/70 border border-white/10 overflow-hidden mb-4">
        {kind === 'image' && (
          <img src={url} alt={filename} className="max-h-[480px] w-full object-contain bg-[repeating-conic-gradient(#0a0a0a_0%_25%,#111_0%_50%)_50%/16px_16px]" />
        )}
        {kind === 'pdf' && (
          <iframe src={url} title="PDF preview" className="w-full h-[520px] bg-zinc-900" />
        )}
        {kind === 'text' && (
          <pre className="text-zinc-200 text-xs whitespace-pre-wrap p-4 max-h-[440px] overflow-auto font-mono">{textPreview || 'Loading preview…'}</pre>
        )}
        {(kind === 'archive' || kind === 'office' || kind === 'binary') && (
          <div className="p-8 text-center">
            <FileArchive className="w-12 h-12 text-zinc-500 mx-auto mb-2"/>
            <p className="text-zinc-400 text-sm">Preview not available for this file type.</p>
            <p className="text-zinc-500 text-xs mt-1">Use download or open in a new tab.</p>
          </div>
        )}
      </div>

      {/* Action buttons */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2.5">
        <Button onClick={handleDownload} className="bg-white text-zinc-900 hover:bg-zinc-100 h-10">
          <Download className="w-4 h-4 mr-2"/> Download
        </Button>
        <Button onClick={handleOpenNew} variant="outline" className="bg-zinc-900 text-white border-white/10 hover:bg-zinc-800 hover:text-white h-10">
          <ExternalLink className="w-4 h-4 mr-2"/> Open in new tab
        </Button>
        {(isMobile() || (typeof navigator !== 'undefined' && navigator.share)) && (
          <Button onClick={handleShare} variant="outline" className="bg-zinc-900 text-white border-white/10 hover:bg-zinc-800 hover:text-white h-10">
            <Share2 className="w-4 h-4 mr-2"/> Share / Save
          </Button>
        )}
        {kind === 'text' && (
          <Button onClick={handleCopyText} variant="outline" className="bg-zinc-900 text-white border-white/10 hover:bg-zinc-800 hover:text-white h-10">
            <Copy className="w-4 h-4 mr-2"/> Copy text
          </Button>
        )}
      </div>

      {isIOS() && (
        <div className="mt-4 rounded-xl bg-blue-500/10 border border-blue-400/20 p-3.5 flex items-start gap-3">
          <Apple className="w-4 h-4 text-blue-300 mt-0.5 shrink-0"/>
          <div className="text-xs text-blue-100 leading-relaxed">
            <p className="font-medium mb-0.5">Saving on iPhone / iPad</p>
            <p className="text-blue-200/80">Tap <span className="font-medium text-white">Share / Save</span> and choose <span className="font-medium text-white">Save to Files</span> — or tap <span className="font-medium text-white">Open in new tab</span> then use Safari's Share menu → Save to Files.</p>
          </div>
        </div>
      )}
    </div>
  );
}
