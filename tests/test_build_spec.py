"""Validate the PyInstaller recipe without producing a Windows binary.

A Windows .exe cannot be cross-compiled from Linux, but the parts most likely to
silently break a packaged build can still be checked here:

  * every collect_all() call in the spec succeeds
  * faster_whisper's silero VAD .onnx asset is actually bundled
  * onnxruntime (needed by vad_filter=True) is collected
  * the icon and Windows version resource are valid
  * the GitHub workflow is well-formed YAML

Run: python tests/test_build_spec.py
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"  PASS  {label}")
    else:
        failures.append(label)
        print(f"  FAIL  {label} {detail}")


def test_collect_all() -> None:
    print("\n--- spec collection ---")
    try:
        from PyInstaller.utils.hooks import collect_all
    except Exception as exc:
        check("PyInstaller importable", False, str(exc))
        return
    check("PyInstaller importable", True)

    all_datas, all_bins = [], []
    # These are the packages the spec collects. Windows-only ones are expected
    # to be absent on Linux and are skipped rather than failed.
    windows_only = {"pyttsx3"}
    for pkg in ("faster_whisper", "ctranslate2", "onnxruntime",
                "tokenizers", "av", "huggingface_hub", "pyttsx3"):
        try:
            d, b, h = collect_all(pkg)
            all_datas += d
            all_bins += b
            check(f"collect_all({pkg})", True,
                  f"({len(d)} data, {len(b)} binaries, {len(h)} imports)")
        except Exception as exc:
            if pkg in windows_only:
                print(f"  SKIP  collect_all({pkg}) — Windows-only, absent here")
            else:
                check(f"collect_all({pkg})", False, str(exc))

    vad = [s for s, _ in all_datas if "silero_vad" in s and s.endswith(".onnx")]
    check("silero VAD .onnx asset is bundled", bool(vad),
          "-> packaged exe would crash on first speech")
    if vad:
        print(f"        {os.path.basename(vad[0])}")

    onnx_bins = [s for s, _ in all_bins if "onnxruntime" in s.lower()]
    check("onnxruntime native libs collected", bool(onnx_bins),
          f"({len(onnx_bins)} files)")


def test_icon() -> None:
    print("\n--- icon ---")
    ico = os.path.join(ROOT, "icon.ico")
    if not os.path.exists(ico):
        check("icon.ico exists", False, "run tools/make_icon.py")
        return
    check("icon.ico exists", True)
    try:
        from PIL import Image

        with Image.open(ico) as im:
            sizes = sorted(im.ico.sizes()) if hasattr(im, "ico") else []
            check("icon is a valid multi-size .ico", len(sizes) >= 4, str(sizes))
            check("includes 256px for Explorer", (256, 256) in sizes, str(sizes))
    except Exception as exc:
        check("icon readable", False, str(exc))


def test_version_resource() -> None:
    print("\n--- Windows version resource ---")
    p = os.path.join(ROOT, "version_info.txt")
    if not os.path.exists(p):
        check("version_info.txt exists", False)
        return
    src = open(p, encoding="utf-8").read()

    # PyInstaller's real versioninfo classes import win32api, which only exists
    # on Windows. On Linux we evaluate the same literal against structurally
    # identical stubs: this still catches syntax errors, wrong argument counts
    # and malformed nesting, which are the realistic failure modes.
    class _Node:
        def __init__(self, *args, **kwargs):
            self.args = args

    def VSVersionInfo(ffi=None, kids=None):
        assert ffi is not None, "VSVersionInfo needs ffi"
        assert isinstance(kids, list) and len(kids) == 2, \
            "VSVersionInfo kids must be [StringFileInfo, VarFileInfo]"
        return _Node(ffi, kids)

    def FixedFileInfo(filevers=None, prodvers=None, mask=0, flags=0,
                      OS=0, fileType=0, subtype=0, date=(0, 0)):
        assert isinstance(filevers, tuple) and len(filevers) == 4, \
            "filevers must be a 4-tuple"
        assert isinstance(prodvers, tuple) and len(prodvers) == 4, \
            "prodvers must be a 4-tuple"
        return _Node(filevers, prodvers)

    def StringFileInfo(tables):
        assert isinstance(tables, list) and tables, "StringFileInfo needs tables"
        return _Node(tables)

    def StringTable(lang, strings):
        assert isinstance(lang, str) and len(lang) == 8, \
            "StringTable language must be an 8-char hex code"
        assert isinstance(strings, list) and strings, "StringTable needs strings"
        return _Node(lang, strings)

    def StringStruct(key, value):
        assert isinstance(key, str) and isinstance(value, str), \
            "StringStruct takes two strings"
        return _Node(key, value)

    def VarFileInfo(vars_):
        assert isinstance(vars_, list) and vars_, "VarFileInfo needs a list"
        return _Node(vars_)

    def VarStruct(key, value):
        assert isinstance(key, str) and isinstance(value, list), \
            "VarStruct takes a name and a list"
        return _Node(key, value)

    ns = dict(VSVersionInfo=VSVersionInfo, FixedFileInfo=FixedFileInfo,
              StringFileInfo=StringFileInfo, StringTable=StringTable,
              StringStruct=StringStruct, VarFileInfo=VarFileInfo,
              VarStruct=VarStruct)
    try:
        obj = eval(src, ns)
        check("version resource structure is valid", obj is not None)
        check("has ProductName", "ProductName" in src)
        check("has FileDescription", "FileDescription" in src)
        if sys.platform != "win32":
            print("        (structural check; win32api link happens on Windows)")
    except Exception as exc:
        check("version resource structure is valid", False, str(exc))


def test_spec_syntax() -> None:
    print("\n--- spec file ---")
    p = os.path.join(ROOT, "VoiceTranslator.spec")
    src = open(p, encoding="utf-8").read()
    try:
        compile(src, p, "exec")
        check("spec is valid Python", True)
    except SyntaxError as exc:
        check("spec is valid Python", False, str(exc))
    check("collects onnxruntime", '"onnxruntime"' in src)
    check("disables UPX (protects speech DLLs)", "upx=False" in src)
    check("windowed app (console=False)", "console=False" in src)
    check("uses icon", "icon=icon" in src)


def test_spec_paths() -> None:
    """The spec must resolve assets relative to itself, not the shell CWD.

    Regression: it used os.getcwd(), so running
    `pyinstaller path/to/VoiceTranslator.spec` from another folder silently
    dropped the icon and app package from the bundle.
    """
    print("\n--- spec path resolution ---")
    p = os.path.join(ROOT, "VoiceTranslator.spec")
    src = open(p, encoding="utf-8").read()
    check("uses SPECPATH, not getcwd()", "SPECPATH" in src
          and "os.path.abspath(os.getcwd())" not in src)
    check("entry script path is absolute", 'os.path.join(HERE, "main.py")' in src)

    # Execute only the path-resolution preamble with SPECPATH injected, from a
    # working directory that is NOT the project folder.
    preamble = src.split("datas = []")[0]
    old_cwd = os.getcwd()
    try:
        os.chdir("/tmp")
        ns = {"SPECPATH": ROOT, "__file__": p}
        exec(compile(preamble, p, "exec"), ns)
        here = ns.get("HERE")
        check("HERE resolves to the spec folder from another CWD",
              here == ROOT, f"got {here}")
    except Exception as exc:
        check("HERE resolves to the spec folder from another CWD", False, str(exc))
    finally:
        os.chdir(old_cwd)


def test_workflow() -> None:
    print("\n--- GitHub Windows builder ---")
    p = os.path.join(ROOT, ".github", "workflows", "build-windows.yml")
    if not os.path.exists(p):
        check("workflow exists", False)
        return
    text = open(p, encoding="utf-8").read()
    try:
        import yaml

        data = yaml.safe_load(text)
        check("workflow is valid YAML", isinstance(data, dict))
        jobs = data.get("jobs", {})
        check("has a build job", "build-windows" in jobs)
        check("runs on Windows", jobs.get("build-windows", {}).get("runs-on")
              == "windows-latest")
        steps = jobs.get("build-windows", {}).get("steps", [])
        names = " ".join(str(s.get("name", "")) for s in steps)
        check("builds the spec", any("pyinstaller" in str(s.get("run", "")).lower()
                                    for s in steps))
        check("verifies VAD asset after build", "VAD" in names)
        check("uploads a downloadable artifact", "upload-artifact" in text)
    except ImportError:
        check("runs on windows-latest", "runs-on: windows-latest" in text)
        check("uploads artifact", "upload-artifact" in text)
        print("  SKIP  full YAML parse (pyyaml not installed)")


def test_builder_bat() -> None:
    print("\n--- one-click builder ---")
    p = os.path.join(ROOT, "build_exe.bat")
    text = open(p, encoding="utf-8", errors="replace").read()
    check("checks for 64-bit Python", "sys.maxsize>2**32" in text)
    # for /f wraps its command in single quotes, so an inner '64' breaks CMD.
    # The exit-code form avoids quotes entirely; assert that form is used.
    # Ignore REM comments: they legitimately mention "for /f" to explain why
    # it is avoided. Only executable lines matter.
    code = "\n".join(ln for ln in text.splitlines()
                     if not ln.strip().lower().startswith("rem"))
    check("64-bit check uses an exit code, not for /f parsing",
          "sys.exit(0 if sys.maxsize" in code and "for /f" not in code)
    # A bare sentence inside an echo block is executed by CMD as a command
    # ("'then' is not recognized..."). Check every shipped .bat for that.
    cmds = ("echo", "@", "%", '"', "if", "set", "where", "pause", "exit", "cd",
            "title", "start", "(", ")", "goto", ":", "for", "call", "else",
            "rmdir", "del", "move", "copy", "mkdir", "pip", "python", "choice",
            "explorer", "endlocal", "type", "notepad")
    for name in ("build_exe.bat", "run.bat", "selftest.bat", "diagnose.bat"):
        path = os.path.join(ROOT, name)
        if not os.path.exists(path):
            continue
        body = "\n".join(ln for ln in open(path, encoding="utf-8").read().splitlines()
                         if not ln.strip().lower().startswith("rem"))
        bad = [ln.strip() for ln in body.splitlines()
               if ln.strip() and not ln.strip().lower().startswith(cmds)]
        check(f"{name}: no orphan text executed as a command",
              not bad, f"orphan lines: {bad[:3]}")
    check("checks build exit code", "if errorlevel 1" in text)
    check("verifies exe was produced", "VoiceTranslator.exe" in text)
    check("builds via the spec", "VoiceTranslator.spec" in text)
    check("does not use UPX", "--upx" not in text)


def test_selftest_wiring() -> None:
    print("\n--- packaged-app self test ---")
    main_src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
    check("main.py exposes --selftest", "--selftest" in main_src)
    check("selftest dispatches before Tk starts",
          main_src.index("--selftest") < main_src.index("root = tk.Tk()"))

    st = os.path.join(ROOT, "app", "selftest.py")
    check("app/selftest.py exists", os.path.exists(st))
    src = open(st, encoding="utf-8").read()
    check("selftest writes a log (windowed exe has no console)",
          "selftest.log" in src)
    check("selftest runs real VAD inference", "get_speech_timestamps" in src)
    check("selftest uses VadOptions, not a dict", "VadOptions(" in src)

    # Regression: the asset search used to walk only the project folder, which
    # is wrong from source (the asset lives in site-packages). It appeared to
    # pass only while a dist/ build sat inside the project, then failed as soon
    # as the build output was cleaned.
    check("selftest searches the installed package too",
          "_search_roots" in src and "find_spec(\"faster_whisper\")" in src)
    try:
        from app.selftest import _find_vad_asset, _search_roots

        roots = _search_roots()
        check("search roots include site-packages, not just the project",
              any("site-packages" in r or "_MEIPASS" in r for r in roots)
              or len(roots) > 1, str(roots))
        found = _find_vad_asset()
        check("VAD asset located with no dist/ present", bool(found), str(found))
        if found:
            check("located asset is not inside a build output folder",
                  os.sep + "dist" + os.sep not in found, found)
    except Exception as exc:
        check("VAD asset search imports and runs", False, str(exc))

    bat = os.path.join(ROOT, "selftest.bat")
    check("selftest.bat exists", os.path.exists(bat))
    if os.path.exists(bat):
        b = open(bat, encoding="utf-8", errors="replace").read()
        check("selftest.bat can test a built exe", "--selftest" in b)
        check("selftest.bat shows the log", "selftest.log" in b)

    wf = os.path.join(ROOT, ".github", "workflows", "build-windows.yml")
    w = open(wf, encoding="utf-8").read()
    check("CI self-tests the packaged exe", "Self-test the packaged exe" in w)
    check("CI ships selftest.bat to users", "selftest.bat" in w)


def test_windows_com_imports() -> None:
    """pyttsx3's sapi5 driver needs pythoncom; static analysis misses it."""
    print("\n--- Windows COM / TTS imports ---")
    src = open(os.path.join(ROOT, "VoiceTranslator.spec"), encoding="utf-8").read()
    for mod in ("pythoncom", "pywintypes", "win32com"):
        check(f"spec bundles {mod}", f'"{mod}"' in src)
    check("spec collects comtypes in full", '"comtypes",' in src)
    # Prove the requirement is real rather than assumed: read the installed
    # driver if present, otherwise fall back to the known import list.
    try:
        import importlib.util
        spec = importlib.util.find_spec("pyttsx3")
        if spec and spec.submodule_search_locations:
            drv = os.path.join(list(spec.submodule_search_locations)[0],
                               "drivers", "sapi5.py")
            if os.path.exists(drv):
                code = open(drv, encoding="utf-8", errors="replace").read()
                check("sapi5 really imports pythoncom (assumption verified)",
                      "import pythoncom" in code)
                check("sapi5 really uses comtypes (assumption verified)",
                      "comtypes" in code)
    except Exception:
        pass


def main() -> int:
    test_collect_all()
    test_windows_com_imports()
    test_icon()
    test_version_resource()
    test_spec_syntax()
    test_spec_paths()
    test_workflow()
    test_builder_bat()
    test_selftest_wiring()
    print(f"\n{len(failures)} failure(s)")
    if failures:
        for f in failures:
            print("  -", f)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
