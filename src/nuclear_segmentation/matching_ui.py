"""Independent matching workspace; does not alter segmentation/filter sessions."""
import json
from pathlib import Path
import numpy as np
from qtpy.QtCore import QEvent
from qtpy.QtWidgets import (QWidget,QVBoxLayout,QFormLayout,QLineEdit,QPushButton,QLabel,
    QSpinBox,QDoubleSpinBox,QComboBox,QCheckBox,QFileDialog,QMessageBox,QScrollArea,QDialog)
from .matching import Matching
from .runtime import validate_roi_geometry


class MatchingPanel(QWidget):
    def __init__(self,viewer):
        super().__init__();self.viewer=viewer;self.model=None;self.layers=[];self.references=[];self.plots=[];self.dirty=False
        layout=QVBoxLayout(self);form=QFormLayout();layout.addLayout(form)
        self.paths=[]
        for title in ['DAPI mask TIFF','MAP2 mask TIFF']:
            edit=QLineEdit();self.paths.append(edit);form.addRow(title,edit)
            b=QPushButton('Browse '+title);b.clicked.connect(lambda checked=False,e=edit:self.browse(e));form.addRow(b)
        self.spacing=QLineEdit();self.spacing.setPlaceholderText('Only if metadata missing: Z,Y,X in µm');form.addRow('Fallback spacing',self.spacing)
        for title,fn in [('Load masks',self.load),('Open saved matching…',self.open_saved),('Add reference image TIFF…',self.add_reference)]:
            b=QPushButton(title);b.clicked.connect(lambda checked=False,f=fn:self.safe(f));layout.addWidget(b)
        self.aligned=QCheckBox('I checked that the two masks share the same original grid and alignment')
        layout.addWidget(self.aligned)
        self.qc_layer=None
        self.split_preview=None;self.split_layer=None;self._split_visibility=[]
        self.multiple_layers=[];self._multiple_visibility=[]
        auto_form=QFormLayout();layout.addLayout(auto_form)
        self.match_min=QDoubleSpinBox();self.match_min.setRange(.001,1);self.match_min.setDecimals(3);self.match_min.setSingleStep(.05);self.match_min.setValue(.5)
        self.second_max=QDoubleSpinBox();self.second_max.setRange(0,1);self.second_max.setDecimals(3);self.second_max.setSingleStep(.05);self.second_max.setValue(.2)
        auto_form.addRow('Minimum best DAPI overlap fraction',self.match_min)
        auto_form.addRow('Maximum runner-up DAPI fraction',self.second_max)
        for title,fn in [('Run automatic matching',self.run_automatic),('Automatic proportions and volumes',self.plot_automatic),
                         ('Select best overlap candidate',self.best_candidate)]:
            b=QPushButton(title);b.clicked.connect(lambda checked=False,f=fn:self.safe(f));layout.addWidget(b)
        self.auto_group=QComboBox();self.auto_group.addItems(['All','MATCH','AMBIGUOUS','NO_MATCH'])
        auto_form.addRow('Show automatic group',self.auto_group)
        self.auto_group.currentTextChanged.connect(lambda *_:self.update_qc())
        self.auto_note=QLabel('Automatic: green MATCH; yellow AMBIGUOUS; red NO_MATCH. Manual decisions are preserved.');self.auto_note.setWordWrap(True);layout.addWidget(self.auto_note)
        self.sample=QLineEdit();self.animal=QLineEdit();form2=QFormLayout();layout.addLayout(form2)
        form2.addRow('Sample ID',self.sample);form2.addRow('Animal ID (optional)',self.animal)
        self.dapi=QSpinBox();self.map2=QSpinBox()
        for title,w in [('DAPI mask ID',self.dapi),('MAP2 mask ID',self.map2)]:
            w.setRange(0,2147483647);form2.addRow(title,w)
        self.comment=QLineEdit();form2.addRow('Comment (optional)',self.comment)
        self.reveal=QCheckBox('Show volumes during matching');layout.addWidget(self.reveal)
        self.only=QCheckBox('Show selected objects only');layout.addWidget(self.only)
        self.multiple=QCheckBox('Show multiple associations');layout.addWidget(self.multiple)
        self.multiple.setToolTip('Show all DAPI objects currently linked to a shared MAP2 object. Uses automatic and manual decisions.')
        self.multiple_note=QLabel();self.multiple_note.setWordWrap(True);layout.addWidget(self.multiple_note)
        self.multiple.toggled.connect(self.toggle_multiple)
        self.seed_fraction=QDoubleSpinBox();self.seed_fraction.setRange(.001,1);self.seed_fraction.setDecimals(3);self.seed_fraction.setValue(.5)
        layout.addWidget(QLabel('Minimum DAPI fraction inside MAP2 for split seeds'));layout.addWidget(self.seed_fraction)
        for title,fn in [('Preview MAP2 split from DAPI',self.preview_map2_split),
                         ('Accept MAP2 split',self.accept_map2_split),('Cancel split preview',self.cancel_split)]:
            b=QPushButton(title);b.clicked.connect(lambda checked=False,f=fn:self.safe(f));layout.addWidget(b)
        self.split_note=QLabel('Split: select a MAP2 containing at least two qualifying DAPI seeds. Preview before accepting.');self.split_note.setWordWrap(True);layout.addWidget(self.split_note)
        for title,fn in [('Pick DAPI object',lambda:self.pick(0)),('Pick MAP2 object',lambda:self.pick(1)),
                         ('Link selected objects',lambda:self.decide('Matched')),
                         ('Mark DAPI as unmatched',lambda:self.decide('Unmatched')),
                         ('Mark DAPI as uncertain',lambda:self.decide('Uncertain')),
                         ('Remove link / Reset review',lambda:self.decide('Not reviewed')),
                         ('Undo last decision',self.undo)]:
            b=QPushButton(title);b.clicked.connect(lambda checked=False,f=fn:self.safe(f));layout.addWidget(b)
        self.details=QLabel('Load both label TIFFs. Volumes are hidden during matching.');self.details.setWordWrap(True);layout.addWidget(self.details)
        self.one=QCheckBox('Compare one-to-one matches only');layout.addWidget(self.one)
        self.proportions=QCheckBox('Histogram: fraction of each group');self.proportions.setChecked(True);layout.addWidget(self.proportions)
        for title,fn in [('Compare DAPI volumes',self.compare),('Save matching and comparison…',self.save)]:
            b=QPushButton(title);b.clicked.connect(lambda checked=False,f=fn:self.safe(f));layout.addWidget(b)
        self.message=QLabel();self.message.setWordWrap(True);layout.addWidget(self.message)
        self.dapi.valueChanged.connect(self.select_dapi);self.map2.valueChanged.connect(self.select_map2)
        self.reveal.toggled.connect(self.refresh);self.only.toggled.connect(self.isolate)
        for w in [self.sample,self.animal]:w.textChanged.connect(self.mark_dirty)
        for w in [self.one,self.proportions]:w.toggled.connect(self.mark_dirty)
        for l in self.layers:l.editable=False

    def preview_map2_split(self):
        model=self.checked()
        self.cancel_split()
        preview=model.preview_split(self.map2.value(),self.seed_fraction.value())
        self.multiple.setChecked(False);self.only.setChecked(False)
        self.split_preview=preview
        self._split_visibility=[(l,l.visible) for l in self.layers+([self.qc_layer] if self.qc_layer is not None else [])]
        for l,_ in self._split_visibility:l.visible=False
        self.split_layer=self.viewer.add_labels(preview['data'].copy(),name='MAP2 split preview — editable',
                                               scale=model.spacing,opacity=.65)
        self.split_layer.units=('um',)*3
        self.split_layer.selected_label=next(iter(preview['children'].values()))
        self.viewer.layers.selection.active=self.split_layer
        mapping=', '.join(f'DAPI {d} → MAP2 {c}' for d,c in preview['children'].items())
        self.split_note.setText('PREVIEW ONLY. '+mapping+'\nInspect across Z with a reference image. Paint between proposed child IDs if needed; preserve the original volume and DAPI seed voxels. Accept or cancel before saving.')

    def cancel_split(self):
        if self.split_layer is not None and self.split_layer in self.viewer.layers:
            self.viewer.layers.remove(self.split_layer)
        for l,visible in self._split_visibility:
            if l in self.viewer.layers:l.visible=visible
        self.split_layer=None;self.split_preview=None;self._split_visibility=[]
        self.split_note.setText('No pending split preview.')

    def accept_map2_split(self):
        model=self.checked()
        if self.split_preview is None or self.split_layer not in self.viewer.layers:
            raise ValueError('Generate a split preview first.')
        validate_roi_geometry(self.split_layer,model.spacing,model.map2.shape)
        model.accept_split(self.split_preview,self.split_layer.data)
        self.cancel_split();self.dirty=True
        self.sync_map2_geometry()
        self.message.setText('MAP2 split accepted. Linked DAPI partners now use the child IDs. Undo restores the previous geometry and links. Save matching to export corrected masks.')

    def sync_map2_geometry(self):
        self.layers[1].data=self.model.map2.astype(np.uint32,copy=False)
        self.select_dapi(self.dapi.value());self.update_qc();self.refresh()

    def run_automatic(self):
        model=self.checked()
        self.cancel_split()
        model.run_automatic(self.match_min.value(),self.second_max.value())
        self.only.setChecked(False)
        self.dirty=True;self.select_dapi(self.dapi.value());self.update_qc()
        self.message.setText('All nuclei classified. Manual decisions preserved. Save matching to keep results.')

    def update_qc(self):
        if not self.model:return
        if not self.model.auto_rows:
            if self.qc_layer is not None and self.qc_layer in self.viewer.layers:self.viewer.layers.remove(self.qc_layer)
            self.qc_layer=None;self.auto_note.setText('No automatic result yet. Run automatic matching.');self.update_multiple();return
        from .automatic_matching import COLORS
        from napari.utils.colormaps import DirectLabelColormap
        rows=self.model.auto_rows;group=self.auto_group.currentText()
        selected=[r['dapi_id'] for r in rows if group=='All' or r['auto_class']==group]
        data=self.model.dapi if group=='All' else np.where(np.isin(self.model.dapi,selected),self.model.dapi,0)
        color_dict={None:(0,0,0,0),0:(0,0,0,0),**{r['dapi_id']:COLORS[r['auto_class']] for r in rows}}
        cmap=DirectLabelColormap(color_dict=color_dict)
        if self.qc_layer is None or self.qc_layer not in self.viewer.layers:
            self.qc_layer=self.viewer.add_labels(data,name='DAPI_MATCH_QC',scale=self.model.spacing,colormap=cmap,opacity=.7)
            self.qc_layer.units=('um',)*3;self.qc_layer.editable=False
            self.qc_layer.mouse_drag_callbacks.append(self.on_click)
            self.qc_layer.events.selected_label.connect(lambda e:self.dapi.setValue(int(e.source.selected_label)))
        else:self.qc_layer.data=data;self.qc_layer.colormap=cmap
        self.qc_layer.visible=True;self.layers[0].visible=False;self.layers[1].visible=False
        self.viewer.layers.selection.active=self.qc_layer
        setting=self.model.auto_settings
        counts={k:sum(r['auto_class']==k for r in rows) for k in COLORS}
        self.auto_note.setText(f"Automatic counts: {counts}\nGreen MATCH; yellow AMBIGUOUS; red NO_MATCH.\nLast run: best ≥ {setting['match_min']:.3f}, runner-up ≤ {setting['second_match_max']:.3f}.\nQC and automatic plots show pre-review results.")

        self.update_multiple()

    def toggle_multiple(self, enabled):
        if enabled:
            self.cancel_split()
            self._multiple_visibility=[(layer,layer.visible) for layer in self.layers + ([self.qc_layer] if self.qc_layer is not None else [])]
            self.only.setChecked(False)
            self.update_multiple()
        else:
            for layer in self.multiple_layers:
                if layer in self.viewer.layers:layer.visible=False
            for layer,visible in self._multiple_visibility:
                if layer in self.viewer.layers:layer.visible=visible
            self._multiple_visibility=[]
            self.multiple_note.setText('')

    def update_multiple(self):
        """Refresh the inspection layers from current recorded links, not QC classes."""
        if not self.multiple.isChecked():return
        if self.model is None:
            self.multiple_note.setText('Load both masks first.');return
        table=self.model.table()
        rows=table.loc[table.status.eq('Matched') & table.multiple_association]
        dapi_ids=rows.dapi_id.to_numpy(dtype=np.int64)
        map2_ids=rows.map2_id.dropna().astype('int64').unique()
        for i,(name,source,ids) in enumerate([
            ('Multiple associations — DAPI',self.model.dapi,dapi_ids),
            ('Multiple associations — MAP2',self.model.map2,map2_ids),
        ]):
            data=np.where(np.isin(source,ids),source,0).astype(np.uint32,copy=False)
            if len(self.multiple_layers)<=i or self.multiple_layers[i] not in self.viewer.layers:
                layer=self.viewer.add_labels(data,name=name,scale=self.model.spacing,opacity=.65)
                layer.units=('um',)*3;layer.editable=False
                layer.mouse_drag_callbacks.append(self.on_click)
                selector=[self.dapi,self.map2][i]
                layer.events.selected_label.connect(lambda e,w=selector:w.setValue(int(e.source.selected_label)))
                if len(self.multiple_layers)<=i:self.multiple_layers.append(layer)
                else:self.multiple_layers[i]=layer
            else:
                layer=self.multiple_layers[i];layer.data=data
            layer.show_selected_label=False;layer.visible=True
            layer.selected_label=[self.dapi,self.map2][i].value()
        for layer in self.layers:layer.visible=False
        if self.qc_layer is not None:self.qc_layer.visible=False
        self.viewer.layers.selection.active=self.multiple_layers[0]
        self.multiple_note.setText(
            f'{len(map2_ids)} shared MAP2 objects; {len(dapi_ids)} linked DAPI objects. '
            'Current automatic + manual links. Updates after each decision. '
            'Click either inspection layer to select an object; colors identify labels, not match status.'
            if len(rows) else 'No multiple associations in the current recorded links.'
        )

    def best_candidate(self):
        model=self.checked();row=next((r for r in model.auto_rows if r['dapi_id']==self.dapi.value()),None)
        if row is None:raise ValueError('Run automatic matching and select a DAPI ID first.')
        if row['best_map2_id'] is None:raise ValueError('This DAPI object has no MAP2 overlap.')
        self.cancel_split();self.multiple.setChecked(False)
        self.map2.setValue(row['best_map2_id']);self.only.setChecked(True)
        self.layers[0].visible=True;self.layers[1].visible=True
        if self.qc_layer is not None:self.qc_layer.visible=False
        self.message.setText('Best candidate selected; Link selected objects confirms or changes the recorded association.')

    def plot_automatic(self):
        model=self.checked()
        if not model.auto_rows:raise ValueError('Run automatic matching first.')
        from .automatic_matching import plot_automatic
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        dialog=QDialog(self);dialog.setWindowTitle('Automatic matching — proportions and DAPI volumes')
        box=QVBoxLayout(dialog);box.addWidget(FigureCanvasQTAgg(plot_automatic(model.auto_rows,model.sample_id)))
        box.addWidget(QLabel('All loaded DAPI objects. Automatic classes before manual review; ambiguous objects are a separate group.'))
        dialog.resize(1100,500);self.plots.append(dialog);dialog.show()

    def mark_dirty(self,*_):
        if self.model:self.dirty=True

    def safe(self,fn):
        try:fn()
        except Exception as exc:self.message.setText(str(exc))

    def browse(self,edit):
        path,_=QFileDialog.getOpenFileName(self,'Select label TIFF','','TIFF (*.tif *.tiff)')
        if path:edit.setText(path)

    def may_replace(self):
        return not (self.dirty or self.split_preview is not None) or QMessageBox.question(self,'Unsaved matching','Discard unsaved matching changes?')==QMessageBox.StandardButton.Yes

    def load(self):
        if not self.may_replace():return
        fallback=[float(x) for x in self.spacing.text().split(',')] if self.spacing.text().strip() else None
        model=Matching(self.paths[0].text(),self.paths[1].text(),fallback)
        self.attach(model)

    def attach(self,model):
        self.cancel_split()
        if any(max(v)>2147483647 for v in model.volumes):raise ValueError('Mask IDs exceed the supported selector range; use label IDs below 2^31.')
        self.multiple.setChecked(False)
        for layer in self.multiple_layers:
            if layer in self.viewer.layers:self.viewer.layers.remove(layer)
        self.multiple_layers=[]
        for layer in self.layers+self.references:
            if layer in self.viewer.layers:self.viewer.layers.remove(layer)
        if self.qc_layer is not None and self.qc_layer in self.viewer.layers:self.viewer.layers.remove(self.qc_layer)
        self.qc_layer=None
        self.layers=[];self.references=[];self.model=model
        for name,data in [('DAPI masks — matching',model.dapi),('MAP2 masks — matching',model.map2.astype(np.uint32,copy=False))]:
            layer=self.viewer.add_labels(data,name=name,scale=model.spacing,opacity=.5)
            layer.units=('um',)*3;layer.editable=False;layer.mouse_drag_callbacks.append(self.on_click)
            layer.events.selected_label.connect(self.on_selection);self.layers.append(layer)
        self.viewer.dims.axis_labels=('Z','Y','X');self.viewer.dims.ndisplay=2
        self.sample.setText(model.sample_id);self.animal.setText(model.animal_id)
        self.aligned.setChecked(False);self.only.setChecked(False);self.dapi.setValue(0);self.map2.setValue(0)
        self.reveal.setChecked(False);self.dirty=False;self.refresh()
        if model.auto_settings:
            self.match_min.setValue(model.auto_settings['match_min']);self.second_max.setValue(model.auto_settings['second_match_max'])
        self.update_qc()
        self.message.setText('Check alignment across Z, then confirm above. Click Pick DAPI / Pick MAP2 to activate each layer.')

    def checked(self, alignment=True):
        if self.model is None:raise ValueError('Load DAPI and MAP2 masks first.')
        for layer in self.layers:
            if layer not in self.viewer.layers:raise ValueError('A matching layer was removed. Reload the matching workspace.')
            validate_roi_geometry(layer,self.model.spacing,self.model.dapi.shape)
        if alignment and not self.aligned.isChecked():raise ValueError('Review and confirm alignment before recording or saving matches.')
        self.model.sample_id=self.sample.text().strip();self.model.animal_id=self.animal.text().strip()
        if not self.model.sample_id:raise ValueError('Enter a sample ID.')
        return self.model

    def on_click(self,layer,event):
        if event.button!=1 or self.viewer.dims.ndisplay!=2:return
        coords=np.asarray(layer.world_to_data(event.position),dtype=float)
        if coords.shape!=(3,) or not np.isfinite(coords).all():return
        index=np.floor(coords+.5).astype(int)
        if np.all(index>=0) and np.all(index<layer.data.shape):layer.selected_label=int(layer.data[tuple(index)])

    def on_selection(self,event):
        for layer,selector in zip(self.layers,[self.dapi,self.map2]):
            if event.source is layer:selector.setValue(int(layer.selected_label))

    def select_dapi(self,value):
        if not self.model:return
        if self.layers[0].selected_label!=value:self.layers[0].selected_label=value
        if self.qc_layer is not None and self.qc_layer.selected_label!=value:self.qc_layer.selected_label=value
        if self.multiple_layers and self.multiple_layers[0].selected_label!=value:self.multiple_layers[0].selected_label=value
        r=self.model.records.get(value,{})
        self.map2.setValue(r.get('map2_id') or 0);self.comment.setText(r.get('comment',''));self.refresh()

    def select_map2(self,value):
        if len(self.multiple_layers)>1 and self.multiple_layers[1].selected_label!=value:self.multiple_layers[1].selected_label=value
        if self.layers and self.layers[1].selected_label!=value:self.layers[1].selected_label=value
        self.refresh()

    def isolate(self,checked):
        if checked:self.multiple.setChecked(False)
        for layer in self.layers:layer.show_selected_label=checked
        if self.qc_layer is not None:self.qc_layer.show_selected_label=checked

    def pick(self,index):
        self.checked(False);self.cancel_split();self.multiple.setChecked(False);self.viewer.dims.ndisplay=2
        self.viewer.layers.selection.active=self.layers[index];self.layers[index].visible=True
        self.layers[index].mode='pan_zoom'
        if index==0:self.only.setChecked(False)

    def decide(self,status):
        model=self.checked();model.set(self.dapi.value(),status,self.map2.value(),self.comment.text())
        self.cancel_split()
        self.dirty=True;self.select_dapi(self.dapi.value());self.update_multiple();self.message.setText('Decision recorded. Save matching to preserve it.')

    def undo(self):
        self.checked();self.cancel_split();self.model.undo();self.dirty=True;self.sync_map2_geometry()

    def refresh(self,*_):
        if not self.model:return
        table=self.model.table();counts=table.status.value_counts();label=self.dapi.value()
        r=self.model.records.get(label,{})
        lines=[f'{s}: {counts.get(s,0)}' for s in ['Matched','Unmatched','Uncertain','Not reviewed']]
        lines += [f'DAPI {label}: '+r.get('status','Not reviewed'),f'Selected MAP2: {self.map2.value()}', f'Recorded MAP2 link: {r.get("map2_id") or "none"}']
        lines.append('Decision source: '+r.get('source','manual' if r else 'none'))
        auto=next((a for a in self.model.auto_rows if a['dapi_id']==label),None)
        if auto:
            lines.append(f"Automatic: {auto['auto_class']}; best MAP2 {auto['best_map2_id']}; overlap {auto['best_dapi_fraction']:.1%}; runner-up {auto['second_dapi_fraction']:.1%}")
        partner=self.map2.value()
        shared=table.loc[table.map2_id.eq(partner),'dapi_id'].tolist()
        if shared:lines.append('DAPI IDs linked to this MAP2: '+', '.join(map(str,shared)))
        if len(shared)>1:lines.append('Multiple association: inspect possible merged MAP2 objects.')
        if self.reveal.isChecked():
            for title,key,vol in [('DAPI',label,self.model.volumes[0]),('MAP2',partner,self.model.volumes[1])]:
                if key in vol:lines.append(f'{title} volume: {vol[key]:.4g} µm³')
        self.details.setText('\n'.join(lines))

    def compare(self):
        model=self.checked();fig=model.figure(self.one.isChecked(),self.proportions.isChecked())
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        dialog=QDialog(self);dialog.setWindowTitle('DAPI volume comparison');box=QVBoxLayout(dialog)
        box.addWidget(FigureCanvasQTAgg(fig));summary=model.summary(self.one.isChecked())
        box.addWidget(QLabel(summary[['status','n','median_um3','iqr_um3']].to_string(index=False)))
        box.addWidget(QLabel('Uncertain / Not reviewed are excluded. Descriptive results for this sample; no pooled animal-level inference.'))
        dialog.resize(950,600);self.plots.append(dialog);dialog.show()

    def save(self):
        model=self.checked()
        if self.split_preview is not None:raise ValueError('Accept or cancel the split preview before saving.')
        parent=QFileDialog.getExistingDirectory(self,'Select export parent folder')
        if not parent:return
        folder=model.save(parent,self.one.isChecked(),self.proportions.isChecked());self.dirty=False
        self.message.setText('Saved matching, CSV tables and plots: '+str(folder))

    def open_saved(self):
        if not self.may_replace():return
        path,_=QFileDialog.getOpenFileName(self,'Open dapi_matching.json','','JSON (*.json)')
        if not path:return
        record=json.loads(Path(path).read_text());paths=list(record['paths'])
        if record.get('relative_mask_paths'):
            paths=[str(Path(path).parent/n) for n in record['relative_mask_paths']]
        for i,title in enumerate(['DAPI','MAP2']):
            if not Path(paths[i]).is_file():
                replacement,_=QFileDialog.getOpenFileName(self,'Locate original '+title+' mask TIFF','','TIFF (*.tif *.tiff)')
                if not replacement:return
                paths[i]=replacement
        model=Matching(*paths,fallback=record['spacing_zyx_um']);model.restore(record,base_dir=Path(path).parent)
        self.attach(model)
        for edit,p in zip(self.paths,paths):edit.setText(p)
        self.one.setChecked(record.get('one_to_one_only',False));self.proportions.setChecked(record.get('proportions',True))
        self.dirty=False;self.message.setText('Matching restored. Confirm alignment before continuing.')

    def add_reference(self):
        model=self.checked(False);path,_=QFileDialog.getOpenFileName(self,'Reference intensity TIFF','','TIFF (*.tif *.tiff)')
        if not path:return
        from .core import load_tiff_czyx,read_voxel_spacing_um
        data,_=load_tiff_czyx(path);spacing,_=read_voxel_spacing_um(path)
        if data.shape[1:]!=model.dapi.shape or not np.allclose(spacing,model.spacing):raise ValueError('Reference image must share the mask grid and calibration.')
        for c,array in enumerate(data):
            layer=self.viewer.add_image(array,name=f'Reference C{c}: {Path(path).name}',scale=spacing,visible=c==0)
            layer.units=('um',)*3;self.references.append(layer)
            self.viewer.layers.move(self.viewer.layers.index(layer), 0)
        self.viewer.layers.selection.active=self.layers[0]

    def eventFilter(self,obj,event):
        if event.type()==QEvent.Type.Close and not self.may_replace():event.ignore();return True
        return super().eventFilter(obj,event)


def open_matching_workspace():
    import napari
    viewer=napari.Viewer(title='DAPI matching')
    panel=MatchingPanel(viewer);scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(panel);scroll.setMinimumWidth(400)
    viewer.window.add_dock_widget(scroll,name='DAPI matching',area='right')
    viewer.window._qt_window.installEventFilter(panel)
    return viewer,panel
