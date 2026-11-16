# NuCytoSeg Phase 2A.2 Leica LIF import

Phase 2A.2 adds optional direct Leica LIF visualization through BioIO and
`bioio-lif`. Fiji, ImageJ, Java, and Bio-Formats are not required.

## Installation

```bash
python3.12 -m venv .venv-nucytoseg
source .venv-nucytoseg/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[gui,lif,test]'
QT_API=pyqt6 nucytoseg
```

The verified dependency set in the development environment was BioIO 3.7.1,
bioio-lif 2.0.1, and liffile 2026.11.12. LIF dependencies remain optional so
TIFF-only installations do not acquire proprietary-format readers.

## Import behavior

- The Image Series Browser enumerates acquisitions before any complete pixel
  volume is read.
- It displays the original BioIO/Leica scene identifier, dimensions, channels,
  Z planes, time points, objective magnification, calibration, and acquisition
  date when those values are available.
- Missing values are displayed as `Not available`; values are not fabricated.
- Only the explicitly selected series and time point are computed from BioIO's
  Dask-backed CZYX access.
- Pixel values, channel order, and dtype are preserved.
- BioIO channel names are retained as original reader metadata and remain
  user-editable in Phase 2A.3. LUT-derived names such as `Green` are not treated
  as fluorophore identities.
- Available raw Leica channel attributes for LUT/name/wavelength fields are
  retained in project acquisition metadata without biological interpretation.
- Reader instances are released after catalog enumeration or selected-volume
  computation.

BioIO mosaic reconstruction is deliberately disabled. Multi-tile series are
listed but blocked with an explanation until their stage geometry and stitching
behavior can be validated. Multi-sample pixel layouts are also blocked.

## Validation boundary

Synthetic/mock tests validate series selection, dimensions, time selection,
channel ordering, calibration, cleanup, missing values, optional dependency
behavior, and unsupported layouts. The optional dependency set installs and
imports under Python 3.12 in an isolated environment.

No representative Leica Stellaris LIF file was available in the development
environment. Real-file parsing, Leica XML completeness, memory behavior, and
native macOS rendering therefore remain pending native validation and must not
be inferred from the mocked tests.
