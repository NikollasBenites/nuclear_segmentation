"""Embedded, debounced plots of measured objects and the current selection."""
import numpy as np
from qtpy.QtCore import QTimer
from qtpy.QtWidgets import QWidget, QVBoxLayout, QComboBox, QLabel
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT


class LivePlots(QWidget):
    def __init__(self, ns):
        super().__init__()
        self.ns = ns
        self.dirty = True
        self.disposed = False
        self.z_profile = []
        self.z_error = None
        self.z_timer = QTimer(self)
        self.z_timer.setInterval(10)
        self.z_timer.timeout.connect(self.compute_z_plane)
        self.render_count = 0
        self.last_retained_ids = []
        layout = QVBoxLayout(self)
        self.kind = QComboBox()
        self.kind.addItems(['Volume', 'Sphericity', 'Intensity', 'Perinuclear', 'Z profile'])
        self.column = QComboBox()
        layout.addWidget(self.kind); layout.addWidget(self.column)
        self.status = QLabel('Graphs update automatically after filters change.')
        self.status.setWordWrap(True); layout.addWidget(self.status)
        self.figure = Figure(figsize=(5, 4), layout='constrained')
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setMinimumSize(160, 180)
        self.axes = self.figure.add_subplot(111)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        self.toolbar.setMinimumWidth(0)
        layout.addWidget(self.toolbar); layout.addWidget(self.canvas, 1)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(300)
        self.timer.timeout.connect(self.redraw)
        self.kind.currentTextChanged.connect(self.populate_columns)
        self.column.currentIndexChanged.connect(self.schedule)
        self.populate_columns()
        from .ui_layout import compact
        compact(self)

    def populate_columns(self, *_):
        self.column.blockSignals(True)
        self.column.clear()
        props = self.ns.get('filter_properties')
        kind = self.kind.currentText()
        if props is not None and kind == 'Intensity':
            prefix = self.ns.get('intensity_column_prefix', 'mean_intensity_')
            for name in props.columns:
                if name.startswith(prefix):
                    self.column.addItem(name[len(prefix):], name)
        elif kind == 'Perinuclear':
            for name, info in self.ns.get('PERINUCLEAR_MARKER_RESULTS', {}).items():
                self.column.addItem(name, info['fraction_column'])
        self.column.setVisible(kind in {'Intensity', 'Perinuclear'})
        self.column.blockSignals(False)
        self.schedule()

    def schedule(self, *_):
        if self.disposed:
            return
        self.dirty = True
        self.status.setText('Updating graphs…')
        # Hidden tabs keep only a dirty flag, avoiding unnecessary rendering.
        if self.isVisible():
            self.timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        if self.dirty and not self.disposed:
            self.timer.start()

    def hideEvent(self, event):
        self.timer.stop()
        self.z_timer.stop()
        super().hideEvent(event)

    def dispose(self):
        self.timer.stop()
        self.z_timer.stop()
        self.disposed = True
        self.ns = {}

    def compute_z_plane(self):
        # Compute once per session, one plane per event-loop turn; changing
        # filters only redraws the cached profile and boundary markers.
        volume = self.ns.get('segmentation_volume')
        if self.disposed or not self.isVisible() or self.kind.currentText() != 'Z profile':
            self.z_timer.stop()
            return
        try:
            z = len(self.z_profile)
            plane = np.asarray(volume[z])
            values = plane[np.isfinite(plane)]
            self.z_profile.append(np.percentile(values, [50, 99]) if values.size else [np.nan, np.nan])
            self.status.setText(f'Calculating Z profile: {z + 1}/{volume.shape[0]} planes')
            if len(self.z_profile) == volume.shape[0]:
                self.z_timer.stop()
                self.redraw()
        except Exception as exc:
            self.z_timer.stop()
            self.z_error = str(exc)
            self.redraw()

    def redraw(self):
        if self.disposed:
            return
        self.timer.stop()
        self.dirty = False
        self.render_count += 1
        ax = self.axes
        ax.clear()
        props, keep = self.ns.get('filter_properties'), self.ns.get('current_keep')
        self.last_retained_ids = []
        if props is None or keep is None:
            self.status.setText('Graphs unavailable: load an analysis or fix invalid filter settings.')
            self.canvas.draw_idle()
            return
        selected = keep.reindex(props.index, fill_value=False).to_numpy(dtype=bool)
        self.last_retained_ids = props.index[selected].tolist()
        kind = self.kind.currentText()
        settings = self.ns['current_filter_settings']() if 'current_filter_settings' in self.ns else {}
        if kind == 'Z profile':
            volume = self.ns.get('segmentation_volume')
            if volume is None or self.z_error:
                self.status.setText('Z profile unavailable: ' + (self.z_error or 'no image volume'))
                self.canvas.draw_idle()
                return
            if len(self.z_profile) < volume.shape[0]:
                self.status.setText('Calculating Z profile…')
                self.z_timer.start()
                self.canvas.draw_idle()
                return
            profile = np.asarray(self.z_profile)
            ax.plot(profile[:, 0], label='Median')
            ax.plot(profile[:, 1], label='P99')
            for key in ('first_tissue_z', 'last_tissue_z'):
                if key in settings:
                    ax.axvline(settings[key], linestyle='--', color='darkorange')
            ax.set(xlabel='Z plane', ylabel='Segmentation input intensity')
        elif kind == 'Sphericity':
            x = props['volume_um3'].to_numpy(float)
            y = props['sphericity'].to_numpy(float)
            finite = np.isfinite(x) & np.isfinite(y)
            for mask, label, color in [(~selected, 'Excluded', 'gray'), (selected, 'Retained', 'royalblue')]:
                valid = mask & finite
                ax.scatter(x[valid], y[valid], s=15, alpha=.55, label=label, color=color)
            ax.set(xlabel='Mask volume (µm³)', ylabel='Sphericity', ylim=(0, 1.05))
            if 'minimum_sphericity' in settings:
                ax.axhline(settings['minimum_sphericity'], color='darkorange', linestyle='--')
        else:
            column = 'volume_um3' if kind == 'Volume' else self.column.currentData()
            if not column or column not in props:
                self.status.setText('No measured channels available for this graph.')
                self.canvas.draw_idle()
                return
            values = props[column].to_numpy(float)
            finite = np.isfinite(values)
            all_values, retained = values[finite], values[finite & selected]
            bins = np.linspace(0, 1, 31) if kind == 'Perinuclear' else np.histogram_bin_edges(all_values, bins=30)
            if kind != 'Perinuclear' and all_values.size and np.ptp(all_values) == 0:
                center = float(all_values[0]); pad = max(abs(center) * .05, .5)
                bins = np.linspace(center - pad, center + pad, 21)
            ax.hist(all_values, bins=bins, alpha=.35, color='gray', label='All masks')
            ax.hist(retained, bins=bins, alpha=.7, color='royalblue', label='Retained')
            xlabel = 'Mask volume (µm³)' if kind == 'Volume' else self.column.currentText()
            if kind == 'Intensity':
                xlabel += ' — mean intensity (' + self.ns.get('INTENSITY_MODE', 'raw') + ')'
            if kind == 'Perinuclear':
                xlabel += ' — positive shell fraction'
                record = settings.get('perinuclear_filters', {}).get(self.column.currentText(), {})
                threshold = record.get('minimum_positive_fraction')
            elif kind == 'Volume':
                threshold = settings.get('minimum_volume_um3')
            else:
                threshold = None
            if threshold is not None:
                ax.axvline(threshold, color='darkorange', linestyle='--')
            ax.set(xlabel=xlabel, ylabel='Number of masks', xlim=(bins[0], bins[-1]))
        ax.set_title('Segmentation input — full stack' if kind == 'Z profile' else kind + ' — current selection')
        ax.grid(alpha=.2); ax.legend(loc='best')
        note = ' ROI pending review.' if self.ns.get('roi_reviewed') is False else ''
        self.status.setText(('Full stack profile (includes zeros). Z boundaries follow filters.'
                             if kind == 'Z profile' else f'Retained: {int(selected.sum())} / {len(props)}. Automatic update.') + note)
        self.canvas.draw_idle()
