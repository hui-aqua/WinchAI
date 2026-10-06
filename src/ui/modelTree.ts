import type { AquaSimModel } from '@/parser/types';
import type { AquaSimViewer } from '@/viewer/AquaSimViewer';

export interface ModelTreeElements {
    treeContainer: HTMLElement;
    statsContainer: HTMLElement;
}

export class ModelTreeUI {
    private elements: ModelTreeElements;
    private viewer: AquaSimViewer;

    constructor(elements: ModelTreeElements, viewer: AquaSimViewer) {
        this.elements = elements;
        this.viewer = viewer;
    }

    public update(model: AquaSimModel, filename: string): void {
        this.renderStats(model, filename);
        this.renderTree(model);
    }

    private escapeHtml(value: string): string { const span = document.createElement('span'); span.textContent = value; return span.innerHTML.replace(/"/g, '&quot;'); }
    private renderStats(model: AquaSimModel, filename: string): void {
        const { report, boundingBox } = model;
        const bb = boundingBox;

        this.elements.statsContainer.innerHTML = `
            <div class="stats-group">
                <div class="stats-title">Model Statistics</div>
                <div class="stat-row"><span class="stat-label">File:</span><span class="stat-value" title="${this.escapeHtml(filename)}">${this.escapeHtml(filename)}</span></div>
                <div class="stat-row"><span class="stat-label">Nodes:</span><span class="stat-value stat-highlight">${report.nodeCount.toLocaleString()}</span></div>
                <div class="stat-row"><span class="stat-label">Total Elements:</span><span class="stat-value stat-highlight">${report.totalElementCount.toLocaleString()}</span></div>
                <div class="stat-row"><span class="stat-label">Beam Comps / Elems:</span><span class="stat-value">${report.beamComponentCount} / ${report.beamElementCount.toLocaleString()}</span></div>
                <div class="stat-row"><span class="stat-label">Truss Comps / Elems:</span><span class="stat-value">${report.trussComponentCount} / ${report.trussElementCount.toLocaleString()}</span></div>
                <div class="stat-row"><span class="stat-label">Membrane Comps / Elems:</span><span class="stat-value">${report.membraneComponentCount} / ${report.membraneElementCount.toLocaleString()}</span></div>
                <div class="stat-row"><span class="stat-label">Invalid References:</span><span class="stat-value ${report.invalidReferences > 0 ? 'stat-warning' : 'stat-ok'}">${report.invalidReferences}</span></div>
            </div>
            <div class="stats-group">
                <div class="stats-title">Bounding Box (m)</div>
                <div class="stat-row"><span class="stat-label">X:</span><span class="stat-value">${bb.min.x.toFixed(1)} → ${bb.max.x.toFixed(1)}</span></div>
                <div class="stat-row"><span class="stat-label">Y:</span><span class="stat-value">${bb.min.y.toFixed(1)} → ${bb.max.y.toFixed(1)}</span></div>
                <div class="stat-row"><span class="stat-label">Z:</span><span class="stat-value">${bb.min.z.toFixed(1)} → ${bb.max.z.toFixed(1)}</span></div>
            </div>
        `;
    }

    private renderTree(model: AquaSimModel): void {
        this.elements.treeContainer.innerHTML = '';

        const categories = [
            {
                type: 'beam' as const,
                title: 'Beam',
                color: '#00d2ff',
                components: model.beams
            },
            {
                type: 'truss' as const,
                title: 'Truss',
                color: '#ffaa00',
                components: model.trusses
            },
            {
                type: 'membrane' as const,
                title: 'Membrane',
                color: '#10b981',
                components: model.membranes
            }
        ];

        categories.forEach((cat) => {
            const catElem = document.createElement('div');
            catElem.className = 'tree-category';

            // Category Header
            const header = document.createElement('div');
            header.className = 'category-header';

            const expandBtn = document.createElement('span');
            expandBtn.className = 'expand-icon expanded';
            expandBtn.textContent = '▼';

            const catCheckbox = document.createElement('input');
            catCheckbox.type = 'checkbox';
            catCheckbox.checked = true;
            catCheckbox.className = 'category-checkbox';

            const title = document.createElement('span');
            title.className = 'category-title';
            title.style.borderLeft = `3px solid ${cat.color}`;
            title.textContent = `${cat.title} (${cat.components.length})`;

            header.appendChild(expandBtn);
            header.appendChild(catCheckbox);
            header.appendChild(title);
            catElem.appendChild(header);

            // Component List
            const list = document.createElement('div');
            list.className = 'component-list';

            const compCheckboxes: HTMLInputElement[] = [];

            cat.components.forEach((comp, idx) => {
                const compId = comp.id ?? idx;
                const compKey = `${cat.type}_${compId}`;

                const item = document.createElement('div');
                item.className = 'component-item';

                const compCheckbox = document.createElement('input');
                compCheckbox.type = 'checkbox';
                compCheckbox.checked = true;
                compCheckbox.dataset.key = compKey;
                compCheckboxes.push(compCheckbox);

                const swatch = document.createElement('span');
                swatch.className = 'comp-swatch';
                const colR = comp.color ? Math.round(comp.color.r * 255) : 128;
                const colG = comp.color ? Math.round(comp.color.g * 255) : 128;
                const colB = comp.color ? Math.round(comp.color.b * 255) : 128;
                swatch.style.backgroundColor = comp.color
                    ? `rgb(${colR}, ${colG}, ${colB})`
                    : cat.color;

                const compLabel = document.createElement('span');
                compLabel.className = 'component-label';
                compLabel.textContent = comp.name || `Component ${compId}`;
                compLabel.title = `${comp.name} (${comp.elements.length} elements)`;

                const badge = document.createElement('span');
                badge.className = 'count-badge';
                badge.textContent = `${comp.elements.length}`;

                item.appendChild(compCheckbox);
                item.appendChild(swatch);
                item.appendChild(compLabel);

                if ('section' in comp && comp.section) {
                    const sec = comp.section;
                    const secBadge = document.createElement('span');
                    secBadge.className = 'section-badge';
                    if (sec.shape === 'circular') {
                        const d = (sec.outerDiameter ?? (sec.radius ?? 0) * 2) * 1000;
                        secBadge.textContent = `Ø${Math.round(d)}mm`;
                        secBadge.title = `Circular section: Diameter ${Math.round(d)} mm`;
                    } else {
                        const w = Math.round((sec.width ?? 0.2) * 1000);
                        const h = Math.round((sec.height ?? 0.2) * 1000);
                        secBadge.textContent = `${w}×${h}mm`;
                        secBadge.title = `Profile section: ${w} mm × ${h} mm`;
                    }
                    item.appendChild(secBadge);
                }

                item.appendChild(badge);
                list.appendChild(item);

                // Child checkbox change
                compCheckbox.addEventListener('change', () => {
                    this.viewer.setComponentVisibility(compKey, compCheckbox.checked);
                    const allChecked = compCheckboxes.every((c) => c.checked);
                    const someChecked = compCheckboxes.some((c) => c.checked);
                    catCheckbox.checked = someChecked;
                    catCheckbox.indeterminate = someChecked && !allChecked;
                });
            });

            catElem.appendChild(list);
            this.elements.treeContainer.appendChild(catElem);

            // Expand/collapse toggle
            expandBtn.addEventListener('click', () => {
                const isExpanded = expandBtn.classList.contains('expanded');
                if (isExpanded) {
                    expandBtn.classList.remove('expanded');
                    expandBtn.textContent = '▶';
                    list.style.display = 'none';
                } else {
                    expandBtn.classList.add('expanded');
                    expandBtn.textContent = '▼';
                    list.style.display = 'block';
                }
            });

            // Master category checkbox toggle
            catCheckbox.addEventListener('change', () => {
                catCheckbox.indeterminate = false;
                const checked = catCheckbox.checked;

                compCheckboxes.forEach((c) => {
                    c.checked = checked;
                    if (c.dataset.key) {
                        this.viewer.setComponentVisibility(c.dataset.key, checked);
                    }
                });
            });
        });
    }
}
