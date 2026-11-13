import numpy as np
import pytest

from nuclear_segmentation.napari_embedding import (
    DEMO_SPACING_ZYX_UM,
    DISPLAY_NAME,
    make_demo_volumes,
)


def test_demo_volumes_are_deterministic_and_independent():
    first = make_demo_volumes((8, 24, 32))
    second = make_demo_volumes((8, 24, 32))
    assert DISPLAY_NAME == "NuCytoSeg"
    assert first.nuclear.dtype == np.float32
    assert first.cytoplasmic.dtype == np.float32
    assert first.labels.dtype == np.uint16
    assert first.nuclear.shape == first.cytoplasmic.shape == first.labels.shape == (8, 24, 32)
    assert first.multiscale[1].shape == (8, 12, 16)
    np.testing.assert_array_equal(first.nuclear, second.nuclear)
    assert set(np.unique(first.labels)) == {0, 1, 2, 3}
    first.labels[0, 0, 0] = 99
    assert second.labels[0, 0, 0] == 0
    assert DEMO_SPACING_ZYX_UM[0] > DEMO_SPACING_ZYX_UM[1]


@pytest.mark.parametrize("shape", [(7, 24, 32), (8, 7, 32), (8, 24, 7), (8, 24)])
def test_demo_volumes_reject_invalid_shape(shape):
    with pytest.raises(ValueError):
        make_demo_volumes(shape)
