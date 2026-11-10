/** Descarga un Blob con un nombre de archivo saneado. */
export function download(blob: Blob, filename: string): void {
  // Letras y números de cualquier idioma (ñ, á…); fuera separadores de ruta y caracteres raros
  const safe = filename.replace(/[^\p{L}\p{N}._\- ]+/gu, '_').trim() || 'extrude';
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = safe;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function downloadText(text: string, filename: string, type: string): void {
  download(new Blob([text], { type }), filename);
}
