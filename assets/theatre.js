// The simulated operating room behind the page.
//
// The room itself is modelled in Blender (scene/or.blend) and exported to assets/or.glb.
// This module lights it, drives the robot arm, draws the live monitor and the perception
// overlays, and orbits the camera around the table as the page scrolls.
// If anything here fails, <html> gets the class "no-gl" and the CSS gradient shows instead.

import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";

const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
const BG = 0x071a1f, TEAL = 0x58d6cc, AMBER = 0xffb35c;
const mono = (px) => `500 ${px}px 'Geist Mono', ui-monospace, Consolas, monospace`;
const lerp = (a, b, t) => a + (b - a) * t;
const root = document.documentElement;

async function start() {
    const canvas = document.getElementById("or");
    const renderer = new THREE.WebGLRenderer({ canvas, powerPreference: "high-performance" });
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.toneMapping = THREE.NeutralToneMapping;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(BG);
    scene.fog = new THREE.FogExp2(BG, 0.05);
    const pmrem = new THREE.PMREMGenerator(renderer);
    scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;   // soft reflections for the steel
    pmrem.dispose();
    scene.environmentIntensity = 0;
    const camera = new THREE.PerspectiveCamera(36, 1, 0.1, 60);

    // ----- the Blender model -----
    const gltf = await new GLTFLoader().loadAsync(new URL("./or.glb", import.meta.url).href);
    const room = gltf.scene;
    scene.add(room);
    room.updateMatrixWorld(true);
    const node = (name) => {
        const o = room.getObjectByName(name);
        if (!o) throw new Error(`or.glb has no object named "${name}"`);
        return o;
    };
    const at = (name) => node(name).getWorldPosition(new THREE.Vector3());
    const leds = new Set();
    room.traverse((o) => {
        if (!o.isMesh) return;
        o.receiveShadow = true;
        o.castShadow = !o.name.startsWith("lamp_");      // the light heads must not shadow their own beams
        if (o.material.name === "lamp_led") leds.add(o.material);
    });
    const ledGlow = [...leds].map((m) => [m, m.emissiveIntensity]);

    const lineMat = (hex, opacity) => new THREE.LineBasicMaterial({ color: hex, transparent: true, opacity });
    const V = (x, y, z) => new THREE.Vector3(x, y, z);

    // ----- simulation overlays: floor grid and sterile zone -----
    const grid = new THREE.GridHelper(14, 28, 0x2f8f93, 0x1c5a61);
    grid.position.set(0, 0.004, -0.5);
    grid.material.transparent = true;
    grid.material.opacity = 0.42;
    scene.add(grid);
    const zone = [[-1.7, -1.45, 2.1, -1.45], [2.1, -1.45, 2.1, 1.45], [2.1, 1.45, -1.7, 1.45], [-1.7, 1.45, -1.7, -1.45]]
        .flatMap(([a, b, c, d]) => [V(a, 0.008, b), V(c, 0.008, d)]);
    scene.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(zone), lineMat(TEAL, 0.55)));

    // ----- surgical lights: a spot and a visible beam on each modelled head -----
    const shadows = [], small = innerWidth < 1000;      // phones and tablets get one smaller shadow map
    function lamp(tag, r, shadow) {
        const head = node(`lamp_${tag}`), pos = at(`lamp_${tag}`), aim = at(`lamp_${tag}_aim`);
        const dir = aim.clone().sub(pos), len = dir.length();
        dir.normalize();
        const geo = new THREE.CylinderGeometry(r * 0.85, r * 0.85 + len * 0.36, len, 40, 1, true);
        geo.translate(0, -len / 2, 0);                   // the head's local -Y is the beam axis
        const beam = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({
            color: 0xbff3f0, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, side: THREE.DoubleSide, depthWrite: false,
        }));
        head.add(beam);
        const spot = new THREE.SpotLight(0xf2fbff, 0, 12, 0.58, 0.8, 2);
        spot.position.copy(pos).addScaledVector(dir, 0.03);
        spot.target.position.copy(aim);
        if (shadow) {
            spot.castShadow = true;
            spot.shadow.mapSize.set(small ? 1024 : 2048, small ? 1024 : 2048);
            spot.shadow.camera.near = 0.3;
            spot.shadow.camera.far = 7;
            spot.shadow.bias = -0.0004;
            spot.shadow.normalBias = 0.01;
            spot.shadow.autoUpdate = false;              // refreshed by hand: only the arm ever moves
            shadows.push(spot.shadow);
        }
        scene.add(spot, spot.target);
        return (p, candela) => { spot.intensity = candela * p; beam.material.opacity = 0.035 * p; };
    }
    const lampA = lamp("A", 0.38, true), lampB = lamp("B", 0.3, !small);
    const hemi = new THREE.HemisphereLight(0xa9e4e6, 0x04090b, 0);
    const fill = new THREE.DirectionalLight(0xbfe9ee, 0);
    fill.position.set(4, 5, 6);
    scene.add(hemi, fill);

    // ----- robot arm: two-link inverse kinematics, tool pointing down over the knee -----
    const KNEE = at("knee_target");
    const yaw = node("arm_yaw"), upper = node("arm_upper"), fore = node("arm_fore"), wrist = node("arm_wrist");
    const SHOULDER = at("arm_yaw"), L1 = fore.position.x, L2 = wrist.position.x, TOOL = node("arm_tool_tip").position.x;
    const pathGeo = new THREE.BufferGeometry().setFromPoints([V(0, 0, 0), V(0, 0, 0)]);
    scene.add(new THREE.Line(pathGeo, lineMat(AMBER, 0.9)));
    const ring = new THREE.Mesh(new THREE.RingGeometry(0.03, 0.036, 32), new THREE.MeshBasicMaterial({ color: AMBER, side: THREE.DoubleSide }));
    ring.rotation.x = -Math.PI / 2;
    scene.add(ring);
    function pose(t) {
        const tx = KNEE.x + Math.cos(t * 0.5) * 0.07, ty = KNEE.y + 0.2 + Math.sin(t * 0.8) * 0.035, tz = KNEE.z + Math.sin(t * 0.5) * 0.05;
        const dx = tx - SHOULDER.x, dz = tz - SHOULDER.z, r = Math.hypot(dx, dz), y = ty + TOOL - SHOULDER.y;
        const d = Math.min(Math.hypot(r, y), L1 + L2 - 0.001);
        const a1 = Math.atan2(y, r) + Math.acos((L1 * L1 + d * d - L2 * L2) / (2 * L1 * d));
        const a2 = a1 - (Math.PI - Math.acos((L1 * L1 + L2 * L2 - d * d) / (2 * L1 * L2)));
        yaw.rotation.set(0, Math.atan2(-dz, dx), 0);
        upper.rotation.set(0, 0, a1);
        fore.rotation.set(0, 0, a2 - a1);
        wrist.rotation.set(0, 0, -Math.PI / 2 - a2);
        const p = pathGeo.attributes.position;
        p.setXYZ(0, tx, ty, tz);
        p.setXYZ(1, tx, KNEE.y + 0.012, tz);
        p.needsUpdate = true;
        ring.position.set(tx, KNEE.y + 0.012, tz);
    }
    // perception overlay: the detected target
    const bbox = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(0.36, 0.2, 0.3)), lineMat(AMBER, 0.95));
    bbox.position.set(KNEE.x, KNEE.y + 0.03, KNEE.z);
    scene.add(bbox);

    // ----- live displays, drawn to canvases mapped onto the modelled screens -----
    const screens = [];
    function display(name, w, h) {
        const c = document.createElement("canvas");
        c.width = w; c.height = h;
        const tex = new THREE.CanvasTexture(c);
        tex.colorSpace = THREE.SRGBColorSpace;
        tex.flipY = false;                               // glTF texture coordinates
        const mesh = node(name);
        mesh.material = new THREE.MeshBasicMaterial({ map: tex });
        mesh.castShadow = false;
        screens.push(mesh.material);
        return { g: c.getContext("2d"), tex };
    }

    // vitals monitor: a sweep display
    const mon = display("monitor_screen", 512, 296), m2 = mon.g;
    const gauss = (p, c, w) => Math.exp(-(((p - c) / w) ** 2));
    const LANES = [
        { c: "#62E0A6", y: 78, amp: 46, label: "HR", value: "72", f: (p) => 0.12 * gauss(p, 0.2, 0.03) - 0.12 * gauss(p, 0.345, 0.012) + gauss(p, 0.37, 0.012) - 0.22 * gauss(p, 0.395, 0.012) + 0.25 * gauss(p, 0.62, 0.05) },
        { c: "#62C9F2", y: 172, amp: 30, label: "SpO2", value: "99", f: (p) => 0.9 * gauss(p, 0.5, 0.11) + 0.35 * gauss(p, 0.74, 0.1) - 0.2 },
        { c: "#FFB35C", y: 258, amp: 30, label: "ABP", value: "118/76", f: (p) => 0.95 * gauss(p, 0.48, 0.09) + 0.4 * gauss(p, 0.7, 0.12) - 0.2 },
    ];
    const SWEEP = 368;
    let sweepX = 0, sweepT = 0, carry = 0;
    function monitorFrame() {
        m2.fillStyle = "#03090B"; m2.fillRect(0, 0, 512, 296);
        m2.textBaseline = "alphabetic";
        LANES.forEach((l) => {
            m2.fillStyle = l.c;
            m2.font = mono(15); m2.fillText(l.label, 388, l.y - 34);
            m2.font = mono(l.value.length > 3 ? 30 : 46); m2.fillText(l.value, 388, l.y + 8);
            l.prev = null;
        });
        sweepX = 0;
    }
    function monitorStep(dt) {
        carry += dt * 92;
        let n = Math.floor(carry);
        carry -= n;
        m2.lineWidth = 2.4; m2.lineJoin = "round";
        while (n-- > 0) {
            sweepT += 1 / 92;
            const phase = (sweepT * 1.2) % 1;
            m2.fillStyle = "#03090B";
            m2.fillRect(sweepX + 1, 0, 16, 296);
            m2.fillRect(SWEEP, 0, 12, 296);
            LANES.forEach((l) => {
                const y = l.y - l.amp * l.f(phase);
                if (l.prev !== null) { m2.strokeStyle = l.c; m2.beginPath(); m2.moveTo(sweepX - 1, l.prev); m2.lineTo(sweepX, y); m2.stroke(); }
                l.prev = y;
            });
            if (++sweepX >= SWEEP) { sweepX = 0; LANES.forEach((l) => { l.prev = null; }); }
        }
    }
    function monitorStill() { monitorFrame(); for (let i = 0; i < 40; i++) monitorStep(0.1); mon.tex.needsUpdate = true; }
    monitorFrame();

    // wall display: knee alignment, a nod to the orthopaedic work
    const wall = display("wall_screen", 512, 292), w2 = wall.g;
    function wallFrame() {
        w2.fillStyle = "#03090B"; w2.fillRect(0, 0, 512, 292);
        w2.strokeStyle = "#8FE3DC"; w2.lineWidth = 2.5; w2.lineJoin = "round";
        w2.beginPath();                                   // femur
        w2.moveTo(150, 0); w2.bezierCurveTo(156, 70, 150, 96, 124, 122); w2.bezierCurveTo(112, 142, 150, 150, 176, 138);
        w2.bezierCurveTo(190, 150, 232, 146, 226, 122); w2.bezierCurveTo(206, 96, 200, 70, 204, 0); w2.stroke();
        w2.beginPath();                                   // tibia
        w2.moveTo(120, 166); w2.lineTo(232, 162); w2.bezierCurveTo(226, 200, 204, 214, 200, 292);
        w2.moveTo(120, 166); w2.bezierCurveTo(130, 204, 152, 214, 156, 292); w2.stroke();
        w2.strokeStyle = "#FFB35C"; w2.lineWidth = 1.6; w2.setLineDash([7, 6]);
        w2.beginPath(); w2.moveTo(182, 0); w2.lineTo(172, 292); w2.moveTo(96, 156); w2.lineTo(256, 150); w2.stroke();
        w2.setLineDash([]);
        w2.fillStyle = "#8FE3DC"; w2.font = mono(17); w2.fillText("knee_R  AP", 300, 44);
        w2.fillStyle = "#FFB35C"; w2.font = mono(34); w2.fillText("178.4°", 300, 132);
        w2.font = mono(17); w2.fillText("hip-knee-ankle", 300, 160);
        w2.fillStyle = "#8FE3DC"; w2.fillText("plan  ready", 300, 250);
        wall.tex.needsUpdate = true;
    }
    wallFrame();

    // ----- dust in the beams -----
    const motes = new Float32Array(180 * 3);
    for (let i = 0; i < 180; i++) {
        motes[i * 3] = 0.3 + (Math.random() - 0.5) * 2.2;
        motes[i * 3 + 1] = 1.15 + Math.random() * 1.2;
        motes[i * 3 + 2] = (Math.random() - 0.5) * 1.6;
    }
    const dustGeo = new THREE.BufferGeometry();
    dustGeo.setAttribute("position", new THREE.BufferAttribute(motes, 3));
    const dust = new THREE.Points(dustGeo, new THREE.PointsMaterial({ color: 0xdff7f5, size: 0.012, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false }));
    scene.add(dust);

    // ----- labels (drawn once the webfont is in) -----
    function label(text, css, pos, width) {
        const c = document.createElement("canvas");
        c.width = 512; c.height = 96;
        const g = c.getContext("2d");
        g.font = mono(40); g.fillStyle = css; g.textBaseline = "middle"; g.fillText(text, 6, 50);
        const tex = new THREE.CanvasTexture(c);
        tex.colorSpace = THREE.SRGBColorSpace;
        const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false }));
        s.center.set(0, 0.5);
        s.scale.set(width, width * 96 / 512, 1);
        s.position.copy(pos);
        scene.add(s);
    }
    (document.fonts ? document.fonts.ready : Promise.resolve()).then(() => {
        label("knee_R  0.98", "#FFB35C", V(KNEE.x - 0.18, KNEE.y + 0.19, KNEE.z + 0.16), 0.5);
        label("arm_01  tracking", "#8FE3DC", V(SHOULDER.x + 0.22, SHOULDER.y + 0.36, SHOULDER.z), 0.56);
        wallFrame();
        if (REDUCED) { monitorStill(); still(); } else { monitorFrame(); }
    });

    // ----- post-processing: bloom on the lamps and screens, then tone mapping -----
    const composer = new EffectComposer(renderer, new THREE.WebGLRenderTarget(1, 1, { type: THREE.HalfFloatType, samples: 4 }));
    composer.addPass(new RenderPass(scene, camera));
    const bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.3, 0.6, 0.9);
    composer.addPass(bloom);
    composer.addPass(new OutputPass());

    // ----- camera, power-up, loop -----
    const look = new THREE.Vector3();
    let compact = false, quality = 1, scrollT = 0, eased = 0, mx = 0, my = 0, mxT = 0, myT = 0;
    function readScroll() {
        const max = root.scrollHeight - innerHeight;
        scrollT = max > 0 ? Math.min(1, Math.max(0, scrollY / max)) : 0;
    }
    function place(t) {
        const e = eased * eased * (3 - 2 * eased);
        const arrive = REDUCED ? 1 : 1 - Math.pow(1 - Math.min(1, t / 3), 3);
        const theta = lerp(0.62, -0.42, e) + mx * 0.07, phi = lerp(0.17, 0.36, e) - my * 0.03;
        const R = (lerp(6.3, 4.9, e) + (1 - arrive) * 3) * (compact ? 1.25 : Math.max(1, Math.pow(1.95 / camera.aspect, 0.7)));
        look.set(lerp(0.2, 0.4, e), lerp(1.0, 1.05, e), 0);
        camera.position.set(look.x + R * Math.sin(theta) * Math.cos(phi), look.y + R * Math.sin(phi), look.z + R * Math.cos(theta) * Math.cos(phi));
        camera.lookAt(look);
    }
    const flicker = (x) => (x < 0 ? 0 : x < 0.07 ? 0.75 : x < 0.15 ? 0.1 : x < 0.24 ? 0.95 : x < 0.3 ? 0.45 : 1);
    function power(t) {
        const a = REDUCED ? 1 : flicker(t - 0.45), b = REDUCED ? 1 : flicker(t - 0.85);
        const on = Math.min(a, b);
        lampA(a, 16); lampB(b, 11);
        hemi.intensity = 0.25 + 0.9 * on;
        fill.intensity = 0.2 + 0.9 * a;
        scene.environmentIntensity = 0.06 + 0.34 * on;
        dust.material.opacity = 0.5 * on;
        ledGlow.forEach(([m, base]) => { m.emissiveIntensity = base * (0.02 + 0.98 * Math.max(a, b)); });
        screens.forEach((m) => m.color.setScalar(0.25 + 0.75 * a));
    }
    function resize() {
        const w = innerWidth, h = innerHeight;
        compact = w < 1000;
        // cap the pixel count: the scene renders through a multisampled float buffer and a bloom pass
        const dpr = Math.min(devicePixelRatio || 1, 1.5, Math.sqrt(4.2e6 / (w * h))) * quality;
        renderer.setPixelRatio(dpr);
        renderer.setSize(w, h, false);
        composer.setPixelRatio(dpr);
        composer.setSize(w, h);
        camera.aspect = w / h;
        camera.fov = w / h < 0.8 ? 50 : 36;
        // shift the room toward the clear part of the page: right of the copy, or above it on narrow screens
        if (compact) camera.setViewOffset(w, h, 0, h * 0.2, w, h); else camera.setViewOffset(w, h, -w * 0.21, 0, w, h);
        camera.updateProjectionMatrix();
        readScroll();
    }
    const refreshShadows = () => shadows.forEach((s) => { s.needsUpdate = true; });
    function still() { place(Infinity); power(Infinity); pose(0); refreshShadows(); composer.render(); }

    let began = null, last = 0, tick = 0, frames = 0, slow = 0;
    function frame(now) {
        if (began === null) { began = now; last = now; }
        const t = (now - began) / 1000, raw = (now - last) / 1000, dt = Math.min(0.05, raw);
        last = now;
        eased += (scrollT - eased) * Math.min(1, dt * 4);
        mx += (mxT - mx) * Math.min(1, dt * 3);
        my += (myT - my) * Math.min(1, dt * 3);
        place(t); power(t); pose(t); monitorStep(dt);
        if (++tick % 2 === 0) mon.tex.needsUpdate = true;
        if (tick % 3 === 0) refreshShadows();
        dust.rotation.y = t * 0.02;
        composer.render();
        // A machine that cannot hold ~30 fps gets a lighter picture: no bloom, fewer pixels.
        // Gaps over 120 ms are the browser throttling a hidden or covered window, not a slow GPU, so they don't count.
        if (quality === 1 && t > 4 && raw < 0.12 && ++frames <= 90) {
            if (raw > 0.034) slow++;
            if (frames === 90 && slow > 45) { quality = 0.7; bloom.enabled = false; resize(); }
        }
        requestAnimationFrame(frame);
    }

    resize();
    addEventListener("scroll", readScroll, { passive: true });
    if (REDUCED) {
        monitorStill();
        still();
        addEventListener("resize", () => { resize(); still(); });
    } else {
        addEventListener("resize", resize);
        addEventListener("pointermove", (e) => { mxT = e.clientX / innerWidth - 0.5; myT = e.clientY / innerHeight - 0.5; }, { passive: true });
        requestAnimationFrame(frame);
    }
    root.classList.add("gl-ready");
}

start().catch((err) => {
    console.error("Operating-room scene failed to start:", err);
    root.classList.add("no-gl");
});
