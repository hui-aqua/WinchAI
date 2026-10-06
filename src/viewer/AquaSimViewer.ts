import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import type { AquaSimModel } from '@/parser/types';
import { createBeamGroup, updateBeamScale } from './renderers/beamRenderer';
import { createTrussGroup, updateTrussScale } from './renderers/trussRenderer';
import { createMembraneGroup } from './renderers/membraneRenderer';
import { updateLineState } from './renderers/lineRenderer';
import { fitCameraToModel } from './cameraUtils';

export class AquaSimViewer {
    private container: HTMLElement;
    private scene: THREE.Scene;
    private perspCamera: THREE.PerspectiveCamera;
    private orthoCamera: THREE.OrthographicCamera;
    private camera: THREE.PerspectiveCamera | THREE.OrthographicCamera;
    private cameraMode: 'perspective' | 'orthographic' = 'perspective';

    private renderer: THREE.WebGLRenderer;
    private controls: OrbitControls;

    private modelGroup: THREE.Group;
    private beamGroup: THREE.Group;
    private trussGroup: THREE.Group;
    private membraneGroup: THREE.Group;

    private gridVisible = false;
    private axesHelper: THREE.AxesHelper;

    // XYZ Reference Grid Planes
    private gridHelperXY: THREE.GridHelper | null = null; // Sea surface Z=0
    private gridHelperXZ: THREE.GridHelper | null = null; // Lateral Y=0
    private gridHelperYZ: THREE.GridHelper | null = null; // Longitudinal X=0
    private planeVisibility = { xy: true, xz: false, yz: false };

    private currentBounds: THREE.Box3 = new THREE.Box3();
    private animFrameId: number | null = null;
    private resizeObserver: ResizeObserver;
    private isDestroyed: boolean = false;

    // Cross-section visual controls
    private currentScaleFactor: number = 1.0;
    private renderMode: 'solid' | 'wireframe' | 'both' = 'both';

    // Component map for quick visibility toggling: key -> THREE.Object3D
    private componentObjectMap: Map<string, THREE.Object3D> = new Map();
    private currentNodes: AquaSimModel['nodes'] = new Map();

    constructor(container: HTMLElement) {
        this.container = container;

        // 1. Scene
        this.scene = new THREE.Scene();
        this.scene.background = new THREE.Color(0x0f172a); // Slate-900 engineering dark background

        // 2. Cameras setup (Perspective + Orthographic)
        const width = this.container.clientWidth || window.innerWidth;
        const height = this.container.clientHeight || window.innerHeight;
        const aspect = width / height;

        // Perspective Camera (+Z is vertical depth/elevation)
        this.perspCamera = new THREE.PerspectiveCamera(45, aspect, 0.1, 10000);
        this.perspCamera.up.set(0, 0, 1);
        this.perspCamera.position.set(200, -200, 150);

        // Orthographic Camera
        const frustumSize = 300;
        this.orthoCamera = new THREE.OrthographicCamera(
            (-frustumSize * aspect) / 2,
            (frustumSize * aspect) / 2,
            frustumSize / 2,
            -frustumSize / 2,
            -10000,
            10000
        );
        this.orthoCamera.up.set(0, 0, 1);
        this.orthoCamera.position.set(200, -200, 150);

        this.camera = this.perspCamera;

        // 3. Renderer
        this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
        this.renderer.setSize(width, height);
        this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
        this.container.appendChild(this.renderer.domElement);

        // 4. OrbitControls
        this.controls = new OrbitControls(this.camera, this.renderer.domElement);
        this.controls.enableDamping = true;
        this.controls.dampingFactor = 0.08;
        this.controls.screenSpacePanning = true;

        // 5. Lighting: Ambient + Directionals for full visibility of underwater structure
        const ambientLight = new THREE.AmbientLight(0xffffff, 0.75);
        this.scene.add(ambientLight);

        const dirLight1 = new THREE.DirectionalLight(0xffffff, 0.9);
        dirLight1.position.set(300, -400, 500);
        this.scene.add(dirLight1);

        const dirLight2 = new THREE.DirectionalLight(0x90cdf4, 0.5); // Subtle sea-blue fill from below
        dirLight2.position.set(-300, 400, -500);
        this.scene.add(dirLight2);

        // 6. Helpers
        this.axesHelper = new THREE.AxesHelper(50);
        this.axesHelper.visible = false;
        this.axesHelper.renderOrder = 999;
        this.scene.add(this.axesHelper);

        this.setupReferenceGridPlanes(200);

        // 7. Model Groups
        this.modelGroup = new THREE.Group();
        this.modelGroup.name = 'modelGroup';
        this.scene.add(this.modelGroup);

        this.beamGroup = new THREE.Group();
        this.beamGroup.name = 'beamGroup';
        this.modelGroup.add(this.beamGroup);

        this.trussGroup = new THREE.Group();
        this.trussGroup.name = 'trussGroup';
        this.modelGroup.add(this.trussGroup);

        this.membraneGroup = new THREE.Group();
        this.membraneGroup.name = 'membraneGroup';
        this.modelGroup.add(this.membraneGroup);

        // 8. Event listeners
        window.addEventListener('resize', this.onResize);
        this.resizeObserver = new ResizeObserver(() => this.onResize());
        this.resizeObserver.observe(this.container);

        // 9. Start render loop
        this.animate();
    }

