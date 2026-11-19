"""Embedded, debounced plots of measured objects and the current selection."""
import numpy as np
import pandas as pd
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
        selected = self.column.currentData()
        self.column.blockSignals(True)
        self.column.clear()
        props = self.ns.get('filter_properties')
        kind = self.kind.currentText()
        if props is not None and kind == 'Intensity':
            columns = self.ns.get('INTENSITY_COLUMNS')
            if columns is not None:
                for label, name in columns:
                    if name in props:
                        self.column.addItem(label, name)
            else:
                prefix = self.ns.get('intensity_column_prefix', 'mean_intensity_')
                for name in props.columns:
                    if name.startswith(prefix):
                        self.column.addItem(name[len(prefix):], name)
        elif kind == 'Perinuclear':
            for name, info in self.ns.get('PERINUCLEAR_MARKER_RESULTS', {}).items():
                self.column.addItem(name, info['fraction_column'])
        self.column.setVisible(kind in {'Intensity', 'Perinuclear'})
        if selected is not None:
            index = self.column.findData(selected)
            if index >= 0:
                self.column.setCurrentIndex(index)
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
        self._apply_canvas_theme()
        props, keep = self.ns.get('filter_properties'), self.ns.get('current_keep')
        self.last_retained_ids = []
        if props is None or keep is None:
            self.status.setText(self.ns.get(
                'unavailable_message',
                'Graphs unavailable: load an analysis or fix invalid filter settings.',
            ))
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
            if 'minimum_volume_um3' in settings:
                ax.axvline(settings['minimum_volume_um3'], color='darkorange', linestyle='--')
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
            elif kind == 'Intensity':
                record = settings.get('intensity_filters', {}).get(
                    self.column.currentText(), {}
                )
                threshold = None
                for value in (record.get('minimum'), record.get('maximum')):
                    if value is not None:
                        ax.axvline(value, color='darkorange', linestyle='--')
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
        self._apply_canvas_theme()
        self.canvas.draw_idle()

    def _apply_canvas_theme(self):
        if not self.ns.get('dark_theme'):
            return
        background, foreground, grid = '#0f1923', '#dce7f1', '#607589'
        self.figure.set_facecolor(background)
        self.axes.set_facecolor(background)
        self.axes.tick_params(colors=foreground)
        self.axes.xaxis.label.set_color(foreground)
        self.axes.yaxis.label.set_color(foreground)
        self.axes.title.set_color(foreground)
        for spine in self.axes.spines.values():
            spine.set_color(grid)
        legend = self.axes.get_legend()
        if legend is not None:
            legend.get_frame().set_facecolor(background)
            legend.get_frame().set_edgecolor(grid)
            for text in legend.get_texts():
                text.set_color(foreground)


class ProjectLivePlots(LivePlots):
    """Bind the existing LivePlots widget to current desktop project state."""

    def __init__(self, project_state):
        self.project_state = project_state
        self._measurement_identity = None
        self._profile_identity = None
        super().__init__({})
        project_state.segmentation_result_changed.connect(self.sync)
        project_state.filtering_result_changed.connect(self.sync)
        project_state.scientific_roi_changed.connect(self.sync)
        self.sync()

    def _segmentation_volume(self, result):
        volume = result.get('model_input_volume')
        if volume is not None:
            return volume
        corrections = result.get('z_corrections') or {}
        channel = result.get('segmentation_channel')
        correction = corrections.get(channel, {})
        if correction.get('segmentation'):
            return None
        state = self.project_state
        descriptor = next(
            (item for item in state.channels if item.name == channel), None
        )
        if state.working_dataset is None or descriptor is None:
            return None
        return state.working_dataset.data_czyx[descriptor.index]

    def sync(self, *args):
        if self.disposed:
            return
        state = self.project_state
        segmentation = state.segmentation_result
        filtering = state.filtering_result
        self.ns.clear()
        self.ns['dark_theme'] = True
        if segmentation is None or not state.segmentation_is_current:
            self.ns['unavailable_message'] = 'Graphs unavailable: no current segmentation.'
            self._measurement_identity = None
            self._set_profile_source(None, None)
            self.populate_columns()
            return

        volume = self._segmentation_volume(segmentation)
        profile_identity = (
            segmentation.get('completed_at_utc'),
            id(segmentation),
            id(volume),
        )
        self._set_profile_source(volume, profile_identity)
        if filtering is None:
            self.ns['unavailable_message'] = (
                'Graphs unavailable: calculate scientific measurements.'
            )
            self._measurement_identity = None
            self.populate_columns()
            return
        if not state.filtering_is_current:
            self.ns['unavailable_message'] = (
                'Graphs unavailable: scientific measurements are outdated.'
            )
            self._measurement_identity = None
            self.populate_columns()
            return

        properties = filtering['properties']
        retained = set(int(value) for value in filtering['retained_ids'])
        self.ns.update(
            filter_properties=properties,
            current_keep=pd.Series(
                properties.index.isin(retained), index=properties.index
            ),
            current_filter_settings=lambda: filtering['settings'],
            roi_reviewed=bool(state.scientific_roi_reviewed),
            segmentation_volume=volume,
            PERINUCLEAR_MARKER_RESULTS={
                item['name']: {'fraction_column': item['column']}
                for item in filtering.get('perinuclear_definitions', [])
            },
        )
        intensity = filtering.get('intensity_definitions', [])
        if intensity:
            self.ns['INTENSITY_COLUMNS'] = [
                (item['name'], item['column']) for item in intensity
            ]
            column = intensity[0]['column']
            self.ns['INTENSITY_MODE'] = (
                'background-corrected'
                if column.startswith('background_corrected_mean_') else 'raw'
            )
        identity = id(filtering.get('properties'))
        if identity != self._measurement_identity:
            self._measurement_identity = identity
            self.populate_columns()
        else:
            self.schedule()

    def _set_profile_source(self, volume, identity):
        self.ns['segmentation_volume'] = volume
        if identity == self._profile_identity:
            return
        self._profile_identity = identity
        self.z_timer.stop()
        self.z_profile = []
        self.z_error = None

    def dispose(self):
        if not self.disposed:
            state = self.project_state
            state.segmentation_result_changed.disconnect(self.sync)
            state.filtering_result_changed.disconnect(self.sync)
            state.scientific_roi_changed.disconnect(self.sync)
        super().dispose()
