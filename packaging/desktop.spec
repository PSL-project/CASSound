# Native OS builds. Model packs and FFmpeg are installed separately.
from pathlib import Path
import os
from PyInstaller.utils.hooks import collect_data_files,copy_metadata
from importlib.metadata import PackageNotFoundError
root=Path(SPECPATH).parent
data=collect_data_files('cassound',includes=['resources/*','resources/model-notices/*/*'])
hidden=['pyloudnorm','ssqueezepy','librosa','sounddevice','sklearn.cluster','sklearn.model_selection','sklearn.metrics','cassound.runtime.worker','cassound.runtime.laboratory','cassound.runtime.extensions','matplotlib.backends.backend_svg','matplotlib.backends.backend_pdf','matplotlib.backends.backend_agg']
for distribution in ['cassound','numpy','scipy','soundfile','librosa','ssqueezepy','scikit-learn','pyloudnorm','numba','llvmlite','soxr','cryptography','onnxruntime','matplotlib','pyarrow']:
    try:data.extend(copy_metadata(distribution))
    except PackageNotFoundError:pass
for module in ['librosa','ssqueezepy']:
    data.extend(collect_data_files(module,include_py_files=True))
native=[]
host=os.environ.get('CASSOUND_VST3_HOST')
if host:
    path=Path(host).resolve()
    if not path.is_file():raise RuntimeError('Native VST3 host is missing')
    native.append((str(path),'cassound/native'))
    licenses=path.parent/'licenses'
    if not licenses.is_dir():raise RuntimeError('Native host licenses are required')
    data.extend((str(p),'cassound/native/licenses') for p in licenses.glob('*.txt'))
a=Analysis([str(root/'packaging/launch.py')],pathex=[str(root/'src')],binaries=native,datas=data,hiddenimports=hidden,excludes=['PySide6.QtWebEngineCore','PySide6.QtWebEngineWidgets','PySide6.QtQml','PySide6.QtQuick'],noarchive=False)
pyz=PYZ(a.pure)
icon=str(root/'src/cassound/resources/cassound.ico')
gui=EXE(pyz,a.scripts,[],exclude_binaries=True,name='CASSound',icon=icon,console=False,upx=False)
cli=EXE(pyz,a.scripts,[],exclude_binaries=True,name='CASSoundCLI',icon=icon,console=True,upx=False)
coll=COLLECT(gui,cli,a.binaries,a.datas,strip=False,upx=False,name='CASSound')