    /**
     * Setup 3 reference grid planes:
     * - XY plane (Sea Surface Z=0)
     * - XZ plane (Lateral vertical plane Y=0)
     * - YZ plane (Longitudinal vertical plane X=0)
     */
    private setupReferenceGridPlanes(size: number): void {
        const divisions = 40;

        // Remove old helpers
        if (this.gridHelperXY) {
            this.scene.remove(this.gridHelperXY);
            this.gridHelperXY.dispose();
        }
        if (this.gridHelperXZ) {
            this.scene.remove(this.gridHelperXZ);
            this.gridHelperXZ.dispose();
        }
        if (this.gridHelperYZ) {
            this.scene.remove(this.gridHelperYZ);
            this.gridHelperYZ.dispose();
        }

        // 1. XY Grid (Sea Surface at Z=0): Cyan/Slate
        this.gridHelperXY = new THREE.GridHelper(size, divisions, 0x38bdf8, 0x1e293b);
        this.gridHelperXY.rotation.x = Math.PI / 2;
        this.gridHelperXY.position.set(0, 0, 0);
        this.gridHelperXY.visible = this.planeVisibility.xy && this.gridVisible;
        this.scene.add(this.gridHelperXY);

        // 2. XZ Grid (Lateral Cross-section at Y=0): Emerald/Slate
        this.gridHelperXZ = new THREE.GridHelper(size, divisions, 0x10b981, 0x1e293b);
        this.gridHelperXZ.position.set(0, 0, 0);
        this.gridHelperXZ.visible = this.planeVisibility.xz && this.gridVisible;
        this.scene.add(this.gridHelperXZ);

        // 3. YZ Grid (Longitudinal Cross-section at X=0): Rose/Slate
        this.gridHelperYZ = new THREE.GridHelper(size, divisions, 0xf43f5e, 0x1e293b);
        this.gridHelperYZ.rotation.z = Math.PI / 2;
        this.gridHelperYZ.position.set(0, 0, 0);
        this.gridHelperYZ.visible = this.planeVisibility.yz && this.gridVisible;
        this.scene.add(this.gridHelperYZ);
    }

    /**
     * Toggle visibility of specific XYZ reference planes
     */
    public setPlaneVisibility(plane: 'xy' | 'xz' | 'yz', visible: boolean): void {
        this.planeVisibility[plane] = visible;
        if (plane === 'xy' && this.gridHelperXY) {
            this.gridHelperXY.visible = visible && this.gridVisible;
        } else if (plane === 'xz' && this.gridHelperXZ) {
            this.gridHelperXZ.visible = visible && this.gridVisible;
        } else if (plane === 'yz' && this.gridHelperYZ) {
            this.gridHelperYZ.visible = visible && this.gridVisible;
        }
    }

    public getPlaneVisibility(plane: 'xy' | 'xz' | 'yz'): boolean {
        return this.planeVisibility[plane];
    }

