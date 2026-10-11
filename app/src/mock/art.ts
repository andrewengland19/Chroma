// Album-art stand-in for the mock engine: the warm gradient of the gui-1 mockup.
const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="120" height="120" viewBox="0 0 120 120">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#f5a35b"/><stop offset="0.5" stop-color="#e5484d"/><stop offset="1" stop-color="#7c3aed"/>
</linearGradient></defs><rect width="120" height="120" fill="url(#g)"/></svg>`;

export const MOCK_ART = `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
