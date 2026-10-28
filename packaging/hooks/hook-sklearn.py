"""Preserve scikit-learn's dynamically loaded Windows runtimes at their wheel paths."""

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_delvewheel_libs_directory,
)

# _distributor_init uses ctypes.WinDLL with absolute sklearn/.libs paths.
# Dependency scanning alone can omit these DLLs or place them at the bundle root.
datas = collect_data_files("sklearn")
binaries = collect_dynamic_libs("sklearn")
# Also support wheels repaired into a sibling delvewheel directory.
datas, binaries = collect_delvewheel_libs_directory(
    "sklearn", libdir_name="scikit_learn.libs", datas=datas, binaries=binaries
)
