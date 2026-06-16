export function parseContentDispositionFilename(header: string | null | undefined): string | null {
  if (!header) return null;

  const encodedMatch = header.match(/filename\*=([^;]+)/i);
  if (encodedMatch) {
    const rawValue = encodedMatch[1].trim().replace(/^"|"$/g, '');
    const encodedFilename = rawValue.includes("''") ? rawValue.split("''").slice(1).join("''") : rawValue;
    try {
      return decodeURIComponent(encodedFilename);
    } catch {
      // Fall through to the ASCII filename fallback below.
    }
  }

  const quotedMatch = header.match(/filename="([^"]+)"/i);
  if (quotedMatch) return quotedMatch[1];

  const plainMatch = header.match(/filename=([^;]+)/i);
  return plainMatch ? plainMatch[1].trim() : null;
}

export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
