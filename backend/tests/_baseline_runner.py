#!/usr/bin/env python3
"""
Baseline runner — corre cada archivo de test en su propio subproceso con
timeout de pared. En Windows, un test async que cuelga mata el proceso entero
(pytest-timeout usa os._exit), perdiendo el resumen. Aislando por archivo,
un cuelgue solo pierde ESE archivo y lo marcamos HUNG.

Uso: venv/Scripts/python.exe tests/_baseline_runner.py
Salida: tests/_baseline_report.txt (CSV: file,passed,failed,errors,skipped,xfail,status)
"""
import re
import subprocess
import sys
import time
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TESTS_DIR.parent
PY = sys.executable
PER_FILE_WALL = 90  # segundos de pared por archivo antes de declararlo HUNG

# CUARENTENA: archivos que lanzan/manipulan Edge/Firefox REAL vía CDP attach.
# NO se corren — matarían la sesión de Edge del usuario (mismo incidente que
# disparó esta auditoría). Se reportan como QUARANTINED.
_QUARANTINE_SUBSTR = (
    "firefox", "edge", "browser", "playwright", "ytmusic", "youtube",
    "selectors", "visible", "navigation", "cdp", "close_tab", "quick_browser",
)

# La cuarentena mira el NOMBRE del archivo, no lo que hace. Eso es a propósito
# —es conservador—, pero atrapa de más: un test que razona SOBRE el browser sin
# abrir ninguno queda fuera de la suite por llamarse "browser_algo", y entonces
# no protege nada.
#
# Un archivo entra acá solo si NO instancia `BrowserController`, `BrowserConfig`
# ni `AppController`. El patrón seguro es `BrowserController.__new__(...)` más
# stubs, que no abre nada. Verificalo antes de agregar un nombre.
_QUARANTINE_EXEMPT = frozenset({
    "test_browser_page_ownership.py",      # __new__ + FakePage, cero Playwright
    "test_navigate_uses_your_browser.py",  # __new__ + AsyncMock, cero Playwright
})


def is_quarantined(name: str) -> bool:
    if Path(name).name in _QUARANTINE_EXEMPT:
        return False
    low = name.lower()
    return any(s in low for s in _QUARANTINE_SUBSTR)

# Patrón del resumen de pytest: "X passed, Y failed, Z skipped in 1.23s"
_SUMMARY_RE = re.compile(
    r"(?:(\d+) passed)?(?:.*?(\d+) failed)?(?:.*?(\d+) error)?"
    r"(?:.*?(\d+) skipped)?(?:.*?(\d+) xfailed)?",
)


def parse_counts(text: str):
    passed = failed = errors = skipped = xfail = 0
    for kind, val in re.findall(r"(\d+) (passed|failed|error|errors|skipped|xfailed|xpassed)", text):
        n = int(kind)
        if val == "passed":
            passed = n
        elif val == "failed":
            failed = n
        elif val in ("error", "errors"):
            errors = n
        elif val == "skipped":
            skipped = n
        elif val == "xfailed":
            xfail = n
    return passed, failed, errors, skipped, xfail


def main():
    files = sorted(
        f for f in TESTS_DIR.glob("test_*.py")
    )
    report = TESTS_DIR / "_baseline_report.txt"
    lines = ["file,passed,failed,errors,skipped,xfail,status,seconds"]
    totals = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "xfail": 0}
    hung_files, error_files, failed_files, quarantined = [], [], [], []

    for i, f in enumerate(files, 1):
        rel = f.relative_to(BACKEND_DIR).as_posix()

        if is_quarantined(f.name):
            quarantined.append(rel)
            lines.append(f"{rel},0,0,0,0,0,QUARANTINED,0")
            print(f"[{i}/{len(files)}] {rel}: QUARANTINED (browser real, no se corre)", flush=True)
            continue

        t0 = time.time()
        status = "OK"
        try:
            proc = subprocess.run(
                [PY, "-m", "pytest", str(f), "-q", "--tb=no",
                 "--timeout=20", "-p", "no:cacheprovider", "-p", "no:randomly"],
                cwd=str(BACKEND_DIR),
                capture_output=True,
                text=True,
                timeout=PER_FILE_WALL,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            p, fa, er, sk, xf = parse_counts(out)
        except subprocess.TimeoutExpired:
            status = "HUNG"
            p = fa = er = sk = xf = 0
            hung_files.append(rel)

        dur = round(time.time() - t0, 1)
        if status != "HUNG":
            totals["passed"] += p
            totals["failed"] += fa
            totals["errors"] += er
            totals["skipped"] += sk
            totals["xfail"] += xf
            if er > 0:
                status = "COLLECT_ERR"
                error_files.append(rel)
            elif fa > 0:
                status = "FAIL"
                failed_files.append(rel)

        lines.append(f"{rel},{p},{fa},{er},{sk},{xf},{status},{dur}")
        print(f"[{i}/{len(files)}] {rel}: {status} (p{p} f{fa} e{er} s{sk} x{xf}) {dur}s", flush=True)

    lines.append("")
    lines.append(f"# TOTALS passed={totals['passed']} failed={totals['failed']} "
                 f"errors={totals['errors']} skipped={totals['skipped']} xfail={totals['xfail']}")
    lines.append(f"# HUNG ({len(hung_files)}): {', '.join(hung_files)}")
    lines.append(f"# COLLECT_ERR ({len(error_files)}): {', '.join(error_files)}")
    lines.append(f"# FAIL ({len(failed_files)}): {', '.join(failed_files)}")
    lines.append(f"# QUARANTINED ({len(quarantined)}): {', '.join(quarantined)}")
    report.write_text("\n".join(lines), encoding="utf-8")
    print("\n=== DONE ===")
    print(f"passed={totals['passed']} failed={totals['failed']} errors={totals['errors']} "
          f"skipped={totals['skipped']} xfail={totals['xfail']}")
    print(f"HUNG={len(hung_files)} COLLECT_ERR={len(error_files)} FAIL_FILES={len(failed_files)} "
          f"QUARANTINED={len(quarantined)}")
    print(f"reporte: {report}")
    # Exit != 0 si algo falló: sin esto la CI quedaba verde con tests rotos.
    return 1 if (failed_files or error_files or hung_files) else 0


if __name__ == "__main__":
    sys.exit(main())
