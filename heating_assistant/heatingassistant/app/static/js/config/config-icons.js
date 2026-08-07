// Teal line-art icons for configuration landing cards.
const TEAL = '#00d4aa';

const ICONS = {
  display: `<svg viewBox="0 0 100 100" aria-hidden="true">
    <line x1="20" y1="18" x2="20" y2="80" stroke="${TEAL}" stroke-width="5" stroke-linecap="round" opacity="0.4"/>
    <line x1="20" y1="80" x2="84" y2="80" stroke="${TEAL}" stroke-width="5" stroke-linecap="round" opacity="0.4"/>
    <polyline points="26,66 44,48 58,58 82,28" fill="none" stroke="${TEAL}" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>
    <polyline points="26,74 44,64 58,70 82,52" fill="none" stroke="${TEAL}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="2 6" opacity="0.55"/>
  </svg>`,
  rooms: `<svg viewBox="0 0 100 100" aria-hidden="true">
    <path d="M18 84 V46 L50 20 L82 46 V84 Z" fill="none" stroke="${TEAL}" stroke-width="6" stroke-linejoin="round" stroke-linecap="round"/>
    <path d="M42 84 V60 H58 V84" fill="none" stroke="${TEAL}" stroke-width="5" stroke-linejoin="round" stroke-linecap="round" opacity="0.6"/>
  </svg>`,
  sources: `<svg viewBox="0 0 100 100" aria-hidden="true">
    <path d="M52 16 C 66 36, 76 46, 76 60 a26 26 0 0 1 -52 0 C 24 50, 34 48, 38 34 C 46 42, 52 40, 52 16 Z"
      fill="none" stroke="${TEAL}" stroke-width="6" stroke-linejoin="round" stroke-linecap="round"/>
    <path d="M50 78 a12 12 0 0 1 -12 -14 C 42 60, 46 56, 48 48 C 52 56, 62 58, 62 66 a12 12 0 0 1 -12 12 Z"
      fill="none" stroke="${TEAL}" stroke-width="4" stroke-linejoin="round" stroke-linecap="round" opacity="0.55"/>
  </svg>`,
  system: `<svg viewBox="0 0 100 100" aria-hidden="true">
    <circle cx="38" cy="38" r="13" fill="none" stroke="${TEAL}" stroke-width="5"/>
    <g stroke="${TEAL}" stroke-width="4" stroke-linecap="round" opacity="0.7">
      <line x1="38" y1="14" x2="38" y2="8"/>
      <line x1="18" y1="38" x2="12" y2="38"/>
      <line x1="22" y1="22" x2="17" y2="17"/>
      <line x1="54" y1="22" x2="59" y2="17"/>
    </g>
    <path d="M44 78 H72 a13 13 0 0 0 1 -26 a18 18 0 0 0 -34 -2 a13 13 0 0 0 -7 28 Z"
      fill="none" stroke="${TEAL}" stroke-width="5" stroke-linejoin="round" stroke-linecap="round"/>
  </svg>`,
  params: `<svg viewBox="0 0 100 100" aria-hidden="true">
    <ellipse cx="50" cy="28" rx="28" ry="10" fill="none" stroke="${TEAL}" stroke-width="5" stroke-linecap="round"/>
    <path d="M22 28 v18 c0 5.5 12.5 10 28 10 s28 -4.5 28 -10 V28" fill="none" stroke="${TEAL}" stroke-width="5" stroke-linecap="round" opacity="0.7"/>
    <path d="M22 46 v18 c0 5.5 12.5 10 28 10 s28 -4.5 28 -10 V46" fill="none" stroke="${TEAL}" stroke-width="5" stroke-linecap="round" opacity="0.45"/>
  </svg>`,
};

export { ICONS };
