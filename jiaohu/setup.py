import subprocess
import sys
from importlib.metadata import version, PackageNotFoundError

REQUIRED = {
    "pyserial": "3.5",
    "numpy": None,
    "PyQt5": None,
    "pyqtgraph": None,
    "pymodbus": None,
    "minimalmodbus": None,
    "pandas": None,
    "openpyxl": None,
}

PYTHON = sys.executable


def get_installed():
    installed = {}
    for pkg in REQUIRED:
        try:
            installed[pkg] = version(pkg)
        except PackageNotFoundError:
            installed[pkg] = None
    return installed


def install(package, ver=None):
    spec = f"{package}=={ver}" if ver else package
    print(f"正在安装 {spec} ...")
    subprocess.check_call([PYTHON, "-m", "pip", "install", spec])


def main():
    installed = get_installed()

    missing = [p for p, v in installed.items() if v is None]
    outdated = [
        p for p, v in installed.items()
        if v is not None and REQUIRED[p] is not None
        and tuple(map(int, v.split("."))) < tuple(map(int, REQUIRED[p].split(".")))
    ]

    if not missing and not outdated:
        print("所有依赖已满足，无需安装。")
        return

    for pkg in missing:
        install(pkg, REQUIRED.get(pkg))

    for pkg in outdated:
        install(pkg, REQUIRED[pkg])

    print("\n依赖安装完成！")
    print("已安装版本:")
    for pkg, ver in get_installed().items():
        print(f"  {pkg}: {ver}")


if __name__ == "__main__":
    main()