    /**
     * Switch between Perspective and Orthographic camera modes
     */
    public setCameraMode(mode: 'perspective' | 'orthographic'): void {
        if (this.cameraMode === mode) return;
        this.cameraMode = mode;

        const width = this.container.clientWidth || window.innerWidth;
        const height = this.container.clientHeight || window.innerHeight;
        const aspect = width / height;

        if (mode === 'orthographic') {
            const distance = this.perspCamera.position.distanceTo(this.controls.target);
            const fovRad = THREE.MathUtils.degToRad(this.perspCamera.fov);
            const frustumHeight = 2 * distance * Math.tan(fovRad / 2);
            const frustumWidth = frustumHeight * aspect;

            this.orthoCamera.left = -frustumWidth / 2;
            this.orthoCamera.right = frustumWidth / 2;
            this.orthoCamera.top = frustumHeight / 2;
            this.orthoCamera.bottom = -frustumHeight / 2;
            this.orthoCamera.position.copy(this.perspCamera.position);
            this.orthoCamera.up.copy(this.perspCamera.up);
            this.orthoCamera.lookAt(this.controls.target);
            this.orthoCamera.updateProjectionMatrix();

            this.camera = this.orthoCamera;
            this.controls.object = this.orthoCamera;
        } else {
            this.perspCamera.position.copy(this.orthoCamera.position);
            this.perspCamera.up.copy(this.orthoCamera.up);
            this.perspCamera.lookAt(this.controls.target);
            this.perspCamera.updateProjectionMatrix();

            this.camera = this.perspCamera;
            this.controls.object = this.perspCamera;
        }

        this.controls.update();
    }

    public getCameraMode(): 'perspective' | 'orthographic' {
        return this.cameraMode;
    }

    public toggleCameraMode(): 'perspective' | 'orthographic' {
        const nextMode = this.cameraMode === 'perspective' ? 'orthographic' : 'perspective';
        this.setCameraMode(nextMode);
        return nextMode;
    }

    /**
     * Align camera quickly to X+, Y+, Z+, or Isometric 3D views
     */
    public alignView(axis: 'x+' | 'y+' | 'z+' | 'iso'): void {
        const center = new THREE.Vector3();
        const size = new THREE.Vector3();

        if (!this.currentBounds.isEmpty()) {
            this.currentBounds.getCenter(center);
            this.currentBounds.getSize(size);
        }

        const maxDim = Math.max(size.x, size.y, size.z, 50);
        const distance = maxDim * 1.5;

        switch (axis) {
            case 'z+': // Top / Sea surface plan view
                this.camera.position.set(center.x, center.y, center.z + distance);
                this.camera.up.set(0, 1, 0); // +Y points up in top plan view
                break;
            case 'x+': // Front elevation view (looking along lateral axis)
                this.camera.position.set(center.x + distance, center.y, center.z);
                this.camera.up.set(0, 0, 1);
                break;
            case 'y+': // Side elevation view (looking along longitudinal axis)
                this.camera.position.set(center.x, center.y + distance, center.z);
                this.camera.up.set(0, 0, 1);
                break;
            case 'iso': // Natural 3D isometric view
            default: {
                const dir = new THREE.Vector3(1, -1.2, 0.8).normalize();
                this.camera.position.copy(center).add(dir.multiplyScalar(distance));
                this.camera.up.set(0, 0, 1);
                break;
            }
        }

        this.controls.target.copy(center);
        this.camera.lookAt(center);

        if (this.cameraMode === 'orthographic') {
            const aspect = (this.orthoCamera.right - this.orthoCamera.left) / (this.orthoCamera.top - this.orthoCamera.bottom) || 1;
            const frustumHeight = maxDim * 1.4;
            const frustumWidth = frustumHeight * aspect;
            this.orthoCamera.left = -frustumWidth / 2;
            this.orthoCamera.right = frustumWidth / 2;
            this.orthoCamera.top = frustumHeight / 2;
            this.orthoCamera.bottom = -frustumHeight / 2;
            this.orthoCamera.updateProjectionMatrix();
        }

        this.controls.update();
    }

