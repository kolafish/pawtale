// Canvas-only artwork keeps text accessible in native WXML and avoids raster/video downloads.
type Command = [
    "M" | "L",
    number,
    number
] | [
    "C",
    number,
    number,
    number,
    number,
    number,
    number
] | [
    "Q",
    number,
    number,
    number,
    number
] | [
    "Z"
];
export const ease = (t: number): number => { t = Math.min(1, Math.max(0, t)); return t * t * (3 - 2 * t); };
const clamp = (t: number): number => Math.min(1, Math.max(0, t));
export function drawStoryBook(ctx: CanvasRenderingContext2D, width: number, height: number, dpr: number, seconds: number, opacity = 1, particleRise = 65): void {
    function shape(color: string | CanvasGradient | null, commands: Command[], stroke?: string) {
        ctx.beginPath();
        for (const command of commands) {
            const [type] = command;
            if (type === 'M')
                ctx.moveTo(command[1], command[2]);
            else if (type === 'L')
                ctx.lineTo(command[1], command[2]);
            else if (type === 'C')
                ctx.bezierCurveTo(command[1], command[2], command[3], command[4], command[5], command[6]);
            else if (type === 'Q')
                ctx.quadraticCurveTo(command[1], command[2], command[3], command[4]);
            else if (type === 'Z')
                ctx.closePath();
        }
        if (color) {
            ctx.fillStyle = color;
            ctx.fill();
        }
        if (stroke) {
            ctx.strokeStyle = stroke;
            ctx.lineWidth = .65;
            ctx.stroke();
        }
    }
    function gradient(x1: number, y1: number, x2: number, y2: number, stops: [
        number,
        string
    ][]) { const g = ctx.createLinearGradient(x1, y1, x2, y2); for (const [at, color] of stops)
        g.addColorStop(at, color); return g; }
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, width * dpr, height * dpr);
    const scale = width / 400;
    const pageScale = scale * .82;
    ctx.setTransform(dpr * scale, 0, 0, dpr * pageScale, 0, dpr * (height - 290 * pageScale - 8));
    ctx.save();
    ctx.globalAlpha = opacity;
    ctx.fillStyle = '#75634b18';
    ctx.beginPath();
    ctx.ellipse(205, 267, 154, 10, 0, 0, Math.PI * 2);
    ctx.fill();
    let unfold = 0;
    if (seconds < .8)
        unfold = ease(seconds / .8);
    else if (seconds < 2.25)
        unfold = 1 + .05 * Math.sin((seconds - .8) * Math.PI);
    else if (seconds < 2.95) {
        const p = clamp((seconds - 2.25) / .7);
        unfold = (1 - ease(p)) - .12 * Math.sin(p * Math.PI * 2) * Math.sin(p * Math.PI);
    }
    ctx.translate(200, 260);
    ctx.scale(1 + .016 * unfold, 1 + .018 * unfold);
    ctx.translate(-200, -260 - 1.5 * unfold);
    const curl = 5 * unfold;
    const cover = gradient(0, 250, 0, 273, [[0, '#a15b3c'], [.55, '#8b4432'], [1, '#673528']]);
    shape(cover, [['M', 44, 261], ['C', 92, 253, 149, 239, 187, 258], ['Q', 200, 249, 213, 258], ['C', 252, 238, 307, 252, 356, 261], ['L', 357, 266], ['C', 304, 267, 248, 256, 218, 267], ['Q', 213, 278, 200, 278], ['Q', 187, 278, 182, 267], ['C', 146, 256, 91, 266, 43, 266], ['Z']], '#9c593c');
    const edgeFill = gradient(0, 192, 0, 264, [[0, '#f3d2a3'], [.7, '#eab98c'], [1, '#dca177']]);
    shape(edgeFill, [['M', 81, 177], ['C', 121, 176, 167, 202, 200, 246], ['C', 234, 200, 280, 177, 318, 178], ['L', 351, 260], ['C', 297, 249, 247, 236, 216, 258], ['Q', 200, 251, 184, 258], ['C', 151, 237, 101, 248, 49, 260], ['Z']]);
    for (let i = 0; i < 12; i++) {
        const v = i / 11;
        const ox = 56 + 23 * (1 - v), oy = 255 - 66 * (1 - v), cy = 250 - 15 * (1 - v);
        const stroke = i % 3 === 0 ? '#ae744a68' : '#b98b5842';
        shape(null, [['M', ox, oy], ['C', 111, oy - 15, 156, cy - 29, 189, cy]], stroke);
        shape(null, [['M', 400 - ox, oy], ['C', 289, oy - 15, 244, cy - 29, 211, cy]], stroke);
    }
    const leftFill = gradient(75, 150, 204, 232, [[0, '#fff4d8'], [.5, '#fbe8c2'], [1, '#eac89a']]);
    const rightFill = gradient(198, 230, 326, 155, [[0, '#e8c89e'], [.4, '#f9e6be'], [1, '#fff5dc']]);
    shape(leftFill, [['M', 76, 171], ['C', 120, 163, 169, 187, 200, 246], ['C', 163, 218, 109, 226, 53, 241], ['L', 76, 171], ['Z']], '#e7c79a80');
    shape(rightFill, [['M', 324, 171], ['C', 280, 163, 231, 187, 200, 246], ['C', 237, 218, 291, 226, 347, 241], ['L', 324, 171], ['Z']], '#e7c79a80');
    for (let i = 0; i < 7; i++) {
        const v = i / 7;
        shape(null, [['M', 83 - v * 8, 183 + v * 46], ['C', 120, 176 + v * 34, 158, 201 + v * 19, 179, 220 + v * 12]], '#b69a6630');
        shape(null, [['M', 317 + v * 8, 183 + v * 46], ['C', 280, 176 + v * 34, 242, 201 + v * 19, 221, 220 + v * 12]], '#b69a6630');
    }
    // Preserve the approved open-book illustration; curl only the top sheets.
    shape(gradient(95, 130, 200, 247, [[0, '#fff7df'], [.5, '#fbe6bf'], [1, '#edc89c']]), [
        ['M', 200, 247], ['C', 173, 210, 126, 195 - curl, 66, 201 - curl * .5], ['Q', 88, 174 - curl, 100, 135 - curl], ['C', 139, 158 - curl, 177, 156 - curl * .5, 198, 201], ['L', 200, 247], ['Z']
    ], '#e6c89c65');
    shape(gradient(200, 247, 309, 126, [[0, '#ecca9d'], [.6, '#fff0cd'], [1, '#fff8e4']]), [
        ['M', 200, 247], ['C', 225, 210, 278, 193 - curl, 337, 201 - curl * .5], ['Q', 317, 173 - curl, 306, 131 - curl], ['C', 273, 154 - curl, 229, 157 - curl * .5, 201, 199], ['L', 200, 247], ['Z']
    ], '#e6c89c65');
    for (let i = 0; i < 5; i++) {
        shape(null, [['M', 106 - i * 3, 153 + i * 8 - curl], ['C', 134, 166 + i * 6 - curl, 166, 174 + i * 6 - curl * .5, 184, 205 + i * 5]], '#baa06d22');
        shape(null, [['M', 300 + i * 3, 150 + i * 8 - curl], ['C', 273, 166 + i * 6 - curl, 240, 174 + i * 6 - curl * .5, 217, 205 + i * 5]], '#baa06d22');
    }
    const glow = ease((seconds - .25) / .7) * (1 - ease((seconds - 2.85) / .95)) * .32;
    if (glow > 0) {
        const light = ctx.createRadialGradient(200, 192, 2, 200, 192, 88);
        light.addColorStop(0, `rgba(255,226,150,${glow})`);
        light.addColorStop(1, 'rgba(255,220,130,0)');
        ctx.fillStyle = light;
        ctx.fillRect(112, 104, 176, 176);
    }
    for (let i = 0; i < 15; i++) {
        const p = (seconds - (.55 + i * .96 / 14)) / 1.7;
        if (p <= 0 || p >= 1)
            continue;
        const alpha = Math.sin(p * Math.PI) * .48;
        const x = 120 + ((i * 7) % 15) * 160 / 14 + Math.sin(p * 2.4 + i) * 6;
        const y = 184 - particleRise * p + (i % 3) * 5;
        const radius = i % 3 === 0 ? 1.65 : 1.1;
        ctx.save();
        ctx.globalAlpha = opacity * alpha;
        ctx.shadowColor = '#e8bb62';
        ctx.shadowBlur = 6;
        ctx.fillStyle = '#b98832';
        ctx.beginPath();
        ctx.arc(x, y, radius, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
    }
    ctx.restore();
}
