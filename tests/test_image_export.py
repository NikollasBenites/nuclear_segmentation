import json
import xml.etree.ElementTree as ET
import numpy as np
import pytest
import tifffile
from nuclear_segmentation.image_export import export_images, export_layers


def test_four_channel_roundtrip(tmp_path):
    arrays = [np.arange(60, dtype=np.float32).reshape(3, 4, 5) * (i + .125) for i in range(4)]
    names = ['DAPI', 'MAP2 — Z corrected', 'VGLUT', 'CC3']
    path = export_images(tmp_path/'four.ome.tif', arrays, names, [.68,.28,.28], [{'gain':[1,2,3]}])
    with tifffile.TiffFile(path) as tif:
        assert tif.series[0].axes == 'CZYX'
        np.testing.assert_array_equal(tif.asarray(), np.stack(arrays))
        assert tif.asarray().dtype == np.float32
        root = ET.fromstring(tif.ome_metadata)
        pixels = root.find('.//{*}Pixels')
        assert float(pixels.attrib['PhysicalSizeZ']) == .68
        assert pixels.attrib['PhysicalSizeXUnit'] == 'µm'
        assert [c.attrib['Name'] for c in pixels.findall('{*}Channel')] == names
    record = json.loads((tmp_path/'four.ome.tif.json').read_text())
    assert record['channel_names'] == names
    assert record['channels'][0]['gain'] == [1,2,3]
    with pytest.raises(ValueError): export_images(path, arrays, names, [1,1,1])


def test_single_channel_and_invalid_inputs(tmp_path):
    a = np.ones((3,4,5),dtype=np.float32)*1.234
    path=export_images(tmp_path/'one', [a], ['MAP2'], [1,2,3])
    np.testing.assert_array_equal(tifffile.imread(path), a)
    with pytest.raises(ValueError): export_images(tmp_path/'bad', [a,a[:1]], ['A','B'], [1,1,1])
    with pytest.raises(ValueError): export_images(tmp_path/'bad', [a], ['A'], [0,1,1])
    with pytest.raises(ValueError): export_images(path, [a], ['A'], [1,1,1], protected_paths=[path])


def test_layer_geometry(tmp_path):
    from napari.layers import Image, Labels
    a = Image(np.ones((3,4,5)), scale=(1,2,3), name='A')
    b = Image(np.ones((3,4,5)), scale=(1,2,3), name='B')
    b.translate=(1,0,0)
    with pytest.raises(ValueError): export_layers(tmp_path/'bad', [a,b], [1,2,3])
    with pytest.raises(ValueError): export_layers(tmp_path/'bad', [Labels(np.ones((3,4,5),dtype=int))], [1,2,3])
