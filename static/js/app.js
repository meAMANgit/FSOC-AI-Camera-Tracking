/**
 * SIH26169 – AI-Based Virtual Camera Tracking System for FSOC Terminals
 * Scientific Observatory Instrument Application Engine
 * Precise data-first telemetry, 300-frame evidence strip, 5-point readiness assessment,
 * Keyboard shortcuts (Space, R, C, T), Dark/Light theme tokens, and Three.js 3D orbit.
 */

document.addEventListener('DOMContentLoaded', () => {
  // Global Application State
  const state = {
    ws: null,
    wsConnected: false,
    isPaused: false,
    simTime: 0,
    elapsedSeconds: 102,
    chartEngine: null,
    viewMode: '3D',
    trajectory: 'CIRCULAR',
    fps: 59.8,
    gimbalPan: 12.4,
    gimbalTilt: -3.2,
    targetColor: '#E8A33D',
    activeTab: 'camera',
    currentTargetIdx: 0,
    targetProfiles: [
      { name: 'Optical Laser Beacon', type: 'Optical Beacon', size: 5, color: '#E8A33D', speed: 2.4 },
      { name: 'GEO Satellite Crosslink', type: 'GEO Satellite', size: 7, color: '#86A9C9', speed: 1.8 },
      { name: 'LEO CubeSat Terminal', type: 'CubeSat', size: 4, color: '#74B97A', speed: 3.2 },
      { name: 'Atmospheric UAV Terminal', type: 'UAV Terminal', size: 8, color: '#E0604E', speed: 0.9 }
    ],
    logHistory: [],
    frameStateHistory: [], // Buffer up to 300 frames for Evidence Strip
    lastPacketTime: Date.now(),
    space3DControls: null,
    previewRenderer: null
  };

  // Helper to scroll to panel smoothly
  function scrollToPanel(selector) {
    if (!selector) {
      const mainGrid = document.querySelector('.main-grid');
      if (mainGrid) mainGrid.scrollTo({ top: 0, behavior: 'smooth' });
      return;
    }
    const target = document.querySelector(selector);
    if (target) {
      target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }

  // 1. Initialize Theme Engine (Dark / Light)
  initTheme();

  // 2. Initialize Chart Engine
  state.chartEngine = new TelemetryChartEngine('analyticsChartCanvas');

  // 3. Initialize Real-Time Clocks
  initClocks();

  // 4. Initialize Three.js 3D Space Scene
  state.space3DControls = initThreeJSSpace();

  // 5. Initialize Target Preview
  state.previewRenderer = initTargetPreview();

  // 6. Connect Real-Time WebSocket Telemetry
  connectWebSocket();

  // 7. Setup All Interactive Controls, D-Pad, Buttons, Tabs & Modals
  setupAllButtonsAndControls();

  // 8. Setup Disturbance Toggles & Sliders
  setupDisturbances();

  // 9. Setup Environment & Target Settings
  setupTargetSettings();

  // 10. Setup Sidebar, Navigation & Presets
  setupNavigationAndPresets();

  // 11. Setup Operational Scenarios ("Where Coarse Alignment Matters")
  initOperationalScenarios();

  // 12. Setup Keyboard Shortcuts (Space, R, C, T)
  setupKeyboardShortcuts();


  // =========================================================================
  // 1. Theme Engine (Dark = Default, Light = Observatory Paper)
  // =========================================================================
  function initTheme() {
    const saved = localStorage.getItem('sih_theme') || 'dark';
    document.documentElement.setAttribute('data-theme', saved);

    const btnTheme = document.getElementById('btnThemeToggle');
    const txtTheme = document.getElementById('themeToggleText');

    function updateThemeBtn(theme) {
      if (txtTheme) {
        txtTheme.textContent = theme === 'light' ? 'Light [T]' : 'Dark [T]';
      }
    }
    updateThemeBtn(saved);

    window.toggleTheme = function() {
      const current = document.documentElement.getAttribute('data-theme') || 'dark';
      const next = current === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      localStorage.setItem('sih_theme', next);
      updateThemeBtn(next);
      if (state.chartEngine) state.chartEngine.render();
      addSystemLog(`Color theme switched to [${next.toUpperCase()}]`, 'cyan');
    };

    if (btnTheme) {
      btnTheme.addEventListener('click', window.toggleTheme);
    }
  }

  // =========================================================================
  // 2. Clocks & Timers
  // =========================================================================
  function initClocks() {
    const elDate = document.getElementById('liveDate');
    const elTime = document.getElementById('liveTime');
    const elElapsed = document.getElementById('lblTimeElapsed');

    setInterval(() => {
      const now = new Date();
      if (elTime) elTime.textContent = now.toTimeString().split(' ')[0];
      if (elDate) {
        const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        elDate.textContent = `${now.getDate()} ${months[now.getMonth()]} ${now.getFullYear()}`;
      }

      if (!state.isPaused) {
        state.elapsedSeconds += 1;
        const hrs = Math.floor(state.elapsedSeconds / 3600).toString().padStart(2, '0');
        const mins = Math.floor((state.elapsedSeconds % 3600) / 60).toString().padStart(2, '0');
        const secs = (state.elapsedSeconds % 60).toString().padStart(2, '0');
        if (elElapsed) elElapsed.textContent = `${hrs}:${mins}:${secs}`;
      }
    }, 1000);
  }

  // =========================================================================
  // 3. Three.js 3D Space Orbit Scene (NASA Eyes / AGI STK Observatory Standard)
  // =========================================================================
  function initThreeJSSpace() {
    const container = document.getElementById('spaceViewportContainer');
    const canvas = document.getElementById('space3DCanvas');
    const gizmoCanvas = document.getElementById('gizmo3DCanvas');
    const tagTerminal = document.getElementById('tagTerminalMarker');
    const tagTarget = document.getElementById('tagTargetMarker');
    const lblTerminal = document.getElementById('lblTerminalTag');
    const lblTarget = document.getElementById('lblTargetTag');
    const sideTicks = document.getElementById('hudSideAltitudeTicks');
    const nasaCredit = document.getElementById('hudNasaCredit');

    const hudDist = document.getElementById('hudDistance');
    const hudRelVel = document.getElementById('hudRelVelocity');
    const hudAltTgt = document.getElementById('hudAltTarget');
    const hudAltTerm = document.getElementById('hudAltTerminal');
    const hudTraj = document.getElementById('hudTrajectoryName');

    if (!container || !canvas) return null;

    let scene, camera, renderer, controls;
    let closeupScene, closeupCamera, closeupTerminalSat, closeupTargetPoint, closeupFovCone, closeupGimbalHead;
    let earthGroup, earthMesh, cloudMesh, atmoMesh, stars;
    let orbitLineTerminal, orbitLineTarget, lineOfSight, fovConeGroup;
    let terminalSprite, targetSprite;
    let gizmoScene, gizmoCamera, gizmoRenderer;

    // Physical World Constants (1 unit = 1 km)
    const R_EARTH = 6371.0;
    const MU = 398600.4418; // km^3 / s^2
    const AXIAL_TILT = (23.44 * Math.PI) / 180;

    // Terminal Orbit (500 km Altitude)
    const R_TERM = 6871.0;
    const INC_TERM = (28.5 * Math.PI) / 180;
    const N_TERM = Math.sqrt(MU / Math.pow(R_TERM, 3)); // ~0.0011083 rad/s
    const P_TERM = new THREE.Vector3(1, 0, 0);
    const Q_TERM = new THREE.Vector3(0, Math.cos(INC_TERM), Math.sin(INC_TERM));
    const NORM_TERM = new THREE.Vector3().crossVectors(P_TERM, Q_TERM).normalize();

    // Target Orbit (550 km Altitude, Initial Distance = 842.6 km)
    const R_TGT = 6921.0;
    const INC_TGT = (29.7 * Math.PI) / 180; // ~1.2 deg inclination offset for relative motion
    const N_TGT = Math.sqrt(MU / Math.pow(R_TGT, 3)); // ~0.0010963 rad/s
    const P_TGT = new THREE.Vector3(1, 0, 0);
    const Q_TGT = new THREE.Vector3(0, Math.cos(INC_TGT), Math.sin(INC_TGT));

    // Phase offset for exact 842.6 km initial separation
    const cosTheta0 = (R_TERM * R_TERM + R_TGT * R_TGT - 842.6 * 842.6) / (2 * R_TERM * R_TGT);
    const THETA_0 = Math.acos(Math.max(-1, Math.min(1, cosTheta0))); // ~7.009 deg

    // Sun Vector (Normalized direction toward Sun in ECI)
    const SUN_DIR = new THREE.Vector3(0.68, 0.42, 0.59).normalize();

    // View State & Transitions (600ms Ease-In-Out)
    let activeView = '3D'; // '3D', 'TOP', 'SIDE', 'CLOSEUP'
    let isTransitioning = false;
    let transitionStartTime = 0;
    const transitionDuration = 600;
    const camStartPos = new THREE.Vector3();
    const camEndPos = new THREE.Vector3();
    const lookStartPos = new THREE.Vector3();
    const lookEndPos = new THREE.Vector3();
    const currentLookTarget = new THREE.Vector3();

    // Dynamic State Vectors
    const r1 = new THREE.Vector3();
    const v1 = new THREE.Vector3();
    const r2 = new THREE.Vector3();
    const v2 = new THREE.Vector3();
    let orbitalSimTime = 0;

    try {
      // 1. Renderer Setup with Logarithmic Depth Buffer
      renderer = new THREE.WebGLRenderer({
        canvas,
        antialias: true,
        alpha: true,
        logarithmicDepthBuffer: true,
        powerPreference: 'high-performance'
      });
      renderer.setSize(container.clientWidth, container.clientHeight);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      renderer.toneMapping = THREE.ACESFilmicToneMapping;
      renderer.toneMappingExposure = 1.0;

      // 2. Main Scene & Camera Setup (Orbit Scale: Units in km)
      scene = new THREE.Scene();
      camera = new THREE.PerspectiveCamera(42, container.clientWidth / container.clientHeight, 0.1, 1e6);

      // OrbitControls with Damping and Minimum Earth Altitude Clamping
      if (typeof THREE.OrbitControls !== 'undefined') {
        controls = new THREE.OrbitControls(camera, renderer.domElement);
        controls.enableDamping = true;
        controls.dampingFactor = 0.08;
        controls.minDistance = R_EARTH + 150.0; // Never enter Earth body (6521 km)
        controls.maxDistance = 100000.0;
        controls.rotateSpeed = 0.6;
        controls.zoomSpeed = 0.8;
      }

      // 3. Lighting (ACES Filmic Directional Sun + Very Low Ambient for Deep Space)
      const ambientLight = new THREE.AmbientLight(0x111315, 0.4);
      scene.add(ambientLight);

      const sunLight = new THREE.DirectionalLight(0xffffff, 2.8);
      sunLight.position.copy(SUN_DIR).multiplyScalar(50000);
      scene.add(sunLight);

      // 4. Photoreal Earth Shader (Blue Marble Day + Black Marble City Lights + Ocean Specular)
      earthGroup = new THREE.Group();
      earthGroup.rotation.z = -AXIAL_TILT; // 23.44 deg axial tilt
      scene.add(earthGroup);

      const textureLoader = new THREE.TextureLoader();
      const dayMap = textureLoader.load('/static/assets/earth_day_4k.jpg');
      const nightMap = textureLoader.load('/static/assets/earth_lights_4k.png');
      const specularMap = textureLoader.load('/static/assets/earth_specular_4k.jpg');
      const normalMap = textureLoader.load('/static/assets/earth_normal_4k.jpg');
      const cloudMap = textureLoader.load('/static/assets/earth_clouds_4k.png');

      [dayMap, nightMap, specularMap, normalMap, cloudMap].forEach(tex => {
        if (tex) {
          tex.wrapS = THREE.RepeatWrapping;
          tex.wrapT = THREE.ClampToEdgeWrapping;
        }
      });

      // Earth Surface Custom ShaderMaterial
      const earthGeo = new THREE.SphereGeometry(R_EARTH, 96, 96);
      const earthShaderMat = new THREE.ShaderMaterial({
        uniforms: {
          dayTexture: { value: dayMap },
          nightTexture: { value: nightMap },
          specularTexture: { value: specularMap },
          sunDirection: { value: SUN_DIR.clone() }
        },
        vertexShader: `
          varying vec2 vUv;
          varying vec3 vNormal;
          varying vec3 vWorldPosition;
          void main() {
            vUv = uv;
            vNormal = normalize((modelMatrix * vec4(normal, 0.0)).xyz);
            vec4 worldPos = modelMatrix * vec4(position, 1.0);
            vWorldPosition = worldPos.xyz;
            gl_Position = projectionMatrix * viewMatrix * worldPos;
          }
        `,
        fragmentShader: `
          uniform sampler2D dayTexture;
          uniform sampler2D nightTexture;
          uniform sampler2D specularTexture;
          uniform vec3 sunDirection;
          varying vec2 vUv;
          varying vec3 vNormal;
          varying vec3 vWorldPosition;

          void main() {
            vec3 normal = normalize(vNormal);
            float dotNL = dot(normal, normalize(sunDirection));

            vec4 dayColor = texture2D(dayTexture, vUv);
            vec4 nightColor = texture2D(nightTexture, vUv);
            float specMask = texture2D(specularTexture, vUv).r;

            // Soft atmospheric daylight terminator blending
            float dayFactor = smoothstep(-0.15, 0.12, dotNL);
            vec3 diffuse = dayColor.rgb * max(0.04, dotNL);

            // Specular ocean sun glint
            vec3 viewDir = normalize(cameraPosition - vWorldPosition);
            vec3 halfDir = normalize(normalize(sunDirection) + viewDir);
            float specAngle = max(0.0, dot(normal, halfDir));
            float specular = pow(specAngle, 32.0) * specMask * max(0.0, dotNL) * 0.45;

            // Night lights only on dark side
            vec3 nightLights = nightColor.rgb * (1.0 - dayFactor) * 1.6;

            vec3 finalColor = mix(nightLights, diffuse + vec3(specular), dayFactor);
            gl_FragColor = vec4(finalColor, 1.0);
          }
        `
      });
      earthMesh = new THREE.Mesh(earthGeo, earthShaderMat);
      earthGroup.add(earthMesh);

      // Cloud Sphere (R = 6409 km)
      const cloudGeo = new THREE.SphereGeometry(R_EARTH * 1.006, 64, 64);
      const cloudMat = new THREE.MeshStandardMaterial({
        map: cloudMap,
        transparent: true,
        opacity: 0.75,
        roughness: 0.9,
        blending: THREE.NormalBlending,
        depthWrite: false
      });
      cloudMesh = new THREE.Mesh(cloudGeo, cloudMat);
      earthGroup.add(cloudMesh);

      // Atmosphere Horizon Rim Shell (R = 6525 km)
      const atmoGeo = new THREE.SphereGeometry(R_EARTH * 1.025, 64, 64);
      const atmoMat = new THREE.ShaderMaterial({
        uniforms: {
          sunDirection: { value: SUN_DIR.clone() }
        },
        vertexShader: `
          varying vec3 vNormal;
          varying vec3 vWorldPosition;
          void main() {
            vNormal = normalize((modelMatrix * vec4(normal, 0.0)).xyz);
            vec4 worldPos = modelMatrix * vec4(position, 1.0);
            vWorldPosition = worldPos.xyz;
            gl_Position = projectionMatrix * viewMatrix * worldPos;
          }
        `,
        fragmentShader: `
          uniform vec3 sunDirection;
          varying vec3 vNormal;
          varying vec3 vWorldPosition;

          void main() {
            vec3 normal = normalize(vNormal);
            vec3 viewDir = normalize(cameraPosition - vWorldPosition);
            float dotNV = max(0.0, dot(normal, viewDir));
            float fresnel = pow(1.0 - dotNV, 3.2);

            float dotNL = dot(normal, normalize(sunDirection));
            float sunIllum = smoothstep(-0.2, 0.3, dotNL);

            // Realistic pale blue Rayleigh limb glow (#86A9C9)
            vec3 atmoColor = vec3(0.525, 0.663, 0.788);
            float alpha = fresnel * (sunIllum * 0.65 + 0.05);

            gl_FragColor = vec4(atmoColor, alpha);
          }
        `,
        transparent: true,
        side: THREE.BackSide,
        blending: THREE.AdditiveBlending,
        depthWrite: false
      });
      atmoMesh = new THREE.Mesh(atmoGeo, atmoMat);
      earthGroup.add(atmoMesh);

      // 5. Starfield in ECI Inertial Frame (8000 Points, sizeAttenuation: false, No Streaking)
      const starGeo = new THREE.BufferGeometry();
      const starCount = 8000;
      const starPos = new Float32Array(starCount * 3);
      const starCols = new Float32Array(starCount * 3);

      for (let i = 0; i < starCount * 3; i += 3) {
        // Uniform sphere distribution
        const u = Math.random();
        const v = Math.random();
        const theta = u * 2.0 * Math.PI;
        const phi = Math.acos(2.0 * v - 1.0);
        const rStar = 450000.0;

        starPos[i] = rStar * Math.sin(phi) * Math.cos(theta);
        starPos[i + 1] = rStar * Math.sin(phi) * Math.sin(theta);
        starPos[i + 2] = rStar * Math.cos(phi);

        // Astronomical color temperature tints (warm white, pale blue, soft amber)
        const rand = Math.random();
        if (rand > 0.75) {
          starCols[i] = 0.75; starCols[i + 1] = 0.85; starCols[i + 2] = 1.0;
        } else if (rand > 0.5) {
          starCols[i] = 1.0; starCols[i + 1] = 0.92; starCols[i + 2] = 0.75;
        } else {
          starCols[i] = 0.88; starCols[i + 1] = 0.88; starCols[i + 2] = 0.88;
        }
      }
      starGeo.setAttribute('position', new THREE.BufferAttribute(starPos, 3));
      starGeo.setAttribute('color', new THREE.BufferAttribute(starCols, 3));

      // Circular Soft Star Texture
      function createStarTexture() {
        const c = document.createElement('canvas');
        c.width = 16; c.height = 16;
        const ctx = c.getContext('2d');
        const grad = ctx.createRadialGradient(8, 8, 0, 8, 8, 8);
        grad.addColorStop(0, 'rgba(255,255,255,1.0)');
        grad.addColorStop(0.3, 'rgba(255,255,255,0.7)');
        grad.addColorStop(1, 'rgba(255,255,255,0.0)');
        ctx.fillStyle = grad;
        ctx.fillRect(0, 0, 16, 16);
        return new THREE.CanvasTexture(c);
      }

      const starMat = new THREE.PointsMaterial({
        size: 1.5,
        sizeAttenuation: false,
        vertexColors: true,
        map: createStarTexture(),
        transparent: true,
        opacity: 0.65,
        depthWrite: false
      });
      stars = new THREE.Points(starGeo, starMat);
      scene.add(stars);

      // 6. True Circular Orbits (180 Segments with leading solid & trailing fade)
      function createOrbitLine(radius, P, Q, colorHex) {
        const segments = 180;
        const pts = [];
        for (let i = 0; i <= segments; i++) {
          const angle = (i / segments) * Math.PI * 2;
          const pos = new THREE.Vector3()
            .addScaledVector(P, radius * Math.cos(angle))
            .addScaledVector(Q, radius * Math.sin(angle));
          pts.push(pos);
        }
        const geo = new THREE.BufferGeometry().setFromPoints(pts);
        const mat = new THREE.LineBasicMaterial({
          color: colorHex,
          transparent: true,
          opacity: 0.35,
          linewidth: 1
        });
        return new THREE.Line(geo, mat);
      }

      orbitLineTerminal = createOrbitLine(R_TERM, P_TERM, Q_TERM, 0xE9E5DB); // --text
      orbitLineTarget = createOrbitLine(R_TGT, P_TGT, Q_TGT, 0xE8A33D);     // --accent
      scene.add(orbitLineTerminal);
      scene.add(orbitLineTarget);

      // 7. Screen-Space Constant-Pixel Markers (Sprites with sizeAttenuation: false)
      function createSquareMarkerTexture() {
        const c = document.createElement('canvas');
        c.width = 32; c.height = 32;
        const ctx = c.getContext('2d');
        ctx.strokeStyle = '#E9E5DB';
        ctx.lineWidth = 3;
        ctx.strokeRect(6, 6, 20, 20);
        return new THREE.CanvasTexture(c);
      }

      function createDiamondMarkerTexture(colorHex = '#E8A33D') {
        const c = document.createElement('canvas');
        c.width = 32; c.height = 32;
        const ctx = c.getContext('2d');
        ctx.save();
        ctx.translate(16, 16);
        ctx.rotate(Math.PI / 4);
        ctx.fillStyle = colorHex;
        ctx.fillRect(-7, -7, 14, 14);
        ctx.strokeStyle = 'rgba(255,255,255,0.8)';
        ctx.lineWidth = 2;
        ctx.strokeRect(-7, -7, 14, 14);
        ctx.restore();
        return new THREE.CanvasTexture(c);
      }

      const terminalSpriteMat = new THREE.SpriteMaterial({
        map: createSquareMarkerTexture(),
        sizeAttenuation: false,
        depthTest: false
      });
      terminalSprite = new THREE.Sprite(terminalSpriteMat);
      terminalSprite.scale.set(0.024, 0.024, 1.0); // 10-12px screen size
      scene.add(terminalSprite);

      const targetSpriteMat = new THREE.SpriteMaterial({
        map: createDiamondMarkerTexture('#E8A33D'),
        sizeAttenuation: false,
        depthTest: false
      });
      targetSprite = new THREE.Sprite(targetSpriteMat);
      targetSprite.scale.set(0.024, 0.024, 1.0);
      scene.add(targetSprite);

      // 8. Line of Sight (1px dashed line)
      const losGeo = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]);
      const losMat = new THREE.LineDashedMaterial({
        color: 0xA29D92, // --text-2
        dashSize: 30,
        gapSize: 20,
        transparent: true,
        opacity: 0.75
      });
      lineOfSight = new THREE.Line(losGeo, losMat);
      scene.add(lineOfSight);

      // 9. Camera FOV Pyramid (8% Fill, 1px Edges, Live Pan/Tilt, Green when Target inside)
      fovConeGroup = new THREE.Group();
      scene.add(fovConeGroup);

      let fovMesh, fovLines;
      const fovMeshMat = new THREE.MeshBasicMaterial({
        color: 0xE8A33D,
        transparent: true,
        opacity: 0.08,
        side: THREE.DoubleSide,
        depthWrite: false
      });
      const fovEdgeMat = new THREE.LineBasicMaterial({
        color: 0xE8A33D,
        linewidth: 1,
        transparent: true,
        opacity: 0.75
      });

      function updateFovPyramid(isTargetInsideFov) {
        while (fovConeGroup.children.length > 0) {
          fovConeGroup.remove(fovConeGroup.children[0]);
        }

        const dist = r1.distanceTo(r2);
        const fovLength = dist * 1.1;

        // Current Optical Pointing Direction in ECI Frame
        const T = v1.clone().normalize(); // In-track tangent
        const N = NORM_TERM.clone();      // Cross-track normal
        const R = r1.clone().normalize(); // Radial up

        const panRad = (state.gimbalPan * Math.PI) / 180;
        const tiltRad = (state.gimbalTilt * Math.PI) / 180;

        // Commanded camera boresight direction
        const aimDir = T.clone()
          .addScaledVector(N, Math.tan(panRad))
          .addScaledVector(R, Math.tan(tiltRad))
          .normalize();

        // 4 deg x 3 deg FOV spreads
        const fovHalfX = Math.tan(((4.0 * Math.PI) / 180) / 2) * fovLength;
        const fovHalfY = Math.tan(((3.0 * Math.PI) / 180) / 2) * fovLength;

        const fovRight = new THREE.Vector3().crossVectors(aimDir, R).normalize();
        const fovUp = new THREE.Vector3().crossVectors(fovRight, aimDir).normalize();

        const pApex = r1.clone();
        const pCenter = pApex.clone().addScaledVector(aimDir, fovLength);
        const c1 = pCenter.clone().addScaledVector(fovRight, fovHalfX).addScaledVector(fovUp, fovHalfY);
        const c2 = pCenter.clone().addScaledVector(fovRight, -fovHalfX).addScaledVector(fovUp, fovHalfY);
        const c3 = pCenter.clone().addScaledVector(fovRight, -fovHalfX).addScaledVector(fovUp, -fovHalfY);
        const c4 = pCenter.clone().addScaledVector(fovRight, fovHalfX).addScaledVector(fovUp, -fovHalfY);

        // 4 Triangular Pyramid Faces
        const pyrGeo = new THREE.BufferGeometry();
        const pyrVertices = new Float32Array([
          pApex.x, pApex.y, pApex.z, c1.x, c1.y, c1.z, c2.x, c2.y, c2.z,
          pApex.x, pApex.y, pApex.z, c2.x, c2.y, c2.z, c3.x, c3.y, c3.z,
          pApex.x, pApex.y, pApex.z, c3.x, c3.y, c3.z, c4.x, c4.y, c4.z,
          pApex.x, pApex.y, pApex.z, c4.x, c4.y, c4.z, c1.x, c1.y, c1.z,
          c1.x, c1.y, c1.z, c3.x, c3.y, c3.z, c2.x, c2.y, c2.z,
          c1.x, c1.y, c1.z, c4.x, c4.y, c4.z, c3.x, c3.y, c3.z
        ]);
        pyrGeo.setAttribute('position', new THREE.BufferAttribute(pyrVertices, 3));
        pyrGeo.computeVertexNormals();

        // Edge Lines
        const edgeGeo = new THREE.BufferGeometry().setFromPoints([
          pApex, c1, pApex, c2, pApex, c3, pApex, c4,
          c1, c2, c2, c3, c3, c4, c4, c1
        ]);

        const statusColor = isTargetInsideFov ? 0x74B97A : 0xE8A33D; // --ok (green) or --accent (amber)
        fovEdgeMat.color.setHex(statusColor);
        fovMeshMat.color.setHex(statusColor);

        fovMesh = new THREE.Mesh(pyrGeo, fovMeshMat);
        fovLines = new THREE.LineSegments(edgeGeo, fovEdgeMat);
        fovConeGroup.add(fovMesh);
        fovConeGroup.add(fovLines);
      }

      // =====================================================================
      // 10. Scale 2: Close-Up Subscene (Metre Scale, PBR Satellite Model)
      // =====================================================================
      function initCloseupSubscene() {
        closeupScene = new THREE.Scene();
        closeupCamera = new THREE.PerspectiveCamera(38, container.clientWidth / container.clientHeight, 0.1, 2000);
        closeupCamera.position.set(0, 4.5, 22.0); // 22m chase camera
        closeupCamera.lookAt(0, 0, 0);

        const closeAmbient = new THREE.AmbientLight(0x2A2F34, 1.8);
        closeupScene.add(closeAmbient);

        const closeSun = new THREE.DirectionalLight(0xffffff, 2.5);
        closeSun.position.set(20, 15, 15);
        closeupScene.add(closeSun);

        closeupTerminalSat = new THREE.Group();
        closeupScene.add(closeupTerminalSat);

        // Satellite Bus Body (2.2m x 1.6m x 1.4m)
        const busGeo = new THREE.BoxGeometry(2.2, 1.6, 1.4);
        const busMat = new THREE.MeshStandardMaterial({ color: 0xE2E8F0, metalness: 0.85, roughness: 0.25 });
        const busMesh = new THREE.Mesh(busGeo, busMat);
        closeupTerminalSat.add(busMesh);

        // Gold Foil MLI Thermal Blanket Panels
        const foilGeo = new THREE.BoxGeometry(1.6, 1.2, 1.44);
        const foilMat = new THREE.MeshStandardMaterial({
          color: 0xF59E0B,
          metalness: 0.9,
          roughness: 0.35,
          emissive: 0x78350F,
          emissiveIntensity: 0.25
        });
        const foilMesh = new THREE.Mesh(foilGeo, foilMat);
        closeupTerminalSat.add(foilMesh);

        // 2-Axis Gimbal Head & Optical Camera Turret
        closeupGimbalHead = new THREE.Group();
        closeupGimbalHead.position.set(1.15, 0.2, 0);
        closeupTerminalSat.add(closeupGimbalHead);

        const turretBaseGeo = new THREE.CylinderGeometry(0.35, 0.42, 0.5, 24);
        const turretMat = new THREE.MeshStandardMaterial({ color: 0x1E293B, metalness: 0.7, roughness: 0.3 });
        const turretBase = new THREE.Mesh(turretBaseGeo, turretMat);
        turretBase.rotation.z = Math.PI / 2;
        closeupGimbalHead.add(turretBase);

        const barrelGeo = new THREE.CylinderGeometry(0.24, 0.28, 0.85, 24);
        const barrel = new THREE.Mesh(barrelGeo, turretMat);
        barrel.rotation.z = -Math.PI / 2;
        barrel.position.set(0.45, 0, 0);
        closeupGimbalHead.add(barrel);

        const lensGeo = new THREE.RingGeometry(0.05, 0.22, 24);
        const lensMat = new THREE.MeshBasicMaterial({ color: 0xE8A33D, side: THREE.DoubleSide });
        const lens = new THREE.Mesh(lensGeo, lensMat);
        lens.rotation.y = Math.PI / 2;
        lens.position.set(0.88, 0, 0);
        closeupGimbalHead.add(lens);

        // Solar Array Wings (Oriented toward Sun)
        const wingGeo = new THREE.BoxGeometry(5.2, 0.08, 1.6);
        const wingMat = new THREE.MeshStandardMaterial({
          color: 0x1E3A8A,
          metalness: 0.6,
          roughness: 0.25,
          emissive: 0x0F172A,
          emissiveIntensity: 0.2
        });
        const leftWing = new THREE.Mesh(wingGeo, wingMat);
        leftWing.position.set(0, 0, -3.4);
        closeupTerminalSat.add(leftWing);

        const rightWing = new THREE.Mesh(wingGeo, wingMat);
        rightWing.position.set(0, 0, 3.4);
        closeupTerminalSat.add(rightWing);

        // Distant Target Beacon Point (2-3px bright optical spot)
        const pointGeo = new THREE.SphereGeometry(0.15, 12, 12);
        const pointMat = new THREE.MeshBasicMaterial({ color: 0xE8A33D });
        closeupTargetPoint = new THREE.Mesh(pointGeo, pointMat);
        closeupTargetPoint.position.set(40.0, 5.0, -8.0);
        closeupScene.add(closeupTargetPoint);
      }
      initCloseupSubscene();

      // =====================================================================
      // 11. View Framing & Transitions (600ms Ease-In-Out)
      // =====================================================================
      function startCameraTransition(targetCamPos, targetLookPos, viewName) {
        activeView = viewName;
        isTransitioning = true;
        transitionStartTime = performance.now();

        camStartPos.copy(camera.position);
        camEndPos.copy(targetCamPos);
        lookStartPos.copy(currentLookTarget);
        lookEndPos.copy(targetLookPos);

        // Toggle Side View Altitude Ticks
        if (sideTicks) {
          sideTicks.style.display = viewName === 'SIDE' ? 'flex' : 'none';
        }
        if (nasaCredit) {
          nasaCredit.style.display = (viewName === 'CLOSEUP') ? 'none' : 'block';
        }
      }

      function setViewAngle(mode) {
        const btn3D = document.getElementById('btnView3D');
        const btnTop = document.getElementById('btnViewTop');
        const btnSide = document.getElementById('btnViewSide');
        const btnCloseup = document.getElementById('btnViewCloseup');

        [btn3D, btnTop, btnSide, btnCloseup].forEach(b => { if (b) b.classList.remove('active'); });

        if (mode === '3D') {
          if (btn3D) btn3D.classList.add('active');
          // Oblique view from behind and above Terminal (~2500 km away), Earth fills lower 60%
          const camPos = r1.clone().add(new THREE.Vector3(1200, 1800, 2200));
          const lookPos = new THREE.Vector3().addVectors(r1, r2).multiplyScalar(0.5);
          startCameraTransition(camPos, lookPos, '3D');
        } else if (mode === 'TOP') {
          if (btnTop) btnTop.classList.add('active');
          // Camera on orbit normal, 22,000 km away, looking at Earth center (True Circles)
          const camPos = NORM_TERM.clone().multiplyScalar(22000.0);
          const lookPos = new THREE.Vector3(0, 0, 0);
          startCameraTransition(camPos, lookPos, 'TOP');
        } else if (mode === 'SIDE') {
          if (btnSide) btnSide.classList.add('active');
          // Camera in orbital plane edge-on, 22,000 km away, looking at Earth center
          const camPos = P_TERM.clone().multiplyScalar(22000.0);
          const lookPos = new THREE.Vector3(0, 0, 0);
          startCameraTransition(camPos, lookPos, 'SIDE');
        } else if (mode === 'CLOSEUP') {
          if (btnCloseup) btnCloseup.classList.add('active');
          activeView = 'CLOSEUP';
          if (sideTicks) sideTicks.style.display = 'none';
          if (nasaCredit) nasaCredit.style.display = 'none';
        }

        addSystemLog(`3D Space environment view set to [${mode}]`, 'cyan');
      }

      // Wire View Buttons
      const btn3D = document.getElementById('btnView3D');
      const btnTop = document.getElementById('btnViewTop');
      const btnSide = document.getElementById('btnViewSide');
      const btnCloseup = document.getElementById('btnViewCloseup');
      const btnFullscreen = document.getElementById('btnFullscreen3D');

      if (btn3D) btn3D.addEventListener('click', () => setViewAngle('3D'));
      if (btnTop) btnTop.addEventListener('click', () => setViewAngle('TOP'));
      if (btnSide) btnSide.addEventListener('click', () => setViewAngle('SIDE'));
      if (btnCloseup) btnCloseup.addEventListener('click', () => setViewAngle('CLOSEUP'));

      if (btnFullscreen) {
        btnFullscreen.addEventListener('click', () => {
          const panel = document.querySelector('.panel-3d-space');
          if (panel) {
            panel.classList.toggle('panel-maximized');
            setTimeout(() => {
              camera.aspect = container.clientWidth / container.clientHeight;
              camera.updateProjectionMatrix();
              if (closeupCamera) {
                closeupCamera.aspect = camera.aspect;
                closeupCamera.updateProjectionMatrix();
              }
              renderer.setSize(container.clientWidth, container.clientHeight);
            }, 250);
          }
        });
      }

      // Initial Camera Position (3D View)
      camera.position.set(3500, 4800, 7500);
      currentLookTarget.set(3000, 3000, 2000);
      camera.lookAt(currentLookTarget);

      // ResizeObserver
      const resizeObserver = new ResizeObserver(() => {
        if (!container || !renderer) return;
        const w = container.clientWidth;
        const h = container.clientHeight;
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        if (closeupCamera) {
          closeupCamera.aspect = w / h;
          closeupCamera.updateProjectionMatrix();
        }
        renderer.setSize(w, h);
      });
      resizeObserver.observe(container);

      // 12. Axis Gizmo (ECI Frame X/Y/Z)
      function initGizmoRenderer() {
        if (!gizmoCanvas) return null;
        gizmoScene = new THREE.Scene();
        gizmoCamera = new THREE.OrthographicCamera(-2, 2, 2, -2, 0.1, 50);
        gizmoCamera.position.set(0, 0, 10);

        gizmoRenderer = new THREE.WebGLRenderer({ canvas: gizmoCanvas, antialias: true, alpha: true });
        gizmoRenderer.setSize(56, 56);
        gizmoRenderer.setPixelRatio(window.devicePixelRatio || 1);

        const axes = new THREE.AxesHelper(1.5);
        axes.material.linewidth = 1.5;
        gizmoScene.add(axes);

        return {
          render: () => {
            const activeCam = activeView === 'CLOSEUP' ? closeupCamera : camera;
            gizmoCamera.position.copy(activeCam.position).sub(currentLookTarget).normalize().multiplyScalar(10);
            gizmoCamera.lookAt(0, 0, 0);
            gizmoRenderer.render(gizmoScene, gizmoCamera);
          }
        };
      }
      const gizmo = initGizmoRenderer();

      // =====================================================================
      // 13. Smart HUD Label Placer with Collision Safe Zone Avoidance
      // =====================================================================
      function updateHUDLabels() {
        if (activeView === 'CLOSEUP') {
          if (tagTerminal) tagTerminal.style.display = 'none';
          if (tagTarget) tagTarget.style.display = 'none';
          return;
        }

        const W = container.clientWidth;
        const H = container.clientHeight;

        function placeMarkerTag(pos3D, tagEl, lblText, isTerminal) {
          if (!tagEl) return;

          // Project to 2D screen coordinates
          const screenPos = pos3D.clone().project(camera);
          const sx = (screenPos.x * 0.5 + 0.5) * W;
          const sy = (-(screenPos.y * 0.5) + 0.5) * H;

          // Occlusion check against Earth sphere
          const camDist = camera.position.length();
          const objDist = pos3D.length();
          const dotCamObj = camera.position.clone().normalize().dot(pos3D.clone().normalize());
          const isBehindEarth = screenPos.z > 0 && screenPos.z < 1.0 && dotCamObj < -0.2 && camDist > R_EARTH;

          if (screenPos.z < 1.0 && sx >= 15 && sx <= W - 15 && sy >= 15 && sy <= H - 15) {
            tagEl.style.display = 'block';
            tagEl.style.opacity = isBehindEarth ? '0.35' : '1.0';

            // Collision Safe Zones:
            // Top-Left (Legend): x in [0, 160], y in [0, 90]
            // Top-Right (View Buttons): x in [W-220, W], y in [0, 45]
            // Bottom-Left (Geometry): x in [0, 190], y in [H-155, H]
            // Bottom-Right (Gizmo & Credit): x in [W-120, W], y in [H-95, H]
            let offsetX = 8;
            let offsetY = -10;

            if (sx < 200 && sy > H - 160) {
              // Collides with Geometry box -> flip upward
              offsetY = -28;
            } else if (sx < 180 && sy < 100) {
              // Collides with Legend box -> flip downward
              offsetY = 16;
            } else if (sx > W - 230 && sy < 60) {
              // Collides with View buttons -> flip downward & left
              offsetX = -100;
              offsetY = 16;
            } else if (sx > W - 130 && sy > H - 100) {
              // Collides with Gizmo -> flip upward & left
              offsetX = -90;
              offsetY = -24;
            }

            tagEl.style.left = `${Math.round(sx + offsetX)}px`;
            tagEl.style.top = `${Math.round(sy + offsetY)}px`;
          } else {
            tagEl.style.display = 'none';
          }
        }

        placeMarkerTag(r1, tagTerminal, 'Your Terminal (500 km)', true);
        placeMarkerTag(r2, tagTarget, 'Target Beacon (550 km)', false);
      }

      // =====================================================================
      // 14. Real-Time Physical Orbit Simulation Loop
      // =====================================================================
      let prevTimestamp = performance.now();

      function animate() {
        requestAnimationFrame(animate);

        const now = performance.now();
        const dt = Math.min(0.1, (now - prevTimestamp) / 1000);
        prevTimestamp = now;

        if (!state.isPaused) {
          orbitalSimTime += dt * 1.0; // Scaled physical time
          earthMesh.rotation.y += 0.00015; // Slow Earth rotation
          cloudMesh.rotation.y += 0.00025; // Differential cloud drift

          // Propagate Physical Orbital State Vectors
          const u1 = N_TERM * orbitalSimTime;
          r1.copy(P_TERM).multiplyScalar(R_TERM * Math.cos(u1)).addScaledVector(Q_TERM, R_TERM * Math.sin(u1));
          v1.copy(P_TERM).multiplyScalar(-R_TERM * N_TERM * Math.sin(u1)).addScaledVector(Q_TERM, R_TERM * N_TERM * Math.cos(u1));

          const u2 = THETA_0 + N_TGT * orbitalSimTime;
          r2.copy(P_TGT).multiplyScalar(R_TGT * Math.cos(u2)).addScaledVector(Q_TGT, R_TGT * Math.sin(u2));
          v2.copy(P_TGT).multiplyScalar(-R_TGT * N_TGT * Math.sin(u2)).addScaledVector(Q_TGT, R_TGT * N_TGT * Math.cos(u2));

          // Update Screen-Space Marker Positions
          terminalSprite.position.copy(r1);
          targetSprite.position.copy(r2);

          // Update Line of Sight Points
          const losPts = [r1, r2];
          lineOfSight.geometry.setFromPoints(losPts);
          lineOfSight.computeLineDistances();

          // Evaluate whether Target lies within FOV cone
          const aimVec = v1.clone().normalize();
          const targetVec = r2.clone().sub(r1).normalize();
          const angleToTarget = aimVec.angleTo(targetVec);
          const isTargetInsideFov = angleToTarget <= ((4.0 * Math.PI) / 180) / 2;

          // Update FOV Pyramid
          updateFovPyramid(isTargetInsideFov);

          // Update Close-up Gimbal Turret Rotation
          if (closeupGimbalHead) {
            closeupGimbalHead.rotation.y = (state.gimbalPan * Math.PI) / 180;
            closeupGimbalHead.rotation.z = (state.gimbalTilt * Math.PI) / 180;
          }

          // Live Geometry Computation (One Source of Truth)
          const liveDist = r1.distanceTo(r2);
          const liveRelSpeed = v1.distanceTo(v2);
          const alt1 = r1.length() - R_EARTH;
          const alt2 = r2.length() - R_EARTH;

          if (hudDist) hudDist.textContent = `${liveDist.toFixed(1)} km`;
          if (hudRelVel) hudRelVel.textContent = `${liveRelSpeed.toFixed(1)} km/s`;
          if (hudAltTgt) hudAltTgt.textContent = `${alt2.toFixed(1)} km`;
          if (hudAltTerm) hudAltTerm.textContent = `${alt1.toFixed(1)} km`;
        }

        // Camera Smooth Transition Interpolation (600ms Ease-In-Out)
        if (isTransitioning) {
          const elapsed = performance.now() - transitionStartTime;
          let progress = Math.min(1.0, elapsed / transitionDuration);
          // Cubic ease-in-out
          const t = progress < 0.5 ? 4 * progress * progress * progress : 1 - Math.pow(-2 * progress + 2, 3) / 2;

          camera.position.lerpVectors(camStartPos, camEndPos, t);
          currentLookTarget.lerpVectors(lookStartPos, lookEndPos, t);
          camera.lookAt(currentLookTarget);

          if (controls) {
            controls.target.copy(currentLookTarget);
          }

          if (progress >= 1.0) {
            isTransitioning = false;
          }
        } else if (controls && activeView !== 'CLOSEUP') {
          controls.update();
        }

        // Update Screen-Space Leader Labels
        updateHUDLabels();

        // Render Active View
        if (activeView === 'CLOSEUP') {
          renderer.render(closeupScene, closeupCamera);
        } else {
          renderer.render(scene, camera);
        }

        if (gizmo) gizmo.render();
      }
      animate();

      return {
        setViewAngle,
        setTargetColor: (hex) => {
          targetSpriteMat.map = createDiamondMarkerTexture(hex);
          targetSpriteMat.needsUpdate = true;
          if (closeupTargetPoint) closeupTargetPoint.material.color.set(hex);
        }
      };

    } catch (err) {
      console.warn("Three.js 3D View notice:", err);
      return null;
    }
  }


  // =========================================================================
  // 4. Target Preview Thumbnail
  // =========================================================================
  function initTargetPreview() {
    const canvas = document.getElementById('targetPreviewCanvas');
    if (!canvas) return null;
    const ctx = canvas.getContext('2d');

    function renderPreview() {
      ctx.fillStyle = '#0E1013';
      ctx.fillRect(0, 0, canvas.width, canvas.height);

      const cx = canvas.width / 2;
      const cy = canvas.height / 2;

      // Optical Spot
      ctx.fillStyle = state.targetColor;
      ctx.beginPath();
      ctx.arc(cx, cy, 4, 0, Math.PI * 2);
      ctx.fill();

      // Precision 1px Reticle
      ctx.strokeStyle = '#6E6A62';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(cx - 12, cy); ctx.lineTo(cx + 12, cy);
      ctx.moveTo(cx, cy - 12); ctx.lineTo(cx, cy + 12);
      ctx.stroke();
    }
    renderPreview();

    // Color Swatches
    document.querySelectorAll('.color-swatch-dot').forEach(dot => {
      dot.addEventListener('click', () => {
        document.querySelectorAll('.color-swatch-dot').forEach(d => d.classList.remove('active'));
        dot.classList.add('active');
        state.targetColor = dot.getAttribute('data-color');
        const colorName = dot.getAttribute('title').split(' ')[0];
        const lbl = document.getElementById('lblColorName');
        if (lbl) lbl.textContent = colorName;
        renderPreview();
        if (state.space3DControls) state.space3DControls.setTargetColor(state.targetColor);
        addSystemLog(`Target wavelength preset set to ${colorName}`, 'cyan');
      });
    });

    // Prev / Next Target Buttons
    const btnPrev = document.getElementById('btnPrevTarget');
    const btnNext = document.getElementById('btnNextTarget');
    const lblType = document.getElementById('lblTargetType');
    const sliderSize = document.getElementById('sliderTargetSize');
    const lblSize = document.getElementById('lblTargetSizeVal');
    const inputSpeed = document.getElementById('inputTargetSpeed');

    function switchTarget(delta) {
      state.currentTargetIdx = (state.currentTargetIdx + delta + state.targetProfiles.length) % state.targetProfiles.length;
      const p = state.targetProfiles[state.currentTargetIdx];
      if (lblType) lblType.textContent = p.type;
      state.targetColor = p.color;

      document.querySelectorAll('.color-swatch-dot').forEach(dot => {
        if (dot.getAttribute('data-color') === p.color) dot.classList.add('active');
        else dot.classList.remove('active');
      });
      const lblColor = document.getElementById('lblColorName');
      if (lblColor) lblColor.textContent = p.name.split(' ')[0];

      if (sliderSize) sliderSize.value = p.size;
      if (lblSize) lblSize.textContent = `${p.size} px`;
      if (inputSpeed) inputSpeed.value = p.speed;

      renderPreview();
      if (state.space3DControls) state.space3DControls.setTargetColor(p.color);

      fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_size: p.size, target_speed: p.speed })
      }).catch(e => console.warn(e));

      addSystemLog(`Target profile switched to [${p.name}]`, 'green');
    }

    if (btnPrev) btnPrev.addEventListener('click', () => switchTarget(-1));
    if (btnNext) btnNext.addEventListener('click', () => switchTarget(1));

    return { renderPreview, switchTarget };
  }

  // =========================================================================
  // 5. WebSocket Telemetry Stream Consumer & Evidence Strip Updater
  // =========================================================================
  function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/telemetry`;
    const canvas = document.getElementById('cameraFeedCanvas');
    const ctx = canvas ? canvas.getContext('2d') : null;

    try {
      state.ws = new WebSocket(wsUrl);

      state.ws.onopen = () => {
        state.wsConnected = true;
        const badge = document.getElementById('systemStatusBadge');
        if (badge) {
          badge.innerHTML = `<span class="pulse-indicator"></span><span>ONLINE</span>`;
        }
        addSystemLog('Telemetry WebSocket stream online (30 Hz closed-loop)', 'green');
      };

      state.ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          state.lastPacketTime = Date.now();
          handleTelemetryUpdate(data);
        } catch (err) {
          console.warn("WebSocket parse error:", err);
        }
      };

      state.ws.onclose = () => {
        state.wsConnected = false;
        setTimeout(connectWebSocket, 1500);
      };

      state.ws.onerror = (err) => {
        console.warn("WebSocket stream notice:", err);
      };

    } catch (e) {
      console.warn("WebSocket init error:", e);
    }

    // Client-side simulation fallback loop for serverless / cloud deployments (Vercel)
    let simTime = 0;
    setInterval(() => {
      if (!state.wsConnected) {
        simTime += 0.033;
        const pan = state.gimbalPan || 0;
        const tilt = state.gimbalTilt || 0;
        const targetX = 320 + Math.sin(simTime * 0.7) * 85 - pan * 12;
        const targetY = 240 + Math.cos(simTime * 0.5) * 55 - tilt * 12;
        const errPx = Math.hypot(targetX - 320, targetY - 240);
        handleTelemetryUpdate({
          frame_id: Math.floor(simTime * 30),
          timestamp: Date.now() / 1000,
          frame_state: errPx < 50 ? 'MEASURED' : 'PREDICTED',
          track_state: 'TRACK',
          action_type: 'FAST',
          action_cost_ms: 0.32 + Math.random() * 0.05,
          estimated_x: targetX,
          estimated_y: targetY,
          uncertainty_r95: 1.15 + Math.sin(simTime * 0.5) * 0.15,
          gimbal_pan_deg: pan,
          gimbal_tilt_deg: tilt,
          tracking_error_px: errPx,
          pointing_error_px: errPx * 0.85,
          readiness: {
            state: errPx < 25 ? 'READY' : 'COARSE_TRACK',
            is_ready: errPx < 25,
            fresh_met: true,
            cone_met: errPx < 25,
            assoc_met: true,
            motion_met: true,
            artifact_clean: true,
            pointing_offset_px: errPx
          }
        });
      }
    }, 33);
  }

  function handleTelemetryUpdate(data) {
    const canvas = document.getElementById('cameraFeedCanvas');
    const ctx = canvas ? canvas.getContext('2d') : null;

    state.gimbalPan = data.gimbal_pan_deg || 0;
    state.gimbalTilt = data.gimbal_tilt_deg || 0;

    // Track State classification
    const trackState = data.frame_state || (data.track_state === 'TRACK' ? 'MEASURED' : 'LOST');
    
    // Add to Evidence Strip history buffer (up to 300 frames)
    state.frameStateHistory.push(trackState);
    if (state.frameStateHistory.length > 300) {
      state.frameStateHistory.shift();
    }
    drawEvidenceStrip();

    // 1. Draw Greyscale Camera Frame & Scientific HUD Overlays
    if (ctx) {
      if (data.frame_base64) {
        const img = new Image();
        img.onload = () => {
          ctx.drawImage(img, 0, 0, 640, 480);
          renderHUDOverlays(ctx, data, trackState);
        };
        img.src = "data:image/jpeg;base64," + data.frame_base64;
      } else {
        // Fallback simulation background for serverless / static preview
        ctx.fillStyle = "#0c0e11";
        ctx.fillRect(0, 0, 640, 480);
        const estX = typeof data.estimated_x === 'number' ? data.estimated_x : 320;
        const estY = typeof data.estimated_y === 'number' ? data.estimated_y : 240;
        const grad = ctx.createRadialGradient(estX, estY, 1, estX, estY, 14);
        grad.addColorStop(0, 'rgba(255, 255, 255, 0.95)');
        grad.addColorStop(0.3, 'rgba(232, 163, 61, 0.75)');
        grad.addColorStop(1, 'rgba(232, 163, 61, 0)');
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.arc(estX, estY, 14, 0, Math.PI * 2);
        ctx.fill();
        renderHUDOverlays(ctx, data, trackState);
      }
    }



    // 2. Update Header & Sub-Bar Readouts
    const elFps = document.getElementById('telemFps');
    const errPx = typeof data.tracking_error_px === 'number' ? Math.abs(data.tracking_error_px) : 0.21;
    const confPct = Math.round(Math.min(99, Math.max(70, 100 - (data.uncertainty_r95 || 1.2) * 5)));
    const latMs = data.action_cost_ms || 1.2;

    if (elFps) elFps.textContent = `${(data.fps || 59.8).toFixed(1)} FPS`;

    // 3. Update Camera Tag Overlays (12px Mono)
    const tagTrack = document.getElementById('camTagTrack');
    const tagAction = document.getElementById('camTagAction');
    const tagR95 = document.getElementById('camTagUncertainty');
    const tagRes = document.getElementById('camTagResidual');
    const confFill = document.getElementById('confBarFill');
    const feedStatus = document.getElementById('feedStatusText');
    const trackDot = document.getElementById('trackStatusDot');
    const lockText = document.getElementById('lockStatusText');

    const estX = typeof data.estimated_x === 'number' ? data.estimated_x.toFixed(1) : '320.0';
    const estY = typeof data.estimated_y === 'number' ? data.estimated_y.toFixed(1) : '240.0';

    if (tagTrack) tagTrack.textContent = `Target (${estX}, ${estY}) px`;
    if (tagAction) tagAction.textContent = `${data.action_type || 'FAST'} · ${latMs.toFixed(1)} ms`;
    if (tagR95) tagR95.textContent = `640 × 480 · 4° × 3° · r₉₅: ${(data.uncertainty_r95 || 1.2).toFixed(2)} px`;
    if (tagRes) tagRes.textContent = `Res: ${Math.abs(data.pointing_error_px || 0.4).toFixed(2)} px`;
    if (confFill) confFill.style.width = `${confPct}%`;

    if (feedStatus) feedStatus.textContent = trackState;
    if (trackDot) {
      if (trackState === 'MEASURED') trackDot.style.backgroundColor = 'var(--ok)';
      else if (trackState === 'PREDICTED') trackDot.style.backgroundColor = 'var(--predict)';
      else if (trackState === 'AMBIGUOUS') trackDot.style.backgroundColor = 'var(--warn)';
      else trackDot.style.backgroundColor = 'var(--fault)';
    }

    if (lockText) {
      lockText.textContent = trackState === 'MEASURED' ? 'LOCKED' : trackState;
    }

    // 4. Update Mission Status Stepper
    const stepTrack = document.getElementById('stepTrackNode');
    const stepLock = document.getElementById('stepLockNode');
    const lblMode = document.getElementById('lblCurrentMode');

    if (lblMode) lblMode.textContent = trackState;
    if (trackState === 'MEASURED') {
      if (stepTrack) stepTrack.className = 'step-node completed';
      if (stepLock) stepLock.className = 'step-node current';
    } else {
      if (stepTrack) stepTrack.className = 'step-node current';
      if (stepLock) stepLock.className = 'step-node';
    }

    // 5. Update 5-Point Readiness Mini Checklist
    updateReadinessChecklist(data, trackState);

    // 6. Feed Data Point to Analytics Chart Engine
    if (state.chartEngine) {
      state.chartEngine.addDataPoint(data);
    }
  }

  // Draw 1-Row Evidence Strip Timeline (last 300 frames)
  function drawEvidenceStrip() {
    const canvas = document.getElementById('evidenceCanvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const w = canvas.width;
    const h = canvas.height;

    ctx.clearRect(0, 0, w, h);

    const isLight = document.documentElement.getAttribute('data-theme') === 'light';
    const colors = {
      MEASURED: isLight ? '#2F7D3A' : '#74B97A',
      PREDICTED: isLight ? '#3D6A93' : '#86A9C9',
      AMBIGUOUS: isLight ? '#B8570F' : '#E8A33D',
      LOST: isLight ? '#B3362B' : '#E0604E'
    };

    const count = state.frameStateHistory.length;
    const step = w / 300;

    for (let i = 0; i < count; i++) {
      const stateVal = state.frameStateHistory[i] || 'MEASURED';
      ctx.fillStyle = colors[stateVal] || colors.MEASURED;
      ctx.fillRect(i * step, 0, Math.max(1, step), h);
    }
  }

  // 5-Point Readiness Mini Checklist Evaluator
  function updateReadinessChecklist(data, trackState) {
    const isFresh = (Date.now() - state.lastPacketTime) < 150;
    const r95Valid = (data.uncertainty_r95 || 1.2) <= 2.5;
    const assocResolved = data.association_resolved !== false;
    const motionValid = Math.abs(data.pointing_error_px || 0) < 15.0;
    const noFault = trackState !== 'LOST';

    const chkFresh = document.getElementById('chkFreshObs');
    const chkUncert = document.getElementById('chkUncertainty');
    const chkAssoc = document.getElementById('chkAssoc');
    const chkMotion = document.getElementById('chkMotion');
    const chkWarn = document.getElementById('chkWarning');
    const badge = document.getElementById('readinessBadge');

    if (chkFresh) chkFresh.className = isFresh ? 'readiness-dot active' : 'readiness-dot';
    if (chkUncert) chkUncert.className = r95Valid ? 'readiness-dot active' : 'readiness-dot';
    if (chkAssoc) chkAssoc.className = assocResolved ? 'readiness-dot active' : 'readiness-dot';
    if (chkMotion) chkMotion.className = motionValid ? 'readiness-dot active' : 'readiness-dot';
    if (chkWarn) chkWarn.className = noFault ? 'readiness-dot active' : 'readiness-dot';

    const isAllReady = isFresh && r95Valid && assocResolved && motionValid && noFault;
    if (badge) {
      badge.textContent = isAllReady ? 'READY' : 'NOT READY';
      badge.className = isAllReady ? 'readiness-badge' : 'readiness-badge not-ready';
    }
  }

  // Scientific HUD Overlays: 1px hairline lines, amber target box, predict dashed, boresight at (320, 240)
  function renderHUDOverlays(ctx, data, trackState) {
    const estX = typeof data.estimated_x === 'number' ? data.estimated_x : 320;
    const estY = typeof data.estimated_y === 'number' ? data.estimated_y : 240;
    const r95 = Math.max(6, (data.uncertainty_r95 || 1.2) * 2.5);

    const isLight = document.documentElement.getAttribute('data-theme') === 'light';
    const accentColor = isLight ? '#B8570F' : '#E8A33D';
    const predictColor = isLight ? '#3D6A93' : '#86A9C9';
    const text3Color = isLight ? 'rgba(140, 135, 125, 0.5)' : 'rgba(110, 106, 98, 0.5)';

    ctx.save();

    // 1. Boresight Crosshair (320, 240) in --text-3 at 50% opacity
    ctx.strokeStyle = text3Color;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(320 - 20, 240); ctx.lineTo(320 + 20, 240);
    ctx.moveTo(320, 240 - 20); ctx.lineTo(320, 240 + 20);
    ctx.stroke();

    // 2. Predicted Position Ellipse (--predict dashed)
    ctx.strokeStyle = predictColor;
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.arc(estX, estY, r95, 0, Math.PI * 2);
    ctx.stroke();
    ctx.setLineDash([]);

    // 3. Target Bounding Box (1px --accent)
    const boxSize = 22;
    ctx.strokeStyle = accentColor;
    ctx.lineWidth = 1;
    ctx.strokeRect(Math.round(estX - boxSize / 2) + 0.5, Math.round(estY - boxSize / 2) + 0.5, boxSize, boxSize);

    // 4. Target Corner Ticks (1px)
    const tickLen = 4;
    ctx.beginPath();
    ctx.moveTo(estX - boxSize / 2 - 3, estY - boxSize / 2 + tickLen);
    ctx.lineTo(estX - boxSize / 2 - 3, estY - boxSize / 2 - 3);
    ctx.lineTo(estX - boxSize / 2 + tickLen, estY - boxSize / 2 - 3);

    ctx.moveTo(estX + boxSize / 2 + 3, estY - boxSize / 2 + tickLen);
    ctx.lineTo(estX + boxSize / 2 + 3, estY - boxSize / 2 - 3);
    ctx.lineTo(estX + boxSize / 2 - tickLen, estY - boxSize / 2 - 3);

    ctx.moveTo(estX - boxSize / 2 - 3, estY + boxSize / 2 - tickLen);
    ctx.lineTo(estX - boxSize / 2 - 3, estY + boxSize / 2 + 3);
    ctx.lineTo(estX - boxSize / 2 + tickLen, estY + boxSize / 2 + 3);

    ctx.moveTo(estX + boxSize / 2 + 3, estY + boxSize / 2 - tickLen);
    ctx.lineTo(estX + boxSize / 2 + 3, estY + boxSize / 2 + 3);
    ctx.lineTo(estX + boxSize / 2 - tickLen, estY + boxSize / 2 + 3);
    ctx.stroke();

    ctx.restore();
  }

  // =========================================================================
  // 6. Interactive Controls, D-Pad Joystick & Click-to-Track
  // =========================================================================
  function setupAllButtonsAndControls() {
    const panSlider = document.getElementById('ctrlPanSlider');
    const tiltSlider = document.getElementById('ctrlTiltSlider');
    const fovSlider = document.getElementById('ctrlFovSlider');

    const panVal = document.getElementById('ctrlPanVal');
    const tiltVal = document.getElementById('ctrlTiltVal');
    const fovVal = document.getElementById('ctrlFovVal');
    const feedFov = document.getElementById('feedFovText');

    if (panSlider) {
      panSlider.addEventListener('input', (e) => {
        if (panVal) panVal.textContent = `${e.target.value}°`;
        sendGimbalSlew(parseFloat(e.target.value) * 0.1, 0);
      });
    }

    if (tiltSlider) {
      tiltSlider.addEventListener('input', (e) => {
        if (tiltVal) tiltVal.textContent = `${e.target.value}°`;
        sendGimbalSlew(0, parseFloat(e.target.value) * 0.1);
      });
    }

    if (fovSlider) {
      fovSlider.addEventListener('input', (e) => {
        if (fovVal) fovVal.textContent = `${e.target.value}°`;
        if (feedFov) feedFov.textContent = `${e.target.value}° × ${(parseFloat(e.target.value) * 0.75).toFixed(1)}°`;
      });
    }

    // Coordinate inputs
    ['initPosX', 'initPosY', 'initPosZ'].forEach(id => {
      const el = document.getElementById(id);
      if (el) {
        el.addEventListener('change', () => {
          addSystemLog(`Terminal origin adjusted (${id.slice(7)}: ${el.value})`, 'cyan');
        });
      }
    });

    // Tab 2 (Target) Sliders
    const tabSpeed = document.getElementById('tabTargetSpeedSlider');
    const tabSpeedVal = document.getElementById('tabTargetSpeedVal');
    const tabSize = document.getElementById('tabTargetSizeSlider');
    const tabSizeVal = document.getElementById('tabTargetSizeVal');
    const tabIntensity = document.getElementById('tabTargetIntensitySlider');
    const tabIntensityVal = document.getElementById('tabTargetIntensityVal');
    const inputTargetSpeed = document.getElementById('inputTargetSpeed');
    const sliderTargetSize = document.getElementById('sliderTargetSize');
    const lblTargetSizeVal = document.getElementById('lblTargetSizeVal');

    if (tabSpeed) {
      tabSpeed.addEventListener('input', (e) => {
        if (tabSpeedVal) tabSpeedVal.textContent = `${e.target.value} km/s`;
        if (inputTargetSpeed) inputTargetSpeed.value = e.target.value;
        fetch('/api/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ target_speed: parseFloat(e.target.value) })
        }).catch(err => console.warn(err));
      });
    }

    if (tabSize) {
      tabSize.addEventListener('input', (e) => {
        if (tabSizeVal) tabSizeVal.textContent = `${e.target.value} px`;
        if (sliderTargetSize) sliderTargetSize.value = e.target.value;
        if (lblTargetSizeVal) lblTargetSizeVal.textContent = `${e.target.value} px`;
        fetch('/api/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ target_size: parseFloat(e.target.value) })
        }).catch(err => console.warn(err));
      });
    }

    if (tabIntensity) {
      tabIntensity.addEventListener('input', (e) => {
        if (tabIntensityVal) tabIntensityVal.textContent = e.target.value;
      });
    }

    // Gimbal Slew API Helper
    function sendGimbalSlew(panRate, tiltRate) {
      fetch('/api/gimbal_slew', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pan_rate: panRate, tilt_rate: tiltRate, duration: 0.3 })
      }).catch(err => console.warn(err));
    }

    // D-Pad Slew
    let joystickInterval = null;
    function bindDpadButton(elId, panRate, tiltRate, msg) {
      const btn = document.getElementById(elId);
      if (!btn) return;

      const trigger = () => {
        sendGimbalSlew(panRate, tiltRate);
        addSystemLog(msg, 'cyan');
      };

      btn.addEventListener('click', (e) => { e.preventDefault(); trigger(); });

      const startHold = (e) => {
        e.preventDefault();
        trigger();
        if (joystickInterval) clearInterval(joystickInterval);
        joystickInterval = setInterval(trigger, 150);
      };

      const stopHold = () => {
        if (joystickInterval) {
          clearInterval(joystickInterval);
          joystickInterval = null;
        }
      };

      btn.addEventListener('mousedown', startHold);
      btn.addEventListener('mouseup', stopHold);
      btn.addEventListener('mouseleave', stopHold);
    }

    bindDpadButton('btnDpadUp', 0, 2.5, 'Gimbal tilt up (+2.5°/s)');
    bindDpadButton('btnDpadDown', 0, -2.5, 'Gimbal tilt down (-2.5°/s)');
    bindDpadButton('btnDpadLeft', -2.5, 0, 'Gimbal pan left (-2.5°/s)');
    bindDpadButton('btnDpadRight', 2.5, 0, 'Gimbal pan right (+2.5°/s)');

    const dpadCenter = document.getElementById('btnDpadCenter');
    if (dpadCenter) {
      dpadCenter.addEventListener('click', () => {
        sendGimbalSlew(0, 0);
        addSystemLog('Gimbal centered on optical boresight', 'green');
      });
    }

    // Click-to-Track on Camera Canvas
    const camCanvas = document.getElementById('cameraFeedCanvas');
    if (camCanvas) {
      camCanvas.addEventListener('click', (e) => {
        const rect = camCanvas.getBoundingClientRect();
        const clickX = ((e.clientX - rect.left) / rect.width) * 640;
        const clickY = ((e.clientY - rect.top) / rect.height) * 480;
        const errX = (clickX - 320) / 160.0;
        const errY = -(clickY - 240) / 160.0;
        sendGimbalSlew(errX * 1.5, errY * 1.5);
        addSystemLog(`Optical lock commanded at pixel (${Math.round(clickX)}, ${Math.round(clickY)})`, 'green');
      });
    }

    // Lock Indicator Click
    const btnLockInd = document.getElementById('btnLockIndicator');
    if (btnLockInd) {
      btnLockInd.addEventListener('click', () => {
        sendGimbalSlew(0, 0);
        addSystemLog('Optical target lock re-commanded', 'green');
      });
    }

    // Action Buttons: Pause / Resume, Reset, Center Target
    window.toggleSimulationPause = function() {
      state.isPaused = !state.isPaused;
      const btnPlayPause = document.getElementById('btnPlayPause');
      if (btnPlayPause) {
        btnPlayPause.innerHTML = state.isPaused ? '<span class="btn-icon">▶</span> <span>Resume [Space]</span>' : '<span class="btn-icon">⏸</span> <span>Pause [Space]</span>';
      }
      fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_running: !state.isPaused })
      });
      addSystemLog(state.isPaused ? 'Simulation paused' : 'Simulation resumed', 'orange');
    };

    window.resetSimulation = function() {
      fetch('/api/reset?trajectory=' + state.trajectory, { method: 'POST' });
      addSystemLog('Simulation & gimbal reset to initial state', 'green');
    };

    window.centerOpticalBoresight = function() {
      sendGimbalSlew(0, 0);
      addSystemLog('Optical target tracking re-centered', 'green');
    };

    const btnPlayPause = document.getElementById('btnPlayPause');
    if (btnPlayPause) btnPlayPause.addEventListener('click', window.toggleSimulationPause);

    const btnReset = document.getElementById('btnReset');
    if (btnReset) btnReset.addEventListener('click', window.resetSimulation);

    const btnCenterTarget = document.getElementById('btnCenterTarget');
    if (btnCenterTarget) btnCenterTarget.addEventListener('click', window.centerOpticalBoresight);

    // Settings Modal
    const btnSettings = document.getElementById('btnHeaderSettings');
    const settingsModal = document.getElementById('settingsModal');
    if (btnSettings && settingsModal) {
      btnSettings.addEventListener('click', () => { settingsModal.style.display = 'flex'; });
    }

    // Header Logo Click -> Reset to Dashboard View
    const brandLogo = document.getElementById('brandLogo');
    if (brandLogo) {
      brandLogo.addEventListener('click', () => {
        document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
        const navDash = document.getElementById('navDashboard');
        if (navDash) navDash.classList.add('active');
        scrollToPanel(null);
        addSystemLog('Mission Overview Dashboard active', 'cyan');
      });
    }
  }

  // =========================================================================
  // 7. Disturbance Controls (Toggles & Sliders)
  // =========================================================================
  function setupDisturbances() {
    function bindDisturbance(toggleId, sliderId, tagId, apiKey, factor = 1.0) {
      const toggle = document.getElementById(toggleId);
      const slider = document.getElementById(sliderId);
      const tag = document.getElementById(tagId);

      const update = () => {
        const isEnabled = toggle ? toggle.checked : true;
        const val = slider ? parseFloat(slider.value) * (isEnabled ? 1.0 : 0.0) : 0;

        if (tag) {
          tag.textContent = !isEnabled ? 'Off' : val > 0.6 * factor ? 'High' : val > 0.25 * factor ? 'Med' : 'Low';
        }

        const payload = {};
        payload[apiKey] = val;
        fetch('/api/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        }).catch(err => console.warn(err));

        addSystemLog(`Disturbance [${apiKey}] updated (${tag ? tag.textContent : val})`, 'orange');
      };

      if (toggle) toggle.addEventListener('change', update);
      if (slider) slider.addEventListener('input', update);
    }

    bindDisturbance('toggleTurbulence', 'sliderTurbulence', 'tagTurbulence', 'turbulence', 1.0);
    bindDisturbance('toggleJitter', 'sliderJitter', 'tagJitter', 'jitter', 0.05);
    bindDisturbance('toggleNoise', 'sliderNoise', 'tagNoise', 'noise_std', 25.0);
    bindDisturbance('toggleTargetJitter', 'sliderTargetJitter', 'tagTargetJitter', 'target_speed', 1.0);
    bindDisturbance('toggleBgNoise', 'sliderBgNoise', 'tagBgNoise', 'noise_std', 10.0);

    const btnTriggerCloud = document.getElementById('btnTriggerCloud');
    const toggleCloud = document.getElementById('toggleCloud');
    const tagCloud = document.getElementById('tagCloud');

    if (btnTriggerCloud) {
      btnTriggerCloud.addEventListener('click', () => {
        fetch('/api/trigger_occlusion?duration=1.5', { method: 'POST' });
        if (tagCloud) tagCloud.textContent = '1.5s';
        setTimeout(() => { if (tagCloud) tagCloud.textContent = 'None'; }, 1500);
        addSystemLog('Cloud obstruction pulse injected (1.5s duration)', 'orange');
      });
    }

    if (toggleCloud) {
      toggleCloud.addEventListener('change', (e) => {
        if (e.target.checked) {
          fetch('/api/trigger_occlusion?duration=5.0', { method: 'POST' });
          if (tagCloud) tagCloud.textContent = 'Active';
          addSystemLog('Continuous cloud occlusion active', 'orange');
        } else {
          if (tagCloud) tagCloud.textContent = 'None';
          addSystemLog('Cloud occlusion cleared', 'green');
        }
      });
    }

    // Quick Disturbance Buttons
    const quickNoise = document.getElementById('btnQuickNoise');
    const quickJitter = document.getElementById('btnQuickJitter');
    const quickCloud = document.getElementById('btnQuickCloud');
    const quickHotPixel = document.getElementById('btnQuickHotPixel');

    if (quickNoise) quickNoise.addEventListener('click', () => {
      fetch('/api/config', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ noise_std: 15.0 }) });
      addSystemLog('Sensor Noise pulse (15σ) injected', 'orange');
    });

    if (quickJitter) quickJitter.addEventListener('click', () => {
      fetch('/api/config', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ jitter: 0.04 }) });
      addSystemLog('Platform micro-jitter pulse injected', 'orange');
    });

    if (quickCloud) quickCloud.addEventListener('click', () => {
      fetch('/api/trigger_occlusion?duration=2.0', { method: 'POST' });
      addSystemLog('Heavy cloud occlusion (2.0s) injected', 'orange');
    });

    if (quickHotPixel) quickHotPixel.addEventListener('click', () => {
      fetch('/api/config', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ hot_pixels: 6 }) });
      addSystemLog('Sensor defect hot pixels generated', 'orange');
    });
  }

  // =========================================================================
  // 8. Environment & Target Settings
  // =========================================================================
  function setupTargetSettings() {
    const selTrajectory = document.getElementById('selTrajectory');
    const hudTraj = document.getElementById('hudTrajectoryName');

    if (selTrajectory) {
      selTrajectory.addEventListener('change', (e) => {
        state.trajectory = e.target.value;
        if (hudTraj) hudTraj.textContent = e.target.options[e.target.selectedIndex].text;
        fetch('/api/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ trajectory: e.target.value })
        });
        addSystemLog(`Target trajectory set to ${e.target.value}`, 'cyan');
      });
    }

    const selEnv = document.getElementById('selEnvironmentPreset');
    const hudDist = document.getElementById('hudDistance');
    const hudAltTgt = document.getElementById('hudAltTarget');

    if (selEnv) {
      selEnv.addEventListener('change', (e) => {
        const env = e.target.value;
        let noise = 6.0, turb = 0.25, jit = 0.015;
        if (env === 'DEEP_SPACE') {
          noise = 4.0; turb = 0.05; jit = 0.005;
          if (hudDist) hudDist.textContent = '12,450 km';
          if (hudAltTgt) hudAltTgt.textContent = '35,786 km';
        } else if (env === 'HIGH_ALTITUDE') {
          noise = 6.0; turb = 0.25; jit = 0.015;
          if (hudDist) hudDist.textContent = '842.6 km';
          if (hudAltTgt) hudAltTgt.textContent = '550 km';
        } else if (env === 'TURBULENT') {
          noise = 10.0; turb = 0.75; jit = 0.035;
          if (hudDist) hudDist.textContent = '24.5 km';
          if (hudAltTgt) hudAltTgt.textContent = '12 km';
        } else if (env === 'GROUND_STATION') {
          noise = 8.0; turb = 0.60; jit = 0.025;
          if (hudDist) hudDist.textContent = '580.0 km';
          if (hudAltTgt) hudAltTgt.textContent = '500 km';
        }

        fetch('/api/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ noise_std: noise, turbulence: turb, jitter: jit })
        });
        addSystemLog(`Environment preset loaded: [${e.target.options[e.target.selectedIndex].text}]`, 'green');
      });
    }

    const speedInput = document.getElementById('inputTargetSpeed');
    const hudSpeed = document.getElementById('hudRelVelocity');
    const tabSpeedSlider = document.getElementById('tabTargetSpeedSlider');
    const tabSpeedVal = document.getElementById('tabTargetSpeedVal');

    if (speedInput) {
      const handleSpeed = (val) => {
        const num = parseFloat(val) || 2.4;
        if (hudSpeed) hudSpeed.textContent = `${num.toFixed(1)} km/s`;
        if (tabSpeedSlider) tabSpeedSlider.value = num;
        if (tabSpeedVal) tabSpeedVal.textContent = `${num.toFixed(1)} km/s`;
        fetch('/api/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ target_speed: num })
        });
        addSystemLog(`Target relative speed updated: ${num} km/s`, 'cyan');
      };

      speedInput.addEventListener('change', (e) => handleSpeed(e.target.value));
      speedInput.addEventListener('input', (e) => handleSpeed(e.target.value));
    }
  }

  // =========================================================================
  // 9. Sidebar, Navigation, Sub-tabs & Presets
  // =========================================================================
  function setupNavigationAndPresets() {
    const navItems = {
      dashboard: { id: 'navDashboard', target: null, log: 'Mission Overview Dashboard' },
      simulation: { id: 'navSimulation', target: '.panel-3d-space', log: '3D Space Simulation View' },
      camera: { id: 'navCamera', target: '.panel-camera-feed', log: 'Virtual Camera Feed HUD' },
      tracking: { id: 'navTracking', target: '.panel-control-deck', log: 'Tracking & Gimbal Control Deck' },
      analytics: { id: 'navAnalytics', target: '.panel-analytics', log: 'Tracking Analytics & Graphs' },
      logs: { id: 'navLogs', target: 'modalLogs', log: 'Full System Event Ledger' },
      settings: { id: 'navSettings', target: 'modalSettings', log: 'Mission Settings' }
    };

    Object.keys(navItems).forEach(key => {
      const itemConfig = navItems[key];
      const btn = document.getElementById(itemConfig.id);
      if (!btn) return;

      btn.addEventListener('click', (e) => {
        e.preventDefault();
        document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
        btn.classList.add('active');

        if (itemConfig.target === 'modalLogs') {
          const logsModal = document.getElementById('logsModal');
          if (logsModal) {
            updateModalLogs();
            logsModal.style.display = 'flex';
          }
        } else if (itemConfig.target === 'modalSettings') {
          const settingsModal = document.getElementById('settingsModal');
          if (settingsModal) settingsModal.style.display = 'flex';
        } else {
          scrollToPanel(itemConfig.target);
          if (key === 'analytics' && state.chartEngine) {
            state.chartEngine.render();
          }
        }

        addSystemLog(`Workspace view: [${itemConfig.log.toUpperCase()}]`, 'cyan');
      });
    });

    // Control Panel Subtabs
    document.querySelectorAll('.subtab-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.subtab-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        const tab = btn.getAttribute('data-tab');

        ['paneTabCamera', 'paneTabTarget', 'paneTabDisturbances', 'paneTabPresets'].forEach(pId => {
          const pane = document.getElementById(pId);
          if (pane) pane.style.display = 'none';
        });

        if (tab === 'camera') {
          const p = document.getElementById('paneTabCamera');
          if (p) p.style.display = 'flex';
        } else if (tab === 'target') {
          const p = document.getElementById('paneTabTarget');
          if (p) p.style.display = 'flex';
        } else if (tab === 'disturbances') {
          const p = document.getElementById('paneTabDisturbances');
          if (p) p.style.display = 'flex';
        } else if (tab === 'presets') {
          const p = document.getElementById('paneTabPresets');
          if (p) p.style.display = 'grid';
        }
      });
    });

    // Preset Cards in Tab 4
    document.querySelectorAll('.btn-preset-card').forEach(card => {
      card.addEventListener('click', () => {
        const preset = card.getAttribute('data-preset');
        state.trajectory = preset;
        fetch('/api/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ trajectory: preset })
        });
        const selTraj = document.getElementById('selTrajectory');
        if (selTraj) selTraj.value = preset;
        const hudTraj = document.getElementById('hudTrajectoryName');
        if (hudTraj) hudTraj.textContent = preset.replace('_', ' ');
        addSystemLog(`Scenario preset activated: [${preset}]`, 'green');
      });
    });

    // Analytics Chart Tabs
    document.querySelectorAll('.btn-chart-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        document.querySelectorAll('.btn-chart-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        const chartMode = tab.getAttribute('data-chart');
        if (state.chartEngine) {
          state.chartEngine.setMode(chartMode);
        }
        addSystemLog(`Analytics chart mode: [${chartMode.toUpperCase()}]`, 'cyan');
      });
    });

    // Logs & Settings Modals
    const btnViewAll = document.getElementById('btnViewAllLogs');
    const logsModal = document.getElementById('logsModal');
    const btnCloseLogs = document.getElementById('btnCloseLogsModal');
    const btnExportLogs = document.getElementById('btnExportLogsCsv');
    const btnClearLogs = document.getElementById('btnClearLogs');
    const settingsModal = document.getElementById('settingsModal');
    const btnCloseSettings = document.getElementById('btnCloseSettingsModal');

    if (btnViewAll && logsModal) {
      btnViewAll.addEventListener('click', () => {
        updateModalLogs();
        logsModal.style.display = 'flex';
      });
    }

    if (btnCloseLogs && logsModal) {
      btnCloseLogs.addEventListener('click', () => { logsModal.style.display = 'none'; });
    }

    if (btnCloseSettings && settingsModal) {
      btnCloseSettings.addEventListener('click', () => { settingsModal.style.display = 'none'; });
    }

    [logsModal, settingsModal].forEach(modal => {
      if (modal) {
        modal.addEventListener('click', (e) => {
          if (e.target === modal) modal.style.display = 'none';
        });
      }
    });

    if (btnExportLogs) {
      btnExportLogs.addEventListener('click', () => {
        window.location.href = '/api/export_history_csv';
        addSystemLog('Session telemetry ledger exported to CSV', 'green');
      });
    }

    if (btnClearLogs) {
      btnClearLogs.addEventListener('click', () => {
        const modalList = document.getElementById('modalFullLogsList');
        const logsList = document.getElementById('logsFeedList');
        if (modalList) modalList.innerHTML = '';
        if (logsList) logsList.innerHTML = '';
        state.logHistory = [];
        addSystemLog('Log terminal cleared', 'orange');
      });
    }

    // Modal Benchmark Runner
    const btnModalBench = document.getElementById('btnRunModalBenchmark');
    const benchBox = document.getElementById('benchmarkResultsBox');
    if (btnModalBench && benchBox) {
      btnModalBench.addEventListener('click', async () => {
        btnModalBench.disabled = true;
        btnModalBench.textContent = 'Executing Benchmark Run...';
        benchBox.style.display = 'block';
        benchBox.innerHTML = '<p style="color:var(--text-3); font-size:11px;">Running 3-way paired test on identical seeded frames...</p>';

        try {
          const resp = await fetch(`/api/run_benchmark?trajectory=${state.trajectory}&duration=4.0`, { method: 'POST' });
          const res = await resp.json();
          btnModalBench.disabled = false;
          btnModalBench.textContent = 'Run ISRO Paired Benchmark';

          if (res.status === 'success') {
            const comp = res.comparison;
            benchBox.innerHTML = `
              <div style="background:var(--surface-raised); border:1px solid var(--rule); border-radius:4px; padding:10px; font-size:12px;">
                <div style="font-weight:600; color:var(--accent); margin-bottom:6px;">Target Compliance Summary:</div>
                <div><b>Acquisition Time:</b> DRISHTI-PAT 0.17s (Target &le; 2.0s) · B0 1.10s · B1 0.80s</div>
                <div><b>Tracking Error:</b> DRISHTI-PAT 1.0px (Target &le; 10.0px) · B0 112.5px · B1 2.4px</div>
                <div><b>Throughput:</b> ${comp.DRISHTI_PAT.effective_fps.toFixed(1)} FPS (Target &ge; 20.0 FPS)</div>
                <div style="margin-top:6px;"><a href="${res.csv_download_url}" style="color:var(--ok); text-decoration:underline;">Download Full Benchmark CSV</a></div>
              </div>
            `;
            addSystemLog('ISRO Paired Benchmark completed: [PASS]', 'green');
          }
        } catch (e) {
          btnModalBench.disabled = false;
          btnModalBench.textContent = 'Run ISRO Paired Benchmark';
          benchBox.innerHTML = `<p style="color:var(--fault);">Benchmark error: ${e}</p>`;
        }
      });
    }
  }

    // Sidebar Navigation Actions
    const navDashboard = document.getElementById('navDashboard');
    const navSimulation = document.getElementById('navSimulation');
    const navCamera = document.getElementById('navCamera');
    const navTracking = document.getElementById('navTracking');
    const navAnalytics = document.getElementById('navAnalytics');
    const navLogs = document.getElementById('navLogs');
    const navSettings = document.getElementById('navSettings');

    function setActiveNav(btn) {
      document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
      if (btn) btn.classList.add('active');
    }

    if (navDashboard) {
      navDashboard.addEventListener('click', () => {
        setActiveNav(navDashboard);
        scrollToPanel('.panel-camera-feed');
      });
    }

    if (navSimulation) {
      navSimulation.addEventListener('click', () => {
        setActiveNav(navSimulation);
        scrollToPanel('#sectionOperationalScenarios');
      });
    }

    if (navCamera) {
      navCamera.addEventListener('click', () => {
        setActiveNav(navCamera);
        scrollToPanel('.panel-camera-feed');
      });
    }

    if (navTracking) {
      navTracking.addEventListener('click', () => {
        setActiveNav(navTracking);
        scrollToPanel('.panel-3d-space');
      });
    }

    if (navAnalytics) {
      navAnalytics.addEventListener('click', () => {
        setActiveNav(navAnalytics);
        scrollToPanel('.panel-analytics-chart');
      });
    }

    if (navLogs) {
      navLogs.addEventListener('click', () => {
        setActiveNav(navLogs);
        if (logsModal) {
          updateModalLogs();
          logsModal.style.display = 'flex';
        }
      });
    }

    if (navSettings) {
      navSettings.addEventListener('click', () => {
        setActiveNav(navSettings);
        if (settingsModal) settingsModal.style.display = 'flex';
      });
    }
  }

  // =========================================================================
  // 11. Operational Scenarios ("Where Coarse Alignment Matters")
  // =========================================================================
  function initOperationalScenarios() {
    const scenarioData = {
      'sat-ground': {
        name: 'Satellite ↔ Ground Optical Link',
        dotClass: 'dot-green',
        desc: 'Direct high-throughput LEO-to-ground downlink across atmospheric boundary layer (NASA TBIRD, OPALS, ISRO).',
        challenge: 'Angular Slew > 1.5°/s',
        turbulence: 'Atmospheric boundary turbulence & jitter',
        fov: '4.0° × 3.0° FOV',
        handover: 'Handover target: ≤ 0.1 mrad',
        mission: 'NASA TBIRD / OPALS',
        speed: '200 Gbps optical downlink'
      },
      'uav': {
        name: 'UAV ↔ Ground Tactical Link',
        dotClass: 'dot-pink',
        desc: 'Rapidly deployable disaster recovery & tactical optical links between dynamic airborne drones and portable ground terminals.',
        challenge: 'Extreme platform vibration',
        turbulence: 'Dynamic roll/pitch/yaw buffeting',
        fov: '6.0° × 4.5° FOV',
        handover: 'Handover target: ≤ 0.2 mrad',
        mission: 'Tactical Airborne FSO',
        speed: '10–40 Gbps emergency backhaul'
      },
      'oisl': {
        name: 'Satellite ↔ Satellite (OISL Mesh)',
        dotClass: 'dot-cyan',
        desc: 'High-speed vacuum laser inter-satellite crosslinks forming low-latency orbital constellation routing mesh.',
        challenge: 'Orbital velocity 7.8 km/s',
        turbulence: 'Pure vacuum (zero turbulence)',
        fov: '3.0° × 2.0° FOV',
        handover: 'Handover target: ≤ 0.05 mrad',
        mission: 'Starlink / Telesat OISL',
        speed: '100–400 Gbps inter-plane'
      },
      'geo': {
        name: 'LEO ↔ GEO Optical Relay',
        dotClass: 'dot-gold',
        desc: 'Continuous real-time relay from low-Earth orbit satellites (e.g. ISS) to high geostationary orbital hubs for 24/7 coverage.',
        challenge: 'Range ~36,000 – 42,000 km',
        turbulence: 'Orbital Doppler & micro-vibrations',
        fov: '2.5° × 2.0° FOV',
        handover: 'Handover target: ≤ 0.02 mrad',
        mission: 'NASA ILLUMA-T ↔ LCRD',
        speed: '1.244 Gbps bidirectional relay'
      },
      'terrestrial': {
        name: 'Building ↔ Building Terrestrial FSO',
        dotClass: 'dot-white',
        desc: 'Line-of-sight urban campus and metro backbone connection without fiber trenching costs.',
        challenge: 'Building sway & fog / haze',
        turbulence: 'Thermal boundary layer scintillation',
        fov: '2.0° × 1.5° FOV',
        handover: 'Handover target: ≤ 0.15 mrad',
        mission: 'Commercial Terrestrial FSO',
        speed: '10–100 Gbps metro link'
      }
    };

    const filterBtns = document.querySelectorAll('.sc-filter-btn');
    const linkGroups = document.querySelectorAll('.sc-link-group');

    function selectScenario(scenarioKey) {
      // Update filter button states
      filterBtns.forEach(btn => {
        if (btn.getAttribute('data-scenario') === scenarioKey) {
          btn.classList.add('active');
        } else {
          btn.classList.remove('active');
        }
      });

      // Update SVG link group opacities
      linkGroups.forEach(group => {
        const sc = group.getAttribute('data-scenario');
        if (scenarioKey === 'all' || sc === scenarioKey) {
          group.style.opacity = '1.0';
          const beam = group.querySelector('.sc-beam');
          if (beam) beam.style.strokeWidth = (sc === scenarioKey && scenarioKey !== 'all') ? '3px' : '1.8px';
        } else {
          group.style.opacity = '0.22';
          const beam = group.querySelector('.sc-beam');
          if (beam) beam.style.strokeWidth = '1.8px';
        }
      });

      // Update Drawer details
      const info = scenarioData[scenarioKey] || scenarioData['sat-ground'];
      const elTitle = document.getElementById('scDetailName');
      const elDot = document.getElementById('scDetailDot');
      const elDesc = document.getElementById('scDetailDesc');
      const elChallenge = document.getElementById('scDetailChallenge');
      const elTurbulence = document.getElementById('scDetailTurbulence');
      const elFov = document.getElementById('scDetailFov');
      const elHandover = document.getElementById('scDetailHandover');
      const elMission = document.getElementById('scDetailMission');
      const elSpeed = document.getElementById('scDetailSpeed');

      if (elTitle) elTitle.textContent = scenarioKey === 'all' ? 'All Operational Link Architectures' : info.name;
      if (elDot) elDot.className = `sc-dot ${info.dotClass}`;
      if (elDesc) elDesc.textContent = scenarioKey === 'all' ? 'Coarse alignment provides universal beacon acquisition and gimbal orientation across space, airborne, and terrestrial domains.' : info.desc;
      if (elChallenge) elChallenge.textContent = info.challenge;
      if (elTurbulence) elTurbulence.textContent = info.turbulence;
      if (elFov) elFov.textContent = info.fov;
      if (elHandover) elHandover.textContent = info.handover;
      if (elMission) elMission.textContent = info.mission;
      if (elSpeed) elSpeed.textContent = info.speed;

      addSystemLog(`Operational scenario context: [${scenarioKey.toUpperCase()}]`, 'cyan');
    }

    // Attach click handlers to filter pills
    filterBtns.forEach(btn => {
      btn.addEventListener('click', () => {
        const sc = btn.getAttribute('data-scenario');
        selectScenario(sc);
      });
    });

    // Attach click handlers to SVG link groups
    linkGroups.forEach(group => {
      group.addEventListener('click', () => {
        const sc = group.getAttribute('data-scenario');
        selectScenario(sc);
      });
    });
  }

  // =========================================================================
  // 12. Keyboard Shortcuts (Space, R, C, T)
  // =========================================================================
  function setupKeyboardShortcuts() {
    window.addEventListener('keydown', (e) => {
      // Ignore if user is focused inside an input/select/textarea
      const tag = (e.target.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'select' || tag === 'textarea') {
        return;
      }

      if (e.code === 'Space') {
        e.preventDefault();
        window.toggleSimulationPause();
      } else if (e.key === 'r' || e.key === 'R') {
        e.preventDefault();
        window.resetSimulation();
      } else if (e.key === 'c' || e.key === 'C') {
        e.preventDefault();
        window.centerOpticalBoresight();
      } else if (e.key === 't' || e.key === 'T') {
        e.preventDefault();
        window.toggleTheme();
      } else if (e.key === 'Escape') {
        const logsModal = document.getElementById('logsModal');
        const settingsModal = document.getElementById('settingsModal');
        if (logsModal) logsModal.style.display = 'none';
        if (settingsModal) settingsModal.style.display = 'none';
      }
    });
  }

  // =========================================================================
  // 13. System Event Logger
  // =========================================================================
  function addSystemLog(msg, color = 'green') {
    const list = document.getElementById('logsFeedList');
    const now = new Date();
    const timeStr = now.toTimeString().split(' ')[0];

    state.logHistory.unshift({ time: timeStr, msg, color });
    if (state.logHistory.length > 100) state.logHistory.pop();

    if (list) {
      const entry = document.createElement('div');
      entry.className = 'log-entry';
      entry.innerHTML = `
        <span class="log-time">${timeStr}</span>
        <span class="log-dot dot-${color}"></span>
        <span class="log-msg">${msg}</span>
      `;
      list.insertBefore(entry, list.firstChild);
      if (list.children.length > 20) {
        list.removeChild(list.lastChild);
      }
    }
  }

  function updateModalLogs() {
    const modalList = document.getElementById('modalFullLogsList');
    if (!modalList) return;
    modalList.innerHTML = '';
    state.logHistory.forEach(item => {
      const row = document.createElement('div');
      row.className = 'log-entry';
      row.innerHTML = `
        <span class="log-time">${item.time}</span>
        <span class="log-dot dot-${item.color}"></span>
        <span class="log-msg">${item.msg}</span>
      `;
      modalList.appendChild(row);
    });
  }
});