    private onResize = (): void => {
        if (!this.container || this.isDestroyed) return;
        const width = this.container.clientWidth;
        const height = this.container.clientHeight;
        if (width === 0 || height === 0) return;

        const aspect = width / height;

        if (this.cameraMode === 'perspective') {
            this.perspCamera.aspect = aspect;
            this.perspCamera.updateProjectionMatrix();
        } else {
            const distance = this.orthoCamera.position.distanceTo(this.controls.target);
            const fovRad = THREE.MathUtils.degToRad(this.perspCamera.fov);
            const frustumHeight = 2 * distance * Math.tan(fovRad / 2);
            const frustumWidth = frustumHeight * aspect;
            this.orthoCamera.left = -frustumWidth / 2;
            this.orthoCamera.right = frustumWidth / 2;
            this.orthoCamera.top = frustumHeight / 2;
            this.orthoCamera.bottom = -frustumHeight / 2;
            this.orthoCamera.updateProjectionMatrix();
        }

        this.renderer.setSize(width, height);
    };

    private animate = (): void => {
        if (this.isDestroyed) return;
        this.animFrameId = requestAnimationFrame(this.animate);
        this.controls.update();
        this.renderer.render(this.scene, this.camera);
    };

    /**
     * Clear existing model geometry and dispose resources.
     */
    public clearModel(): void {
        this.componentObjectMap.clear();
        const geometries = new Set<THREE.BufferGeometry>();
        const materials = new Set<THREE.Material>();
        this.modelGroup.traverse(object => {
            const mesh = object as THREE.Mesh;
            if (mesh.geometry) geometries.add(mesh.geometry);
            if (mesh.material) {
                for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
                    materials.add(material);
                }
            }
        });
        geometries.forEach(geometry => geometry.dispose());
        materials.forEach(material => material.dispose());
        this.modelGroup.clear();
        this.modelGroup.position.set(0, 0, 0);
        this.currentBounds.makeEmpty();
    }

    /**
     * Load an AquaSimModel into the 3D scene.
     */
    public loadModel(model: AquaSimModel): void {
        this.clearModel();
        this.currentNodes = model.nodes;
        this.beamGroup = new THREE.Group();
        this.trussGroup = new THREE.Group();
        this.membraneGroup = new THREE.Group();
        this.modelGroup.add(this.beamGroup, this.trussGroup, this.membraneGroup);

        // 1. Build Beams with amodel sections
        const beams = createBeamGroup(model.beams, model.nodes, {
            scaleFactor: this.currentScaleFactor,
            renderMode: this.renderMode
        });
        [...beams.children].forEach((obj) => {
            const id = obj.userData.componentId ?? obj.name;
            this.componentObjectMap.set(`beam_${id}`, obj);
            this.beamGroup.add(obj);
        });

        // 2. Build Trusses with circular sections
        const trusses = createTrussGroup(model.trusses, model.nodes, {
            scaleFactor: this.currentScaleFactor,
            renderMode: this.renderMode
        });
        [...trusses.children].forEach((obj) => {
            const id = obj.userData.componentId ?? obj.name;
            const mesh = obj.userData.instancedMesh as THREE.InstancedMesh | undefined;
            if (mesh?.material instanceof THREE.MeshStandardMaterial) {
                obj.userData.originalColor = mesh.material.color.clone();
            }
            this.componentObjectMap.set(`truss_${id}`, obj);
            this.trussGroup.add(obj);
        });

        // 3. Build Membranes with all components preserved
        const membranes = createMembraneGroup(model.membranes, model.nodes);
        [...membranes.children].forEach((obj) => {
            const id = obj.userData.componentId ?? obj.name;
            this.componentObjectMap.set(`membrane_${id}`, obj);
            this.membraneGroup.add(obj);
        });

        // 4. Compute bounding box
        this.currentBounds.setFromObject(this.modelGroup);

        if (!this.currentBounds.isEmpty()) {
            const size = new THREE.Vector3();
            this.currentBounds.getSize(size);
            const maxDim = Math.max(size.x, size.y, size.z);

            // Scale axes and grid helper to model scale
            this.axesHelper.scale.setScalar(Math.max(10, maxDim * 0.15));
            this.setupReferenceGridPlanes(Math.max(200, Math.ceil((maxDim * 1.5) / 100) * 100));

            this.setGridVisibility(this.gridVisible);
            // Automatic camera fit
            this.fitView();
        }
    }

    public setTheme(theme: 'light' | 'dark'): void {
        this.scene.background = new THREE.Color(theme === 'light' ? 0xf1f5f9 : 0x0f172a);
    }

    /**
     * Automatic camera fit to entire model
     */
    public fitView(): void {
        if (!this.currentBounds.isEmpty()) {
            fitCameraToModel(this.camera, this.controls, this.currentBounds);
        }
    }

    /**
     * Focus camera specifically on the floating cage structure and nets
     */
    public fitCageView(): void {
        const cageBounds = new THREE.Box3();
        cageBounds.expandByObject(this.beamGroup);
        cageBounds.expandByObject(this.membraneGroup);
        if (!cageBounds.isEmpty()) {
            fitCameraToModel(this.camera, this.controls, cageBounds, 1.8);
        } else {
            this.fitView();
        }
    }

    /**
     * Category level visibility
     */
    public setCategoryVisibility(category: 'beam' | 'truss' | 'membrane', visible: boolean): void {
        const groups = { beam: this.beamGroup, truss: this.trussGroup, membrane: this.membraneGroup };
        groups[category].visible = visible;
    }

    /**
     * Component level visibility
     */
    public setComponentVisibility(key: string, visible: boolean): void {
        const obj = this.componentObjectMap.get(key);
        if (obj) {
            obj.visible = visible;
        }
    }

    /** Apply the rigid vertical pose and tension colors from one playback sample. */
    public applyWinchFrame(
        nodePositions: ReadonlyMap<number, THREE.Vector3>,
        tensionByElementKey: ReadonlyMap<string, number>,
        maximumTensionN: number
    ): void {
        updateLineState(this.beamGroup, nodePositions);
        updateLineState(this.trussGroup, nodePositions, tensionByElementKey, maximumTensionN);
    }

    public clearWinchFrame(): void {
        const positions = new Map<number, THREE.Vector3>();
        for (const [nodeId, node] of this.currentNodes) {
            positions.set(nodeId, new THREE.Vector3(node.x, node.y, node.z));
        }
        updateLineState(this.beamGroup, positions);
        updateLineState(this.trussGroup, positions);
    }

    /**
     * Dynamically scale the cross-sections of beam and truss elements
     */
    public setSectionScale(scale: number): void {
        this.currentScaleFactor = scale;
        updateBeamScale(this.beamGroup, scale);
        updateTrussScale(this.trussGroup, scale);
    }

    /**
     * Switch render mode: solid 3D sections, wireframe centerlines, or both
     */
    public setRenderMode(mode: 'solid' | 'wireframe' | 'both'): void {
        this.renderMode = mode;
        const updateVisibility = (group: THREE.Group) => {
            group.children.forEach((compGroup) => {
                const u = compGroup.userData;
                if (u.instancedMesh) {
                    u.instancedMesh.visible = mode !== 'wireframe';
                }
                if (u.lineSegments) {
                    u.lineSegments.visible = mode !== 'solid';
                }
            });
        };
        updateVisibility(this.beamGroup);
        updateVisibility(this.trussGroup);
    }

    /**
     * Set master grid visibility
     */
    public setGridVisibility(visible: boolean): void {
        this.gridVisible = visible;
        if (this.gridHelperXY) {
            this.gridHelperXY.visible = visible && this.planeVisibility.xy;
        }
        if (this.gridHelperXZ) {
            this.gridHelperXZ.visible = visible && this.planeVisibility.xz;
        }
        if (this.gridHelperYZ) {
            this.gridHelperYZ.visible = visible && this.planeVisibility.yz;
        }
    }

    /**
     * Set axes visibility
     */
    public setAxesVisibility(visible: boolean): void {
        this.axesHelper.visible = visible;
    }

    /**
     * Cleanup viewer
     */
    public destroy(): void {
        if (this.isDestroyed) return;
        this.isDestroyed = true;
        this.resizeObserver.disconnect();
        this.controls.dispose();
        this.axesHelper.dispose();
        this.gridHelperXY?.dispose();
        this.gridHelperXZ?.dispose();
        this.gridHelperYZ?.dispose();
        if (this.animFrameId !== null) {
            cancelAnimationFrame(this.animFrameId);
        }
        window.removeEventListener('resize', this.onResize);
        this.clearModel();
        this.renderer.dispose();
        if (this.renderer.domElement.parentElement) {
            this.renderer.domElement.parentElement.removeChild(this.renderer.domElement);
        }
    }
}
